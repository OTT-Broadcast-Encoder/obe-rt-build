#!/usr/bin/env python3
"""Run the NDI sender over SSH and expose MPEG-TS on local UDP, optionally in Docker.

The sender is uploaded to Python's stdin in memory; nothing is installed on Windows.
Password authentication is optional and requires an existing sshpass executable.
"""
import argparse
import fcntl
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time


def log(event, **details):
    print(json.dumps({'event': event, **details}), file=sys.stderr, flush=True)


def lock_destination(container, port):
    key = hashlib.sha256(f'{container or "host"}:{port}'.encode()).hexdigest()[:16]
    path = Path(tempfile.gettempdir())/f'obe-gateway-{key}.lock'
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        return fd
    except OSError:
        os.close(fd)
        raise RuntimeError('Another gateway already owns this UDP destination')


class TransportStream:
    """Retain packet boundaries despite arbitrary SSH read chunk sizes."""
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data, final=False):
        self.buffer.extend(data)
        unit = 188 if final else 1316
        while len(self.buffer) >= unit:
            packet = bytes(self.buffer[:unit])
            if any(packet[offset] != 0x47 for offset in range(0, unit, 188)):
                raise RuntimeError('SSH output is not an aligned MPEG-TS stream')
            del self.buffer[:unit]
            yield packet
        if final:
            self.buffer.clear()  # A transport interruption may truncate its final TS packet.


def write_all(fd, data, stop, timeout=8):
    view = memoryview(data)
    deadline = time.monotonic() + timeout
    while view and not stop.is_set():
        if time.monotonic() > deadline:
            raise RuntimeError('Local UDP relay stopped accepting data')
        if select.select([], [fd], [], .2)[1]:
            try:
                count = os.write(fd, view)
            except BlockingIOError:
                continue
            if not count:
                raise BrokenPipeError('Relay pipe closed')
            view = view[count:]


def sink(port):
    parser = TransportStream()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.connect(('127.0.0.1', port))
        while True:
            data = os.read(sys.stdin.fileno(), 65536)
            for packet in parser.feed(data, final=not data):
                try:
                    udp.send(packet)
                except ConnectionRefusedError:
                    # UDP consumers may close between OBE probe and encoding.
                    pass
            if not data:
                break


def remote_command(args, size):
    for value in (args.source, args.ndi_runtime, args.ffmpeg):
        if any(character in value for character in '\r\n"%&|<>^!'):
            raise ValueError('Source or executable path contains Windows shell metacharacters')
    # Read exactly the payload size without prefetching any heartbeat bytes.
    loop = 'while len(data)<n:\n chunk=os.read(0,n-len(data))\n if not chunk: raise EOFError()\n data.extend(chunk)'
    bootstrap = (f'import os,sys,msvcrt;msvcrt.setmode(0,os.O_BINARY);data=bytearray();n={size};'
                 f"exec({loop!r});exec(compile(data,'<obe-ndi-gateway>','exec'))")
    return (f'python.exe -u -c "{bootstrap}" --source "{args.source}" '
            f'--ndi-runtime "{args.ndi_runtime}" --ffmpeg "{args.ffmpeg}" '
            f'--field-order {args.field_order} --max-mbps {args.max_mbps} '
            f'--threads {args.threads} --heartbeat-timeout 10')


def start_ssh(args, code, secret):
    command = ['ssh', '-T', '-p', str(args.ssh_port), '-l', args.user,
        '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10',
        '-o', 'ServerAliveInterval=3', '-o', 'ServerAliveCountMax=3',
        '-o', 'LogLevel=ERROR', '-o', 'NumberOfPasswordPrompts=1']
    read_fd = None
    try:
        if secret is None:
            command += ['-o', 'BatchMode=yes']
        else:
            read_fd, write_fd = os.pipe()
            try:
                os.write(write_fd, secret + b'\n')
            finally:
                os.close(write_fd)
            command = ['sshpass', '-d', str(read_fd)] + command
        command += [args.host, remote_command(args, len(code))]
        return subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, pass_fds=() if read_fd is None else (read_fd,))
    finally:
        if read_fd is not None:
            os.close(read_fd)


def finish_process(process):
    if process is None:
        return
    if process.stdin and not process.stdin.closed:
        try:
            process.stdin.close()
        except OSError:
            pass
    try:
        process.wait(timeout=6)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def run(args, secret):
    lock = lock_destination(args.container, args.udp_port)
    stop = threading.Event()
    for name in (signal.SIGINT, signal.SIGTERM):
        signal.signal(name, lambda *_: stop.set())
    code = Path(__file__).with_name('ndi_sender.py').read_bytes()
    relay, process = None, None
    totals, sessions = 0, 0
    started, retry = time.monotonic(), 2
    udp = None
    try:
        if not args.container:
            udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp.connect(('127.0.0.1', args.udp_port))
        log('relay_ready', url=f'udp://127.0.0.1:{args.udp_port}', container=args.container,
            pid=os.getpid())
        while not stop.is_set() and (not args.seconds or time.monotonic()-started < args.seconds):
            process = None
            diagnostics_thread = heart = None
            session_stop = threading.Event()
            parser = TransportStream()
            received = 0
            auth_failed = threading.Event()
            try:
                if args.container and (relay is None or relay.poll() is not None):
                    finish_process(relay)
                    relay = None
                    state = subprocess.run(['docker', 'inspect', '--format', '{{.State.Running}}', args.container],
                                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
                    if state.returncode or state.stdout.strip() != b'true':
                        raise RuntimeError('Waiting for the destination container')
                    relay = subprocess.Popen(['docker', 'exec', '-i', args.container, 'python3',
                        '/work/gateway/ssh_relay.py', '--sink', '--udp-port', str(args.udp_port)], stdin=subprocess.PIPE)
                    os.set_blocking(relay.stdin.fileno(), False)
                process = start_ssh(args, code, secret)
                sessions += 1
                log('ssh_start', session=sessions, pid=process.pid)
                def diagnostics(child):
                    for raw in iter(lambda: child.stderr.readline(8192), b''):
                        if b'Permission denied' in raw or b'Host key verification failed' in raw:
                            auth_failed.set()
                        if secret:
                            raw = raw.replace(secret, b'[redacted]')
                        log('sender', message=raw.decode('utf-8', 'replace').strip()[:2000])
                diagnostics_thread = threading.Thread(target=diagnostics, args=(process,), daemon=True)
                diagnostics_thread.start()
                os.set_blocking(process.stdin.fileno(), False)
                write_all(process.stdin.fileno(), code, stop, timeout=15)
                def heartbeat(child):
                    while not session_stop.is_set() and not stop.is_set():
                        try:
                            os.write(child.stdin.fileno(), b'alive\n')
                        except (OSError, ValueError):
                            return
                        session_stop.wait(2)
                heart = threading.Thread(target=heartbeat, args=(process,), daemon=True)
                heart.start()
                last = time.monotonic()
                while not stop.is_set():
                    if args.seconds and time.monotonic()-started >= args.seconds:
                        stop.set()
                        break
                    if time.monotonic()-last > (12 if received else 30):
                        raise RuntimeError('SSH contribution timed out')
                    if not select.select([process.stdout], [], [], .25)[0]:
                        continue
                    data = os.read(process.stdout.fileno(), 65536)
                    for packet in parser.feed(data, final=not data):
                        if relay:
                            write_all(relay.stdin.fileno(), packet, stop)
                        else:
                            try:
                                udp.send(packet)
                            except ConnectionRefusedError:
                                pass
                        if not received:
                            log('forwarding', session=sessions)
                        received += len(packet)
                        totals += len(packet)
                    if not data:
                        break
                    last = time.monotonic()
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                log('session_error', message=str(error))
            finally:
                session_stop.set()
                finish_process(process)
                if heart:
                    heart.join(timeout=1)
                if diagnostics_thread:
                    diagnostics_thread.join(timeout=2)
                if process:
                    for pipe in (process.stdout, process.stderr):
                        pipe.close()
                    log('ssh_end', exit=process.returncode, bytes=received)
            if auth_failed.is_set() or (secret is not None and process and process.returncode in (5, 6, 7)):
                raise RuntimeError('SSH authentication or host verification failed; automatic retries stopped')
            if received:
                retry = 2
            if not stop.is_set():
                log('retry', seconds=retry)
                stop.wait(retry)
                retry = min(5, retry*2)
    finally:
        stop.set()
        finish_process(relay)
        if udp:
            udp.close()
        os.close(lock)
        log('relay_stopped', sessions=sessions, bytes=totals)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host')
    parser.add_argument('--user')
    parser.add_argument('--ssh-port', type=int, default=22)
    parser.add_argument('--source')
    parser.add_argument('--container', help='Existing container with this repository mounted at /work')
    parser.add_argument('--udp-port', type=int, default=12000)
    parser.add_argument('--seconds', type=float, default=0, help='Optional bounded run; 0 keeps running')
    parser.add_argument('--field-order', choices=('tff', 'bff'), default='tff')
    parser.add_argument('--max-mbps', type=int, choices=range(1, 51), default=15, metavar='1..50')
    parser.add_argument('--threads', type=int, choices=range(1, 5), default=2)
    parser.add_argument('--ndi-runtime', default=r'C:\OndaMultimedia\x64\NDI\Processing.NDI.Lib.x64.dll')
    parser.add_argument('--ffmpeg', default=r'C:\ffmpeg\bin\ffmpeg.exe')
    auth = parser.add_mutually_exclusive_group()
    auth.add_argument('--ask-password', action='store_true')
    auth.add_argument('--password-fd', type=int, help='Read a password once from an inherited descriptor')
    parser.add_argument('--sink', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 1 <= args.udp_port <= 65535 or not 1 <= args.ssh_port <= 65535 or not 0 <= args.seconds < float('inf'):
        parser.error('Invalid port or duration')
    if args.sink:
        sink(args.udp_port)
        return 0
    if not args.host or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]*', args.host):
        parser.error('A valid SSH host is required')
    if not args.user or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', args.user) or not args.source:
        parser.error('SSH user and NDI source are required')
    if args.container and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', args.container):
        parser.error('Invalid container name')
    try:
        remote_command(args, 0)
    except ValueError as error:
        parser.error(str(error))
    secret = None
    if args.ask_password:
        secret = getpass.getpass('Password SSH: ').encode()
    elif args.password_fd is not None:
        with os.fdopen(args.password_fd, 'rb') as descriptor:
            secret = descriptor.readline(4097).rstrip(b'\r\n')
    if secret is not None:
        if not secret or len(secret) > 4096 or b'\n' in secret or b'\x00' in secret:
            parser.error('Invalid SSH password')
        if not shutil.which('sshpass'):
            parser.error('Password mode requires sshpass; use an SSH key otherwise')
    try:
        run(args, secret)
    except (OSError, RuntimeError) as error:
        log('error', message=str(error))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
