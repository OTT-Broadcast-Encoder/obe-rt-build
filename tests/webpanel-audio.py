#!/usr/bin/env python3
"""Multi-track validation, PID allocation and CLI routing without live state."""
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webpanel"))
import configuration as c
from server import DEFAULT_CONFIG, validate_config


def track(source=1, **values):
    return {**c.AUDIO_TRACK_DEFAULTS, "source": source, **values}


class AudioTests(unittest.TestCase):
    def config(self, **values):
        return {**DEFAULT_CONFIG, **values}

    def test_legacy_and_two_track_commands(self):
        sources=[{"id": 1}, {"id": 2}, {"id": 3}]
        old=c.audio_commands(self.config(), sources)
        self.assertEqual(old, c.audio_options(self.config(), 1) + "remove stream 3\nremove stream 2\n")
        config=validate_config(self.config(audio_tracks=[track(3, pid=991, language="ITA"), track(1, pid=992, language="eng", audio_type="visual-impaired")]))
        self.assertEqual(config["audio_tracks"][0]["language"], "ita")
        command=c.audio_commands(config, sources)
        self.assertIn("set stream opts 3:format=mp2,bitrate=192,channel-map=stereo,lang=ita,audio-type=undefined,pid=991\n",command)
        self.assertIn("set stream opts 1:format=mp2,bitrate=192,channel-map=stereo,lang=eng,audio-type=visual-impaired,pid=992\n",command)
        self.assertTrue(command.endswith("remove stream 2\n"))
        self.assertEqual(c.audio_bitrate_total(config),384)

    def test_automatic_pids_avoid_every_explicit_pid(self):
        config=validate_config(self.config(audio_tracks=[track(1),track(2,pid=48),track(3,pid=50)],pcr_pid=49))
        pids=c.effective_pids(config)
        self.assertEqual((pids["pmt_pid"],pids["video_pid"],pids["audio_pid"],pids["pcr_pid"]),(51,52,53,49))
        self.assertIn("pid=53\n",c.audio_commands(config,[{"id":i} for i in (1,2,3)]))

    def test_mux_counts_only_enabled_tracks(self):
        config=self.config(mux_bitrate_kbps=8400,audio_tracks=[track(1),track(2)])
        with self.assertRaises(c.ConfigurationError) as caught: c.validate_extended(config)
        self.assertEqual(caught.exception.field,"mux_bitrate_kbps")
        config["audio_tracks"][1]["enabled"]=False
        c.validate_extended(config)
        self.assertEqual(c.audio_bitrate_total(config),192)

    def test_video_only_and_missing_selected_source(self):
        self.assertEqual(c.audio_commands(self.config(),[]),"")
        self.assertEqual(c.audio_commands(self.config(audio_tracks=[]),[{"id":1},{"id":2}]),"remove stream 2\nremove stream 1\n")
        with self.assertRaises(c.ConfigurationError): c.audio_commands(self.config(audio_tracks=[track(2)]),[{"id":1}])
        self.assertEqual(c.audio_commands(self.config(audio_tracks=[track(2,enabled=False)]),[{"id":1}]),"remove stream 1\n")

    def test_invalid_track_values_and_collisions(self):
        cases=[("audio_tracks",{}),("audio_tracks",[track(i+1) for i in range(9)]),
               ("audio_tracks.0.source",[track(True)]),("audio_tracks.0.source",[track(0)]),
               ("audio_tracks.0.pid",[track(pid=31)]),("audio_tracks.0.pid",[track(pid=8191)]),
               ("audio_tracks.0.language",[track(language="ita,format=mp2")]),
               ("audio_tracks.0.language",[track(audio_type="visual-impaired")]),
               ("audio_tracks.0.audio_type",[track(audio_type=[])]),
               ("audio_tracks.0.enabled",[track(enabled=1)]),
               ("audio_tracks.0",[track(extra=1)]),
               ("audio_tracks.1.pid",[track(1,pid=991),track(2,pid=991)]),
               ("audio_tracks.1.source",[track(1),track(1)])]
        for field,value in cases:
            with self.subTest(field=field,value=value):
                with self.assertRaises(c.ConfigurationError) as caught: c.validate_extended(self.config(audio_tracks=value))
                self.assertEqual(caught.exception.field,field)
        for other in ("pmt_pid","video_pid","pcr_pid"):
            with self.subTest(other=other), self.assertRaises(c.ConfigurationError):
                c.validate_extended(self.config(audio_tracks=[track(pid=991)], **{other:991}))


if __name__ == "__main__": unittest.main()
