#!/usr/bin/env python3
"""Input validation and CLI boundary tests; no devices or network needed."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "webpanel"))
from inputs import INPUT_DEFAULTS, InputError, capability_report, input_commands, reservation, validate_input


class InputTests(unittest.TestCase):
    def test_legacy_url_defaults_and_original_command(self):
        config = {"input_url": "udp://127.0.0.1:12000?fifo_size=100000"}
        self.assertEqual(validate_input(config), INPUT_DEFAULTS)
        self.assertEqual(input_commands(config), "set input url\nset input opts "
                         "location=udp://127.0.0.1:12000?fifo_size=100000\nprobe input\n")

    def test_decklink_connections_and_format_are_explicit(self):
        config = dict(input_type="decklink", input_card_index=2, input_video_format="1080i50",
                      input_video_connection="hdmi", input_audio_connection="embedded", input_url="")
        self.assertEqual(input_commands(config), "set input decklink\nset input opts "
                         "card-idx=2,video-format=1080i50,video-connection=hdmi,audio-connection=embedded\nprobe input\n")
        self.assertEqual(reservation(config), ("decklink", 2))
        self.assertIn("allow-1080p60=1", input_commands(dict(config, input_video_format="1080p50")))

    def test_ndi_preserves_spaces_and_does_not_reserve_capture_card(self):
        config = dict(input_type="ndi", ndi_source="  PLAYOUT (Program HD)  ", input_url="")
        self.assertEqual(input_commands(config), "set input ndi\nset input opts name=PLAYOUT (Program HD)\nprobe input\n")
        self.assertIsNone(reservation(config))
        self.assertIsNone(reservation({}))

    def test_invalid_input_values_and_option_injection(self):
        cases = [
            ("input_type", "decklink\nstart"), ("input_type", ["url"]),
            ("input_card_index", True), ("input_card_index", -1), ("input_card_index", 64),
            ("input_card_index", "1"), ("input_video_format", "1080i50,audio-connection=analogue"),
            ("input_video_connection", "auto"), ("input_audio_connection", "hdmi"),
            ("ndi_source", "cam\nstart"), ("ndi_source", "cam,name=other"),
            ("ndi_source", "cam=other"), ("ndi_source", "cam\\"), ("ndi_source", '"cam"'),
            ("ndi_source", "cam\u200b"), ("ndi_source", "cam\u2028start"),
            ("ndi_source", "cam\ud800"), ("ndi_source", "è" * 128),
        ]
        for field, value in cases:
            with self.subTest(field=field, value=value), self.assertRaises(InputError) as raised:
                validate_input({field: value})
            self.assertEqual(raised.exception.field, field)
        with self.assertRaises(InputError):
            input_commands({"input_type": "ndi", "ndi_source": " "})

    def test_ndi_discovery_suffix_cannot_select_file_paths(self):
        good = dict(input_type="ndi", ndi_source="PLAYOUT (HD) @ 192.0.2.10")
        self.assertIn("name=PLAYOUT (HD) @ 192.0.2.10", input_commands(good))
        for source in ("PLAYOUT @ ../../secret", "PLAYOUT @ 192.0.2.10/../../secret", " @ 192.0.2.1",
                       "PLAYOUT @ 192.0.2.1 @ 192.0.2.2"):
            with self.subTest(source=source), self.assertRaises(InputError):
                input_commands(dict(good, ndi_source=source))

    def test_direct_url_generation_rejects_cli_separators(self):
        for url in (None, "", "udp://127.0.0.1:12000\nstart", "udp://127.0.0.1:12000,name=x",
                    "udp://127.0.0.1:12000\\", "udp://127.0.0.1:12000\x00"):
            with self.subTest(url=url), self.assertRaises(InputError):
                input_commands({"input_url": url})

    def test_capabilities_keep_unknown_build_and_hardware_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = capability_report(tmp)
            self.assertTrue(report["url"]["build_supported"])
            for kind in ("ndi", "decklink"):
                self.assertIsNone(report[kind]["build_supported"])
                self.assertIsNone(report[kind]["available"])
            cli = Path(tmp) / "obe-rt" / "obe"
            cli.mkdir(parents=True)
            # A source template is not evidence of an enabled build.
            (cli / "Makefile.am").write_text("CFLAGS = -DHAVE_DECKLINK\n")
            self.assertIsNone(capability_report(tmp)["decklink"]["build_supported"])
            (cli / "Makefile").write_text("CFLAGS = -O2 \\\n -DHAVE_DECKLINK\n"
                                         "DEFS = -DHAVE_PROCESSING_NDI_LIB_H=1\n")
            report = capability_report(tmp)
            for kind in ("ndi", "decklink"):
                self.assertTrue(report[kind]["build_supported"])
                self.assertIsNone(report[kind]["available"])
            (cli / "Makefile").write_text("# CFLAGS = -DHAVE_DECKLINK\n"
                                         "CFLAGS = -O2 -DHAVE_DECKLINK=0\n"
                                         "DEFS = -DHAVE_PROCESSING_NDI_LIB_H=0\n")
            report = capability_report(tmp)
            for kind in ("ndi", "decklink"):
                self.assertFalse(report[kind]["build_supported"])
                self.assertIsNone(report[kind]["available"])

    def test_partial_build_metadata_does_not_infer_decklink(self):
        with tempfile.TemporaryDirectory() as tmp:
            core = Path(tmp) / "obe-rt"
            core.mkdir()
            (core / "Makefile").write_text("DEFS = -DHAVE_PROCESSING_NDI_LIB_H=1\n")
            report = capability_report(tmp)
            self.assertTrue(report["ndi"]["build_supported"])
            self.assertIsNone(report["decklink"]["build_supported"])


if __name__ == "__main__":
    unittest.main()
