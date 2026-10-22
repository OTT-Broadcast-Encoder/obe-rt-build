#!/usr/bin/env python3
"""Real encrypted SRT telemetry, ARQ, history and alarms on loopback only.

Run inside obe-webpanel with its encoder stopped. The initial profile is restored.
The existing UDP impairment proxy drops 1/23 original packets and delays 1/17
by 80 ms. No production endpoint, router change or retained credential.
"""
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.request import urlopen


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


srt = module('input_srt', 'input-srt.py')
live = module('webpanel_live', 'webpanel-live.py')
loss = module('srt_loss', 'srt-loss.py')
base = srt.base


def alarm_active(state, code):
    return any(alarm['code'] == code for alarm in state['monitoring']['active_alarms'])


def main():
    original = live.request()
    assert not original['process_alive'] and not original.get('recovery', {}).get('active'), 'Encoder must be stopped'
    directory = Path(tempfile.mkdtemp(prefix='webpanel-srt-', dir=base.ROOT/'tests/.build'))
    print(f'Sanitized artifacts: {directory}', flush=True)
    source = srt.reconnect.make_source(directory)
    listener_port = base.port()
    proxy = loss.DatagramProxy(listener_port, impaired=True)
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(('127.0.0.1', 0))
    receiver.settimeout(.2)
    finished = threading.Event()
    media = directory/'output.ts'

    def capture():
        with media.open('wb') as output:
            while not finished.is_set():
                try:
                    output.write(receiver.recv(65536))
                except socket.timeout:
                    pass

    capture_thread = threading.Thread(target=capture)
    capture_thread.start()
    key = srt.new_key()
    sender = None
    try:
        config = dict(original['config'], name='Collaudo SRT statistiche e allarmi',
                      input_url=srt.address(listener_port, 'listener').replace('latency=200000', 'latency=400000'),
                      output_url=f'udp://127.0.0.1:{receiver.getsockname()[1]}', preset='ultrafast',
                      video_bitrate_kbps=3000, mux_bitrate_kbps=6500, vbv_buffer_kbits=3000,
                      bframes=0, lookahead_frames=0, gop_frames=25, threads=2, audio_bitrate_kbps=192)
        state = live.request('/api/config', {'revision': original['revision'], 'config': config}, 'PUT')
        sender = srt.Sender(source, srt.address(proxy.port, 'caller'), key)
        live.request('/api/start', {'revision': state['revision'], 'passphrase': key}, 'POST')
        live.wait(lambda s: s['phase'] == 'running' and s['signal_state'] == 'receiving', timeout=35)
        first = live.wait(lambda s: live.telemetry_ready(s) and s['srt']['status'] == 'live'
                          and (s['srt']['receive_bitrate_kbps'] or 0) > 0
                          and s['srt']['lost_packets'] > 0 and s['srt']['retransmitted_packets'] > 0)
        assert first['encrypted'] and first['srt']['latency_ms'] >= 400
        assert first['srt']['dropped_packets'] == 0, 'ARQ did not recover within latency'
        assert not alarm_active(first, 'srt_drops'), 'Recovered losses must not raise a drop alarm'
        before = live.request('/api/history?limit=1800')['total_samples']
        time.sleep(3.2)  # Collection is independent from browser/API reads.
        history = live.request('/api/history?limit=1800')
        assert history['total_samples'] >= before + 2, 'No autonomous history collection'
        sender.stop()
        sender = None
        missing = live.wait(lambda s: alarm_active(s, 'source_missing'), timeout=25)
        stale = live.wait(lambda s: s['srt']['status'] == 'stale', timeout=12)
        assert stale['srt']['rtt_ms'] is None and stale['srt']['received_packets'] is None
        print('PASS encrypted SRT statistics, ARQ and source-loss alarm', flush=True)
        sender = srt.Sender(source, srt.address(proxy.port, 'caller'), key)
        recovered = live.wait(lambda s: live.telemetry_ready(s) and s['srt']['status'] == 'live'
                              and s['srt']['connection'] > first['srt']['connection']
                              and s['phase'] == 'running' and not alarm_active(s, 'source_missing'), timeout=35)
        time.sleep(4)
        current = live.request()
        assert not current['monitoring']['active_alarms'], 'An alarm failed to recover'
        live.request('/api/stop', {}, 'POST')
        stopped = live.request()
        assert stopped['srt']['status'] == 'idle' and stopped['srt']['rtt_ms'] is None
        sender.stop()
        sender = None
        finished.set()
        capture_thread.join(timeout=2)
        history = live.request('/api/history?limit=1800&format=json')
        run_ids = {sample['run_id'] for sample in history['samples']
                   if sample['context']['name'] == config['name']}
        transitions = [event for event in history['alarms'] if event['run_id'] in run_ids
                       and event['code'] == 'source_missing']
        assert [event['state'] for event in transitions] == ['active', 'resolved'], 'Missing or duplicate alarm transitions'
        with urlopen(live.API + '/api/history?limit=1800&format=csv', timeout=10) as response:
            csv_body = response.read()
        for value in (json.dumps(current).encode(), json.dumps(history).encode(), csv_body):
            assert key.encode() not in value, 'Credential in state/history/export'
        assert b'srt://' not in csv_body and 'input_url' not in json.dumps(history)
        assert proxy.dropped and proxy.recovered and proxy.delayed, 'Impairment proxy did not exercise ARQ'
        streams = base.probe(media, '-show_streams')['streams']
        assert any(stream['codec_name'] == 'h264' for stream in streams)
        assert any(stream['codec_name'] == 'mp2' for stream in streams)
        base.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(media), '-t', '2',
                  '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'],
                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        result = {'status': 'PASS', 'scope': 'local encrypted PAL SRT with induced loss/delay',
                  'first': first['srt'], 'recovered': recovered['srt'], 'stopped': stopped['srt'],
                  'alarm_transitions': transitions, 'history_samples': history['total_samples'],
                  'proxy': {'dropped_once': len(proxy.dropped), 'recovered': len(proxy.recovered), 'delayed': proxy.delayed},
                  'output_bytes': media.stat().st_size,
                  'checks': ['encrypted input', 'RTT/buffer/loss/retransmissions', 'ARQ without drop alarm',
                             'autonomous history', 'stale metrics hidden', 'source alarm/recovery',
                             'new connection counters', 'JSON/CSV without credentials', 'AVC/MP2 decode']}
        (directory/'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result), flush=True)
    finally:
        if sender:
            sender.stop()
        live.request('/api/stop', {}, 'POST')
        finished.set()
        capture_thread.join(timeout=2)
        receiver.close()
        proxy.close()
        state = live.request()
        live.request('/api/config', {'revision': state['revision'], 'config': original['config']}, 'PUT')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(f'FAIL: {srt.sanitize(str(error))}', file=sys.stderr)
        raise SystemExit(1) from None
