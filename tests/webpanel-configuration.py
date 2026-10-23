#!/usr/bin/env python3
"""Broadcast configuration validation and emitted commands; no live state."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "webpanel"))
from configuration import (ConfigurationError, EXTENDED_DEFAULTS, audio_options,
                           effective_pids, mux_options, validate_extended, video_options)


BASE = {
    "name": "Banco 1080i", "input_url": "udp://127.0.0.1:12000",
    "output_url": "udp://127.0.0.1:13000", "video_bitrate_kbps": 8000,
    "mux_bitrate_kbps": 9500, "vbv_buffer_kbits": 8000, "preset": "veryfast",
    "gop_frames": 50, "bframes": 2, "lookahead_frames": 20, "threads": 4,
    "audio_bitrate_kbps": 192,
}


class ConfigurationTests(unittest.TestCase):
    def config(self, **overrides):
        return {**BASE, **overrides}

    def invalid(self, field, **overrides):
        with self.assertRaises(ConfigurationError) as caught:
            validate_extended(self.config(**overrides))
        self.assertEqual(caught.exception.field, field)

    def test_legacy_profiles_have_identical_commands(self):
        self.assertEqual(video_options(BASE, 3, {"scan": "i"}),
            "set stream opts 3:format=avc,preset=veryfast,bitrate=8000,vbv-maxrate=8000,"
            "vbv-bufsize=8000,thread=4,bframes=2,keyint=50,rc-lookahead=20\n")
        self.assertEqual(audio_options(BASE, 7), "set stream opts 7:format=mp2,bitrate=192,channel-map=stereo\n")
        self.assertEqual(mux_options(BASE), "set muxer opts ts-muxrate=9500000,cbr=1,ts-type=dvb\n")
        self.assertEqual(audio_options(BASE, None), "")
        result = validate_extended(BASE)
        self.assertEqual({key: result[key] for key in EXTENDED_DEFAULTS}, EXTENDED_DEFAULTS)
        self.assertNotIn("video_profile", BASE)

    def test_x264_ratecontrol_and_prediction_options(self):
        command = video_options(self.config(qcomp=0.7, ipratio=1.2, mbtree="0", pbratio=1.3,
            qp_min=8, qp_max=49, qp_step=4, chroma_qp_offset=-1, min_gop_frames=20,
            b_bias=10, open_gop="0", direct_pred="auto", weighted_bipred="1", weighted_pred="0",
            fast_pskip="0", transform_8x8="1", entropy_coding="cabac", deblock_alpha=1,
            deblock_beta=-1), 0, {"scan": "i"})
        for option in ("qcomp=0.7", "ipratio=1.2", "pbratio=1.3", "qpmin=8", "qpmax=49",
                       "qpstep=4", "chroma-qp-offset=-1", "min-keyint=20", "b-bias=10",
                       "open-gop=0", "direct=auto", "weightb=1", "weightp=0", "fast-pskip=0",
                       "8x8dct=1", "entropy=cabac", "deblock=1:-1", "mbtree=0"):
            self.assertIn("," + option, command)
        self.assertNotIn("speedcontrol=", command)
        self.assertIn("deblock=0", video_options(self.config(deblock="0"), 0, {"scan": "i"}))

    def test_fixed_analysis_and_psy_are_explicit(self):
        config = self.config(x264_speedcontrol="fixed", motion_estimation="umh", motion_range=32,
                             subme=9, trellis="2", mixed_refs="1", partitions="most",
                             psy="1", psy_rd=0.8, psy_trellis=0.2)
        command = video_options(config, 0, {"scan": "i"})
        for option in ("speedcontrol=0", "me=umh", "merange=32", "subme=9", "trellis=2",
                       "mixed-refs=1", "partitions=i4x4+i8x8+p8x8+b8x8", "psy=1", "psy-rd=0.8:0.2"):
            self.assertIn("," + option, command)
        for field in ("motion_estimation", "motion_range", "subme", "trellis", "mixed_refs", "partitions", "psy_rd"):
            self.invalid(field, **{field: config[field]})
        self.assertIn("psy=0", video_options(self.config(psy="0"), 0, {"scan": "i"}))

    def test_x264_numeric_limits_types_and_cli_injection(self):
        from configuration import X264_INTEGERS, X264_NUMBERS
        for field, (low, high) in {**X264_INTEGERS, **X264_NUMBERS}.items():
            for value in (True, "0", low - 0.01, high + 1, float("nan"), float("inf")):
                with self.subTest(field=field, value=value):
                    self.invalid(field, **{field: value, "x264_speedcontrol": "fixed"})
        for field in ("motion_estimation", "x264_speedcontrol", "partitions", "direct_pred", "psy"):
            self.invalid(field, **{field: "auto\nstart"})

    def test_x264_incompatible_settings_are_rejected(self):
        cases = [
            ("qp_max", {"qp_min": 40, "qp_max": 30}),
            ("min_gop_frames", {"min_gop_frames": 27}),
            ("mbtree", {"mbtree": "1", "lookahead_frames": 0}),
            ("mbtree", {"mbtree": "1", "qcomp": 1}),
            ("mbtree", {"mbtree": "1", "gop_frames": 1}),
            ("pbratio", {"pbratio": 1.2}),
            ("b_bias", {"b_bias": 10, "b_adapt": "0"}),
            ("weighted_bipred", {"bframes": 0, "weighted_bipred": "1"}),
            ("open_gop", {"bframes": 0, "open_gop": "1"}),
            ("transform_8x8", {"video_profile": "main", "transform_8x8": "1"}),
            ("entropy_coding", {"video_profile": "baseline", "entropy_coding": "cabac"}),
            ("trellis", {"entropy_coding": "cavlc", "trellis": "2"}),
            ("deblock_beta", {"deblock_alpha": 1}),
            ("deblock_alpha", {"deblock": "0", "deblock_alpha": 0, "deblock_beta": 0}),
            ("psy_trellis", {"psy_rd": 1}),
            ("psy_rd", {"psy": "0", "psy_rd": 0, "psy_trellis": 0}),
            ("subme", {"subme": 10, "trellis": "1", "aq_mode": "2"}),
            ("motion_range", {"motion_range": 32, "motion_estimation": "hex"}),
            ("psy", {"video_tune": "psnr", "psy_rd": 1, "psy_trellis": 0}),
            ("psy_rd", {"psy_rd": 1, "psy_trellis": 0, "subme": 5}),
            ("psy_trellis", {"psy_rd": 0, "psy_trellis": 1, "trellis": "0"}),
        ]
        for field, overrides in cases:
            with self.subTest(field=field, overrides=overrides):
                self.invalid(field, x264_speedcontrol="fixed", **overrides)
        for order, signal in (("source", {"scan": "i"}), ("tff", {"scan": "i"})):
            with self.assertRaises(ConfigurationError) as caught:
                video_options(self.config(weighted_pred="2", field_order=order), 0, signal)
            self.assertEqual(caught.exception.field, "weighted_pred")
        self.assertIn("weightp=2", video_options(self.config(weighted_pred="2"), 0, {"scan": "p"}))
        self.assertIn("subme=11", video_options(self.config(x264_speedcontrol="fixed", subme=11,
                       trellis="2", aq_mode="2"), 0, {"scan": "i"}))

    def test_types_and_nonfinite_values_are_rejected(self):
        for field in ("reference_frames", "pmt_pid", "ts_id", "audio_offset_ms", "pcr_period_ms"):
            for value in (True, 1.5, "100", None):
                with self.subTest(field=field, value=value):
                    self.invalid(field, **{field: value})
        for value in (True, float("nan"), float("inf"), -61, 21, "1"):
            self.invalid("audio_gain_db", audio_gain_db=value)
        for value in ([], {}, None, "film", "grain\nstart"):
            self.invalid("video_tune", video_tune=value)

    def test_text_cannot_add_cli_options_or_commands(self):
        for field in ("service_name", "provider_name"):
            for value in ("news\nquit", "news,ts-muxrate=1", "news\\", "'news'", "a=b", "a\x00b", " news", "news ", "é", "x" * 64):
                with self.subTest(field=field, value=value):
                    self.invalid(field, **{field: value})
        command = mux_options(self.config(service_name="News HD", provider_name="OTT Broadcast"))
        self.assertIn(",service-name=News HD,provider-name=OTT Broadcast", command)
        self.assertEqual(command.count("\n"), 1)

    def test_pid_collisions_and_reserved_values(self):
        self.invalid("video_pid", pmt_pid=256, video_pid=256)
        self.invalid("audio_pid", video_pid=257, audio_pid=257)
        self.invalid("pcr_pid", audio_pid=258, pcr_pid=258)
        self.invalid("pcr_pid", pmt_pid=256, pcr_pid=256)
        for pid in (1, 31, 8191, -1):
            self.invalid("video_pid", video_pid=pid)
        config = validate_extended(self.config(video_pid=257, pcr_pid=257))
        self.assertEqual(effective_pids(config)["pcr_pid"], 257)

    def test_partial_pid_assignment_cannot_collide_with_engine_defaults(self):
        config = validate_extended(self.config(audio_pid=48, pcr_pid=49))
        pids = effective_pids(config)
        self.assertEqual(pids, {"pmt_pid": 50, "video_pid": 51, "audio_pid": 48, "pcr_pid": 49})
        self.assertIn(",pid=51\n", video_options(config, 0, {"scan": "i"}))
        self.assertIn(",pid=48\n", audio_options(config, 1))
        self.assertIn(",pmt-pid=50,pcr-pid=49\n", mux_options(config))

    def test_encoder_option_names_and_numeric_enums(self):
        config = self.config(video_profile="high", video_level="6.1", video_tune="grain",
                             reference_frames=4, b_pyramid="strict", field_order="bff", aspect_ratio="16:9",
                             vbv_maxrate_kbps=9000, mux_bitrate_kbps=10000)
        command = video_options(config, 0, {"scan": "i"})
        for value in ("profile=high", "level=61", "tune=grain", "max-refs=4", "b-pyramid=1",
                      "interlaced=1,tff=0", "aspect-ratio=16:9", "vbv-maxrate=9000"):
            self.assertIn(value, command)
        self.assertNotIn(",ref=", command)  # Current OBE accepts but ignores this alias.
        self.assertIn("level=9", video_options(self.config(video_level="1b"), 0, {"scan": "i"}))

    def test_quality_controls_preserve_presets_until_explicitly_set(self):
        legacy = video_options(BASE, 0, {"scan": "i"})
        for option in ("b-adapt=", "scenecut=", "aq-mode=", "aq-strength="):
            self.assertNotIn(option, legacy)
        config = self.config(b_adapt="2", scenecut_threshold=45,
                             aq_mode="3", aq_strength=1.25)
        command = video_options(config, 0, {"scan": "i"})
        for option in ("b-adapt=2", "scenecut=45", "aq-mode=3", "aq-strength=1.25"):
            self.assertIn(option, command)
        zero = video_options(self.config(scenecut_threshold=0, aq_mode="0"), 0, {"scan": "i"})
        self.assertIn("scenecut=0", zero)
        self.assertIn("aq-mode=0", zero)
        self.assertIn("aq-strength=0", video_options(self.config(aq_strength=0), 0, {"scan": "i"}))
        self.invalid("scenecut_threshold", scenecut_threshold=101)
        self.invalid("scenecut_threshold", scenecut_threshold=1.5)
        self.invalid("aq_strength", aq_strength=float("nan"))
        self.invalid("aq_strength", aq_strength=3.1)
        self.invalid("aq_strength", aq_mode="0", aq_strength=1)
        self.invalid("b_adapt", b_adapt="3")
        self.invalid("b_adapt", bframes=0, b_adapt="2")
        self.invalid("aq_mode", aq_mode=4)

    def test_field_order_cannot_pretend_to_convert_progressive(self):
        for signal in ({"scan": "p"}, None, {}):
            with self.assertRaises(ConfigurationError) as caught:
                video_options(self.config(field_order="tff"), 0, signal)
            self.assertEqual(caught.exception.field, "field_order")
        self.assertIn("interlaced=1,tff=1", video_options(self.config(field_order="tff"), 0, {"scan": "i"}))
        self.invalid("bframes", video_profile="baseline")
        self.invalid("video_profile", video_profile="baseline", bframes=0, field_order="tff")
        with self.assertRaises(ConfigurationError):
            video_options(self.config(video_profile="baseline", bframes=0), 0, {"scan": "i"})
        self.assertIn("profile=baseline", video_options(self.config(video_profile="baseline", bframes=0), 0, {"scan": "p"}))

    def test_codec_bitrate_constraints(self):
        self.invalid("audio_bitrate_kbps", audio_bitrate_kbps=80)
        validate_extended(self.config(audio_codec="ac3", audio_bitrate_kbps=80))
        validate_extended(self.config(audio_codec="ac3", audio_bitrate_kbps=640))
        self.invalid("audio_bitrate_kbps", audio_codec="ac3", audio_bitrate_kbps=200)
        validate_extended(self.config(audio_codec="e-ac3", audio_bitrate_kbps=200))
        validate_extended(self.config(audio_codec="aac", audio_bitrate_kbps=128))
        self.invalid("audio_bitrate_kbps", audio_codec="aac", aac_profile="he-aac-v2", audio_bitrate_kbps=192)
        validate_extended(self.config(audio_codec="aac", aac_profile="he-aac-v2", audio_bitrate_kbps=32))
        self.invalid("audio_bitrate_kbps", audio_bitrate_kbps=True)

    def test_audio_specific_options_and_language(self):
        command = audio_options(self.config(audio_codec="aac", aac_profile="he-aac-v1", aac_encapsulation="latm",
                                 audio_bitrate_kbps=96, audio_gain_db=-2.5, audio_offset_ms=-120,
                                 audio_language="ita", audio_type="clean-effects"), 1)
        for value in ("format=aac", "aac-profile=he-aac-v1", "aac-encap=latm", "gain=-2.5",
                      "audio-offset=-120", "lang=ita", "audio-type=clean-effects"):
            self.assertIn(value, command)
        self.assertNotIn("mp2-mode", command)
        self.assertIn("mp2-mode=joint-stereo", audio_options(self.config(mp2_mode="joint-stereo"), 1))
        self.invalid("audio_language", audio_language="IT")
        self.invalid("audio_language", audio_type="hearing-impaired")

    def test_vbv_peak_and_mux_headroom(self):
        self.invalid("vbv_maxrate_kbps", vbv_maxrate_kbps=7999)
        self.invalid("mux_bitrate_kbps", vbv_maxrate_kbps=9500)
        validate_extended(self.config(vbv_maxrate_kbps=9180, mux_bitrate_kbps=9500))

    def test_dvb_identifiers_and_periods(self):
        command = mux_options(self.config(ts_id=65535, program_number=42, pcr_period_ms=20, pat_period_ms=100))
        self.assertIn("ts-id=65535,program-num=42,pcr-period=20,pat-period=100", command)
        self.invalid("pcr_period_ms", pcr_period_ms=41)
        self.invalid("pat_period_ms", pat_period_ms=501)
        self.invalid("program_number", program_number=65536)


if __name__ == "__main__":
    unittest.main()
