#!/usr/bin/env python3
"""Isolated tests: measured content delay, receiver selection and control API."""
import base64
import copy
from http.server import ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import random
import socket
import runpy
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webpanel"))
import delivery
from delivery_agent import Agent, Handler, Stream, endpoint, ts_payload
from delivery_signal import Clock, Selector, fingerprint, measure_latency


def series(delay=0, offset=0, static=False, unrelated=False):
    samples = []
    for i in range(100):
        rng = random.Random(4000 if static else i + (3000 if unrelated else 0))
        raw = bytes(rng.randrange(20, 230) for _ in range(144))
        samples.append([100000 + i * 200 + delay + offset, base64.b64encode(raw).decode()])
    return {"schema": 1, "instance": "a" * 32, "revision": 1, "role": "source",
            "clock_ms": samples[-1][0] + 100, "samples": samples, "streams": {},
            "observation": {"status": "live", "decoded_frames": 100, "frame_age_seconds": .1}, "selector": None}


class TimingTests(unittest.TestCase):
    def test_delay_and_independent_clock_offsets(self):
        a, b = series(offset=40000), series(delay=3400, offset=-30000)
        result = measure_latency(a, b, 40000, -30000, 25)
        self.assertEqual(result["status"], "measured")
        self.assertEqual(result["latency_ms"], 3400)
        self.assertEqual(result["uncertainty_ms"], 225)

    def test_rejects_static_unrelated_stale_and_uncertain(self):
        cases = [(series(static=True), series(delay=1000, static=True), 0),
                 (series(), series(delay=1000, unrelated=True), 0), (series(), series(delay=1000), 251)]
        stale = series(delay=1000)
        stale["clock_ms"] += 4000
        cases.append((series(), stale, 0))
        for a, b, error in cases:
            with self.subTest(error=error):
                result = measure_latency(a, b, 0, 0, error)
                self.assertEqual(result["status"], "unavailable")
                self.assertIsNone(result["latency_ms"])

    def test_decoder_bursts_contribute_to_uncertainty(self):
        a, b = series(), series(delay=3400)
        for i, sample in enumerate(b["samples"]):
            sample[0] = 103400 + (i // 3 + 1) * 600 + i % 3 * 5
        b["clock_ms"] = b["samples"][-1][0] + 100
        result = measure_latency(a, b, 0, 0, 10)
        self.assertEqual(result["status"], "measured")
        self.assertGreaterEqual(result["uncertainty_ms"], 400)
        self.assertGreaterEqual(result["observation_jitter_ms"], 190)

    def test_repeated_content_not_arbitrary_latency(self):
        a, b = series(), series(delay=2000)
        for state in (a, b):
            for i in range(len(state["samples"])):
                state["samples"][i][1] = state["samples"][i % 15][1]
        self.assertEqual(measure_latency(a, b, 0, 0, 0)["status"], "unavailable")

    def test_rejects_invalid_remote_samples(self):
        for value in (float("nan"), "123", -1):
            bad = series()
            bad["samples"][-1][0] = value
            with self.assertRaises(ValueError):
                delivery.validate_state(bad)
        bad = series()
        bad["samples"][-1][1] = "?" * 192
        with self.assertRaises(ValueError):
            delivery.validate_state(bad)

    def test_fingerprint_area(self):
        self.assertEqual(fingerprint(bytes([70]) * 160 * 90), bytes([70]) * 144)


class SelectionTests(unittest.TestCase):
    def test_failover_failback_and_no_flapping(self):
        s = Selector(failure_seconds=2, recovery_seconds=30, hold_seconds=5)
        both = {"main": True, "backup": True}
        self.assertIsNone(s.update(both, 0))
        self.assertEqual(s.update(both, 2), "main")
        self.assertIsNone(s.update({"main": False, "backup": True}, 10))
        self.assertIsNone(s.update({"main": False, "backup": True}, 11))
        self.assertEqual(s.update({"main": False, "backup": True}, 12), "backup")
        self.assertEqual(s.update(both, 13), "backup")
        self.assertEqual(s.update(both, 42), "backup")
        self.assertEqual(s.update(both, 43), "main")
        self.assertEqual(s.switches, 3)

    def test_both_failed_and_unproven_backup(self):
        s = Selector()
        s.update({"main": True, "backup": False}, 0)
        s.update({"main": True, "backup": False}, 2)
        self.assertIsNone(s.update({"main": False, "backup": False}, 12))
        self.assertIsNone(s.update({"main": False, "backup": False}, 20))
        self.assertEqual(s.active, "main")
        self.assertEqual(s.switches, 1)

    def test_manual_override_does_not_automatically_failback(self):
        s = Selector()
        s.mode = "backup"
        s.update({"main": True, "backup": True}, 0)
        self.assertEqual(s.update({"main": True, "backup": True}, 2), "backup")
        self.assertEqual(s.update({"main": True, "backup": True}, 200), "backup")
        self.assertIsNone(s.update({"main": True, "backup": False}, 201))

    def test_udp_rtp_validation(self):
        packet = b"\x47\x1f\xff\x10" + bytes(184)
        self.assertEqual(ts_payload(packet), packet)
        rtp = b"\x80\x21" + bytes(10) + packet
        self.assertEqual(ts_payload(rtp, True), packet)
        self.assertIsNone(ts_payload(rtp[:10], True))
        self.assertIsNone(ts_payload(b"\xa0\x21" + bytes(10) + packet + b"\x00", True))
        self.assertIsNone(ts_payload(packet[:-1]))
        for url in ("file:///etc/passwd", "udp://name:1234", "udp://127.0.0.1:1234?bad=1"):
            with self.assertRaises(ValueError):
                endpoint(url)

    def test_continuity_duplicates_and_payload_corruption(self):
        class FakeDecoder:
            def __init__(self, *args): self.process = SimpleNamespace(poll=lambda: None)
            def feed(self, data): pass
            def close(self): pass
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        agent = SimpleNamespace(clock=Clock(), config={"ffmpeg": "unused"}, route=lambda *_: None)
        with patch("delivery_agent.Decoder", FakeDecoder):
            stream = Stream("return", f"udp://127.0.0.1:{port}", agent)
            stream.start()
            sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                original = b"\x47\x01\x00\x10" + bytes(184)
                corrupt = b"\x47\x01\x00\x10" + bytes([1]) * 184
                sender.sendto(original + original + corrupt, ("127.0.0.1", port))
                deadline = time.monotonic() + 2
                while stream.state()["packets"] < 3 and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertEqual(stream.state()["continuity_errors"], 1)
                self.assertEqual(stream.state()["packets"], 3)
            finally:
                sender.close()
                stream.close()


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.client = delivery.Delivery(self.directory.name)

    def tearDown(self):
        self.client.close()
        self.directory.cleanup()

    def config(self, **kwargs):
        return {"revision": self.client.revision, "config": {**delivery.DEFAULTS, **kwargs},
                "tokens": {"receiver": "x" * 48, "source": "y" * 48}}

    def test_private_persistence_and_redacted_state(self):
        self.client.configure(self.config(receiver_url="http://127.0.0.1:18102"))
        self.assertNotIn("x" * 48, json.dumps(self.client.state()))
        self.assertEqual(self.client.path.stat().st_mode & 0o777, 0o600)
        other = delivery.Delivery(self.directory.name)
        try:
            self.assertTrue(other.state()["tokens"]["receiver"])
            self.assertEqual(other.state()["config"]["receiver_url"], "http://127.0.0.1:18102")
        finally:
            other.close()

    def test_revision_and_endpoint_change_drop_token(self):
        payload = self.config(receiver_url="http://127.0.0.1:18102")
        self.client.configure(payload)
        with self.assertRaises(delivery.DeliveryError):
            self.client.configure(payload)
        payload = self.config(receiver_url="http://127.0.0.1:18103")
        payload.pop("tokens")
        self.client.configure(payload)
        self.assertFalse(self.client.state()["tokens"]["receiver"])

    def test_no_remote_controls_without_verified_selector(self):
        with self.assertRaises(delivery.DeliveryError):
            self.client.control({"revision": 1, "mode": "backup"})

    def test_control_api_auth_and_snapshot_validation(self):
        state = series()
        class Fake:
            def state(self): return state
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.agent, server.token = Fake(), "a" * 48
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}"
            with self.assertRaises(delivery.DeliveryError):
                delivery.request(url, "b" * 48)
            result, offset, uncertainty = delivery.request(url, server.token)
            self.assertEqual(result["schema"], 1)
            self.assertGreaterEqual(uncertainty, 0)
            state["clock_ms"] = float("nan")
            with self.assertRaises(delivery.DeliveryError):
                delivery.request(url, server.token)
        finally:
            server.shutdown()
            server.server_close()

    def test_stale_snapshot_never_shows_old_measurement(self):
        with self.client.lock:
            self.client.config["enabled"] = True
            self.client.received = time.monotonic() - 6
            self.client.snapshot = {"latency": {"status": "measured", "latency_ms": 500}}
        self.assertNotIn("latency", self.client.state())


class PanelAPITests(unittest.TestCase):
    def test_channel_persistence_and_isolation(self):
        fixture = runpy.run_path(str(ROOT / "tests/webpanel.py"))["PanelTests"]()
        fixture.setUp()
        try:
            status, initial = fixture.request(path="/api/delivery")
            self.assertEqual(status, 200)
            self.assertEqual(initial["status"], "disabled")
            instance = fixture.request(path="/api/state")[1]["instance_id"]
            status, saved = fixture.request("PUT", "/api/delivery", {"revision": 1, "instance_id": instance,
                "config": {**delivery.DEFAULTS, "receiver_url": "http://127.0.0.1:18102"},
                "tokens": {"receiver": "z" * 48}})
            self.assertEqual(status, 200)
            self.assertNotIn("z" * 48, json.dumps(saved))
            status, _ = fixture.request("POST", "/api/delivery-control", {"revision": 2, "instance_id": instance, "mode": "backup"})
            self.assertEqual(status, 409)
            status, created = fixture.request("POST", "/api/channels", {"name": "Isolato"})
            self.assertEqual(status, 201)
            self.assertFalse(created["state"]["delivery"]["tokens"]["receiver"])
            status, _ = fixture.request("PUT", "/api/channels/" + created["id"] + "/delivery",
                {"revision": 1, "instance_id": instance, "config": delivery.DEFAULTS})
            self.assertEqual(status, 409)
            fixture.stop_server()
            fixture.start_server()
            status, saved = fixture.request(path="/api/state")
            self.assertEqual(status, 200)
            self.assertTrue(saved["delivery"]["tokens"]["receiver"])
            self.assertFalse(saved["process_alive"])
        finally:
            fixture.tearDown()


if __name__ == "__main__":
    unittest.main()
