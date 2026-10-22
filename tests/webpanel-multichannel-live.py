#!/usr/bin/env python3
"""Two real encoders with independent synthetic sources; run in the test container."""
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time

spec = importlib.util.spec_from_file_location('live', Path(__file__).with_name('webpanel-live.py'))
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)
base = live.base


def api(channel, resource='state', body=None, method='GET'):
    return live.request(f'/api/channels/{channel}/{resource}', body, method)


def wait(channel, predicate, seconds=30):
    deadline = time.monotonic()+seconds
    while time.monotonic() < deadline:
        state = api(channel)
        if predicate(state): return state
        if state['phase'] == 'error': raise AssertionError(state['message'])
        time.sleep(.25)
    raise AssertionError(f'{channel}: {state["phase"]}, {state["message"]}')


def main():
    base.ROOT.joinpath('tests/.build').mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='multichannel-', dir=base.ROOT/'tests/.build'))
    channels = live.request('/api/channels')['channels']
    assert not any(c['process_alive'] for c in channels), 'Use an idle, isolated panel'
    if len(channels) == 1:
        live.request('/api/channels', {'name': 'Banco PAL'}, 'POST')
    ids = ['main', 'channel-2']
    originals = {c: api(c) for c in ids}
    senders, receivers, workers = [], [], []
    done = threading.Event()

    def capture(receiver, path):
        with path.open('wb') as output:
            while not done.is_set():
                try: output.write(receiver.recv(65536))
                except socket.timeout: pass

    try:
        for channel, size in zip(ids, [(1920, 1080), (720, 576)]):
            source_port = base.port()
            receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            receiver.bind(('127.0.0.1', 0)); receiver.settimeout(.2)
            receivers.append(receiver)
            worker = threading.Thread(target=capture, args=(receiver, directory/f'{channel}.ts'))
            worker.start(); workers.append(worker)
            config = dict(originals[channel]['config'], input_url=f'udp://127.0.0.1:{source_port}',
                          output_url=f'udp://127.0.0.1:{receiver.getsockname()[1]}', preset='ultrafast',
                          bframes=0, lookahead_frames=0, gop_frames=25, threads=2)
            saved = api(channel, 'config', {'revision': originals[channel]['revision'], 'config': config}, 'PUT')
            command = ['ffmpeg', '-nostdin', '-v', 'error', '-re', '-f', 'lavfi', '-i',
                       f'testsrc2=size={size[0]}x{size[1]}:rate=50', '-f', 'lavfi', '-i',
                       'sine=frequency=997:sample_rate=48000', '-filter_threads', '1',
                       '-vf', 'interlace=scan=tff:lowpass=0', '-c:v', 'mpeg2video', '-threads', '2',
                       '-flags', '+ilme+ildct', '-q:v', '4', '-bf', '0', '-g', '12',
                       '-c:a', 'mp2', '-b:a', '192k', '-ac', '2', '-f', 'mpegts',
                       f'udp://127.0.0.1:{source_port}?pkt_size=1316']
            senders.append(subprocess.Popen(command, env=base.ENV, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            api(channel, 'start', {'revision': saved['revision']}, 'POST')
        states = {c: wait(c, live.telemetry_ready) for c in ids}
        assert states['main']['pid'] != states['channel-2']['pid']
        time.sleep(5)
        for c in ids:
            after = api(c)
            assert after['telemetry']['video_frames'] > states[c]['telemetry']['video_frames']
            assert after['telemetry']['output_errors'] == 0
        api('channel-2', 'stop', {}, 'POST')
        assert not api('channel-2')['process_alive']
        assert api('main')['pid'] == states['main']['pid']
        api('main', 'stop', {}, 'POST')
        done.set()
        for worker in workers: worker.join(2)
        for c, expected in zip(ids, [(1920, 1080), (720, 576)]):
            media = directory/f'{c}.ts'
            video = next(s for s in base.probe(media, '-show_streams')['streams'] if s['codec_type'] == 'video')
            assert (video['codec_name'], video['width'], video['height']) == ('h264', *expected)
            base.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(media), '-t', '2', '-f', 'null', '-'],
                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        result = {'status': 'PASS', 'checks': ['two real encoders', 'independent stop', 'AVC decode PAL and HD'],
                  'channels': {c: {key: states[c][key] for key in ('pid', 'signal', 'telemetry')} for c in ids}}
        (directory/'result.json').write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(result)); print(f'Artifacts: {directory}')
    finally:
        for c in ids: api(c, 'stop', {}, 'POST')
        for sender in senders: base.stop_sender(sender)
        done.set()
        for worker in workers: worker.join(2)
        for receiver in receivers: receiver.close()
        for c in ids:
            api(c, 'config', {'revision': api(c)['revision'], 'config': originals[c]['config']}, 'PUT')


if __name__ == '__main__': main()
