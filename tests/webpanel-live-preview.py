#!/usr/bin/env python3
"""Loopback-only real AVC/AAC preview test in the existing builder container."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

spec = importlib.util.spec_from_file_location('broadcast_fixture', Path(__file__).with_name('webpanel-broadcast-live.py'))
base = importlib.util.module_from_spec(spec); spec.loader.exec_module(base)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hold', type=int, default=0)
    args = parser.parse_args()
    assert 0 <= args.hold <= 120
    with tempfile.TemporaryDirectory(prefix='obe-live-preview-') as folder:
        directory = Path(folder)
        # A fixed port is used only for the optional browser check on an isolated container.
        if args.hold:
            original = base.subprocess.Popen
            def bound_server(command, *a, **kw):
                if isinstance(command, list) and str(base.ROOT/'webpanel/server.py') in command:
                    command = [*command, '--bind', '0.0.0.0', '--port', '8095']
                return original(command, *a, **kw)
            base.subprocess.Popen = bound_server
            old_free = base.free_port
            base.free_port = lambda kind=base.socket.SOCK_DGRAM: 8095 if kind == base.socket.SOCK_STREAM else old_free(kind)
        panel = base.PanelFixture(directory)
        sender = None
        try:
            source, output = base.free_port(), base.free_port()
            state = panel.request('/api/state')
            config = dict(state['config'], input_url=f'udp://127.0.0.1:{source}', output_url=f'udp://127.0.0.1:{output}',
                          preset='ultrafast', bframes=0, lookahead_frames=0, threads=2, gop_frames=25)
            state = panel.request('/api/config', {'revision':state['revision'],'config':config}, 'PUT')
            sender = subprocess.Popen(['ffmpeg','-nostdin','-v','error','-re','-f','lavfi','-i','testsrc2=size=1920x1080:rate=50',
                    '-f','lavfi','-i','sine=frequency=997:sample_rate=48000','-filter_threads','1','-vf','interlace=scan=tff:lowpass=0',
                    '-c:v','mpeg2video','-threads','2','-flags','+ilme+ildct','-q:v','4','-bf','0','-g','12',
                    '-c:a','mp2','-b:a','192k','-ac','2','-f','mpegts',f'udp://127.0.0.1:{source}?pkt_size=1316'],
                    env=base.ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            panel.request('/api/start', {'revision':state['revision']}, 'POST')
            first = panel.wait(lambda s:s['phase']=='running')
            panel.request('/api/preview', {'mode':'video'}, 'POST')
            state = panel.wait(lambda s:s['preview']['status']=='live')
            preview = state['preview']; session = preview['session']; sequence=preview['first_sequence']
            with urlopen(panel.url+'/api/preview-init?session='+session, timeout=5) as response: data=response.read()
            for _ in range(7):
                deadline=time.monotonic()+4
                while True:
                    with urlopen(panel.url+f'/api/preview-segment?session={session}&sequence={sequence}', timeout=5) as response:
                        chunk=response.read()
                    if chunk: break
                    assert time.monotonic()<deadline
                    time.sleep(.1)
                data+=chunk; sequence+=1
            media=directory/'preview.mp4';media.write_bytes(data)
            streams=base.ffprobe(media,'-show_streams')['streams']
            video=next(s for s in streams if s['codec_type']=='video')
            audio=next(s for s in streams if s['codec_type']=='audio')
            assert (video['codec_name'],video['width'],video['height'],video['r_frame_rate'])==('h264',640,360,'50/1'),video
            assert audio['codec_name']=='aac' and audio['sample_rate']=='48000' and audio['channels']==2,audio
            subprocess.run(['ffmpeg','-v','error','-xerror','-i',str(media),'-f','null','-'],env=base.ENV,check=True,stderr=subprocess.PIPE)
            current=panel.request('/api/state')
            assert current['pid']==first['pid'] and current['telemetry']['video_frames']>0
            assert current['telemetry']['output_errors']==0
            print('PASS real preview AVC/AAC 640x360p50, decode clean, encoder unchanged; browser ready',flush=True)
            if args.hold: time.sleep(args.hold)
            panel.request('/api/preview-stop',{},'POST')
            current=panel.request('/api/state')
            assert current['preview']['status']=='idle' and current['pid']==first['pid']
            panel.request('/api/preview',{},'POST')
            panel.wait(lambda s:s['preview']['status']=='live')
            with urlopen(panel.url+'/api/preview-frame',timeout=5) as response:
                assert response.read(2)==b'\xff\xd8'
            panel.request('/api/stop',{},'POST')
            assert panel.request('/api/state')['preview']['status']=='idle'
            print('PASS video stop, JPEG fallback, encoder stop closes preview',flush=True)
        finally:
            panel.close()
            if sender and sender.poll() is None:
                sender.terminate();sender.wait(timeout=5)


if __name__=='__main__':main()
