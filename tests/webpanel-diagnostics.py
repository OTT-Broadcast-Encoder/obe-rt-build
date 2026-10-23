#!/usr/bin/env python3
"""Deterministic proc/sys fixtures for read-only diagnostics."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "webpanel"))
from diagnostics import Diagnostics, MAX_AGE, MAX_INTERFACES, signal_diagnostics


def net_line(name, rx=1000, tx=2000):
    return f"{name}: {rx} 10 2 3 0 0 0 0 {tx} 20 4 5 0 0 0 0\n"


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.proc, self.sys = self.root / "proc", self.root / "sys"
        (self.proc / "net").mkdir(parents=True)
        (self.sys / "class" / "net" / "eth0").mkdir(parents=True)
        self.diagnostics = Diagnostics(self.root, self.proc, self.sys)
        self.put("stat", "cpu 100 10 20 1000 20 5 5 0 50 5\ncpu0 1\ncpu1 1\n")
        self.put("meminfo", "MemTotal: 1000 kB\nMemAvailable: 400 kB\n")
        self.put("loadavg", "1.00 0.50 0.25 1/100 1234\n")
        self.put("uptime", "12345.67 20000.00\n")
        self.put("net/dev", net_line("eth0"))
        for name, value in (("operstate", "up\n"), ("speed", "1000\n"), ("mtu", "1500\n")):
            (self.sys / "class" / "net" / "eth0" / name).write_text(value)

    def put(self, name, text):
        (self.proc / name).write_text(text)

    def test_first_sample_memory_load_disk_and_no_fake_rates(self):
        with mock.patch("diagnostics.shutil.disk_usage", return_value=(1000, 300, 700)):
            state = self.diagnostics.snapshot()
        self.assertIsNone(state["cpu"]["usage_percent"])
        self.assertEqual(state["cpu"]["logical_cpus"], 2)
        self.assertEqual(state["cpu"]["load_1m"], 1.0)
        self.assertEqual(state["uptime_seconds"], 12345.67)
        self.assertEqual(state["memory"]["used_bytes"], 600 * 1024)
        self.assertEqual(state["memory"]["used_percent"], 60.0)
        self.assertEqual(state["disk"]["used_percent"], 30.0)
        interface = state["network"][0]
        self.assertEqual(interface["interface"], "eth0")
        self.assertEqual(interface["link_speed_mbps"], 1000)
        self.assertEqual(interface["rx_errors"], 2)
        self.assertEqual(interface["tx_dropped"], 5)
        self.assertIsNone(interface["rx_bitrate_kbps"])
        self.assertIsNone(state["interval_seconds"])
        # No IP/MAC, hostname, username, environment or local file paths.
        rendered = json.dumps(state)
        self.assertNotIn(str(self.root), rendered)
        self.assertNotIn("address", rendered)

    def test_delta_cpu_excludes_double_counted_guest_and_network_units(self):
        with mock.patch("diagnostics.time.monotonic", return_value=100):
            self.diagnostics.snapshot()
        self.put("stat", "cpu 150 10 20 1050 20 5 5 0 100 10\ncpu0 1\ncpu1 1\n")
        self.put("net/dev", net_line("eth0", rx=251000, tx=502000))
        with mock.patch("diagnostics.time.monotonic", return_value=102):
            state = self.diagnostics.snapshot()
        self.assertEqual(state["interval_seconds"], 2)
        self.assertEqual(state["cpu"]["usage_percent"], 50.0)
        self.assertEqual(state["network"][0]["rx_bitrate_kbps"], 1000.0)
        self.assertEqual(state["network"][0]["tx_bitrate_kbps"], 2000.0)

    def test_counter_reset_missing_interface_and_new_interface(self):
        with mock.patch("diagnostics.time.monotonic", return_value=100):
            self.diagnostics.snapshot()
        self.put("stat", "cpu 1 1 1 1\ncpu0 1\n")
        self.put("net/dev", net_line("eth0", rx=10, tx=20) + net_line("eth1"))
        with mock.patch("diagnostics.time.monotonic", return_value=102):
            state = self.diagnostics.snapshot()
        self.assertIsNone(state["cpu"]["usage_percent"])
        self.assertTrue(all(item["rx_bitrate_kbps"] is None for item in state["network"]))
        self.put("net/dev", net_line("eth1", rx=1100, tx=2100))
        with mock.patch("diagnostics.time.monotonic", return_value=104):
            state = self.diagnostics.snapshot()
        self.assertEqual(len(state["network"]), 1)
        self.assertEqual(state["network"][0]["rx_bitrate_kbps"], 0.4)
        self.assertEqual(state["network"][0]["state"], "unknown")

    def test_missing_and_malformed_metrics_stay_unknown(self):
        self.put("stat", "cpu -1 x 20\n")
        self.put("meminfo", "MemTotal: unknown\n")
        self.put("loadavg", "nan inf -1 1/1 99\n")
        self.put("uptime", "nan 0\n")
        self.put("net/dev", net_line("../private") + "eth0: x\n")
        with mock.patch("diagnostics.shutil.disk_usage", side_effect=OSError("private path")):
            state = self.diagnostics.snapshot()
        self.assertIsNone(state["cpu"]["usage_percent"])
        self.assertIsNone(state["cpu"]["logical_cpus"])
        self.assertIsNone(state["cpu"]["load_1m"])
        self.assertIsNone(state["memory"]["used_bytes"])
        self.assertIsNone(state["disk"]["total_bytes"])
        self.assertIsNone(state["uptime_seconds"])
        self.assertEqual(state["network"], [])
        self.assertNotIn("private", json.dumps(state))

    def test_memory_fallback_and_cgroup_limits(self):
        self.put("meminfo", "MemTotal: 1000 kB\nMemFree: 100 kB\nBuffers: 50 kB\nCached: 200 kB\n"
                 "SReclaimable: 30 kB\nShmem: 10 kB\n")
        group = self.sys / "fs" / "cgroup"
        group.mkdir(parents=True)
        for name, value in (("cpu.max", "150000 100000\n"), ("memory.max", "1000000\n"),
                            ("memory.current", "200000\n")):
            (group / name).write_text(value)
        state = self.diagnostics.snapshot()
        self.assertEqual(state["memory"]["available_bytes"], 370 * 1024)
        self.assertEqual(state["limits"], {"cpu_quota_cores": 1.5, "memory_limit_bytes": 1000000,
                                           "memory_current_bytes": 200000})
        (group / "cpu.max").write_text("max 100000\n")
        (group / "memory.max").write_text("max\n")
        state = self.diagnostics.snapshot()
        self.assertIsNone(state["limits"]["cpu_quota_cores"])
        self.assertIsNone(state["limits"]["memory_limit_bytes"])

    def test_unsupported_environment_and_unverified_hardware(self):
        state = Diagnostics(self.root, self.root / "absent-proc", self.root / "absent-sys").snapshot()
        self.assertIsNone(state["cpu"]["usage_percent"])
        self.assertIsNone(state["memory"]["total_bytes"])
        self.assertEqual(state["network"], [])
        capabilities = state["capabilities"]["inputs"]
        self.assertTrue(capabilities["url"]["build_supported"])
        self.assertIsNone(capabilities["decklink"]["available"])
        self.assertIsNone(capabilities["ndi"]["available"])

    def test_bounded_interfaces_and_zero_elapsed(self):
        self.put("net/dev", "".join(net_line("eth" + str(i)) for i in range(MAX_INTERFACES * 2)))
        with mock.patch("diagnostics.time.monotonic", return_value=100):
            self.diagnostics.snapshot()
            state = self.diagnostics.snapshot()
        self.assertEqual(len(state["network"]), MAX_INTERFACES)
        self.assertIsNone(state["interval_seconds"])
        self.assertIsNone(state["cpu"]["usage_percent"])
        self.assertTrue(all(item["rx_bitrate_kbps"] is None for item in state["network"]))

    def test_cached_state_does_not_read_and_expires_rates(self):
        self.assertEqual(self.diagnostics.state()["status"], "pending")
        with mock.patch("diagnostics.time.monotonic", return_value=100):
            self.diagnostics.sample()
        self.put("net/dev", net_line("eth0", rx=2000, tx=3000))
        with mock.patch("diagnostics.time.monotonic", return_value=101):
            self.diagnostics.sample()
            with mock.patch("diagnostics._read", side_effect=AssertionError("cache must not read")):
                cached = self.diagnostics.state()
                self.assertEqual(cached["status"], "live")
                self.assertEqual(cached["network"][0]["rx_bitrate_kbps"], 8.0)
                cached["network"][0]["rx_bitrate_kbps"] = 123
                self.assertEqual(self.diagnostics.state()["network"][0]["rx_bitrate_kbps"], 8.0)
        with mock.patch("diagnostics.time.monotonic", return_value=102 + MAX_AGE):
            cached = self.diagnostics.state()
        self.assertEqual(cached["status"], "stale")
        self.assertIsNone(cached["cpu"]["usage_percent"])
        self.assertIsNone(cached["network"][0]["rx_bitrate_kbps"])
        self.assertEqual(cached["network"][0]["rx_bytes"], 2000)

    def test_video_format_is_nominal_and_unknown_metadata_stays_null(self):
        signal = signal_diagnostics({"width": 1920, "height": 1080, "scan": "i", "fps": "25/1"})
        self.assertEqual(signal["frame_rate"], 25)
        self.assertEqual(signal["field_rate"], 50)
        self.assertIsNone(signal["field_order"])
        self.assertIsNone(signal["color_matrix"])
        self.assertFalse(signal["field_motion_verified"])
        self.assertFalse(signal["audio_video_sync_verified"])
        progressive = signal_diagnostics({"width": 1920, "height": 1080, "scan": "p", "fps": "50/1"})
        self.assertEqual(progressive["frame_rate"], 50)
        self.assertIsNone(progressive["field_rate"])
        for fps in ("1/0", "NaN", "inf", "999999999/1", 50, None):
            self.assertIsNone(signal_diagnostics({"fps": fps})["frame_rate"])


if __name__ == "__main__":
    unittest.main()
