#!/usr/bin/env python3
"""Input metadata from the encoder must reach synthetic tests without leaking logs."""
import copy
import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webpanel"))
import vapoursynth_script

SERVER = runpy.run_path(str(ROOT / "webpanel/server.py"))
SAMPLE = {"width": 720, "height": 576, "fps_num": 25, "fps_den": 1,
          "pixel_format": "YUV420P8", "props": {
              "_FieldBased": 1, "_SARNum": 16, "_SARDen": 15,
              "_Matrix": 5, "_Primaries": 5, "_Transfer": 5, "_ColorRange": 1,
              "_DurationNum": 1, "_DurationDen": 25}}


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="obe-vs-props-test-")
        self.panel = SERVER["Panel"](ROOT, self.temp.name)
        self.run = SERVER["Run"](mock.Mock(), "_Matrix", "start")
        self.run.signal = {"width": 720, "height": 576, "scan": "i", "fps": "25/1"}
        self.panel.run = self.run

    def tearDown(self):
        self.temp.cleanup()

    def feed(self, data):
        self.panel._line(self.run, "[filter_vapoursynth]: OBE_VS_INPUT " + json.dumps(data))

    def test_detected_properties_reach_test_context(self):
        self.feed(SAMPLE)
        self.assertEqual(self.panel.signal["vapoursynth_input"], SAMPLE)
        context = vapoursynth_script.test_context(self.panel.signal)
        self.assertEqual(context["props"]["_FieldBased"], 1)
        self.assertEqual(context["props"]["_SARNum"], 16)
        self.assertEqual(context["props"]["_Matrix"], 5)
        self.assertEqual(context["pixel_format"], "YUV420P8")

    def test_invalid_and_stale_metadata_cannot_replace_signal_or_leak(self):
        self.feed(SAMPLE)
        before = copy.deepcopy(self.panel.signal)
        events = list(self.panel.events)
        bad = [None, [], {**SAMPLE, "pixel_format": "private-value"},
               {**SAMPLE, "width": True}, {**SAMPLE, "fps_den": 0},
               {**SAMPLE, "props": {"secret": "private-value"}},
               {**SAMPLE, "props": {"_Matrix": "private-value"}},
               {**SAMPLE, "props": {"_SARNum": -1}},
               {**SAMPLE, "props": {"_FieldBased": 3}}]
        for data in bad:
            self.feed(data)
            self.assertEqual(self.panel.signal, before)
            self.assertEqual(list(self.panel.events), events)
        self.panel._line(mock.Mock(), "OBE_VS_INPUT " + json.dumps({**SAMPLE, "width": 1440}))
        self.assertEqual(self.panel.signal, before)
        self.run.cancel.set()
        self.feed({**SAMPLE, "width": 1440})
        self.assertEqual(self.panel.signal, before)


if __name__ == "__main__":
    unittest.main()
