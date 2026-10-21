#!/usr/bin/env python3
"""Exercise URL recovery in the real encoder after a UDP source disappears.

Run in the existing builder image just like tests/input-url.py. Every run keeps
source/output media, configuration and process logs in tests/.build/.
"""

import argparse
from array import array
import importlib.util
import json
import math
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time


sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("input_url", Path(__file__).with_name("input-url.py"))
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


def wait_for(predicate, timeout, encoder, description):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert encoder.poll() is None, f"OBE exited while waiting for {description}"
        assert time.monotonic() < deadline, f"Timed out waiting for {description}"
        time.sleep(0.1)


def read_packets(path):
    # Live MPEG-TS captures can end within a PES. Retain diagnostics without
    # rejecting that expected tail; timestamp checks below cover the interior.
    result = base.run(["ffprobe", "-v", "error", "-show_streams", "-show_packets",
                       "-show_entries", "stream=index,id,codec_type,codec_name,width,height,"
                       "sample_rate,channels,field_order,sample_aspect_ratio:"
                       "packet=stream_index,pts_time,dts_time,flags", "-of", "json", str(path)],
                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    path.with_suffix(path.suffix + ".probe.log").write_text(result.stderr)
    return json.loads(result.stdout)


def stream_packets(data, kind):
    stream = next(s for s in data["streams"] if s["codec_type"] == kind)
    packets = [p for p in data["packets"] if p["stream_index"] == stream["index"]]
    return stream, packets


def check_resume(before, after, continuous=True):
    resumed = []
    clock_errors = []
    gap_ends = []
    for kind, minimum, step in (("video", 60, 0.04), ("audio", 100, 0.024)):
        old_stream, old_packets = stream_packets(before, kind)
        stream, packets = stream_packets(after, kind)
        assert stream == old_stream, ("Output stream identity/format changed", old_stream, stream)
        extra = packets[len(old_packets):]
        assert len(extra) >= minimum, ("Input did not resume", kind, len(extra))
        for field in ("pts_time", "dts_time"):
            values = [float(p[field]) for p in packets]
            assert all(b > a for a, b in zip(values, values[1:])), ("Timestamp went backwards", kind, field)
            if continuous:
                assert all(abs(b - a - step) < 0.0001 for a, b in zip(values, values[1:])), (
                    "Timestamp gap across reconnection", kind, field)
            elif field == "pts_time":
                gap_ends.extend(b for a, b in zip(values, values[1:]) if b - a > step + 0.0001)
        clock_errors.append(values[-1] - values[0] - (len(values) - 1) * step)
        assert all("C" not in p["flags"] or float(p["pts_time"]) >= float(packets[-1]["pts_time"]) - 0.15
                   for p in packets), ("Corrupt interior packet after recovery", kind)
        resumed.append((float(extra[0]["pts_time"]), float(extra[-1]["pts_time"])))
    resumed_pts = max([start for start, end in resumed] + gap_ends)
    assert min(end for start, end in resumed) - resumed_pts >= 2, (
        "Resumed audio/video have no common two-second interval", resumed, gap_ends)
    if continuous:
        assert abs(clock_errors[0] - clock_errors[1]) < 0.001, (
            "Cumulative audio/video clock drift", clock_errors)
    audio_packets = stream_packets(after, "audio")[1]
    video_packets = stream_packets(after, "video")[1]
    boundary = resumed[0][0] if continuous else next(
        float(p["pts_time"]) for p in video_packets if float(p["pts_time"]) >= resumed_pts)
    boundary_skew = min(abs(float(p["pts_time"]) - boundary) for p in audio_packets)
    assert boundary_skew <= 0.0241, ("Audio/video gap after reconnection", boundary_skew)
    return resumed_pts, boundary_skew


def verify_recovery(output, before, after, continuous=True):
    resumed_pts, boundary_skew = check_resume(before, after, continuous)
    # Seek into the resumed portion, allowing an IDR before strict decoding.
    first_pts = min(float(p["pts_time"]) for p in after["packets"])
    seek = str(resumed_pts - first_pts + 1)
    base.run(["ffmpeg", "-v", "error", "-xerror", "-ss", seek,
              "-i", str(output), "-t", "1", "-map", "0:v:0", "-map", "0:a:0",
              "-f", "null", "-"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    decoded = base.run(["ffmpeg", "-v", "error", "-ss", seek,
                        "-i", str(output), "-t", "1", "-map", "0:a:0", "-f", "f32le", "-"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
    samples = array("f")
    samples.frombytes(decoded)
    assert len(samples) >= 48000, "No full second of resumed audio"
    rms = math.sqrt(sum(value * value for value in samples) / len(samples))
    assert 0.02 < rms < 0.15, ("Silent or corrupted resumed audio", rms)
    print(f"PASS UDP recovery: A/V decode and RMS, same streams, "
          f"{'continuous' if continuous else 'monotonic'} PTS/DTS; "
          f"boundary packet skew {boundary_skew * 1000:.2f}ms", flush=True)



class TrackLossProxy:
    """Drop one MPEG-TS PID while PAT/PMT and the other track keep arriving."""

    def __init__(self, destination):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.settimeout(0.1)
        self.address = f"udp://127.0.0.1:{self.socket.getsockname()[1]}"
        self.destination = ("127.0.0.1", int(destination.rsplit(":", 1)[1]))
        self.drop_pid = None
        self.dropped = self.forwarded_during_loss = 0
        self.errors = []
        self.finished = threading.Event()
        self.thread = threading.Thread(target=self.forward, daemon=True)
        self.thread.start()

    def forward(self):
        try:
            while not self.finished.is_set():
                try:
                    data = self.socket.recv(65536)
                except socket.timeout:
                    continue
                assert len(data) % 188 == 0, "Misaligned MPEG-TS datagram"
                kept = []
                for offset in range(0, len(data), 188):
                    packet = data[offset:offset + 188]
                    assert packet[0] == 0x47, "Missing MPEG-TS sync byte"
                    pid = ((packet[1] & 31) << 8) | packet[2]
                    if pid == self.drop_pid:
                        self.dropped += 1
                    else:
                        kept.append(packet)
                        if self.drop_pid is not None:
                            self.forwarded_during_loss += 1
                if kept:
                    self.socket.sendto(b"".join(kept), self.destination)
        except Exception as error:
            self.errors.append(error)

    def close(self):
        self.finished.set()
        self.thread.join(timeout=2)
        self.socket.close()
        assert not self.thread.is_alive(), "UDP proxy did not stop"
        assert not self.errors, self.errors


def make_source(directory):
    source = directory / "source.ts"
    base.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=720x576:rate=50",
              "-f", "lavfi", "-i", "sine=frequency=997:sample_rate=48000", "-t", "30",
              "-vf", "interlace=scan=tff:lowpass=0,setsar=16/15", "-c:v", "mpeg2video",
              "-threads", "2", "-flags", "+ilme+ildct", "-g", "12", "-bf", "0", "-q:v", "4",
              "-pix_fmt", "yuv420p", "-c:a", "mp2", "-b:a", "192k", "-ac", "2",
              "-f", "mpegts", str(source)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return source


def exercise(directory, source, shutdown):
    address = f"udp://127.0.0.1:{base.port()}"
    output = directory / f"{shutdown}.ts"
    config = directory / f"{shutdown}.cfg"
    log = directory / f"{shutdown}-obe.log"
    config.write_text(
        "set obe opts system-type=generic,max-probe-time=8\n"
        "set input url\n"
        f"set input opts location={address}\n"
        "probe input\n"
        "set stream opts 0:format=avc,preset=ultrafast,tune=zerolatency,bitrate=5000,vbv-maxrate=5000,"
        "vbv-bufsize=5000,thread=2,bframes=0,keyint=25,rc-lookahead=0\n"
        "set stream opts 1:format=mp2,bitrate=192,channel-map=stereo\n"
        "set muxer mpegts\n"
        "set muxer opts ts-muxrate=6500000,cbr=1,ts-type=dvb\n"
        "set outputs 1\n"
        f"set output opts 0:type=filets,target={output}\n"
        "start\n"
    )
    sender = encoder = proxy = None
    track_loss = shutdown in ("track-loss", "video-loss")
    try:
        if track_loss:
            proxy = TrackLossProxy(address)
        with log.open("wb") as encoder_log, (directory / f"{shutdown}-sender.log").open("wb") as sender_log:
            def start_sender():
                return subprocess.Popen(["ffmpeg", "-v", "error", "-re", "-i", str(source),
                                         "-map", "0:v:0", "-map", "0:a:0", "-c", "copy",
                                         "-f", "mpegts", f"{proxy.address if proxy else address}?pkt_size=1316"],
                                        cwd=base.ROOT, env=base.ENV, stdin=subprocess.DEVNULL,
                                        stdout=sender_log, stderr=subprocess.STDOUT)

            encoder = subprocess.Popen(["stdbuf", "-oL", "-eL", str(base.ROOT / "run.sh"), "-c", str(config)],
                                       cwd=base.ROOT, env=base.ENV, stdin=subprocess.PIPE,
                                       stdout=encoder_log, stderr=subprocess.STDOUT, text=True)
            sender = start_sender()
            wait_for(lambda: output.exists() and output.stat().st_size >= 4 * 6500000 // 8,
                     25, encoder, "four seconds of initial output")
            if track_loss:
                before = read_packets(output)
                proxy.drop_pid = 257 if shutdown == "track-loss" else 256
                time.sleep(3.2)
                proxy.drop_pid = None
                assert proxy.dropped and proxy.forwarded_during_loss, "Single-track loss was not injected"
                wait_for(lambda: "URL input: reconnected;" in log.read_text(errors="replace"),
                         15, encoder, "reconnection after single-track loss")
                checkpoint = output.stat().st_size
                wait_for(lambda: output.stat().st_size >= checkpoint + 6 * 6500000 // 8,
                         20, encoder, "six seconds of recovered single-track output")
                base.stop_sender(sender)
                time.sleep(7)
                verify_recovery(output, before, read_packets(output), continuous=False)
                print(f"PASS {shutdown}: dropped {proxy.dropped} TS packets while the other track continued", flush=True)
            else:
                base.stop_sender(sender)
            # The input can still pace packets buffered by libavformat before
            # its deadline fires. Recovery cases let buffers and timeout elapse;
            # the silent case exercises cancellation before the first retry.
            if not track_loss:
                time.sleep(0.3 if shutdown == "silent" else 7)
            if shutdown in ("resume", "repeat"):
                before = read_packets(output)
                for attempt in range(3 if shutdown == "repeat" else 1):
                    checkpoint = output.stat().st_size
                    sender = start_sender()  # Same PIDs/format, restarted timestamps.
                    wait_for(lambda: output.stat().st_size >= checkpoint + 4 * 6500000 // 8,
                             25, encoder, f"audio/video output after source restart {attempt + 1}")
                    base.stop_sender(sender)
                    time.sleep(7)
                    after = read_packets(output)
                    verify_recovery(output, before, after)
                    before = after
            elif shutdown == "backoff":
                # Confirm at least a second retry before quitting in a retry
                # delay. Also fail the old implementation, which never retries.
                def retries():
                    return sum("URL input:" in line and
                               ("retry" in line.lower() or "reconnect" in line.lower())
                               for line in log.read_text(errors="replace").splitlines())
                previous_attempts = retries()
                wait_for(lambda: retries() >= max(2, previous_attempts + 1),
                         20, encoder, "a new retry delay")
            stopped = base.stop_obe(encoder)
        assert log.read_text(errors="replace").count("Encoding started") == 1, "CLI restarted or never started encoding"
        print(f"PASS {shutdown}: shutdown with sender absent in {stopped:.2f}s", flush=True)
    except Exception:
        print(f"FAIL {shutdown}; artifacts: {directory}", flush=True)
        if log.exists():
            print(log.read_text(errors="replace")[-8000:], flush=True)
        raise
    finally:
        base.stop_sender(sender)
        if encoder and encoder.poll() is None:
            encoder.kill()
            encoder.wait(timeout=3)
        if proxy:
            proxy.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", choices=("resume", "repeat", "silent", "backoff", "track-loss", "video-loss"))
    args = parser.parse_args()
    (base.ROOT / "tests/.build").mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="url-reconnect-", dir=base.ROOT / "tests/.build"))
    print(f"Artifacts: {directory}", flush=True)
    source = make_source(directory)
    for shutdown in args.case or ("repeat", "silent", "backoff", "track-loss", "video-loss"):
        exercise(directory, source, shutdown)
    print("PASS URL reconnection checks", flush=True)


if __name__ == "__main__":
    main()
