#!/usr/bin/env python3
"""Test encrypted SRT input, reconnection and bounded shutdown in the builder.

Only sanitized logs and MPEG-TS fixtures are retained in tests/.build/.
Credentials are generated per run and never written to configuration files.
"""

import argparse
import importlib.util
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time


sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("input_url_reconnect", Path(__file__).with_name("input-url-reconnect.py"))
reconnect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reconnect)
base = reconnect.base
KEYS = []


def sanitize(text):
    for key in KEYS:
        text = text.replace(key, "[redacted]")
    return re.sub(r"(?i)(passphrase=)[^&\s\"']+", r"\1[redacted]", text)


def new_key():
    key = secrets.token_hex(16)
    KEYS.append(key)
    return key


def address(port, mode):
    return (f"srt://127.0.0.1:{port}?mode={mode}&transtype=live"
            "&latency=200000&connect_timeout=1000")


class Sender:
    """Reconnect a real FFmpeg sender after OBE closes its probe connection."""

    def __init__(self, source, url, key):
        self.command = ["ffmpeg", "-v", "error", "-nostdin", "-re", "-i", str(source),
                        "-map", "0:v:0", "-map", "0:a:0", "-c", "copy", "-f", "mpegts",
                        f"{url}&pbkeylen=32&enforced_encryption=1&passphrase={key}"]
        self.finished = threading.Event()
        self.launches = 0
        self.errors = []
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        try:
            while not self.finished.is_set():
                # FFmpeg errors may contain the complete output URL. Do not
                # persist stderr or use helpers which print argv on failure.
                process = subprocess.Popen(self.command, cwd=base.ROOT, env=base.ENV,
                                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL)
                self.launches += 1
                try:
                    while process.poll() is None and not self.finished.wait(0.1):
                        pass
                finally:
                    base.stop_sender(process)
                self.finished.wait(0.2)
        except Exception as error:
            self.errors.append(sanitize(str(error)))

    def stop(self):
        self.finished.set()
        self.thread.join(timeout=5)
        assert not self.thread.is_alive(), "SRT sender supervisor did not stop"
        assert not self.errors, self.errors


class Encoder:
    def __init__(self, commands, key, log):
        self.leaked = False
        self.log = log
        # Keep the CLI script in memory. In particular, it is never necessary
        # to serialize a credential-bearing environment or FFmpeg command.
        fd = os.memfd_create("obe-srt-test")
        try:
            with os.fdopen(os.dup(fd), "w") as config:
                config.write(commands)
            os.lseek(fd, 0, os.SEEK_SET)
            env = dict(base.ENV, OBE_SRT_PASSPHRASE=key)
            self.process = subprocess.Popen(
                ["stdbuf", "-oL", "-eL", str(base.ROOT / "run.sh"), "-c", f"/proc/self/fd/{fd}"],
                cwd=base.ROOT, env=env, pass_fds=(fd,), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        finally:
            os.close(fd)
        self.thread = threading.Thread(target=self.collect, daemon=True)
        self.thread.start()

    def collect(self):
        with self.log.open("w", buffering=1) as output:
            for line in self.process.stdout:
                self.leaked |= any(key in line for key in KEYS)
                output.write(sanitize(line))

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=3)
        self.thread.join(timeout=3)
        self.process.stdout.close()
        self.process.stdin.close()
        assert not self.thread.is_alive(), "SRT log collector did not stop"
        assert not self.leaked, "OBE exposed a passphrase (redacted before writing logs)"


def commands(url, output=None, probe_only=False):
    script = ("set obe opts system-type=generic,max-probe-time=8\n"
              "set input url\n"
              f"set input opts location={url}\n"
              "probe input\n")
    if output is None:
        if probe_only:
            return script + "show input streams\nquit\n"
        return script.replace("max-probe-time=8", "max-probe-time=3") + "start\nquit\n"
    return (script +
            "set stream opts 0:format=avc,preset=ultrafast,tune=zerolatency,bitrate=5000,vbv-maxrate=5000,"
            "vbv-bufsize=5000,thread=2,bframes=0,keyint=25,rc-lookahead=0\n"
            "set stream opts 1:format=mp2,bitrate=192,channel-map=stereo\n"
            "set muxer mpegts\n"
            "set muxer opts ts-muxrate=6500000,cbr=1,ts-type=dvb\n"
            "set outputs 1\n"
            f"set output opts 0:type=filets,target={output}\n"
            "start\n")


def exercise(directory, source, mode):
    key = new_key()
    port = base.port()
    sender_url = address(port, "listener" if mode == "caller" else "caller")
    output = directory / f"{mode}.ts"
    log = directory / f"{mode}-obe.log"
    sender = encoder = None
    try:
        sender = Sender(source, sender_url, key)
        encoder = Encoder(commands(address(port, mode), output), key, log)
        reconnect.wait_for(lambda: output.exists() and output.stat().st_size >= 4 * 6500000 // 8,
                           40, encoder.process, "four seconds of encrypted SRT output")
        assert sender.launches >= 2, "Sender never reopened after probe closed its connection"
        sender.stop()
        time.sleep(7)  # Drain already buffered packets and let the read timeout expire.
        before = reconnect.read_packets(output)
        video = reconnect.stream_packets(before, "video")[0]
        audio = reconnect.stream_packets(before, "audio")[0]
        assert (video["codec_name"], video["width"], video["height"], video["sample_aspect_ratio"]) == (
            "h264", 720, 576, "16:15"), video
        assert video["field_order"] in ("tt", "tb"), video
        assert (audio["codec_name"], int(audio["sample_rate"]), audio["channels"]) == ("mp2", 48000, 2), audio
        checkpoint = output.stat().st_size
        sender = Sender(source, sender_url, key)  # Same tracks, timestamps start again.
        reconnect.wait_for(lambda: output.stat().st_size >= checkpoint + 4 * 6500000 // 8,
                           40, encoder.process, "audio/video after SRT sender restart")
        sender.stop()
        time.sleep(7)
        reconnect.verify_recovery(output, before, reconnect.read_packets(output))
        stopped = base.stop_obe(encoder.process)
        encoder.close()
        assert log.read_text().count("Encoding started") == 1, "CLI restarted or never started encoding"
        assert "URL input: reconnected;" in log.read_text(), "No recovery was reported"
        print(f"PASS encrypted SRT {mode}: sender restart, same streams and PTS reset; "
              f"quit without sender in {stopped:.2f}s", flush=True)
    finally:
        try:
            if sender:
                sender.stop()
        finally:
            if encoder:
                encoder.close()


def wrong_key(directory, source):
    port = base.port()
    log = directory / "wrong-key-obe.log"
    sender = encoder = None
    try:
        key = new_key()
        sender = Sender(source, address(port, "listener"), key)
        time.sleep(0.3)  # Allow the listener to bind before the negative probe.
        # Establish a positive control on this exact endpoint, including when
        # --case wrong-key is run alone. A dead sender or unsupported protocol
        # must never be mistaken for a rejected secret.
        control = directory / "correct-key-probe.log"
        attempts = sender.launches
        encoder = Encoder(commands(address(port, "caller"), probe_only=True), key, control)
        # Config-file quit ends config parsing; the CLI then reads stdin.
        encoder.process.stdin.write("quit\n")
        encoder.process.stdin.flush()
        try:
            encoder.process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            raise AssertionError("Correct SRT passphrase did not complete its control probe") from None
        encoder.close()
        assert encoder.process.returncode == 0, "Correct-key control probe failed"
        assert "Detected input streams:" in control.read_text(), "Correct-key control did not discover streams"
        # The probe closed its SRT connection; await a fresh listener instance.
        deadline = time.monotonic() + 6
        while sender.launches <= attempts:
            assert not sender.errors and time.monotonic() < deadline, "SRT listener did not reopen after control probe"
            time.sleep(0.1)
        time.sleep(0.3)
        started = time.monotonic()
        encoder = Encoder(commands(address(port, "caller")), new_key(), log)
        encoder.process.stdin.write("quit\n")
        encoder.process.stdin.flush()
        try:
            encoder.process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            raise AssertionError("Wrong SRT passphrase did not fail within 12 seconds") from None
        elapsed = time.monotonic() - started
        encoder.close()
        text = log.read_text()
        assert encoder.process.returncode == 0, "CLI failed after rejecting the SRT source"
        assert "No active devices" in text, "Wrong passphrase was not rejected at probe"
        assert "Encoding started" not in text, "Wrong passphrase allowed encoding"
        assert not any(marker in text.lower() for marker in (
            "supported schemes", "protocol not found", "url input is not supported",
            "crypto options are forbidden")), "SRT failed before the encrypted handshake"
        print(f"PASS wrong SRT passphrase: rejected and exited in {elapsed:.2f}s", flush=True)
    finally:
        try:
            if sender:
                sender.stop()
        finally:
            if encoder:
                encoder.close()


def preflight():
    result = base.run(["ffmpeg", "-hide_banner", "-protocols"], stdout=subprocess.PIPE,
                      stderr=subprocess.PIPE, text=True)
    inputs, outputs = result.stdout.split("Output:", 1)
    assert "srt" in inputs.split() and "srt" in outputs.split(), (
        "FFmpeg build has no SRT input/output; build with libSRT before running this test")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", choices=("caller", "listener", "wrong-key"))
    args = parser.parse_args()
    preflight()
    (base.ROOT / "tests/.build").mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="input-srt-", dir=base.ROOT / "tests/.build"))
    print(f"Sanitized artifacts: {directory}", flush=True)
    source = reconnect.make_source(directory)
    for case in args.case or ("caller", "listener", "wrong-key"):
        if case == "wrong-key":
            wrong_key(directory, source)
        else:
            exercise(directory, source, case)
    print("PASS encrypted SRT input checks (no credentials retained)", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # TimeoutExpired and CalledProcessError can include credential-bearing
        # argv. Never let an unsanitized traceback escape this test runner.
        print(f"FAIL SRT: {sanitize(str(error))}", file=sys.stderr, flush=True)
        sys.exit(1)
