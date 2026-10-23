#!/usr/bin/env python3
"""Real subprocess/UDP checks for transport lifetime and private control IPC."""
import json
import os
from pathlib import Path
import runpy
import secrets
import select
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from urllib.parse import urlsplit

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webpanel"))
from transport_client import Client
from transport import TransportError

PACKET = (b"\x47\x1f\xff\x10" + bytes(184)) * 7


def receiver():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(2)
    return sock


def output(sock, name="mirror", protocol="rtp"):
    return {"id": name, "name": name, "protocol": protocol,
            "url": f"{protocol}://127.0.0.1:{sock.getsockname()[1]}"}


def send(url):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as source:
        source.sendto(PACKET, ("127.0.0.1", urlsplit(url).port))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="obe-output-service-")
        self.directory = Path(self.temp.name)
        self.service = None
        self.start_service()
        self.client = Client(self.directory / "control.sock", "main")

    def start_service(self):
        self.service = subprocess.Popen([sys.executable, "-B", str(ROOT / "webpanel/transport_service.py"),
            "--root", str(getattr(self, "runtime", ROOT)), "--state-dir", str(self.directory)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertTrue(select.select([self.service.stdout], [], [], 5)[0], "Service start timeout")
        self.assertEqual(self.service.stdout.readline(), b"Distributore pronto.\n")

    def stop_service(self, crash=False):
        if self.service.poll() is None:
            self.service.kill() if crash else self.service.terminate()
        _, error = self.service.communicate(timeout=10)
        self.assertEqual(error, b"")

    def tearDown(self):
        self.stop_service()
        self.temp.cleanup()

    def test_new_control_client_and_source_keep_port_and_rtp_sequence(self):
        with receiver() as primary, receiver() as mirror:
            config = [output(mirror)]
            primary_url = output(primary, protocol="udp")["url"]
            url = self.client.start(config, primary_url=primary_url)
            send(url)
            first = mirror.recv(2048)
            self.assertEqual(primary.recv(2048), PACKET)
            before = self.client.state()
            self.assertNotEqual(before["service_pid"], os.getpid())
            # A new panel/client and a new encoder socket do not own the output.
            new_client = Client(self.directory / "control.sock", "main")
            self.assertEqual(new_client.start(config, primary_url=primary_url), url)
            send(url)
            second = mirror.recv(2048)
            self.assertEqual(first[8:12], second[8:12])
            self.assertEqual((struct.unpack("!H", first[2:4])[0] + 1) & 65535,
                             struct.unpack("!H", second[2:4])[0])
            self.assertEqual(before["service_instance"], new_client.state()["service_instance"])

    def test_profile_failure_aborts_staged_output_without_interrupting_primary(self):
        with receiver() as primary, receiver() as extra:
            primary_url = output(primary, protocol="udp")["url"]
            url = self.client.start([], primary_url=primary_url)
            saved = (self.directory / "outputs.json").read_bytes()
            def fail():
                raise OSError("Fixture profile save failure")
            with self.assertRaises(OSError):
                self.client.reconfigure([output(extra)], primary_url=primary_url, commit=fail)
            state = self.client.state()
            self.assertEqual([v["id"] for v in state["outputs"]], ["primary"])
            self.assertEqual((self.directory / "outputs.json").read_bytes(), saved)
            send(url)
            self.assertEqual(primary.recv(2048), PACKET)

    def test_service_restart_restores_destination_and_same_ingress(self):
        with receiver() as primary:
            url = self.client.start([], primary_url=output(primary, protocol="udp")["url"])
            old = self.client.state()["service_instance"]
            self.stop_service(crash=True)
            self.start_service()
            state = self.client.state()
            self.assertNotEqual(state["service_instance"], old)
            self.assertEqual(state["input_url"], url)
            send(url)
            self.assertEqual(primary.recv(2048), PACKET)

    def test_idle_channel_reserves_output_until_removed(self):
        with receiver() as primary:
            url = output(primary, protocol="udp")["url"]
            self.client.start([], primary_url=url)
            other = Client(self.directory / "control.sock", "channel-2")
            with self.assertRaises(TransportError):
                other.start([], primary_url=url)
            self.client.remove()
            other.start([], primary_url=url)
            self.assertFalse(self.client.state()["running"])
            self.assertTrue(other.state()["running"])

    def test_secrets_private_and_unavailable_service_explicit(self):
        with receiver() as primary:
            self.client.start([], primary_url=output(primary, protocol="udp")["url"])
            for name in ("control.sock", "outputs.json"):
                self.assertEqual((self.directory / name).stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
            self.stop_service()
            self.assertFalse(self.client.state()["service_available"])
            with self.assertRaises(OSError):
                self.client.start([], primary_url=output(primary, protocol="udp")["url"])

    def test_source_silence_does_not_close_output(self):
        with receiver() as primary:
            url = self.client.start([], primary_url=output(primary, protocol="udp")["url"])
            self.assertEqual(self.client.state()["input_status"], "waiting")
            send(url)
            self.assertEqual(primary.recv(2048), PACKET)
            self.assertEqual(self.client.state()["input_status"], "live")
            time.sleep(2.1)
            state = self.client.state()
            self.assertTrue(state["running"])
            self.assertEqual(state["input_status"], "stale")
            send(url)
            self.assertEqual(primary.recv(2048), PACKET)

    def test_second_service_cannot_take_over_live_control_socket(self):
        duplicate = subprocess.run([sys.executable, "-B", str(ROOT / "webpanel/transport_service.py"),
            "--root", str(ROOT), "--state-dir", str(self.directory)], capture_output=True, timeout=5)
        self.assertEqual(duplicate.returncode, 1)
        self.assertEqual(duplicate.stderr, b"")
        self.assertEqual(self.client.state()["service_pid"], self.service.pid)

    def test_truncated_reply_is_a_recoverable_io_failure(self):
        with mock.patch("transport_client.read_message", side_effect=ValueError("Truncated reply")):
            with self.assertRaises(OSError):
                self.client.start([], primary_url="udp://127.0.0.1:19001")
        self.assertTrue(self.client.state()["service_available"])


class PanelLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.broker = ServiceTests()
        self.broker.setUp()
        panel_class = runpy.run_path(str(ROOT / "tests/webpanel.py"))["PanelTests"]
        self.panel = panel_class()
        self.panel.start_server = lambda extra_args=(): panel_class.start_server(self.panel,
            (*extra_args, "--transport-socket", str(self.broker.directory / "control.sock")))
        self.panel.setUp()
        self.primary = receiver()
        self.mirror = receiver()
        status, state = self.panel.save(output_url=output(self.primary, protocol="udp")["url"],
                                        outputs=[output(self.mirror)])
        self.assertEqual(status, 200, state)
        self.ingress = state["transport"]["input_url"]

    def tearDown(self):
        self.panel.tearDown()
        self.broker.tearDown()
        self.primary.close()
        self.mirror.close()

    def packet(self):
        send(self.ingress)
        self.assertEqual(self.primary.recv(2048), PACKET)
        packet = self.mirror.recv(2048)
        self.assertEqual(packet[12:], PACKET)
        return struct.unpack("!H", packet[2:4])[0], packet[8:12]

    def assert_retained(self, before):
        state = self.panel.state()
        self.assertEqual(state["transport"]["service_instance"], before["transport"]["service_instance"])
        self.assertEqual(state["transport"]["input_url"], self.ingress)
        self.assertTrue(state["transport"]["running"])
        return state

    def test_encoder_stop_start_and_crash_keep_destination(self):
        first = self.panel.start_encoder()
        self.assertTrue(first["capabilities"]["outputs"]["independent_process"])
        sequence, ssrc = self.packet()
        status, _ = self.panel.request("POST", "/api/stop", {})
        self.assertEqual(status, 200)
        stopped = self.assert_retained(first)
        self.assertFalse(stopped["process_alive"])
        second = self.panel.start_encoder()
        self.assertNotEqual(first["pid"], second["pid"])
        self.assertEqual(self.packet(), ((sequence + 1) & 65535, ssrc))
        os.kill(second["pid"], signal.SIGKILL)
        self.panel.wait_phase({"error"})
        self.assert_retained(first)
        self.assertEqual(self.packet(), ((sequence + 2) & 65535, ssrc))

    def test_encoding_apply_and_panel_restart_keep_destination(self):
        before = self.panel.start_encoder()
        sequence, ssrc = self.packet()
        status, _ = self.panel.request("POST", "/api/apply", {"revision": before["revision"], "instance_id": before["instance_id"],
            "config": {**before["config"], "video_bitrate_kbps": 7000}})
        self.assertEqual(status, 202)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            after = self.panel.state()
            if after["phase"] == "running" and after["pid"] != before["pid"]:
                break
            time.sleep(.05)
        self.assertEqual(after["phase"], "running")
        self.assertNotEqual(after["pid"], before["pid"])
        self.assert_retained(before)
        self.assertEqual(self.packet(), ((sequence + 1) & 65535, ssrc))
        self.panel.stop_server()
        self.assertEqual(self.packet(), ((sequence + 2) & 65535, ssrc))
        self.panel.start_server()
        restored = self.assert_retained(before)
        self.assertFalse(restored["process_alive"])
        self.panel.start_encoder()
        self.assertEqual(self.packet(), ((sequence + 3) & 65535, ssrc))

    def test_apply_outputs_while_encoder_stopped_and_delete_channel(self):
        before = self.panel.state()
        with receiver() as extra:
            status, state = self.panel.request("POST", "/api/apply", {"revision": before["revision"], "instance_id": before["instance_id"],
                "config": {**before["config"], "outputs": [output(extra, protocol="udp")]}})
            self.assertEqual(status, 200, state)
            self.assertFalse(state["process_alive"])
            self.assertEqual(state["apply_mode"], "outputs")
            send(self.ingress)
            self.assertEqual(extra.recv(2048), PACKET)
        status, created = self.panel.request("POST", "/api/channels", {"name": "Temporary channel"})
        self.assertEqual(status, 201, created)
        client = Client(self.broker.directory / "control.sock", created["id"])
        self.assertTrue(client.state()["running"])
        state = created["state"]
        status, deleted = self.panel.request("DELETE", "/api/channels/" + created["id"],
            {"revision": state["revision"], "instance_id": state["instance_id"]})
        self.assertEqual(status, 200, deleted)
        self.assertFalse(client.state()["running"])

    def test_service_crash_recovers_port_without_restarting_encoder(self):
        before = self.panel.start_encoder()
        self.packet()
        self.broker.stop_service(crash=True)
        failed = self.panel.state()
        self.assertEqual(failed["pid"], before["pid"])
        self.assertFalse(failed["transport"]["service_available"])
        self.broker.start_service()
        self.assertEqual(self.panel.state()["pid"], before["pid"])
        self.packet()

    def test_saving_independent_listener_cannot_claim_running_encoder_input(self):
        before = self.panel.start_encoder()
        status, created = self.panel.request("POST", "/api/channels", {"name": "Port reservation"})
        self.assertEqual(status, 201)
        helper = self.panel.root / "webpanel/bin/obe-srt-output"
        helper.parent.mkdir(parents=True)
        helper.write_text("#!/bin/sh\nexit 0\n")
        helper.chmod(0o700)
        state = created["state"]
        listener = {"id": "mirror", "name": "Listener", "protocol": "srt", "srt_mode": "listener",
                    "url": "srt://127.0.0.1:" + before["config"]["input_url"].rsplit(":", 1)[1]}
        status, response = self.panel.request("PUT", "/api/channels/" + created["id"] + "/config",
            {"revision": state["revision"], "config": {**state["config"], "outputs": [listener]}})
        self.assertEqual(status, 409, response)
        self.assertEqual(response["error"]["code"], "input_in_use")


@unittest.skipUnless((ROOT / "target-root/usr/local/lib/libsrt.a").is_file(), "LibSRT non compilata")
class SRTProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="obe-service-srt-")
        cls.runtime = Path(cls.temp.name)
        helper = cls.runtime / "webpanel/bin/obe-srt-output"
        subprocess.run(["sh", str(ROOT / "webpanel/build-srt-output.sh"), str(helper)], check=True, capture_output=True)
        source = runpy.run_path(str(ROOT / "tests/webpanel-transport.py"))["RECEIVER_SOURCE"]
        source = source.replace("timeout = 2000", "timeout = 10000")
        (cls.runtime / "receive.c").write_text(source)
        prefix = ROOT / "target-root/usr/local"
        subprocess.run(["cc", "-I" + str(prefix / "include"), "-c", str(cls.runtime / "receive.c"),
                        "-o", str(cls.runtime / "receive.o")], check=True, capture_output=True)
        cls.receiver = cls.runtime / "receive"
        subprocess.run(["c++", str(cls.runtime / "receive.o"), str(prefix / "lib/libsrt.a"),
                        "-lssl", "-lcrypto", "-lpthread", "-lm", "-o", str(cls.receiver)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.broker = ServiceTests()
        self.broker.runtime = self.runtime
        self.broker.setUp()

    def tearDown(self):
        self.broker.tearDown()

    def wait_live(self, client):
        deadline = time.monotonic() + 7
        while time.monotonic() < deadline:
            item = next(v for v in client.state()["outputs"] if v["id"] == "mirror")
            if item["status"] == "live":
                return item
            time.sleep(.05)
        self.fail("SRT did not connect")

    def test_encrypted_srt_connection_survives_source_gap_and_new_controller(self):
        for mode in ("caller", "listener"):
            with self.subTest(mode=mode), receiver() as primary, tempfile.TemporaryFile() as captured:
                with receiver() as reservation:
                    settings = {**output(reservation, protocol="srt"), "srt_mode": mode, "latency_ms": 20}
                    port = reservation.getsockname()[1]
                key = secrets.token_hex(16)
                command = [str(self.receiver), str(port), "listener" if mode == "caller" else "caller", "6"]
                def launch():
                    return subprocess.Popen(command, env={**os.environ, "OBE_OUTPUT_SRT_PASSPHRASE": key},
                                            stdout=captured, stderr=subprocess.DEVNULL)
                peer = None
                client = self.broker.client
                try:
                    if mode == "caller":
                        peer = launch()
                        time.sleep(.1)
                    url = client.start([settings], {"mirror": key}, output(primary, protocol="udp")["url"])
                    if mode == "listener":
                        time.sleep(.1)
                        peer = launch()
                    self.wait_live(client)
                    for _ in range(3):
                        send(url)
                        time.sleep(.04)
                    time.sleep(2.5)
                    replacement = Client(client.path, "main")
                    self.assertEqual(replacement.start([settings], {"mirror": key}, output(primary, protocol="udp")["url"]), url)
                    item = self.wait_live(replacement)
                    self.assertEqual(item["restarts"], 0)
                    self.assertTrue(key not in json.dumps(replacement.state()))
                    for _ in range(3):
                        send(url)
                        time.sleep(.04)
                    self.assertEqual(peer.wait(timeout=4), 0)
                    captured.seek(0)
                    self.assertEqual(captured.read(), PACKET * 6)
                finally:
                    client.remove()
                    if peer and peer.poll() is None:
                        peer.terminate()
                        peer.wait(timeout=3)

    def test_broker_crash_releases_helper_waiting_for_srt_peer(self):
        with receiver() as reservation:
            settings = {**output(reservation, protocol="srt"), "srt_mode": "listener", "latency_ms": 20}
        # Release the reservation before configuring the listener.
        url = self.broker.client.start([settings], primary_url="udp://127.0.0.1:19001")
        def children():
            result = []
            for path in Path("/proc").glob("[0-9]*/stat"):
                try:
                    stat = path.read_text().rsplit(")", 1)[1].split()
                    if int(stat[1]) == self.broker.service.pid:
                        result.append(path.parent)
                except (OSError, ValueError, IndexError):
                    pass
            return result
        deadline = time.monotonic() + 3
        helpers = children()
        while not helpers and time.monotonic() < deadline:
            time.sleep(.05)
            helpers = children()
        self.assertEqual(len(helpers), 1)
        self.broker.stop_service(crash=True)
        deadline = time.monotonic() + 3
        def still_alive():
            try:
                return helpers[0].joinpath("stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
            except FileNotFoundError:
                return False
        while still_alive() and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertFalse(still_alive())
        self.broker.start_service()
        self.assertEqual(self.broker.client.state()["input_url"], url)


if __name__ == "__main__":
    unittest.main()
