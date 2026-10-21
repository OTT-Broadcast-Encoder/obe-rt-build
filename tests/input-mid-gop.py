#!/usr/bin/env python3
"""Live H.264 join before SPS/PPS: raw paced TS preserves the missing headers."""
import importlib.util
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time

spec = importlib.util.spec_from_file_location('base', Path(__file__).with_name('input-url.py'))
base = importlib.util.module_from_spec(spec); spec.loader.exec_module(base)


def main():
    directory = Path(tempfile.mkdtemp(prefix='mid-gop-', dir=base.ROOT/'tests/.build'))
    source, output, log = directory/'source.ts', directory/'output.ts', directory/'obe.log'
    base.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=720x576:rate=50',
              '-f', 'lavfi', '-i', 'sine=sample_rate=48000', '-t', '25', '-filter_threads', '1',
              '-vf', 'interlace=scan=tff:lowpass=0,setsar=16/15', '-c:v', 'libx264', '-preset', 'ultrafast',
              '-threads', '2', '-flags', '+ilme+ildct', '-pix_fmt', 'yuv420p', '-b:v', '1200k',
              '-maxrate', '1200k', '-bufsize', '1200k', '-g', '50', '-keyint_min', '50', '-sc_threshold', '0',
              '-bf', '0', '-color_primaries', 'smpte170m', '-color_trc', 'smpte170m', '-colorspace', 'smpte170m',
              '-c:a', 'mp2', '-b:a', '192k', '-ac', '2', '-muxrate', '3000000', '-f', 'mpegts', str(source)],
             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    data = source.read_bytes()
    offset = int(.4*3000000/8)//1316*1316
    address = ('127.0.0.1', base.port())
    stopped = threading.Event()
    def send():
        started = time.monotonic()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            for index in range(offset, len(data)-1315, 1316):
                if stopped.wait(max(0, started+(index-offset)*8/3000000-time.monotonic())): break
                sock.sendto(data[index:index+1316], address)
    config = directory/'encoder.cfg'
    config.write_text('set obe opts system-type=generic,max-probe-time=8\nset input url\n'
                      f'set input opts location=udp://{address[0]}:{address[1]}\nprobe input\n'
                      'set stream opts 0:format=avc,preset=ultrafast,bitrate=5000,vbv-maxrate=5000,vbv-bufsize=5000,thread=2,bframes=0,keyint=25,rc-lookahead=0\n'
                      'set stream opts 1:format=mp2,bitrate=192,channel-map=stereo\nset muxer mpegts\n'
                      'set muxer opts ts-muxrate=6500000,cbr=1,ts-type=dvb\nset outputs 1\n'
                      f'set output opts 0:type=filets,target={output}\nstart\n')
    encoder = worker = None
    try:
        with log.open('wb') as handle:
            encoder = subprocess.Popen(['stdbuf', '-oL', '-eL', str(base.ROOT/'run.sh'), '-c', str(config)],
                                       cwd=base.ROOT, env=base.ENV, stdin=subprocess.PIPE,
                                       stdout=handle, stderr=subprocess.STDOUT, text=True)
            time.sleep(.3)
            worker = threading.Thread(target=send); worker.start()
            deadline = time.monotonic()+21
            while time.monotonic() < deadline:
                assert encoder.poll() is None, 'OBE exited; inspect the test log'
                if output.exists() and output.stat().st_size > 3*6500000//8: break
                time.sleep(.1)
            else: raise AssertionError('OBE did not recover a mid-GOP join within its bounded timeout')
            assert 'non-existing PPS' in log.read_text(), 'Fixture did not exercise missing codec headers'
            base.stop_obe(encoder)
        base.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(output), '-t', '2', '-f', 'null', '-'],
                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        print(f'PASS mid-GOP join, probe, encoding and decode. Artifacts: {directory}')
    finally:
        stopped.set()
        if worker: worker.join(2)
        if encoder and encoder.poll() is None: base.stop_obe(encoder)
        print(f'Artifacts: {directory}')


if __name__ == '__main__': main()
