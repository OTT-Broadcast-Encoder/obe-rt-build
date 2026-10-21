#!/usr/bin/env python3
"""Test the running local panel against real OBE/FFmpeg inside its container.

No production endpoints: generated PAL source and UDP output use loopback.
The saved panel profile is restored; an already-running encoder is rejected.
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
from urllib.request import Request, urlopen

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('input_url', Path(__file__).with_name('input-url.py'))
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
API = 'http://127.0.0.1:8088'


def request(path='/api/state', body=None, method='GET'):
    data = None if body is None else json.dumps(body).encode()
    req = Request(API+path, data=data, method=method, headers={'Content-Type': 'application/json'})
    with urlopen(req, timeout=15) as response:
        return json.load(response)


def wait(predicate, timeout=25):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        state = request()
        if predicate(state):
            return state
        if state['phase'] == 'error':
            raise AssertionError(state['message'])
        time.sleep(.2)
    raise AssertionError(f"Panel state timed out: {state['phase']}, {state['message']}")


def main():
    original = request()
    assert not original['process_alive'], 'Stop the encoder before running this isolated test'
    directory = Path(tempfile.mkdtemp(prefix='webpanel-live-', dir=base.ROOT/'tests/.build'))
    source_port = base.port()
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(('127.0.0.1', 0))
    receiver.settimeout(.2)
    output_port = receiver.getsockname()[1]
    finished = threading.Event()
    media = directory/'output.ts'

    def capture():
        with media.open('wb') as output:
            while not finished.is_set():
                try:
                    output.write(receiver.recv(65536))
                except socket.timeout:
                    pass

    thread = threading.Thread(target=capture)
    thread.start()
    command = ['ffmpeg', '-nostdin', '-v', 'error', '-re', '-f', 'lavfi', '-i',
               'testsrc2=size=720x576:rate=50', '-f', 'lavfi', '-i',
               'sine=frequency=997:sample_rate=48000', '-filter_threads', '1',
               '-vf', 'interlace=scan=tff:lowpass=0,setsar=16/15', '-c:v', 'mpeg2video',
               '-threads', '2', '-flags', '+ilme+ildct', '-q:v', '4', '-bf', '0',
               '-g', '12', '-c:a', 'mp2', '-b:a', '192k', '-ac', '2', '-f', 'mpegts',
               f'udp://127.0.0.1:{source_port}?pkt_size=1316']
    sender = None
    try:
        config = dict(original['config'], name='Collaudo webpanel PAL',
                      input_url=f'udp://127.0.0.1:{source_port}',
                      output_url=f'udp://127.0.0.1:{output_port}', preset='ultrafast',
                      video_bitrate_kbps=5000, mux_bitrate_kbps=6500, vbv_buffer_kbits=5000,
                      bframes=0, lookahead_frames=0, gop_frames=25, threads=2, audio_bitrate_kbps=192)
        state = request('/api/config', {'revision': original['revision'], 'config': config}, 'PUT')
        sender = subprocess.Popen(command, cwd=base.ROOT, env=base.ENV,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        request('/api/probe', {'revision': state['revision']}, 'POST')
        probe = wait(lambda s: s['phase'] == 'idle')
        assert probe['signal']['width'] == 720 and not probe['encoding_started'], probe
        request('/api/start', {'revision': state['revision']}, 'POST')
        wait(lambda s: s['phase'] == 'running' and s['signal_state'] == 'receiving')
        time.sleep(4)
        running = request()
        assert running['metrics']['memory_mb'] > 0
        base.stop_sender(sender)
        sender = None
        wait(lambda s: s['phase'] == 'reconnecting')
        sender = subprocess.Popen(command, cwd=base.ROOT, env=base.ENV,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        wait(lambda s: s['phase'] == 'running' and s['signal_state'] == 'receiving')
        time.sleep(4)
        request('/api/stop', {}, 'POST')
        stopped = wait(lambda s: s['phase'] == 'idle')
        assert not stopped['process_alive']
        finished.set()
        thread.join(timeout=2)
        streams = base.probe(media, '-show_streams')['streams']
        video = next(s for s in streams if s['codec_type'] == 'video')
        audio = next(s for s in streams if s['codec_type'] == 'audio')
        assert (video['codec_name'], video['width'], video['height']) == ('h264', 720, 576)
        assert video['field_order'] in ('tt', 'tb') and audio['codec_name'] == 'mp2'
        base.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(media), '-t', '2',
                  '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'],
                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        result = {'status': 'PASS', 'probe': probe['signal'], 'metrics': running['metrics'],
                  'output_bytes': media.stat().st_size,
                  'checks': ['probe', 'start', 'metrics', 'reconnect', 'stop', 'AVC/MP2 decode']}
        (directory/'result.json').write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(result))
        print(f'Artifacts: {directory}')
    finally:
        base.stop_sender(sender)
        request('/api/stop', {}, 'POST')
        finished.set()
        thread.join(timeout=2)
        receiver.close()
        state = request()
        request('/api/config', {'revision': state['revision'], 'config': original['config']}, 'PUT')


if __name__ == '__main__':
    main()
