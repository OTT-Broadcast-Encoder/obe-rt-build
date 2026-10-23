#!/usr/bin/env python3
"""Local transport tests. No encoder, public endpoint or saved profile is used."""
import json
import os
from pathlib import Path
import secrets
import select
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from urllib.parse import urlsplit

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webpanel"))
import transport


PACKET = (b"\x47\x1f\xff\x10" + bytes(range(184))) * 7


def output(identifier="main", protocol="udp", port=14000, **extra):
    return {"id": identifier, "name": identifier.upper(), "protocol": protocol,
            "url": f"{protocol}://127.0.0.1:{port}", **extra}


def wait(predicate, seconds=4):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("Condizione non raggiunta entro il timeout")


def udp_socket():
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", 0))
    receiver.settimeout(2)
    return receiver


def send(url, data=PACKET):
    parts = urlsplit(url)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        sender.sendto(data, (parts.hostname, parts.port))


class Validation(unittest.TestCase):
    def test_defaults_and_backward_compatibility(self):
        self.assertEqual(transport.validate_outputs([]), [])
        normal = transport.validate_outputs([output()])[0]
        self.assertEqual(normal["latency_ms"], 200)
        self.assertEqual((normal["srt_key_length"], normal["srt_max_bandwidth_kbps"],
                          normal["srt_connect_timeout_ms"], normal["srt_send_timeout_ms"],
                          normal["srt_drop_late_packets"]), (32, 0, 1000, 1000, True))
        self.assertTrue(normal["enabled"])
        configured = transport.validate_outputs([output(protocol="srt", srt_key_length=16,
            srt_max_bandwidth_kbps=12000, srt_connect_timeout_ms=3000,
            srt_send_timeout_ms=2000, srt_drop_late_packets=False)])[0]
        self.assertEqual((configured["srt_key_length"], configured["srt_max_bandwidth_kbps"],
                          configured["srt_connect_timeout_ms"], configured["srt_send_timeout_ms"],
                          configured["srt_drop_late_packets"]), (16, 12000, 3000, 2000, False))
        manager = transport.Transport(ROOT)
        self.assertIsNone(manager.start([], {}))
        self.assertFalse(manager.state()["running"])
        manager.stop()
        manager.stop()

    def test_bad_public_fields_and_urls(self):
        invalid = [None, {}, [output(), output()], [output(id="primary")],
                   [output(enabled=1)], [output(latency_ms=True)], [output(latency_ms=9000)],
                   [output(srt_key_length=17)], [output(srt_key_length=True)],
                   [output(srt_max_bandwidth_kbps=-1)], [output(srt_max_bandwidth_kbps=319)],
                   [output(srt_max_bandwidth_kbps=True)], [output(srt_max_bandwidth_kbps=1000001)],
                   [output(srt_connect_timeout_ms=99)], [output(srt_connect_timeout_ms=True)],
                   [output(srt_send_timeout_ms=60001)], [output(srt_send_timeout_ms="1000")],
                   [output(srt_drop_late_packets=1)],
                   [output(protocol="file")], [output(srt_mode="rendezvous")],
                   [output(name="x\n")], [output(passphrase="secret-value")]]
        for url in ("udp://localhost", "udp://127.0.0.1:0", "udp://user:password@localhost:12",
                    "udp://127.0.0.1:3?ttl=2", "udp://127.0.0.1:3/path",
                    "udp://127.0.0.1:3#secret", "udp://%31%32%37.0.0.1:3",
                    "udp://host;command:3", "udp://0.0.0.0:9"):
            invalid.append([output(url=url)])
        for value in invalid:
            with self.subTest(shape=type(value).__name__), self.assertRaises(transport.TransportError):
                transport.validate_outputs(value)

    def test_listener_collision(self):
        first = output(protocol="srt", srt_mode="listener")
        second = output("backup", protocol="srt", srt_mode="listener")
        with self.assertRaises(transport.TransportError):
            transport.validate_outputs([first, second])
        second["enabled"] = False
        self.assertEqual(len(transport.validate_outputs([first, second])), 2)

    def test_credentials_are_separate_and_bound_to_endpoint(self):
        configs = [output(protocol="srt")]
        key = secrets.token_hex(16)
        saved = transport.merge_credentials([], configs, {}, {"main": key})
        self.assertTrue(saved["main"] == key)
        self.assertEqual(transport.public_credentials(configs, saved), {"main": True})
        self.assertTrue(transport.merge_credentials(configs, configs, saved)["main"] == key)
        self.assertEqual(transport.merge_credentials(configs, [output(protocol="srt", port=14001)], saved), {})
        self.assertEqual(transport.merge_credentials(configs, configs, saved, {"main": ""}), {})
        self.assertEqual(transport.merge_credentials(configs, [], saved), {})
        self.assertTrue(key not in json.dumps(transport.validate_outputs(configs)))

    def test_reject_bad_credentials(self):
        for patch in ({"unknown": "long-enough-value"}, {"main": "short"}, {"main": "a" * 80},
                      {"main": "b" * 10 + "\n"}, {"main": None}, []):
            with self.assertRaises(transport.TransportError):
                transport.merge_credentials([], [output(protocol="srt")], {}, patch)
        with self.assertRaises(transport.TransportError):
            transport.merge_credentials([], [output()], {}, {"main": "long-enough-value"})

    def test_unavailable_srt_is_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = transport.Transport(folder)
            with self.assertRaises(transport.TransportError):
                manager.start([output(protocol="srt")])
            self.assertFalse(manager.state()["running"])


class Fanout(unittest.TestCase):
    def setUp(self):
        self.manager = transport.Transport(ROOT)

    def tearDown(self):
        self.manager.stop()

    def test_udp_and_rtp_preserve_exact_transport_stream(self):
        with udp_socket() as udp, udp_socket() as rtp:
            url = self.manager.start([output(port=udp.getsockname()[1]), output("backup", "rtp", rtp.getsockname()[1])])
            send(url)
            self.assertEqual(udp.recv(2048), PACKET)
            first = rtp.recv(2048)
            self.assertEqual(first[12:], PACKET)
            version, payload_type, sequence, timestamp, ssrc = struct.unpack("!BBHII", first[:12])
            self.assertEqual((version, payload_type), (128, 33))
            send(url)
            second = rtp.recv(2048)
            self.assertEqual(struct.unpack("!H", second[2:4])[0], (sequence + 1) & 65535)
            self.assertEqual(second[8:12], first[8:12])
            def both_sent():
                current = self.manager.state()
                return current if all(item["sent_packets"] == 2 for item in current["outputs"]) else None
            state = wait(both_sent)
            self.assertEqual(state["input_packets"], 2)
            self.assertTrue(all(item["sent_bytes"] == len(PACKET) * 2 for item in state["outputs"]))

    def test_primary_stays_available_for_preview(self):
        with udp_socket() as primary, udp_socket() as extra:
            url = self.manager.start([output(port=extra.getsockname()[1])], primary_url=f"udp://127.0.0.1:{primary.getsockname()[1]}")
            send(url)
            self.assertEqual(primary.recv(2048), PACKET)
            self.assertEqual(extra.recv(2048), PACKET)
            self.assertEqual(self.manager.state()["outputs"][0]["id"], "primary")

    def test_first_output_and_removal_keep_primary_and_ingress(self):
        with udp_socket() as primary, udp_socket() as extra:
            primary_url = f"udp://127.0.0.1:{primary.getsockname()[1]}"
            url = self.manager.start([], primary_url=primary_url)
            worker, receiver, thread = self.manager.outputs[0], self.manager.receiver, self.manager.thread
            send(url)
            self.assertEqual(primary.recv(2048), PACKET)
            self.manager.reconfigure([output(port=extra.getsockname()[1])], primary_url=primary_url)
            send(url)
            self.assertEqual(primary.recv(2048), PACKET)
            self.assertEqual(extra.recv(2048), PACKET)
            removed = self.manager.outputs[1]
            self.manager.reconfigure([], primary_url=primary_url)
            send(url)
            self.assertEqual(primary.recv(2048), PACKET)
            self.assertIs(self.manager.outputs[0], worker)
            self.assertIs(self.manager.receiver, receiver)
            self.assertIs(self.manager.thread, thread)
            wait(lambda: worker.state()["sent_packets"] == 3)
            self.assertFalse(removed.thread.is_alive())

    def test_rename_and_other_destination_change_preserve_rtp_sequence(self):
        with udp_socket() as rtp, udp_socket() as old, udp_socket() as new:
            retained = output("retained", "rtp", rtp.getsockname()[1])
            url = self.manager.start([retained, output(port=old.getsockname()[1])])
            worker = self.manager.outputs[0]
            send(url)
            first = rtp.recv(2048)
            self.assertEqual(old.recv(2048), PACKET)
            self.manager.reconfigure([output(port=new.getsockname()[1]), {**retained, "name": "Renamed"}])
            send(url)
            second = rtp.recv(2048)
            self.assertEqual(new.recv(2048), PACKET)
            self.assertIs(self.manager.outputs[1], worker)
            self.assertEqual(worker.state()["name"], "Renamed")
            self.assertEqual(second[8:12], first[8:12])
            self.assertEqual(struct.unpack("!H", second[2:4])[0], (struct.unpack("!H", first[2:4])[0] + 1) & 65535)

    def test_failed_persistence_or_preparation_keeps_old_outputs(self):
        opened = []
        def factory(config, root, key):
            opened.append(config["id"])
            return transport._DatagramSink(config, root, key)
        self.manager = transport.Transport(ROOT, factory)
        with udp_socket() as receiver, udp_socket() as extra:
            outputs = [output(port=receiver.getsockname()[1])]
            url = self.manager.start(outputs)
            send(url)
            receiver.recv(2048)
            previous = self.manager.outputs[0]
            changed = outputs + [output("new", port=extra.getsockname()[1])]
            with self.assertRaises(OSError):
                self.manager.reconfigure(changed, commit=mock.Mock(side_effect=OSError("disk full")))
            self.assertEqual(opened, ["main"])
            commit = mock.Mock()
            with mock.patch.object(threading.Thread, "start", side_effect=RuntimeError("thread unavailable")):
                with self.assertRaises(RuntimeError):
                    self.manager.reconfigure(changed, commit=commit)
            commit.assert_not_called()
            self.assertEqual(self.manager.outputs, [previous])
            send(url)
            self.assertEqual(receiver.recv(2048), PACKET)

    def test_slow_removed_destination_does_not_pause_retained_stream(self):
        closing, release = threading.Event(), threading.Event()
        class SlowClose(transport._DatagramSink):
            def close(self):
                closing.set()
                release.wait(3)
                super().close()
        self.manager = transport.Transport(ROOT, lambda config, root, key:
            (SlowClose if config["id"] == "slow" else transport._DatagramSink)(config, root, key))
        with udp_socket() as receiver, udp_socket() as retired:
            kept = output(port=receiver.getsockname()[1])
            url = self.manager.start([kept, output("slow", port=retired.getsockname()[1])])
            send(url)
            receiver.recv(2048)
            retired.recv(2048)
            change = threading.Thread(target=self.manager.reconfigure, args=([kept],))
            change.start()
            try:
                self.assertTrue(closing.wait(1))
                for _ in range(5):
                    send(url)
                    self.assertEqual(receiver.recv(2048), PACKET)
            finally:
                release.set()
                change.join(3)
            self.assertFalse(change.is_alive())

    def test_key_change_disable_and_reenable_only_replace_target(self):
        self.manager = transport.Transport(ROOT, lambda config, root, key: transport._DatagramSink(config, root, key))
        with udp_socket() as primary, udp_socket() as extra:
            primary_url = f"udp://127.0.0.1:{primary.getsockname()[1]}"
            target = output(protocol="srt", port=extra.getsockname()[1])
            url = self.manager.start([target], {"main": secrets.token_hex(16)}, primary_url)
            original, replaced = self.manager.outputs
            self.manager.reconfigure([target], {"main": secrets.token_hex(16)}, primary_url)
            self.assertIs(self.manager.outputs[0], original)
            self.assertFalse(replaced.thread.is_alive())
            self.manager.reconfigure([{**target, "enabled": False}], primary_url=primary_url)
            self.assertEqual(self.manager.state()["outputs"][1]["status"], "disabled")
            self.manager.reconfigure([target], primary_url=primary_url)
            send(url)
            self.assertEqual(primary.recv(2048), PACKET)
            self.assertEqual(extra.recv(2048), PACKET)
            self.assertIs(self.manager.outputs[0], original)

    def test_primary_multicast_ttl_is_preserved(self):
        config = transport.primary_output("udp://239.255.0.1:13000?ttl=2")
        sink = transport._DatagramSink(config, ROOT, "")
        try:
            self.assertEqual(sink.socket.getsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL), 2)
        finally:
            sink.close()
        for suffix in ("ttl=256", "ttl=-1", "ttl=2&ttl=3", "unsupported=1"):
            with self.assertRaises(transport.TransportError) as caught:
                transport.primary_output("udp://239.255.0.1:13000?" + suffix)
            self.assertEqual(caught.exception.field, "output_url")

    def test_disabled_outputs_do_not_start_workers(self):
        self.assertIsNone(self.manager.start([output(enabled=False)]))
        self.assertEqual(self.manager.state()["outputs"][0]["status"], "disabled")

    def test_invalid_datagrams_are_rejected_and_large_ts_is_split(self):
        with udp_socket() as receiver:
            url = self.manager.start([output(port=receiver.getsockname()[1])])
            send(url, b"not a transport stream")
            send(url, b"\x00" + PACKET[1:])
            send(url, PACKET * 2)
            self.assertEqual(receiver.recv(2048), PACKET)
            self.assertEqual(receiver.recv(2048), PACKET)
            state = wait(lambda: self.manager.state() if self.manager.state()["invalid_packets"] == 2 else None)
            self.assertEqual(state["input_bytes"], len(PACKET) * 2)

    def test_broken_and_backpressured_output_cannot_block_healthy_one(self):
        class Broken:
            def __init__(self, config, root, key):
                raise OSError("not connected")
        class Slow:
            def __init__(self, config, root, key):
                pass
            def send(self, data):
                time.sleep(0.05)
                return False
            def metrics(self):
                return {"status": "connecting"}
            def close(self):
                pass
        factory = lambda config, root, key: (Broken if config["id"] == "broken" else Slow if config["id"] == "slow" else transport._DatagramSink)(config, root, key)
        self.manager = transport.Transport(ROOT, factory)
        with udp_socket() as receiver:
            url = self.manager.start([output(port=receiver.getsockname()[1]), output("broken"), output("slow")])
            for _ in range(300):
                send(url)
            self.assertEqual(receiver.recv(2048), PACKET)
            wait(lambda: self.manager.state()["outputs"][2]["dropped_packets"] > 0)
            states = self.manager.state()["outputs"]
            self.assertGreater(states[0]["sent_packets"], 0)
            self.assertGreater(states[1]["errors"], 0)
            self.assertLessEqual(states[2]["queued_packets"], transport.QUEUE_PACKETS)

    def test_stop_closes_threads_and_releases_ingest_socket(self):
        with udp_socket() as receiver:
            url = self.manager.start([output(port=receiver.getsockname()[1])])
            send(url)
            receiver.recv(2048)
            workers = [item.thread for item in self.manager.outputs] + [self.manager.thread]
            start = time.monotonic()
            self.manager.stop()
            self.assertLess(time.monotonic() - start, 2)
            self.assertTrue(all(not thread.is_alive() for thread in workers))
            self.assertFalse(self.manager.state()["running"])
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as check:
                check.bind(("127.0.0.1", urlsplit(url).port))

    def test_reconnect_is_per_destination(self):
        attempts = {}
        def factory(config, root, key):
            attempts[config["id"]] = attempts.get(config["id"], 0) + 1
            if config["id"] == "backup" and attempts["backup"] == 1:
                raise OSError("temporary failure")
            return transport._DatagramSink(config, root, key)
        self.manager = transport.Transport(ROOT, factory)
        with udp_socket() as main, udp_socket() as backup:
            url = self.manager.start([output(port=main.getsockname()[1]), output("backup", port=backup.getsockname()[1])])
            wait(lambda: attempts.get("backup", 0) >= 2)
            send(url)
            self.assertEqual(main.recv(2048), PACKET)
            self.assertEqual(backup.recv(2048), PACKET)
            self.assertEqual(attempts["main"], 1)


RECEIVER_SOURCE = r'''
#define _POSIX_C_SOURCE 200809L
#include <arpa/inet.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <srt/srt.h>
int main(int argc, char **argv) {
  if (argc != 3 && argc != 4) return 2;
  srt_startup(); srt_setloglevel(0);
  SRTSOCKET fd = srt_create_socket();
  int type = SRTT_LIVE, latency = 20, timeout = 2000;
  srt_setsockflag(fd, SRTO_TRANSTYPE, &type, sizeof(type));
  srt_setsockflag(fd, SRTO_RCVLATENCY, &latency, sizeof(latency));
  srt_setsockflag(fd, SRTO_CONNTIMEO, &timeout, sizeof(timeout));
  srt_setsockflag(fd, SRTO_RCVTIMEO, &timeout, sizeof(timeout));
  const char *hold = getenv("OBE_TEST_SRT_HOLD_MS");
  if (getenv("OBE_TEST_SRT_KEEP_LATE") || hold) {
    int disabled = 0;
    srt_setsockflag(fd, SRTO_TLPKTDROP, &disabled, sizeof(disabled));
  }
  if (hold) {
    int buffer = 65536;
    srt_setsockflag(fd, SRTO_RCVBUF, &buffer, sizeof(buffer));
  }
  const char *key = getenv("OBE_OUTPUT_SRT_PASSPHRASE");
  if (key && *key) srt_setsockflag(fd, SRTO_PASSPHRASE, key, strlen(key));
  struct sockaddr_in addr = {0};
  addr.sin_family = AF_INET; addr.sin_port = htons(atoi(argv[1]));
  addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  if (!strcmp(argv[2], "listener")) {
    if (srt_bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0 || srt_listen(fd, 1) < 0) return 3;
    SRTSOCKET connection = srt_accept(fd, NULL, NULL);
    if (connection == SRT_INVALID_SOCK) return 4;
    srt_close(fd); fd = connection;
  } else if (srt_connect(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) return 5;
  if (hold) {
    long ms = atol(hold);
    struct timespec delay = { ms / 1000, (ms % 1000) * 1000000 };
    nanosleep(&delay, NULL);
    srt_close(fd); srt_cleanup(); return 0;
  }
  int count = argc == 4 ? atoi(argv[3]) : 10;
  for (int i = 0; i < count; ++i) {
    char data[2048]; int got = srt_recvmsg(fd, data, sizeof(data));
    if (got <= 0 || fwrite(data, 1, got, stdout) != (size_t)got) return 6;
  }
  fflush(stdout); srt_close(fd); srt_cleanup(); return 0;
}
'''


@unittest.skipUnless((ROOT / "target-root/usr/local/lib/libsrt.a").is_file(), "LibSRT non compilata")
class EncryptedSRT(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="obe-transport-test-")
        folder = Path(cls.temp.name)
        source, obj, executable = folder / "receive.c", folder / "receive.o", folder / "receive"
        cls.helper = folder / "obe-srt-output"
        subprocess.run(["sh", str(ROOT / "webpanel/build-srt-output.sh"), str(cls.helper)], check=True, capture_output=True)
        cls.helper_patch = mock.patch.object(transport, "helper_path", return_value=cls.helper)
        cls.helper_patch.start()
        source.write_text(RECEIVER_SOURCE)
        prefix = ROOT / "target-root/usr/local"
        subprocess.run(["cc", "-I" + str(prefix / "include"), "-c", str(source), "-o", str(obj)], check=True, capture_output=True)
        subprocess.run(["c++", str(obj), str(prefix / "lib/libsrt.a"), "-lssl", "-lcrypto", "-lpthread", "-lm", "-o", str(executable)], check=True, capture_output=True)
        cls.receiver = executable

    @classmethod
    def tearDownClass(cls):
        cls.helper_patch.stop()
        cls.temp.cleanup()

    def exercise(self, mode, key=None, **settings):
        key = key or secrets.token_hex(16)
        with udp_socket() as reservation:
            port = reservation.getsockname()[1]
        outputs = [output(protocol="srt", port=port, srt_mode=mode, latency_ms=20, **settings)]
        sinks = []
        def factory(config, root, secret):
            sink = transport._SRTSink(config, root, secret)
            sinks.append(sink)
            return sink
        manager = transport.Transport(ROOT, factory)
        process = None
        try:
            with tempfile.TemporaryFile() as received:
                receiver_mode = "listener" if mode == "caller" else "caller"
                def launch_receiver():
                    return subprocess.Popen([str(self.receiver), str(port), receiver_mode],
                                            env={**os.environ, "OBE_OUTPUT_SRT_PASSPHRASE": key},
                                            stdout=received, stderr=subprocess.DEVNULL)
                if mode == "caller":
                    process = launch_receiver()
                    time.sleep(0.1)
                url = manager.start(outputs, {"main": key})
                if mode == "listener":
                    time.sleep(0.1)
                    process = launch_receiver()
                wait(lambda: manager.state()["outputs"][0]["status"] == "live", seconds=6)
                self.assertTrue(key not in json.dumps(manager.state()))
                for sink in sinks:
                    self.assertTrue(key not in " ".join(sink.process.args))
                    self.assertTrue(key.encode() not in Path(f"/proc/{sink.process.pid}/cmdline").read_bytes())
                    self.assertEqual(sink.process.args[5:], [str(settings.get("srt_key_length", 32)),
                        str(settings.get("srt_max_bandwidth_kbps", 0)),
                        str(settings.get("srt_connect_timeout_ms", 1000)),
                        str(settings.get("srt_send_timeout_ms", 1000)),
                        "1" if settings.get("srt_drop_late_packets", True) else "0"])
                for _ in range(20):
                    send(url)
                    time.sleep(0.03)
                self.assertEqual(process.wait(timeout=4), 0)
                received.seek(0)
                self.assertEqual(received.read(), PACKET * 10)
                state = manager.state()["outputs"][0]
                self.assertTrue(state["encrypted"])
                self.assertGreater(state["sent_bytes"], 0)
        finally:
            start = time.monotonic()
            manager.stop()
            self.assertLess(time.monotonic() - start, 2)
            if process and process.poll() is None:
                process.terminate()
                process.wait(timeout=2)

    def test_encrypted_caller(self):
        self.exercise("caller")

    def test_encrypted_listener(self):
        self.exercise("listener")

    def test_live_add_encrypted_srt_keeps_primary_worker(self):
        for mode in ("caller", "listener"):
            with self.subTest(mode=mode), udp_socket() as primary, tempfile.TemporaryFile() as received:
                with udp_socket() as reservation:
                    port = reservation.getsockname()[1]
                key = secrets.token_hex(16)
                primary_url = f"udp://127.0.0.1:{primary.getsockname()[1]}"
                manager, process = transport.Transport(ROOT), None
                try:
                    url = manager.start([], primary_url=primary_url)
                    original = manager.outputs[0]
                    manager.reconfigure([output(protocol="srt", port=port, srt_mode=mode, latency_ms=20)],
                                        {"main": key}, primary_url)
                    process = subprocess.Popen([str(self.receiver), str(port), "listener" if mode == "caller" else "caller"],
                        env={**os.environ, "OBE_OUTPUT_SRT_PASSPHRASE": key}, stdout=received, stderr=subprocess.DEVNULL)
                    wait(lambda: manager.state()["outputs"][1]["status"] == "live", seconds=6)
                    for _ in range(20):
                        send(url)
                        self.assertEqual(primary.recv(2048), PACKET)
                        time.sleep(.03)
                    self.assertEqual(process.wait(timeout=4), 0)
                    received.seek(0)
                    self.assertEqual(received.read(), PACKET * 10)
                    manager.reconfigure([], primary_url=primary_url)
                    self.assertIs(manager.outputs[0], original)
                    send(url)
                    self.assertEqual(primary.recv(2048), PACKET)
                finally:
                    manager.stop()
                    if process and process.poll() is None:
                        process.terminate()
                        process.wait(timeout=2)

    def test_configurable_srt_options(self):
        for mode in ("caller", "listener"):
            for length in (16, 24, 32):
                with self.subTest(mode=mode, key_length=length):
                    self.exercise(mode, srt_key_length=length, srt_max_bandwidth_kbps=12000,
                                  srt_connect_timeout_ms=3000, srt_send_timeout_ms=2000,
                                  srt_drop_late_packets=False)

    def command(self, port, **settings):
        values = {**transport.OUTPUT_DEFAULTS, **settings}
        names = ("latency_ms", "srt_key_length", "srt_max_bandwidth_kbps",
                 "srt_connect_timeout_ms", "srt_send_timeout_ms", "srt_drop_late_packets")
        return [str(self.helper), "127.0.0.1", str(port), values["srt_mode"],
                *(str(int(values[name])) if type(values[name]) is bool else str(values[name]) for name in names)]

    def test_native_validation_matches_public_contract(self):
        cases = {"latency_ms": (19, 8001), "srt_key_length": (15, 17, 33),
                 "srt_max_bandwidth_kbps": (-1, 1, 319, 1000001),
                 "srt_connect_timeout_ms": (99, 60001), "srt_send_timeout_ms": (99, 60001),
                 "srt_drop_late_packets": (-1, 2)}
        for name, invalid in cases.items():
            for value in (*invalid, "1.5", "x", "999999999999999999999999999999"):
                with self.subTest(field=name, value=value):
                    with self.assertRaises(transport.TransportError):
                        transport.validate_outputs([output(protocol="srt", **{name: value})])
                    result = subprocess.run(self.command(9, **{name: value}),
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
                    self.assertEqual(result.returncode, 2)
                    self.assertFalse(result.stdout or result.stderr)

    def test_connection_timeout_changes_actual_wait(self):
        durations = []
        # An open UDP socket that never speaks SRT avoids immediate ICMP rejection.
        with udp_socket() as blackhole:
            for timeout in (200, 1200):
                started = time.monotonic()
                result = subprocess.run(self.command(blackhole.getsockname()[1], srt_connect_timeout_ms=timeout),
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
                durations.append(time.monotonic() - started)
                self.assertEqual(result.returncode, 3)
                self.assertFalse(result.stdout or result.stderr)
        self.assertGreater(durations[1] - durations[0], .5)
        self.assertLess(durations[0], 3)

    def test_bandwidth_limit_preserves_payload(self):
        count, bandwidth = 160, 400
        peer = sender = None
        key = secrets.token_hex(16)
        with udp_socket() as reservation:
            port = reservation.getsockname()[1]
        try:
            with tempfile.TemporaryFile() as received:
                env = {**os.environ, "OBE_OUTPUT_SRT_PASSPHRASE": key, "OBE_TEST_SRT_KEEP_LATE": "1"}
                peer = subprocess.Popen([str(self.receiver), str(port), "listener", str(count)],
                    env=env, stdout=received, stderr=subprocess.DEVNULL)
                time.sleep(.1)
                started = time.monotonic()
                sender = subprocess.Popen(self.command(port, latency_ms=20, srt_max_bandwidth_kbps=bandwidth,
                    srt_drop_late_packets=False), env=env, stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                pending = memoryview((struct.pack("!H", len(PACKET)) + PACKET) * count)
                os.set_blocking(sender.stdin.fileno(), False)
                deadline = time.monotonic() + 8
                while pending:
                    self.assertLess(time.monotonic(), deadline, "Gateway input stalled")
                    if select.select([], [sender.stdin], [], .1)[1]:
                        try:
                            pending = pending[os.write(sender.stdin.fileno(), pending):]
                        except BlockingIOError:
                            pass
                self.assertEqual(peer.wait(timeout=12), 0)
                elapsed = time.monotonic() - started
                received.seek(0)
                self.assertEqual(received.read(), PACKET * count)
                nominal = count * len(PACKET) * 8 / (bandwidth * 1000)
                self.assertGreater(elapsed, nominal * .7, "Ignoring MAXBW must fail this test")
                self.assertLess(elapsed, nominal * 2 + 3)
        finally:
            for process in (sender, peer):
                if process and process.poll() is None:
                    process.terminate()
                if process:
                    process.communicate(timeout=3)

    def test_send_timeout_bounds_a_stalled_receiver(self):
        durations = []
        for timeout in (200, 1200):
            peer = sender = None
            with udp_socket() as reservation:
                port = reservation.getsockname()[1]
            try:
                peer = subprocess.Popen([str(self.receiver), str(port), "listener"],
                    env={**os.environ, "OBE_TEST_SRT_HOLD_MS": "8000"},
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(.1)
                started = time.monotonic()
                sender = subprocess.Popen(self.command(port, latency_ms=20,
                    srt_send_timeout_ms=timeout, srt_drop_late_packets=False),
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                metrics, _ = sender.communicate((struct.pack("!H", len(PACKET)) + PACKET) * 3000, timeout=6)
                durations.append(time.monotonic() - started)
                self.assertEqual(sender.returncode, 3)
                self.assertIn(b'"status":"live"', metrics)
                self.assertIsNone(peer.poll(), "Receiver must still be connected when send times out")
            finally:
                for process in (sender, peer):
                    if process and process.poll() is None:
                        process.terminate()
                    if process:
                        process.communicate(timeout=3)
        self.assertGreater(durations[1] - durations[0], .5)

    def test_legacy_helper_arguments_still_transmit(self):
        wrapper = Path(self.temp.name) / "legacy-helper"
        # The wrapper emulates the previous backend's four positional arguments.
        wrapper.write_text(f'#!/bin/sh\nexec "{self.helper}" "$1" "$2" "$3" "$4"\n')
        wrapper.chmod(0o700)
        with mock.patch.object(transport, "helper_path", return_value=wrapper):
            self.exercise("caller")

    def test_reconnect_after_peer_replacement(self):
        key = secrets.token_hex(16)
        with udp_socket() as reservation:
            port = reservation.getsockname()[1]
        manager, processes = transport.Transport(ROOT), []
        try:
            with tempfile.TemporaryFile() as received:
                def receiver():
                    process = subprocess.Popen([str(self.receiver), str(port), "listener"],
                                               env={**os.environ, "OBE_OUTPUT_SRT_PASSPHRASE": key},
                                               stdout=received, stderr=subprocess.DEVNULL)
                    processes.append(process)
                    return process
                first = receiver()
                time.sleep(0.1)
                url = manager.start([output(protocol="srt", port=port, latency_ms=20)], {"main": key})
                wait(lambda: manager.state()["outputs"][0]["status"] == "live", seconds=6)
                for _ in range(15):
                    send(url)
                    time.sleep(0.03)
                self.assertEqual(first.wait(timeout=4), 0)
                wait(lambda: manager.state()["outputs"][0]["errors"] > 0, seconds=6)
                second = receiver()
                wait(lambda: manager.state()["outputs"][0]["status"] == "live", seconds=6)
                for _ in range(15):
                    send(url)
                    time.sleep(0.03)
                self.assertEqual(second.wait(timeout=4), 0)
                received.seek(0)
                self.assertEqual(received.read(), PACKET * 20)
                self.assertGreaterEqual(manager.state()["outputs"][0]["restarts"], 1)
        finally:
            manager.stop()
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=2)

    def test_unreachable_output_does_not_stop_other_destination(self):
        key = secrets.token_hex(16)
        manager = transport.Transport(ROOT)
        try:
            with udp_socket() as receiver, udp_socket() as unused:
                outputs = [output(port=receiver.getsockname()[1]), output("backup", "srt", unused.getsockname()[1], latency_ms=20)]
                url = manager.start(outputs, {"backup": key})
                send(url)
                self.assertEqual(receiver.recv(2048), PACKET)
                wait(lambda: manager.state()["outputs"][1]["errors"] > 0, seconds=5)
                send(url)
                self.assertEqual(receiver.recv(2048), PACKET)
                self.assertTrue(key not in json.dumps(manager.state()))
        finally:
            manager.stop()

    def test_wrong_key_is_rejected(self):
        key, wrong_key = secrets.token_hex(16), secrets.token_hex(16)
        with udp_socket() as reservation:
            port = reservation.getsockname()[1]
        process = subprocess.Popen([str(self.receiver), str(port), "listener"],
                                   env={**os.environ, "OBE_OUTPUT_SRT_PASSPHRASE": wrong_key},
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        manager = transport.Transport(ROOT)
        try:
            time.sleep(0.1)
            manager.start([output(protocol="srt", port=port, latency_ms=20)], {"main": key})
            wait(lambda: manager.state()["outputs"][0]["errors"] > 0, seconds=5)
            state = manager.state()["outputs"][0]
            self.assertNotEqual(state["status"], "live")
            self.assertEqual(state["sent_bytes"], 0)
            self.assertTrue(key not in json.dumps(state) and wrong_key not in json.dumps(state))
        finally:
            manager.stop()
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=2)


if __name__ == "__main__":
    unittest.main()
