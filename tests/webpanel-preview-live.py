#!/usr/bin/env python3
"""Real HD preview lifecycle; run only in the isolated panel container."""
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
multi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(multi)
api, base = multi.api, multi.base


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hold', type=int, default=0)
    parser.add_argument('--pilot-seconds', type=int, default=0)
    args = parser.parse_args()
    assert 0 <= args.hold <= 120
    assert args.pilot_seconds == 0 or 10 <= args.pilot_seconds <= 60
    original = api('main')
    assert not original['process_alive'], 'Use an idle test channel'
    directory = Path(tempfile.mkdtemp(prefix='preview-', dir=base.ROOT/'tests/.build'))
    source_port, output_port = base.port(), base.port()
    config = dict(original['config'], name='Collaudo preview HD', input_url=f'udp://127.0.0.1:{source_port}',
                  output_url=f'udp://127.0.0.1:{output_port}', preset='ultrafast', bframes=0,
                  lookahead_frames=0, gop_frames=25, threads=2)
    sender = None
    try:
        saved = api('main', 'config', {'revision': original['revision'], 'config': config}, 'PUT')
        sender = subprocess.Popen(['ffmpeg', '-nostdin', '-v', 'error', '-re', '-f', 'lavfi', '-i',
                 'testsrc2=size=1920x1080:rate=50', '-f', 'lavfi', '-i', 'sine=sample_rate=48000',
                 '-filter_threads', '1', '-vf', 'interlace=scan=tff:lowpass=0,setsar=1',
                 '-c:v', 'mpeg2video', '-threads', '2', '-flags', '+ilme+ildct', '-q:v', '4',
                 '-bf', '0', '-g', '12', '-c:a', 'mp2', '-b:a', '192k', '-ac', '2', '-f', 'mpegts',
                 f'udp://127.0.0.1:{source_port}?pkt_size=1316'], env=base.ENV,
                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        api('main', 'start', {'revision': saved['revision']}, 'POST')
        running = multi.wait('main', lambda s: s['phase'] == 'running')
        api('main', 'preview', {}, 'POST')
        ready = multi.wait('main', lambda s: s['preview']['status'] == 'live', 25)
        with urlopen(multi.live.API+'/api/preview-frame', timeout=5) as response:
            assert response.headers['Content-Type'] == 'image/jpeg'
            frame = response.read(1048577)
        assert len(frame) <= 1048576
        picture = directory/'frame.jpg'; picture.write_bytes(frame)
        video = base.probe(picture, '-show_streams')['streams'][0]
        assert (video['width'], video['height']) == (640, 360), video
        print('Preview HD ready', flush=True)
        if args.pilot_seconds:
            time.sleep(5)
            subprocess.run([sys.executable, 'webpanel/pilot.py', '--panel', multi.live.API,
                            '--seconds', str(args.pilot_seconds), '--output', str(directory/'pilot')], check=True)
        if args.hold: time.sleep(args.hold)
        api('main', 'preview-stop', {}, 'POST')
        stopped = api('main')
        assert stopped['pid'] == running['pid'] and stopped['preview']['status'] == 'idle'
        api('main', 'preview', {}, 'POST')
        multi.wait('main', lambda s: s['preview']['status'] == 'live', 25)
        api('main', 'stop', {}, 'POST')
        assert api('main')['preview']['status'] == 'idle'
        result = {'status': 'PASS', 'checks': ['real HD decode', 'JPEG 640x360', 'independent preview stop',
                  'preview restart', 'encoder stop closes preview'], 'jpeg_bytes': len(frame), 'preview': ready['preview']}
        (directory/'result.json').write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(result)); print(f'Artifacts: {directory}')
    finally:
        api('main', 'stop', {}, 'POST')
        base.stop_sender(sender)
        api('main', 'config', {'revision': api('main')['revision'], 'config': original['config']}, 'PUT')


if __name__ == '__main__': main()
