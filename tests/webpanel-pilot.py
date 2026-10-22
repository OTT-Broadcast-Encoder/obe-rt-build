#!/usr/bin/env python3
"""Pilot classification regressions, without waiting 24 hours."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent/'webpanel'))
import pilot

STATE = {'process_alive': True, 'phase': 'running', 'signal_state': 'receiving', 'pid': 42,
         'revision': 2, 'config': {'mux_bitrate_kbps': 9500, 'input_url': 'udp://source:12', 'output_url': 'udp://dest:13'},
         'signal': {'width': 1920, 'height': 1080, 'scan': 'i', 'fps': '25/1', 'audio': 'PCM stereo 48kHz'},
         'metrics': {'cpu_percent': 180, 'memory_mb': 220},
         'telemetry': {'status': 'live', 'age_seconds': .2, 'video_fps': 25, 'video_queue_frames': 2,
                       'output_errors': 0, 'output_bitrate_kbps': 9500, 'video_frames': 200, 'output_bytes': 10000}}


class PilotTests(unittest.TestCase):
    def test_correct_hd_and_limits(self):
        self.assertEqual(pilot.assess(STATE, STATE, None, pilot.LIMITS), [])
        for section, key, value, expected in (
            ('signal', 'height', 576, 'not_1080i50'), ('signal', 'fps', '50/1', 'not_1080i50'),
            ('signal', 'audio', 'PCM stereo 96kHz', 'audio_not_stereo_48khz'),
            ('metrics', 'memory_mb', 3000, 'memory_mb_outside_limit'),
            ('telemetry', 'status', 'stale', 'telemetry_unavailable'),
            ('telemetry', 'video_queue_frames', 99, 'video_queue_frames_outside_limit'),
            ('telemetry', 'output_errors', 1, 'output_errors_outside_limit'),
            ('telemetry', 'video_fps', None, 'video_fps_missing')):
            state = deepcopy(STATE); state[section][key] = value
            with self.subTest(expected=expected): self.assertIn(expected, pilot.assess(state, STATE, None, pilot.LIMITS))
        state = deepcopy(STATE); state.update(pid=99, revision=3)
        self.assertIn('process_changed', pilot.assess(state, STATE, None, pilot.LIMITS))
        self.assertIn('profile_changed', pilot.assess(state, STATE, None, pilot.LIMITS))
        self.assertIn('video_frames_not_advancing', pilot.assess(STATE, STATE, STATE, pilot.LIMITS))

    def test_short_run_is_not_production_qualification_and_exports_no_urls(self):
        with tempfile.TemporaryDirectory() as temp:
            state = deepcopy(STATE)
            def fetch(*args):
                state['telemetry']['video_frames'] += 25; state['telemetry']['output_bytes'] += 1000
                return deepcopy(state)
            ticks = iter(range(100))
            with mock.patch('pilot.fetch', side_effect=fetch), mock.patch('pilot.time.monotonic', side_effect=lambda: next(ticks)), mock.patch('pilot.time.sleep'):
                report = pilot.observe('http://localhost', 'main', 10, 5, Path(temp)/'pilot', pilot.LIMITS)
            self.assertEqual(report['status'], 'short_pass')
            self.assertFalse(report['production_qualified'])
            self.assertTrue(report['completed_duration'])
            self.assertNotIn('input_url', report['config'])
            self.assertTrue((Path(temp)/'pilot/samples.csv').is_file())

    def test_missing_api_cannot_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            ticks = iter(range(100))
            calls = iter(range(100))
            def fetch(*args):
                if next(calls) == 0: return STATE
                raise OSError()
            with mock.patch('pilot.fetch', side_effect=fetch), mock.patch('pilot.time.monotonic', side_effect=lambda: next(ticks)), mock.patch('pilot.time.sleep'):
                report = pilot.observe('http://localhost', 'main', 10, 5, Path(temp)/'pilot', pilot.LIMITS)
            self.assertEqual(report['status'], 'fail')
            self.assertIn('api_unavailable', report['failures'])


if __name__ == '__main__': unittest.main()
