#!/usr/bin/env python3
"""Relay an existing remote UDP MPEG-TS through SSH into a local test encoder."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import select
import shlex
import signal
import socket
import subprocess
import time
from urllib.parse import urlsplit

PACKET, DATAGRAM = 188, 1316


class Packetizer:
    def __init__(self):
        self.pending = bytearray()
        self.discarded = 0

    def feed(self, chunk):
        self.pending.extend(chunk)
        while len(self.pending) >= DATAGRAM:
            if all(self.pending[i] == 0x47 for i in range(0, DATAGRAM, PACKET)):
                datagram = bytes(self.pending[:DATAGRAM])
                del self.pending[:DATAGRAM]
                yield datagram
            else:
                position = self.pending.find(b'\x47', 1)
                skip = len(self.pending)-PACKET*2 if position < 0 else position
                self.discarded += skip
                del self.pending[:skip]


def source_url(value):
    parts = urlsplit(value)
    try:
        address = ipaddress.IPv4Address(parts.hostname)
        if (parts.scheme != 'udp' or not parts.port or not 1 <= parts.port <= 65535
                or parts.username or parts.path or parts.query or parts.fragment): raise ValueError()
    except (ValueError, TypeError): raise ValueError('Use udp://IPv4:port without credentials or options') from None
    return f'udp://{address}:{parts.port}'


def destination(container, port):
    if container is None: return '127.0.0.1', port
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,100}', container): raise ValueError('Invalid container name')
    result = subprocess.run(['docker', 'inspect', '--format', '{{json .NetworkSettings.Networks}}', container],
                            capture_output=True, text=True, timeout=5, check=True)
    networks = json.loads(result.stdout)
    addresses = [entry.get('IPAddress') for entry in networks.values() if entry.get('IPAddress')]
    if len(addresses) != 1: raise ValueError('Use a running test container with one bridge network')
    address = ipaddress.IPv4Address(addresses[0])
    if not address.is_private or address.is_loopback: raise ValueError('Expected a private container bridge address')
    return str(address), port


def stop(process):
    if process.poll() is None:
        process.terminate()
        try: process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=2)
    process.stdout.close()


def relay(command, target, seconds, retries, max_mbps, idle_timeout=10):
    started, deadline = time.monotonic(), time.monotonic()+seconds
    stats = {'transport': 'SSH/TCP to local UDP', 'connections': 0, 'retries': 0, 'received_bytes': 0,
             'forwarded_bytes': 0, 'datagrams': 0, 'framing_discarded_bytes': 0,
             'udp_packet_loss': None, 'srt_stats': None, 'reason': 'duration_reached'}
    interrupted = False
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as output:
        output.settimeout(.2)
        try:
            while time.monotonic() < deadline:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                stats['connections'] += 1
                packetizer = Packetizer()
                last_data = window_started = time.monotonic()
                window_bytes = 0
                reason = 'duration_reached'
                try:
                    while time.monotonic() < deadline:
                        if not select.select([process.stdout], [], [], min(.2, max(0, deadline-time.monotonic())))[0]:
                            if time.monotonic()-last_data > idle_timeout:
                                reason = 'source_timeout'; break
                            continue
                        chunk = os.read(process.stdout.fileno(), 65536)
                        if not chunk:
                            reason = 'ssh_stream_ended'; break
                        last_data = time.monotonic()
                        stats['received_bytes'] += len(chunk); window_bytes += len(chunk)
                        if last_data-window_started >= 5:
                            if window_bytes*8/(last_data-window_started)/1e6 > max_mbps:
                                reason = 'rate_limit_exceeded'; break
                            window_started, window_bytes = last_data, 0
                        for datagram in packetizer.feed(chunk):
                            output.sendto(datagram, target)
                            stats['forwarded_bytes'] += len(datagram); stats['datagrams'] += 1
                finally:
                    stats['framing_discarded_bytes'] += packetizer.discarded
                    stop(process)
                stats['reason'] = reason
                if reason in ('duration_reached', 'rate_limit_exceeded'): break
                if stats['retries'] >= retries: break
                stats['retries'] += 1
                time.sleep(min(2**stats['retries'], 30, max(0, deadline-time.monotonic())))
        except KeyboardInterrupt:
            interrupted = True
            stats['reason'] = 'interrupted'
        except OSError:
            stats['reason'] = 'local_transport_error'
    stats['elapsed_seconds'] = round(time.monotonic()-started, 3)
    stats['forwarded_mbps'] = round(stats['forwarded_bytes']*8/max(.001, stats['elapsed_seconds'])/1e6, 4)
    stats['status'] = ('interrupted' if interrupted else 'pass' if stats['reason'] == 'duration_reached'
                       and stats['forwarded_bytes'] > 0 else 'fail')
    return stats


def main():
    def interrupted(signum, frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--control-socket')
    parser.add_argument('--known-hosts', type=Path)
    parser.add_argument('--container', help='Optional local Docker test container; input must bind 0.0.0.0')
    parser.add_argument('--udp-port', type=int, default=12000)
    parser.add_argument('--seconds', type=int, default=60)
    parser.add_argument('--retries', type=int, default=5)
    parser.add_argument('--max-mbps', type=int, default=20)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]{0,250}', args.host): parser.error('Use an SSH alias or user@host')
    if not 1 <= args.udp_port <= 65535 or not 1 <= args.seconds <= 86400 or not 0 <= args.retries <= 10 or not 1 <= args.max_mbps <= 100:
        parser.error('Invalid port, duration (1–86400), retries (0–10) or limit (1–100 Mbit/s)')
    try:
        source = source_url(args.source)
        target = destination(args.container, args.udp_port)
    except (ValueError, OSError, subprocess.SubprocessError): parser.error('Check UDP source and local test container')
    command = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=6',
               '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=2', '-o', 'Compression=no']
    if args.control_socket: command += ['-S', args.control_socket]
    if args.known_hosts: command += ['-o', 'UserKnownHostsFile='+str(args.known_hosts)]
    command += [args.host, '/usr/bin/srt-live-transmit '+shlex.quote(source)+' file://con']
    result = relay(command, target, args.seconds, args.retries, args.max_mbps)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))
    return 130 if result['status'] == 'interrupted' else 0 if result['status'] == 'pass' else 1


if __name__ == '__main__': raise SystemExit(main())
