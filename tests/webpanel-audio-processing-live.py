#!/usr/bin/env python3
"""Isolated real-engine checks for input conversion, meters and loudness."""
import argparse
from array import array
import importlib.util
import math
import re
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('audio_live', ROOT / 'tests/webpanel-audio-live.py')
audio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audio)


def source(path, rate, layout, level=0.15):
    tones = [440, 880, 1320, 1760, 2200, 2640]
    channels = {'mono': 1, 'stereo': 2, '5.1': 6}[layout]
    signal = '|'.join(f'{level}*sin(2*PI*{tone}*t)' for tone in tones[:channels])
    audio.run(['ffmpeg', '-v', 'error', '-nostdin', '-f', 'lavfi', '-i', 'testsrc2=size=720x576:rate=50',
               '-f', 'lavfi', '-i', f'aevalsrc={signal}:s={rate}:c={layout}',
               '-filter_threads', '1', '-vf', 'interlace=scan=tff,setsar=16/15', '-c:v', 'mpeg2video',
               '-threads', '2', '-flags', '+ilme+ildct', '-q:v', '4', '-bf', '0', '-g', '12',
               '-c:a', 'aac', '-b:a', '384k', '-t', '30', '-f', 'mpegts', str(path)])


def powers(samples):
    result = []
    for tone in [440, 880, 1320, 1760, 2200, 2640]:
        coefficient = 2 * math.cos(2 * math.pi * tone / 48000)
        a = b = 0.0
        for sample in samples:
            current = sample + coefficient*a - b
            b, a = a, current
        result.append(a*a + b*b - coefficient*a*b)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--case',action='append',choices=['mono-44100','stereo-96000','surround-44100','normalize','gain-clipping','normalized-reconnect','normalized-eight','surround-aac-normalized'])
    args = parser.parse_args()
    folder = args.artifacts; folder.mkdir(parents=True, exist_ok=False)
    engine = folder/'engine'; engine.mkdir()
    shutil.copy2(ROOT/'obe-rt/obe/obecli', engine/'obecli')
    for name in ('run.sh','target-root','NDI'): (engine/name).symlink_to(ROOT/name)
    panel = audio.base.PanelFixture(folder, engine)
    try:
        for name,rate,layout in [('mono-44100',44100,'mono'),('stereo-96000',96000,'stereo'),('surround-44100',44100,'5.1')]:
            if args.case and name not in args.case: continue
            src = folder/(name+'.ts'); source(src, rate, layout)
            actual = next(s for s in audio.base.ffprobe(src,'-show_streams')['streams'] if s['codec_type']=='audio')
            assert int(actual['sample_rate']) == rate and actual['channels'] == {'mono':1,'stereo':2,'5.1':6}[layout]
            media,state = audio.exercise(panel,folder,name,src,[{'source':1,'enabled':True,'pid':991,'language':'ita','audio_type':'undefined'}],[(None,991,'ita')],count=1)
            pcm = array('f'); pcm.frombytes(audio.run(['ffmpeg','-v','error','-ss','1','-i',str(media),'-map','0:a:0','-t','1','-f','f32le','-']))
            left,right = powers(pcm[::2]),powers(pcm[1::2])
            if layout == 'mono':
                assert left[0] > 100*max(left[1:]) and right[0] > 100*max(right[1:])
                assert 0.95 < left[0]/right[0] < 1.05
            elif layout == 'stereo':
                assert left[0] > 100*max(left[1:]) and right[1] > 100*max(right[:1]+right[2:])
            else:
                for values,expected,absent in ((left,(0,2,4),(1,3,5)),(right,(1,2,5),(0,3,4))):
                    assert min(values[i] for i in expected) > 100*max(values[i] for i in absent), (name,values)
                    assert 0.4 < values[2]/values[expected[0]] < 0.6, (name,'center coefficient',values)
            print('PASS input conversion and channel routing:',name,flush=True)
        for name,level,overrides in [('normalize',0.01,{'audio_normalize':True}),('gain-clipping',0.15,{'audio_normalize':False,'audio_gain_db':20})]:
            if args.case and name not in args.case: continue
            src=folder/(name+'.ts'); source(src,48000,'stereo',level)
            media,state=audio.exercise(panel,folder,name,src,[{'source':1,'enabled':True,'pid':991,'language':'ita','audio_type':'undefined'}],[(None,991,'ita')],count=1,overrides=overrides,duration=18 if name=='normalize' else 8)
            meters=state['telemetry']['audio']
            assert len(meters)==1 and meters[0]['status']=='live',state['telemetry']
            if name=='normalize':
                assert meters[0]['normalizing'] and all(-29 < value < -24 for value in meters[0]['rms_dbfs']),meters
                checked=subprocess.run(['ffmpeg','-v','info','-ss','2','-i',str(media),'-map','0:a:0','-af','ebur128=peak=true','-f','null','-'],env=audio.base.ENV,capture_output=True,timeout=40)
                assert checked.returncode==0
                report=checked.stderr.decode(); (folder/'loudness.txt').write_text(report)
                integrated=float(re.findall(r'I:\s*(-?[\d.]+) LUFS',report)[-1])
                peak=float(re.findall(r'Peak:\s*(-?[\d.]+) dBFS',report)[-1])
                assert abs(integrated+23)<1 and peak<=-0.5,(integrated,peak)
                print(f'PASS normalized encoded output: {integrated} LUFS, {peak} dBTP; live PCM meters',flush=True)
            else:
                assert meters[0]['clipped']==[True,True] and meters[0]['peak_dbfs']==[0,0],meters
                print('PASS manual gain clips safely and both channel meters report overload',flush=True)
        if not args.case or 'normalized-reconnect' in args.case:
            src=folder/'normalized-reconnect.ts'; audio.make_source(src,2)
            tracks=[{'source':i+1,'enabled':True,'pid':991+i,'language':'ita','audio_type':'undefined'} for i in range(2)]
            audio.exercise(panel,folder,'normalized-reconnect',src,tracks,[(440,991,'ita'),(880,992,'ita')],reconnect=True,overrides={'audio_normalize':True,'audio_gain_db':0},duration=12)
        if not args.case or 'normalized-eight' in args.case:
            src=folder/'normalized-eight.ts'; audio.make_source(src,8)
            tracks=[{'source':i+1,'enabled':True,'pid':991+i,'language':'ita','audio_type':'undefined'} for i in range(8)]
            _,state=audio.exercise(panel,folder,'normalized-eight',src,tracks,[(440*(i+1),991+i,'ita') for i in range(8)],count=8,overrides={'audio_normalize':True,'audio_gain_db':0},duration=12)
            assert len(state['telemetry']['audio'])==8 and all(m['status']=='live' and m['normalizing'] for m in state['telemetry']['audio'])
        if not args.case or 'surround-aac-normalized' in args.case:
            src=folder/'surround-aac-normalized.ts'; source(src,44100,'5.1')
            _,state=audio.exercise(panel,folder,'surround-aac-normalized',src,[{'source':1,'enabled':True,'pid':991,'language':'ita','audio_type':'undefined'}],[(None,991,'ita')],count=1,codec='aac',overrides={'audio_normalize':True,'audio_gain_db':0},duration=12)
            assert state['telemetry']['audio'][0]['status']=='live'
    finally:
        panel.close()

if __name__ == '__main__': main()
