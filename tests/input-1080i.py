#!/usr/bin/env python3
"""Validate decoded 1080i50 fields, chroma and audiovisual markers through OBE.

No third-party Python packages. Run in the existing Docker builder, like
input-url.py. JSON results and media/logs remain under tests/.build/.
"""

import argparse
from array import array
import importlib.util
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
import wave

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("input_url", Path(__file__).with_name("input-url.py"))
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
W, H, RATE = 1920, 1080, 48000
DURATION, ANALYSE_SECONDS = 24, 6


def fixture_frame(number, order):
    """Native 4:2:2 frame: alternating spatial rows carry consecutive instants."""
    ids = (number * 2, number * 2 + 1)
    if order == "bff":
        ids = ids[::-1]
    id_rows, marker_rows = [], []
    for identity in ids:
        row = bytearray([128]) * W
        for bit in range(12):
            row[128 + bit * 128:256 + bit * 128] = bytes([192 if identity & (1 << bit) else 64]) * 128
        id_rows.append(bytes(row))
        marker = bytearray([128]) * W
        marker[128:640] = bytes([235 if identity % 50 == 0 else 16]) * 512
        marker_rows.append(bytes(marker))
    y = bytes([16]) * (W * 64) + bytes([235]) * (W * 64)
    y += b"".join(id_rows) * 128 + b"".join(marker_rows) * 128
    y += bytes([128]) * (W * (H - 640))
    uv = []
    for plane in range(2):
        rows = [bytes([64 if (identity + plane) % 2 == 0 else 192]) * (W // 2) for identity in ids]
        uv.append(bytes([128]) * (W // 2 * 640) + b"".join(rows) * ((H - 640) // 2))
    return y + b"".join(uv)


def make_fixture(directory, order):
    audio = directory / "markers.wav"
    if not audio.exists():
        second = array("h")
        for n in range(RATE):
            envelope = min(1, n / 96, max(0, (960 - n) / 96)) if n < 960 else 0
            sample = round(0.65 * 32767 * envelope * math.sin(2 * math.pi * 997 * n / RATE))
            second.extend((sample, sample))
        with wave.open(str(audio), "wb") as wav:
            wav.setnchannels(2)
            wav.setsampwidth(2)
            wav.setframerate(RATE)
            for _ in range(DURATION):
                wav.writeframes(second.tobytes())
    source = directory / f"{order}-source.ts"
    with (directory / f"{order}-fixture.log").open("wb") as log:
        process = subprocess.Popen([
            "ffmpeg", "-v", "error", "-f", "rawvideo", "-pixel_format", "yuv422p",
            "-video_size", f"{W}x{H}", "-framerate", "25", "-i", "pipe:0", "-i", str(audio),
            "-vf", f"setfield={order},setsar=1/1,setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709", "-c:v", "mpeg2video", "-profile:v", "0",
            "-seq_disp_ext", "1", "-q:v", "2", "-g", "12", "-bf", "0", "-threads", "2", "-flags", "+ilme+ildct",
            "-field_order", "tt" if order == "tff" else "bb", "-pix_fmt", "yuv422p",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
            "-color_range", "tv", "-c:a", "mp2", "-b:a", "192k", "-ac", "2",
            "-t", str(DURATION), "-f", "mpegts", str(source)], cwd=base.ROOT, env=base.ENV,
            stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT)
        try:
            for number in range(DURATION * 25):
                process.stdin.write(fixture_frame(number, order))
            process.stdin.close()
            assert process.wait(timeout=40) == 0, "Fixture encoder failed"
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
    return source


def metadata(path, kind):
    return base.probe(path, "-select_streams", f"{kind}:0", "-read_intervals", "%+7", "-show_frames", "-show_entries",
                      "frame=best_effort_timestamp_time,interlaced_frame,top_field_first,nb_samples")["frames"]


def patch_mean(data, offset, stride, x, y, step=2):
    return statistics.mean(data[offset + (y + row * step) * stride + x + col]
                           for row in range(8) for col in range(16))


def fields_from_frame(data, pix_fmt, pts, order):
    chroma_height = H if pix_fmt == "yuv422p" else H // 2
    offsets = (W * H, W * H + W // 2 * chroma_height)
    fields = []
    for time_index, parity in enumerate((0, 1) if order == "tff" else (1, 0)):
        bits = [patch_mean(data, 0, W, 184 + bit * 128, 200 + parity) for bit in range(12)]
        assert all(abs(value - 128) > 40 for value in bits), ("Ambiguous temporal identity", bits)
        identity = sum((value > 128) << bit for bit, value in enumerate(bits))
        fields.append({
            "id": identity, "pts": pts + time_index * 0.02, "parity": parity,
            "black": patch_mean(data, 0, W, 400, 24 + parity),
            "white": patch_mean(data, 0, W, 400, 88 + parity),
            "flash": patch_mean(data, 0, W, 256, 448 + parity),
            "u": patch_mean(data, offsets[0], W // 2, 400, (800 if pix_fmt == "yuv422p" else 400) + parity),
            "v": patch_mean(data, offsets[1], W // 2, 400, (800 if pix_fmt == "yuv422p" else 400) + parity),
        })
    return fields


def validate_fields(fields, minimum=250):
    assert len(fields) >= minimum, ("Insufficient decoded fields", len(fields))
    assert fields[0]["id"] % 2 == 0, "Frame starts on the wrong temporal field"
    assert all(b["id"] == a["id"] + 1 for a, b in zip(fields, fields[1:])), "Swapped, duplicated or missing field"
    assert all(abs(b["pts"] - a["pts"] - 0.02) < 0.0001 for a, b in zip(fields, fields[1:])), "Field cadence is not 50 Hz"
    for field in fields:
        assert abs(field["black"] - 16) <= 6 and abs(field["white"] - 235) <= 6, ("Limited-range levels changed", field)
        assert abs(field["flash"] - (235 if field["id"] % 50 == 0 else 16)) <= 6, ("Video marker changed", field)
        assert abs(field["u"] - (64 if field["id"] % 2 == 0 else 192)) <= 10, ("Field chroma mixed or changed", field)
        assert abs(field["v"] - (192 if field["id"] % 2 == 0 else 64)) <= 10, ("Field chroma mixed or changed", field)


def validate_markers(fields, audio_onsets, minimum=4):
    flashes = [f["pts"] for f in fields if f["flash"] > 128]
    errors = []
    for onset in audio_onsets:
        if fields[0]["pts"] + 0.1 < onset < fields[-1]["pts"] - 0.1:
            errors.append(onset - min(flashes, key=lambda flash: abs(onset - flash)))
    assert len(errors) >= minimum, ("Insufficient audiovisual markers", errors)
    assert max(abs(error) for error in errors) <= 0.02, ("Audiovisual marker skew exceeds one field", errors)
    return errors


def expect_rejection(name, operation):
    try:
        operation()
    except AssertionError:
        return name
    raise AssertionError(f"Negative control was not detected: {name}")


def analyse(path, order, directory, source=False):
    streams = base.probe(path, "-show_streams")["streams"]
    video = next(s for s in streams if s["codec_type"] == "video")
    assert (video["width"], video["height"], video["r_frame_rate"]) == (W, H, "25/1"), video
    assert video["sample_aspect_ratio"] == "1:1", video
    assert video.get("color_range") == "tv", video
    assert all(video.get(key) == "bt709" for key in ("color_primaries", "color_transfer", "color_space")), video
    pix_fmt = "yuv422p" if source else "yuv420p"
    assert video["pix_fmt"] == pix_fmt, video
    frames = metadata(path, "v")
    expected_tff = order == "tff"
    assert all(f["interlaced_frame"] and bool(f["top_field_first"]) == expected_tff for f in frames), "Incorrect field signalling"
    frame_size = W * H * (2 if source else 3) // (1 if source else 2)
    fields = []
    first_data = None
    with (directory / f"{path.stem}-decode.log").open("wb") as log:
        process = subprocess.Popen(["ffmpeg", "-v", "error", "-xerror", "-i", str(path),
                                    "-map", "0:v:0", "-frames:v", str(ANALYSE_SECONDS * 25),
                                    "-pix_fmt", pix_fmt, "-fps_mode", "passthrough", "-f", "rawvideo", "-"],
                                   cwd=base.ROOT, env=base.ENV, stdout=subprocess.PIPE, stderr=log)
        try:
            while True:
                data = process.stdout.read(frame_size)
                if not data:
                    break
                assert len(data) == frame_size, "Truncated raw frame"
                number = len(fields) // 2
                pts = float(frames[number]["best_effort_timestamp_time"])
                fields.extend(fields_from_frame(data, pix_fmt, pts, order))
                if first_data is None:
                    first_data = data
            assert process.wait(timeout=10) == 0, "Video decode failed"
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
    validate_fields(fields)
    audio_frames = metadata(path, "a")
    pcm = base.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-map", "0:a:0",
                    "-t", str(ANALYSE_SECONDS), "-f", "s16le", "-"],
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
    samples = array("h")
    samples.frombytes(pcm)
    audio_start = float(audio_frames[0]["best_effort_timestamp_time"])
    onsets, last_active = [], -RATE
    for n, sample in enumerate(samples[::2]):
        if abs(sample) > 4915:
            if n - last_active > RATE // 3:
                onsets.append(audio_start + n / RATE)
            last_active = n
    marker_errors = validate_markers(fields, onsets)
    negatives = []
    for name, duplicate in (("swapped-fields", False), ("duplicated-field", True)):
        damaged = bytearray(first_data)
        offset = 0
        for stride, height in ((W, H), (W // 2, H if source else H // 2), (W // 2, H if source else H // 2)):
            for row in range(0, height, 2):
                a, b = offset + row * stride, offset + (row + 1) * stride
                damaged[b:b + stride] = first_data[a:a + stride]
                if not duplicate:
                    damaged[a:a + stride] = first_data[b:b + stride]
            offset += stride * height
        negatives.append(expect_rejection(name, lambda: validate_fields(
            fields_from_frame(damaged, pix_fmt, fields[0]["pts"], order), minimum=2)))
    mixed = first_data[:W * H] + bytes([128]) * (len(first_data) - W * H)
    negatives.append(expect_rejection("mixed-field-chroma", lambda: validate_fields(
        fields_from_frame(mixed, pix_fmt, fields[0]["pts"], order), minimum=2)))
    negatives.append(expect_rejection("audio-delayed-100ms", lambda: validate_markers(fields, [t + 0.1 for t in onsets])))
    with path.open("rb") as media:
        media_sha256 = hashlib.file_digest(media, "sha256").hexdigest()
    result = {"case": order, "media": path.name, "source": source, "decoded_fields": len(fields),
              "first_field_id": fields[0]["id"], "last_field_id": fields[-1]["id"],
              "av_marker_skew_ms": [round(t * 1000, 3) for t in marker_errors],
              "negative_controls": negatives,
              "signal": {key: video.get(key) for key in ("width", "height", "pix_fmt", "field_order", "sample_aspect_ratio", "color_range", "color_primaries", "color_transfer", "color_space")},
              "max_luma_error": max(abs(f[key] - target) for f in fields for key, target in (("black", 16), ("white", 235))),
              "media_sha256": media_sha256,
              "max_chroma_error": max(abs(f[k] - (64 if (f["id"] + (k == "v")) % 2 == 0 else 192)) for f in fields for k in ("u", "v"))}
    (directory / f"{path.stem}-result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def encode_url(directory, source, order):
    address = f"udp://127.0.0.1:{base.port()}"
    output = directory / f"{order}-output.ts"
    config = directory / f"{order}.cfg"
    config.write_text(
        "set obe opts system-type=generic,max-probe-time=8\nset input url\n"
        f"set input opts location={address}\nprobe input\n"
        "set stream opts 0:format=avc,preset=ultrafast,tune=zerolatency,bitrate=8000,vbv-maxrate=8000,"
        "vbv-bufsize=8000,thread=2,bframes=0,keyint=25,rc-lookahead=0\n"
        "set stream opts 1:format=mp2,bitrate=192,channel-map=stereo\n"
        "set muxer mpegts\nset muxer opts ts-muxrate=9500000,cbr=1,ts-type=dvb\nset outputs 1\n"
        f"set output opts 0:type=filets,target={output}\nstart\n")
    sender = encoder = None
    try:
        with (directory / f"{order}-obe.log").open("wb") as log, (directory / f"{order}-sender.log").open("wb") as sendlog:
            encoder = subprocess.Popen(["stdbuf", "-oL", "-eL", str(base.ROOT / "run.sh"), "-c", str(config)],
                                       cwd=base.ROOT, env=base.ENV, stdin=subprocess.PIPE,
                                       stdout=log, stderr=subprocess.STDOUT, text=True)
            sender = subprocess.Popen(["ffmpeg", "-v", "error", "-re", "-i", str(source), "-map", "0:v:0", "-map", "0:a:0",
                                       "-c", "copy", "-f", "mpegts", f"{address}?pkt_size=1316"],
                                      cwd=base.ROOT, env=base.ENV, stdin=subprocess.DEVNULL,
                                      stdout=sendlog, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 30
            while not output.exists() or output.stat().st_size < 8 * 9500000 // 8:
                assert encoder.poll() is None, "OBE exited early"
                assert sender.poll() is None, "Sender exited early"
                assert time.monotonic() < deadline, "No eight-second output within 30 seconds"
                time.sleep(0.1)
            base.stop_sender(sender)
            time.sleep(3)
            base.stop_obe(encoder)
    finally:
        base.stop_sender(sender)
        if encoder and encoder.poll() is None:
            encoder.kill()
            encoder.wait(timeout=3)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("tff", "bff"), action="append")
    parser.add_argument("--fixture-only", action="store_true", help="Validate synthetic sources without running OBE")
    parser.add_argument("--analyse-dir", type=Path, help="Reanalyse retained sources/captures without starting OBE")
    args = parser.parse_args()
    (base.ROOT / "tests/.build").mkdir(exist_ok=True)
    directory = args.analyse_dir or Path(tempfile.mkdtemp(prefix="input-1080i-", dir=base.ROOT / "tests/.build"))
    print(f"Artifacts: {directory}", flush=True)
    results = []
    try:
        for order in args.case or ("tff", "bff"):
            source = directory / f"{order}-source.ts" if args.analyse_dir else make_fixture(directory, order)
            results.append(analyse(source, order, directory, source=True))
            print(f"PASS {order} fixture: {results[-1]}", flush=True)
            if not args.fixture_only:
                output = directory / f"{order}-output.ts" if args.analyse_dir else encode_url(directory, source, order)
                results.append(analyse(output, order, directory))
                print(f"PASS {order} OBE: {results[-1]}", flush=True)
        (directory / "result.json").write_text(json.dumps({"status": "PASS", "results": results}, indent=2) + "\n")
    except Exception as error:
        (directory / "result.json").write_text(json.dumps({"status": "FAIL", "error": str(error), "results": results}, indent=2) + "\n")
        print(f"FAIL; artifacts: {directory}", flush=True)
        raise


if __name__ == "__main__":
    main()
