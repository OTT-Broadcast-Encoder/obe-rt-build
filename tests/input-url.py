#!/usr/bin/env python3
"""Exercise the real OBE URL input over loopback, without SDI or Internet.

Run inside the existing builder image, with the repository mounted at /work:
  docker run --rm --cpus=6 --memory=8g -v "$PWD:/work" -w /work \
    obe-rt-build:current-2026-10-21 python3 tests/input-url.py

Each run retains its config, source, output and logs under tests/.build/.
No Python packages or additional system dependencies are required.
"""

import argparse
from array import array
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time


ROOT = Path(__file__).resolve().parent.parent
PREFIX = ROOT / "target-root/usr/local"
SITE = PREFIX / "lib/python3.12/site-packages"
ENV = dict(os.environ)
ENV.update(
    PATH=f"{PREFIX}/bin:{SITE}/bin:{ENV.get('PATH', '')}",
    PYTHONPATH=str(SITE),
    LD_LIBRARY_PATH=f"{PREFIX}/lib:{SITE}/vapoursynth:{ROOT}/NDI/sdk/lib/x86_64-linux-gnu",
)
CASES = {"udp-pal": ("udp", 720, 576),
         "udp-1080i50": ("udp", 1920, 1080),
         "rtp-pal": ("rtp", 720, 576),
         "udp-h264-aac": ("udp", 720, 576)}


def run(command, **kwargs):
    try:
        return subprocess.run(command, cwd=ROOT, env=ENV, check=True,
                              timeout=40, **kwargs)
    except subprocess.CalledProcessError as error:
        if error.stderr:
            print(error.stderr.decode(errors="replace") if isinstance(error.stderr, bytes)
                  else error.stderr, flush=True)
        raise


def port():
    # RTP reserves the following port for RTCP as well.
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as first:
            first.bind(("127.0.0.1", 0))
            value = first.getsockname()[1]
            if value >= 65535:
                continue
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as second:
                try:
                    second.bind(("127.0.0.1", value + 1))
                except OSError:
                    continue
                return value


def stop_sender(process):
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def stop_obe(process):
    started = time.monotonic()
    if process.poll() is None:
        process.stdin.write("quit\n")
        process.stdin.flush()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)
        raise AssertionError("OBE did not stop within 8 seconds after the sender stopped")
    assert process.returncode == 0, f"OBE exited with status {process.returncode}"
    return time.monotonic() - started


def probe(path, *options):
    result = run(["ffprobe", "-v", "error", *options, "-of", "json", str(path)],
                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert not result.stderr, result.stderr
    return json.loads(result.stdout)


def verify(path, width, height, source):
    streams = probe(path, "-show_streams")["streams"]
    video = next(s for s in streams if s["codec_type"] == "video")
    audio = next(s for s in streams if s["codec_type"] == "audio")
    assert video["codec_name"] == "h264", video
    assert (video["width"], video["height"]) == (width, height), video
    assert video["r_frame_rate"] == "25/1", video
    assert video["field_order"] in ("tt", "tb"), video
    assert video["sample_aspect_ratio"] == ("1:1" if height == 1080 else "16:15"), video
    reference = next(s for s in probe(source, "-show_streams")["streams"] if s["codec_type"] == "video")
    # MPEG-2 can leave primaries/transfer unspecified even when the generator
    # requested them. Preserve the bitstream's signalling, not the CLI intent.
    assert all(video.get(key, "unknown") == reference.get(key, "unknown")
               for key in ("color_primaries", "color_transfer", "color_space")), video
    assert audio["codec_name"] == "mp2", audio
    assert (int(audio["sample_rate"]), audio["channels"]) == (48000, 2), audio

    packets = probe(path, "-show_packets", "-show_entries",
                    "packet=stream_index,pts_time,dts_time,flags")["packets"]
    intervals = []
    for stream, step in ((video, 0.04), (audio, 0.024)):
        selected = [p for p in packets if p["stream_index"] == stream["index"]]
        assert len(selected) >= 50, (stream["codec_type"], len(selected))
        pts = [float(p["pts_time"]) for p in selected]
        dts = [float(p["dts_time"]) for p in selected]
        assert all(b > a for a, b in zip(dts, dts[1:])), "Nonmonotonic DTS"
        # This test requests bframes=0, so PTS must also advance in packet order.
        assert all(abs((b - a) - step) < 0.0001 for a, b in zip(pts, pts[1:])), (
            "Unexpected timestamp cadence", stream["codec_type"], pts[:8])
        # A live capture can end inside its final PES packet. Corruption
        # anywhere before that trailing packet group is always a failure.
        assert all("C" not in packet["flags"] or float(packet["pts_time"]) >= pts[-1] - 0.15
                   for packet in selected), ("Corrupt packet inside the stream", stream["codec_type"])
        intervals.append((pts[0], pts[-1]))
    assert abs(intervals[0][0] - intervals[1][0]) < 0.25, ("A/V start drift", intervals)
    # An abruptly stopped live TS has a video tail ahead of audio because the
    # mux schedules their different decoder buffers; it is not an EOF drain.
    # Check synchronized clocks over the common recorded interval instead.
    overlap = min(end for start, end in intervals) - max(start for start, end in intervals)
    assert overlap >= 2, ("Insufficient common A/V interval", intervals)

    frames = probe(path, "-select_streams", "v:0", "-read_intervals", "%+2",
                   "-show_frames", "-show_entries",
                   "frame=interlaced_frame,top_field_first")["frames"]
    assert len(frames) >= 25, len(frames)
    assert all(f["interlaced_frame"] and f["top_field_first"] for f in frames), frames[:3]
    # Decode the stable common interval, before the live stop truncates the
    # last PES; -xerror must still reject every decode/demux error within it.
    run(["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-t", "2",
         "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    decoded = run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0",
                   "-t", "2", "-f", "f32le", "-"],
                  stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
    samples = array("f")
    samples.frombytes(decoded)
    assert len(samples) >= 48000, len(samples)
    rms = math.sqrt(sum(value * value for value in samples) / len(samples))
    assert 0.02 < rms < 0.15, ("Missing or corrupted audio", rms)


def silent_url(directory):
    commands = (
        "set obe opts max-probe-time=2\n"
        "set input url\n"
        f"set input opts location=udp://127.0.0.1:{port()}\n"
        "probe input\nstart\nquit\n"
    )
    started = time.monotonic()
    # communicate() bounds the complete probe and shutdown, including a blocked
    # UDP read. No sender is ever started for this test.
    result = subprocess.run([str(ROOT / "run.sh")], cwd=ROOT, env=ENV,
                            input=commands, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, timeout=7)
    elapsed = time.monotonic() - started
    (directory / "silent-url.log").write_text(result.stdout)
    assert result.returncode == 0, result.stdout
    assert "URL input is not supported" not in result.stdout, result.stdout
    assert "No active devices" in result.stdout, result.stdout
    assert "Encoding started" not in result.stdout, result.stdout
    print(f"PASS silent UDP URL: probe and shutdown bounded ({elapsed:.2f}s)", flush=True)


def exercise(name, directory):
    protocol, width, height = CASES[name]
    source = directory / f"{name}-source.ts"
    output = directory / f"{name}-output.ts"
    config = directory / f"{name}.cfg"
    log = directory / f"{name}-obe.log"
    colors = "bt709" if height == 1080 else "smpte170m"
    sar = "1/1" if height == 1080 else "16/15"
    h264 = name == "udp-h264-aac"
    source_codec = (["-c:v", "libx264", "-preset", "ultrafast", "-bf", "2", "-crf", "18"]
                    if h264 else ["-c:v", "mpeg2video", "-bf", "0", "-q:v", "4"])
    # Weave distinct 50 Hz source images into genuinely interlaced TFF frames.
    run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
         f"testsrc2=size={width}x{height}:rate=50", "-f", "lavfi", "-i",
         "sine=frequency=997:sample_rate=48000", "-t", "25",
         "-vf", f"interlace=scan=tff:lowpass=0,setsar={sar}", *source_codec,
         "-threads", "2", "-flags", "+ilme+ildct",
         "-g", "12", "-pix_fmt", "yuv420p",
         "-color_primaries", colors, "-color_trc", colors, "-colorspace", colors,
         "-c:a", "aac" if h264 else "mp2", "-b:a", "192k", "-ac", "2", "-f", "mpegts", str(source)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if h264:
        source_frames = probe(source, "-select_streams", "v:0", "-show_frames",
                              "-show_entries", "frame=pict_type")["frames"]
        assert any(f["pict_type"] == "B" for f in source_frames), "H.264 fixture must contain B-frames"
    address = f"{protocol}://127.0.0.1:{port()}"
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
    sender = None
    encoder = None
    try:
        with log.open("wb") as encoder_log, (directory / f"{name}-sender.log").open("wb") as sender_log:
            encoder = subprocess.Popen(["stdbuf", "-oL", "-eL", str(ROOT / "run.sh"),
                                        "-c", str(config)], cwd=ROOT, env=ENV,
                                       stdin=subprocess.PIPE, stdout=encoder_log,
                                       stderr=subprocess.STDOUT, text=True)
            sender = subprocess.Popen(
                ["ffmpeg", "-v", "error", "-re", "-i", str(source),
                 "-map", "0:v:0", "-map", "0:a:0", "-c", "copy", "-f",
                 "rtp_mpegts" if protocol == "rtp" else "mpegts",
                 f"{address}?pkt_size=1316"], cwd=ROOT, env=ENV,
                stdin=subprocess.DEVNULL, stdout=sender_log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 25
            while not output.exists() or output.stat().st_size < 4 * 6500000 // 8:
                if encoder.poll() is not None:
                    raise AssertionError(f"OBE exited early ({encoder.returncode})")
                if sender.poll() is not None:
                    raise AssertionError(f"Sender exited early ({sender.returncode})")
                messages = log.read_text(errors="replace")
                if "URL input is not supported" in messages or "Invalid input type" in messages:
                    raise AssertionError("OBE URL input is unavailable")
                if "options: Invalid option" in messages:
                    raise AssertionError("OBE rejected a test configuration option")
                assert time.monotonic() < deadline, "No four-second TS output within 25 seconds"
                time.sleep(0.1)
            stop_sender(sender)
            # Allow the bounded network read to expire and queued live output
            # to progress before exercising quit on an already silent source.
            time.sleep(3)
            stopped = stop_obe(encoder)
        verify(output, width, height, source)
        print(f"PASS {name}: H.264/MP2, 25 fps TFF, A/V timestamps and decode; stop {stopped:.2f}s", flush=True)
    except Exception:
        print(f"FAIL {name}; artifacts: {directory}", flush=True)
        if log.exists():
            print(log.read_text(errors="replace")[-6000:], flush=True)
        raise
    finally:
        stop_sender(sender)
        if encoder and encoder.poll() is None:
            encoder.kill()
            encoder.wait(timeout=3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", choices=CASES,
                        help="Run only the selected case (repeatable)")
    args = parser.parse_args()
    (ROOT / "tests/.build").mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="input-url-", dir=ROOT / "tests/.build"))
    for name in args.case or CASES:
        exercise(name, directory)
    silent_url(directory)
    print(f"PASS URL input smoke checks; artifacts: {directory}")


if __name__ == "__main__":
    main()
