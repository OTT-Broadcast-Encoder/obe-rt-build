#!/usr/bin/env python3
"""Validate a running gateway with a separate OBE process in an isolated container.

Run after starting ssh_relay.py into this container, never against an on-air encoder.
With --reconnect, ready-to-interrupt signals when the test's SSH transport may be stopped.
"""
import argparse
import array
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parent.parent
ENV = dict(os.environ, PATH=str(ROOT/'target-root/usr/local/bin')+':'+os.environ['PATH'],
           LD_LIBRARY_PATH=str(ROOT/'target-root/usr/local/lib'))


def wait(log, text, process, timeout=35):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        content = log.read_text(errors='replace')
        if text in content:
            return content
        if process.poll() is not None:
            raise AssertionError('OBE exited before '+text)
        time.sleep(.2)
    raise AssertionError('Timeout waiting for '+text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True)
    parser.add_argument('--port', type=int, default=12000)
    parser.add_argument('--reconnect', action='store_true')
    args = parser.parse_args()
    directory = Path(args.directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    log, output = directory/'obe.log', directory/'encoded.ts'
    with log.open('w') as stdout:
        process = subprocess.Popen(['stdbuf', '-oL', '-eL', str(ROOT/'run.sh')],
            cwd=ROOT, env=ENV, stdin=subprocess.PIPE, stdout=stdout,
            stderr=subprocess.STDOUT, text=True)
        def send(command):
            process.stdin.write(command+'\n')
            process.stdin.flush()
        try:
            send('set obe opts system-type=generic,max-probe-time=8\nset input url\n'
                 f'set input opts location=udp://127.0.0.1:{args.port}\nprobe input')
            content = wait(log, 'Encoder outputs:', process)
            video = re.search(r'Input-stream-id: (\d+) - Video: RAW 720x576i 25/1fps', content)
            audio = re.search(r'Input-stream-id: (\d+) - Audio: PCM stereo 48kHz', content)
            assert video and audio, 'Unexpected gateway input format'
            send(f'set stream opts {video[1]}:format=avc,preset=ultrafast,tune=zerolatency,bitrate=5000,'
                 'vbv-maxrate=5000,vbv-bufsize=5000,thread=2,bframes=0,keyint=25,rc-lookahead=0\n'
                 f'set stream opts {audio[1]}:format=mp2,bitrate=192,channel-map=stereo\n'
                 'set muxer mpegts\nset muxer opts ts-muxrate=6500000,cbr=1,ts-type=dvb\n'
                 f'set outputs 1\nset output opts 0:type=filets,target={output}\nstart')
            wait(log, 'Encoding started', process)
            time.sleep(7)
            if args.reconnect:
                (directory/'ready-to-interrupt').write_text('Interrupt only the gateway test SSH child.\n')
                wait(log, 'URL input: retrying', process)
                wait(log, 'URL input: reconnected;', process)
                time.sleep(7)
        finally:
            if process.poll() is None:
                send('quit')
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
                    raise AssertionError('OBE did not stop cleanly')
            process.stdin.close()
    assert process.returncode == 0
    def command(argv):
        return subprocess.check_output(argv, env=ENV, stderr=subprocess.PIPE, timeout=30)
    info = json.loads(command(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(output)]))
    v, a = info['streams']
    assert (v['codec_name'], v['width'], v['height'], v['field_order']) == ('h264', 720, 576, 'tt'), v
    assert v['display_aspect_ratio'] == '16:9' and v['r_frame_rate'] == '25/1', v
    assert (a['codec_name'], a['sample_rate'], a['channels']) == ('mp2', '48000', 2), a
    packets = json.loads(command(['ffprobe', '-v', 'error', '-show_packets', '-show_entries',
        'packet=stream_index,pts_time,dts_time', '-of', 'json', str(output)]))['packets']
    intervals, steps = [], {}
    for stream in (v, a):
        selected = [p for p in packets if p['stream_index'] == stream['index'] and 'pts_time' in p]
        pts = [float(p['pts_time']) for p in selected]
        dts = [float(p['dts_time']) for p in selected]
        assert len(pts) > 100 and all(b > a for a,b in zip(dts, dts[1:])), stream['codec_type']
        intervals.append((min(pts), max(pts)))
        steps[stream['codec_type']] = max(b-a for a,b in zip(pts, pts[1:]))
    assert abs(intervals[0][0]-intervals[1][0]) < .25, intervals
    duration = min(end for _,end in intervals) - max(start for start,_ in intervals)
    assert duration >= 5
    command(['ffmpeg', '-v', 'error', '-xerror', '-i', str(output), '-t', str(duration-1),
             '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'])
    audio = array.array('f')
    audio.frombytes(command(['ffmpeg', '-v', 'error', '-i', str(output), '-map', '0:a:0',
                             '-t', '5', '-f', 'f32le', '-']))
    rms = math.sqrt(sum(x*x for x in audio)/len(audio))
    result = dict(status='PASS', reconnect=args.reconnect, output_bytes=output.stat().st_size,
                  video='720x576i25 16:9 AVC', audio='MP2 stereo 48kHz', audio_rms=rms,
                  av_initial_difference_ms=round(abs(intervals[0][0]-intervals[1][0])*1000, 3),
                  common_duration_seconds=round(duration, 3), max_packet_step_seconds=steps)
    (directory/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
