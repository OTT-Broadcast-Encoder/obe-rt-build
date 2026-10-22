#!/usr/bin/env python3
"""Measure encrypted live SRT ARQ with bounded, local UDP impairments.

Uses the existing srt-live-transmit binary, Python stdlib and loopback only.
This is a transport test, not an OBE encode or WAN benchmark. All keys remain
in process memory; only sanitized diagnostics and numeric statistics persist.
"""

import argparse
import hashlib
import heapq
import importlib.util
import json
from pathlib import Path
import re
import secrets
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("input_url", Path(__file__).with_name("input-url.py"))
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
PACKETS, PAYLOAD, SPACING = 1007, 1316, 0.005


class DatagramProxy:
    """Bidirectional forwarding; impair only caller-to-listener data packets."""

    def __init__(self, listener_port, impaired):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.settimeout(0.005)
        self.port = self.socket.getsockname()[1]
        self.listener = ("127.0.0.1", listener_port)
        self.caller = None
        self.impaired = impaired
        self.seen, self.dropped, self.recovered = set(), set(), set()
        self.delayed = self.retransmission_flags = self.data_datagrams = 0
        self.control_datagrams = 0
        self.pending, self.errors = [], []
        self.finished = threading.Event()
        self.thread = threading.Thread(target=self.forward, daemon=True)
        self.thread.start()

    def forward(self):
        try:
            while not self.finished.is_set():
                now = time.monotonic()
                while self.pending and self.pending[0][0] <= now:
                    _, _, data = heapq.heappop(self.pending)
                    self.socket.sendto(data, self.listener)
                try:
                    data, origin = self.socket.recvfrom(65536)
                except socket.timeout:
                    continue
                if origin == self.listener:
                    if self.caller:
                        self.socket.sendto(data, self.caller)
                    continue
                self.caller = origin
                assert len(data) >= 16, "Short SRT datagram"
                word0, word1 = struct.unpack_from("!II", data)
                if word0 & 0x80000000:
                    self.control_datagrams += 1
                else:
                    self.data_datagrams += 1
                    self.retransmission_flags += bool(word1 & (1 << 26))
                    sequence = word0 & 0x7fffffff
                    if sequence not in self.seen:
                        self.seen.add(sequence)
                        ordinal = len(self.seen)
                        if self.impaired and ordinal % 23 == 0:
                            self.dropped.add(sequence)
                            continue  # Drop once only, so ARQ can recover it.
                        if self.impaired and ordinal % 17 == 0:
                            self.delayed += 1
                            heapq.heappush(self.pending, (time.monotonic() + 0.080, ordinal, data))
                            continue
                    elif sequence in self.dropped:
                        self.recovered.add(sequence)
                self.socket.sendto(data, self.listener)
        except Exception as error:
            self.errors.append(type(error).__name__)

    def close(self):
        self.finished.set()
        self.thread.join(timeout=2)
        self.socket.close()
        assert not self.thread.is_alive(), "UDP proxy did not stop"
        assert not self.errors, self.errors


class LiveTransmit:
    def __init__(self, source, target, key, directory, name, receiver=False):
        self.key = key
        self.connected = threading.Event()
        self.data = bytearray()
        self.stats = directory / f"{name}-stats.jsonl"
        self.log = directory / f"{name}.log"
        self.process = subprocess.Popen(
            ["stdbuf", "-o0", "-e0", "srt-live-transmit", "-chunk:1316", "-buffering:1", "-autoreconnect:no", "-stats:20", "-fullstats",
             "-statspf:json", f"-statsout:{self.stats}", "-loglevel:error", source, target],
            cwd=base.ROOT, env=base.ENV, stdin=subprocess.DEVNULL if receiver else subprocess.PIPE,
            stdout=subprocess.PIPE if receiver else subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.logger = threading.Thread(target=self.collect_log, daemon=True)
        self.logger.start()
        self.reader = None
        if receiver:
            self.reader = threading.Thread(target=self.collect_data, daemon=True)
            self.reader.start()

    def collect_log(self):
        with self.log.open("w", buffering=1) as output:
            for line in self.process.stderr:
                message = line.decode(errors="replace").replace(self.key, "[redacted]")
                output.write(re.sub(r"(?i)(passphrase=)[^&\s\"']+", r"\1[redacted]", message))
                if "SRT target connected" in message or "Accepted SRT source connection" in message:
                    self.connected.set()

    def collect_data(self):
        while True:
            chunk = self.process.stdout.read1(65536)
            if not chunk:
                break
            self.data.extend(chunk)

    def close(self):
        if self.process.stdin:
            try:
                self.process.stdin.close()
            except BrokenPipeError:
                pass
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        self.logger.join(timeout=2)
        if self.reader:
            self.reader.join(timeout=2)
        assert not self.logger.is_alive() and (not self.reader or not self.reader.is_alive()), "Collector did not stop"
        assert self.key not in self.log.read_text(), "Unsanitized key in diagnostic log"
        if self.stats.exists():
            assert self.key not in self.stats.read_text(), "Key in statistics output"


def stats_max(path, direction, field):
    values = []
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                record = json.loads(line)
                values.append(record[direction][field])
    assert values, f"No native SRT statistics in {path.name}"
    return max(values)


def exercise(directory, impaired):
    name = "loss-delay" if impaired else "baseline"
    key = secrets.token_hex(16)
    listener_port = base.port()
    proxy = DatagramProxy(listener_port, impaired)
    options = ("transtype=live&latency=300&conntimeo=2000&peeridletimeo=3000"
               f"&pbkeylen=32&enforcedencryption=1&passphrase={key}")
    receiver = sender = writer = None
    writer_errors = []
    payloads = []
    for n in range(PACKETS):
        header = struct.pack("!I", n)
        block = hashlib.sha256(header).digest()
        payloads.append(header + (block * ((PAYLOAD + 31) // 32))[:PAYLOAD - 4])
    expected = b"".join(payloads)
    started = time.monotonic()
    try:
        receiver = LiveTransmit(f"srt://127.0.0.1:{listener_port}?mode=listener&{options}", "file://con",
                                key, directory, f"{name}-receiver", receiver=True)
        time.sleep(0.2)
        sender = LiveTransmit("file://con", f"srt://127.0.0.1:{proxy.port}?mode=caller&{options}",
                              key, directory, f"{name}-sender")

        assert sender.connected.wait(timeout=5) and receiver.connected.wait(timeout=5), "SRT handshake did not complete"

        def feed():
            try:
                first = time.monotonic()
                for n, payload in enumerate(payloads):
                    delay = first + n * SPACING - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
                    sender.process.stdin.write(payload)
                    sender.process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                writer_errors.append("Payload writer failed")

        writer = threading.Thread(target=feed, daemon=True)
        writer.start()
        deadline = started + 25
        while len(receiver.data) < len(expected):
            assert not writer_errors, writer_errors
            assert not proxy.errors, proxy.errors
            assert sender.process.poll() is None and receiver.process.poll() is None, "SRT endpoint exited before transfer completed"
            assert time.monotonic() < deadline, f"SRT payload incomplete within 25 seconds: {len(receiver.data)}/{len(expected)} bytes, {len(proxy.seen)} unique packets"
            time.sleep(0.02)
        elapsed = time.monotonic() - started
        writer.join(timeout=1)
        assert not writer.is_alive(), "Payload writer did not finish"
        assert bytes(receiver.data) == expected, "SRT payload differs, contains duplicates or is reordered"
        assert len(proxy.seen) == PACKETS, ("Unexpected SRT packetization", len(proxy.seen))
        if impaired:
            assert len(proxy.dropped) == PACKETS // 23, "Loss injection did not run"
            assert proxy.delayed >= 50, "Delay injection did not run"
            assert proxy.recovered == proxy.dropped, "Not every deliberately dropped sequence was retransmitted"
            assert proxy.retransmission_flags >= len(proxy.dropped), "SRT retransmission flags were not observed"
        sender.close()
        receiver.close()
        assert bytes(receiver.data) == expected, "Final SRT payload differs after draining the receiver"
        sent_retransmits = stats_max(sender.stats, "send", "packetsRetransmitted")
        received_retransmits = stats_max(receiver.stats, "recv", "packetsRetransmitted")
        if impaired:
            assert sent_retransmits > 0 and received_retransmits > 0, "Native SRT counters did not confirm ARQ"
        result = {
            "case": name, "status": "PASS", "scope": "encrypted libSRT live transport; no OBE encoding",
            "bytes": len(expected), "payload_packets": PACKETS, "payload_bytes_per_packet": PAYLOAD,
            "sha256_sent": hashlib.sha256(expected).hexdigest(), "sha256_received": hashlib.sha256(receiver.data).hexdigest(),
            "elapsed_seconds": round(elapsed, 3), "latency_ms": 300,
            "dropped_datagrams": len(proxy.dropped), "recovered_dropped_sequences": len(proxy.recovered),
            "delayed_datagrams": proxy.delayed, "injected_delay_ms": 80 if impaired else 0,
            "observed_retransmission_datagrams": proxy.retransmission_flags,
            "sender_sampled_retransmissions": sent_retransmits,
            "receiver_sampled_retransmissions": received_retransmits,
            "receiver_sampled_dropped_packets": stats_max(receiver.stats, "recv", "packetsDropped"),
        }
        (directory / f"{name}-result.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result), flush=True)
        return result
    finally:
        if sender:
            sender.close()
        if receiver:
            receiver.close()
        if writer:
            writer.join(timeout=2)
        proxy.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("baseline", "loss-delay"), action="append")
    args = parser.parse_args()
    (base.ROOT / "tests/.build").mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="srt-loss-", dir=base.ROOT / "tests/.build"))
    print(f"Artifacts: {directory}", flush=True)
    results = []
    try:
        for name in args.case or ("baseline", "loss-delay"):
            results.append(exercise(directory, name == "loss-delay"))
    except Exception as error:
        # Never stringify arbitrary subprocess exceptions: their argv may carry
        # the ephemeral secret. Assertion text is controlled by this test.
        reason = str(error) if isinstance(error, AssertionError) else type(error).__name__
        (directory / "result.json").write_text(json.dumps({"status": "FAIL", "reason": reason, "results": results}, indent=2) + "\n")
        raise AssertionError(f"SRT loss test failed ({reason}); artifacts: {directory}") from None
    (directory / "result.json").write_text(json.dumps({"status": "PASS", "results": results}, indent=2) + "\n")


if __name__ == "__main__":
    main()
