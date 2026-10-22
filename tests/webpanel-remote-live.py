#!/usr/bin/env python3
"""Opt-in remote contribution smoke; changes only an idle channel in the test panel."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

spec = importlib.util.spec_from_file_location('multi', Path(__file__).with_name('webpanel-multichannel-live.py'))
multi = importlib.util.module_from_spec(spec); spec.loader.exec_module(multi)
api, base = multi.api, multi.base


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--control-socket')
    parser.add_argument('--known-hosts')
    parser.add_argument('--panel', default='http://127.0.0.1:18088')
    parser.add_argument('--container', default='obe-webpanel')
    parser.add_argument('--channel', default='channel-3')
    args = parser.parse_args()
    multi.live.API = args.panel
    original = api(args.channel)
    assert not original['process_alive'], 'Use an idle, isolated test channel'
    directory = Path(tempfile.mkdtemp(prefix='remote-panel-', dir=base.ROOT/'tests/.build'))
    relay = None
    try:
        config = dict(original['config'], input_url='udp://0.0.0.0:12030', output_url='udp://127.0.0.1:13030',
                      preset='ultrafast', bframes=0, lookahead_frames=0, gop_frames=25, threads=2,
                      video_bitrate_kbps=5000, mux_bitrate_kbps=6500, vbv_buffer_kbits=5000)
        saved = api(args.channel, 'config', {'revision': original['revision'], 'config': config}, 'PUT')
        command = [sys.executable, 'gateway/ts_ssh.py', '--host', args.host, '--source', args.source,
                   '--container', args.container, '--udp-port', '12030', '--seconds', '60',
                   '--report', str(directory/'relay.json')]
        if args.control_socket: command += ['--control-socket', args.control_socket]
        if args.known_hosts: command += ['--known-hosts', args.known_hosts]
        relay = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        api(args.channel, 'start', {'revision': saved['revision']}, 'POST')
        running = multi.wait(args.channel, multi.live.telemetry_ready, 40)
        api(args.channel, 'preview', {}, 'POST')
        ready = multi.wait(args.channel, lambda s: s['preview']['status'] == 'live', 20)
        with urlopen(args.panel+f'/api/channels/{args.channel}/preview-frame', timeout=5) as response:
            frame = response.read(1048577)
        assert frame.startswith(b'\xff\xd8') and frame.endswith(b'\xff\xd9') and len(frame) <= 1048576
        time.sleep(5)
        after = api(args.channel)
        assert after['telemetry']['video_frames'] > running['telemetry']['video_frames']
        assert after['telemetry']['output_errors'] == 0
        stdout, stderr = relay.communicate(timeout=65)
        assert relay.returncode == 0, 'Remote relay failed; inspect its bounded report'
        transport = json.loads(stdout)
        assert transport['status'] == 'pass' and transport['forwarded_bytes'] > 0
        result = {'status': 'PASS', 'scope': 'remote source via SSH/TCP, not SRT Internet or an HD pilot',
                  'signal': ready['signal'], 'telemetry': after['telemetry'], 'preview_jpeg_bytes': len(frame),
                  'transport': transport}
        (directory/'result.json').write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(result)); print(f'Artifacts: {directory}')
    finally:
        api(args.channel, 'stop', {}, 'POST')
        if relay and relay.poll() is None:
            relay.terminate()
            try: relay.communicate(timeout=5)
            except subprocess.TimeoutExpired: relay.kill(); relay.communicate(timeout=3)
        api(args.channel, 'config', {'revision': api(args.channel)['revision'], 'config': original['config']}, 'PUT')


if __name__ == '__main__': main()
