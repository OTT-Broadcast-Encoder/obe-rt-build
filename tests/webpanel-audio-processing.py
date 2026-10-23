#!/usr/bin/env python3
"""Loudness configuration and untrusted per-track audio telemetry."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'webpanel'))
import configuration as c
from server import DEFAULT_CONFIG, Panel, Run, validate_config


class ProcessingTests(unittest.TestCase):
    def test_default_is_bypass_and_shared_controls(self):
        config = validate_config(DEFAULT_CONFIG)
        self.assertFalse(config['audio_normalize'])
        self.assertNotIn('audio-normalize', c.audio_options(config, 1))
        config = validate_config({**config, 'audio_normalize': True, 'audio_tracks': [dict(c.AUDIO_TRACK_DEFAULTS), {**c.AUDIO_TRACK_DEFAULTS,'source':2}]})
        commands = c.audio_commands(config,[{'id':1},{'id':2}])
        self.assertEqual(commands.count('audio-normalize=1,loudness-i=-23.000,loudness-tp=-1.000,loudness-lra=7.000'),2)

    def test_invalid_settings(self):
        for name, value in [('audio_normalize',1),('audio_loudness_lufs',-71),('audio_loudness_lufs',float('nan')),
                            ('audio_true_peak_dbtp',1),('audio_loudness_range_lu',0),('audio_true_peak_dbtp',True)]:
            with self.subTest(name=name,value=value), self.assertRaises(c.ConfigurationError):
                c.validate_extended({**DEFAULT_CONFIG,name:value})

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.panel = Panel(Path(self.folder.name),Path(self.folder.name)/'state')
        self.run = Run('test', {}, '', None)
        self.sample = {'schema':3,'monotonic_ns':1000000000,'video_frames':25,'video_bytes':100,'output_bytes':200,
                       'output_datagrams':3,'output_errors':0,'video_queue_frames':1,'video_qp_aq':None,'video_pvmaf':None,
                       'audio':[{'output_id':1,'input_id':2,'peak_dbfs':[-6.02,-12.04],'rms_dbfs':[-9.03,-15.05],
                                 'clipped':[False,False],'age_ms':12,'normalizing':True}]}

    def tearDown(self):
        self.folder.cleanup()

    def test_meter_lifecycle_and_legacy(self):
        self.panel._telemetry_line(self.run,json.dumps(self.sample))
        self.assertFalse(self.run.telemetry_failed)
        state = self.panel._telemetry_state(self.run,True)
        self.assertEqual(state['audio'][0]['status'],'live')
        self.assertEqual(state['audio'][0]['peak_dbfs'],[-6.02,-12.04])
        self.run.telemetry_received -= 2
        self.assertIsNone(self.panel._telemetry_state(self.run,True)['audio'][0]['peak_dbfs'])
        self.assertIsNone(self.panel._telemetry_state(self.run,False)['audio'])
        old = {k:v for k,v in self.sample.items() if k!='audio'}
        old.update(schema=2,monotonic_ns=2000000000)
        self.panel._telemetry_line(self.run,json.dumps(old))
        self.assertFalse(self.run.telemetry_failed)
        self.assertIsNone(self.panel._telemetry_state(self.run,True)['audio'])

    def test_malformed_samples_do_not_replace_good_data(self):
        self.panel._telemetry_line(self.run,json.dumps(self.sample))
        for field,value in [('peak_dbfs',[1,0]),('rms_dbfs',[float('nan'),0]),('clipped',[1,0]),
                            ('age_ms',-1),('output_id',True),('normalizing','yes'),('input_id','2')]:
            bad = copy.deepcopy(self.sample); bad['monotonic_ns'] += 1000000000
            bad['audio'][0][field] = value
            self.panel._telemetry_line(self.run,json.dumps(bad))
            self.assertTrue(self.run.telemetry_failed, field)
            self.assertEqual(self.run.telemetry_sample,self.sample)
        bad = copy.deepcopy(self.sample); bad['audio'] *= 2
        self.panel._telemetry_line(self.run,json.dumps(bad))
        self.assertTrue(self.run.telemetry_failed)

if __name__ == '__main__': unittest.main()
