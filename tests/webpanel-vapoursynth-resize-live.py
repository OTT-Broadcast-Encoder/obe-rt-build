#!/usr/bin/env python3
"""Check horizontal VS resize using an isolated panel, encoder and loopback TS.

No live service or external endpoint is used. Run in the existing build image;
--encoder selects a staging binary and --artifacts retains synthetic media/logs.
"""

import argparse
from contextlib import nullcontext
import json
from math import gcd
from pathlib import Path
import runpy
import shlex
import socket
import struct
import subprocess
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
helpers = runpy.run_path(str(ROOT / "tests/webpanel-broadcast-live.py"))
ENV, free_port, PanelFixture, ffprobe = (
    helpers[key] for key in ("ENV", "free_port", "PanelFixture", "ffprobe"))
HEADER = "import vapoursynth as vs\nvs.core.num_threads = 2\nvs.core.max_cache_size = 1024\n"
IDENTITY = HEADER + "clip.set_output()\n"
RESIZE = HEADER + "clip.resize.Spline36(width=1440).set_output()\n"
LANCZOS = HEADER + "clip.resize.Lanczos(width=1280, filter_param_a=3).set_output()\n"
INVERT = HEADER + "vs.core.std.Invert(clip).resize.Spline36(width=1440).set_output()\n"
FAIL = HEADER + """
def select(n):
    if n >= 100:
        raise RuntimeError("resize-live-intentional-error")
    return clip
vs.core.std.FrameEval(clip, select).resize.Spline36(width=1440).set_output()
"""


def make_source(path, seconds=30):
    """30 continuous seconds of TFF motion, advancing 16 px per field."""
    command = ["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p",
               "-s", "1920x1080", "-r", "50", "-i", "pipe:0", "-f", "lavfi", "-i",
               f"sine=frequency=997:sample_rate=48000:duration={seconds}", "-filter_threads", "1", "-vf",
               "interlace=scan=tff:lowpass=0,setsar=1/1,setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709",
               "-c:v", "libx264", "-threads", "2",
               "-flags", "+ilme+ildct", "-preset", "ultrafast", "-crf", "10", "-bf", "0", "-g", "25",
               "-x264-params", "tff=1:force-cfr=1:scenecut=0:colorprim=bt709:transfer=bt709:colormatrix=bt709",
               "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
               "-c:a", "mp2", "-b:a", "192k", "-ac", "2", "-shortest", "-f", "mpegts", str(path)]
    with (path.parent / "source.log").open("wb") as log:
        process = subprocess.Popen(command, env=ENV, stdin=subprocess.PIPE,
                                   stdout=subprocess.DEVNULL, stderr=log)
        try:
            chroma = bytes([128]) * (1920 * 1080 // 2)
            for field in range(int(seconds * 50)):
                left = 80 + (field % 96) * 16
                # Distinct field luminance detects vertical/temporal blending;
                # the neutral chroma must remain neutral through the resize.
                background, foreground = (36, 190) if field % 2 == 0 else (72, 220)
                row = bytes([background]) * left + bytes([foreground]) * 160 + bytes([background]) * (1760 - left)
                process.stdin.write(row * 1080 + chroma)
            process.stdin.close()
            assert process.wait(timeout=20) == 0, "Generazione sorgente fallita; vedere source.log"
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
    video = next(stream for stream in ffprobe(path, "-show_streams")["streams"] if stream["codec_type"] == "video")
    assert all(video.get(key) == "bt709" for key in ("color_space", "color_transfer", "color_primaries")), video
    assert (video["r_frame_rate"], video["avg_frame_rate"], video["field_order"]) == ("25/1", "25/1", "tt"), video
    packets = ffprobe(path, "-select_streams", "v:0", "-show_packets", "-show_entries", "packet=pts")["packets"]
    timestamps = [int(packet["pts"]) for packet in packets]
    assert all(right - left == 3600 for left, right in zip(timestamps, timestamps[1:])), "PTS della sorgente non a 25 fps"


def transport_rate(path):
    """Measure CBR using byte offsets between PCRs, independent of wall clock."""
    pcrs = {}
    data = path.read_bytes()
    for offset in range(0, len(data) - 187, 188):
        packet = data[offset:offset + 188]
        if packet[3] & 0x20 and packet[4] >= 7 and packet[5] & 0x10:
            pid = ((packet[1] & 31) << 8) | packet[2]
            p = packet[6:12]
            base = (p[0] << 25) | (p[1] << 17) | (p[2] << 9) | (p[3] << 1) | (p[4] >> 7)
            ticks = base * 300 + ((p[4] & 1) << 8) + p[5]
            pcrs.setdefault(pid, []).append((offset, ticks))
    samples = max(pcrs.values(), key=len, default=[])
    assert len(samples) >= 25, "PCR insufficienti"
    first, last = samples[0], samples[-1]
    elapsed = (last[1] - first[1]) % ((1 << 33) * 300)
    assert elapsed > 0
    rate = (last[0] - first[0]) * 8 * 27000 / elapsed
    assert abs(rate - 1300) < 1, f"Muxrate fuori budget: {rate:.3f} kb/s"
    return round(rate, 3)


def complete_pes_capture(path):
    """Keep the raw capture, exclude only each ES's final PES for strict decode.

    UDP recording can stop inside a PES, especially when testing a hard exit.
    No remux or repair is performed; all retained packets remain byte-for-byte.
    Continuity and muxrate are checked separately against the original capture.
    """
    data, final_pes = path.read_bytes(), {}
    for offset in range(0, len(data) - 187, 188):
        packet = data[offset:offset + 188]
        if not packet[1] & 0x40 or not packet[3] & 0x10:
            continue
        cursor = 4 + (packet[4] + 1 if packet[3] & 0x20 else 0)
        if packet[cursor:cursor + 3] == b"\x00\x00\x01":
            final_pes[((packet[1] & 31) << 8) | packet[2]] = offset
    assert len(final_pes) >= 2, "PES audio/video insufficienti"
    result = path.with_name("complete-pes.ts")
    with result.open("wb") as output:
        for offset in range(0, len(data) - 187, 188):
            packet = data[offset:offset + 188]
            pid = ((packet[1] & 31) << 8) | packet[2]
            if pid not in final_pes or offset < final_pes[pid]:
                output.write(packet)
    return result


def check_media(path, width, inverted=False, motion=True, expected_metadata=None):
    raw_capture, path = path, complete_pes_capture(path)
    streams = ffprobe(path, "-show_streams")["streams"]
    video = next(stream for stream in streams if stream["codec_type"] == "video")
    audio = next(stream for stream in streams if stream["codec_type"] == "audio")
    assert (video["codec_name"], video["width"], video["height"]) == ("h264", width, 1080)
    divisor = gcd(1920, width)
    expected = {"sample_aspect_ratio": f"{1920 // divisor}:{width // divisor}",
                "display_aspect_ratio": "16:9", "field_order": "tt", "r_frame_rate": "25/1",
                "pix_fmt": "yuv420p", "profile": "High", "level": 41,
                "color_space": "bt709", "color_transfer": "bt709", "color_primaries": "bt709"}
    expected.update(expected_metadata or {})
    assert all(video.get(key) == value for key, value in expected.items()), {"expected": expected, "actual": video}
    assert (audio["codec_name"], audio.get("profile"), audio["channels"], audio["sample_rate"]) == (
        "aac", "LC", 2, "48000"), audio
    frames = ffprobe(path, "-select_streams", "v:0", "-show_frames", "-show_entries",
                     "frame=width,height,interlaced_frame,top_field_first")["frames"]
    assert len(frames) >= 20, f"Solo {len(frames)} frame in uscita"
    interlaced, tff = int(expected["field_order"] != "progressive"), int(expected["field_order"] == "tt")
    assert all((f["width"], f["height"], f["interlaced_frame"], f["top_field_first"])
               == (width, 1080, interlaced, tff) for f in frames), "Geometria o ordine campi cambiati"
    helpers["check_transport_tables"](raw_capture, 107)
    decode = subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-map", "0:v:0",
                             "-map", "0:a:0", "-f", "null", "-"], env=ENV,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=20)
    assert decode.returncode == 0 and not decode.stderr, decode.stderr.decode(errors="replace")
    if motion:
        # Each output row represents a distinct temporal field. Geometry alone
        # cannot detect accidental blending, repeated fields or swapped TFF/BFF.
        fields = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-an", "-vf",
                                 "separatefields,crop=iw:2:0:128,extractplanes=y",
                                 "-frames:v", "80", "-f", "rawvideo", "pipe:1"],
                                env=ENV, capture_output=True, check=True, timeout=20).stdout
        assert len(fields) == width * 2 * 80, "Mancano 80 semiquadri decodificati"
        positions, backgrounds = [], []
        for offset in range(0, len(fields), width * 2):
            row = fields[offset:offset + width]
            bar = [x for x, value in enumerate(row) if (value < 127 if inverted else value > 127)]
            assert abs(len(bar) - width // 12) <= 4, "Filtro custom o larghezza barra non corretti"
            positions.append(sum(bar) / len(bar))
            backgrounds.append(sum(row[:24]) / 24)
        period, step = 1536 * width / 1920, 16 * width / 1920
        deltas = [(right - left) % period for left, right in zip(positions, positions[1:])]
        assert all(abs(delta - step) < 2 for delta in deltas), f"Movimento campi alterato: {deltas}"
        expected = (219, 183) if inverted else (36, 72)
        assert all(abs(value - expected[index % 2]) < 10 for index, value in enumerate(backgrounds)), (
            f"Luminanza dei campi mescolata o ordine invertito: {backgrounds}")
        chroma = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-an", "-vf",
                                 "extractplanes=u", "-frames:v", "1", "-f", "rawvideo", "pipe:1"],
                                env=ENV, capture_output=True, check=True, timeout=20).stdout
        assert chroma and abs(sum(chroma) / len(chroma) - 128) <= 2, "Crominanza neutra alterata"
    return {"width": width, "sar": video["sample_aspect_ratio"], "field_order": video["field_order"],
            "frames": len(frames), "mux_kbps": transport_rate(raw_capture), "motion_fields": 80 if motion else None,
            **({"video_metadata": {key: video.get(key) for key in expected}} if expected_metadata is not None else {})}


def exercise(panel, directory, source_file, name, width, script=IDENTITY, enabled=True, fail=False,
             expected_metadata=None, motion=True):
    folder = directory / name
    folder.mkdir()
    recording = folder / "output.ts"
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
    receiver.setsockopt(socket.SOL_SOCKET, 40, 1)  # Linux SO_RXQ_OVFL
    receiver.bind(("127.0.0.1", 0))
    receiver.settimeout(0.1)
    stop, captured = threading.Event(), {"bytes": 0, "drops": 0}

    def capture():
        with recording.open("wb") as output:
            while not stop.is_set():
                try:
                    packet, ancillary, _, _ = receiver.recvmsg(65535, socket.CMSG_SPACE(4))
                    for level, kind, value in ancillary:
                        if level == socket.SOL_SOCKET and kind == 40:
                            captured["drops"] = struct.unpack("I", value)[0]
                    output.write(packet)
                    captured["bytes"] += len(packet)
                except socket.timeout:
                    pass

    reader = threading.Thread(target=capture, daemon=True)
    reader.start()
    sender, sender_log = None, (folder / "sender.log").open("wb")
    source = f"udp://127.0.0.1:{free_port()}"
    state = panel.request("/api/state")
    signal_before = state.get("signal", {})
    last_event = max((item["id"] for item in state["events"]), default=0)
    config = {**state["config"], "input_type": "url", "input_url": source,
              "output_url": f"udp://127.0.0.1:{receiver.getsockname()[1]}", "outputs": [],
              "preset": "ultrafast", "x264_speedcontrol": "fixed", "threads": 2,
              "video_bitrate_kbps": 1044, "vbv_maxrate_kbps": 1044, "vbv_buffer_kbits": 1044,
              "mux_bitrate_kbps": 1300, "video_profile": "high", "video_level": "4.1",
              "entropy_coding": "cabac", "transform_8x8": "1", "weighted_pred": "0",
              "field_order": "tff", "aspect_ratio": "source", "gop_frames": 25,
              "bframes": 0, "lookahead_frames": 0, "audio_codec": "aac", "audio_bitrate_kbps": 128,
              "ts_id": 107, "auto_restart": False, "resume_on_restart": False,
              "vapoursynth_enabled": enabled, "vapoursynth_script": script}
    config.pop("vapoursynth_width", None)
    config.pop("vapoursynth_resize_kernel", None)
    try:
        state = panel.request("/api/config", {"revision": state["revision"], "config": config}, "PUT")
        panel.request("/api/start", {"revision": state["revision"]}, "POST")
        sender = subprocess.Popen(["ffmpeg", "-v", "error", "-nostdin", "-re",
                                   "-i", str(source_file), "-map", "0", "-c", "copy", "-f", "mpegts",
                                   source + "?pkt_size=1316"], env=ENV, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=sender_log)
        panel.wait(lambda value: value["encoding_started"] and value["phase"] == "running")
        deadline = time.monotonic() + 18
        while True:
            state = panel.request("/api/state")
            if fail and not state["process_alive"]:
                break
            if not fail:
                assert state["phase"] == "running", state["message"]
                if captured["bytes"] >= 1300 * 1000 // 8 * 5:
                    break
            assert sender.poll() is None, "Generatore sorgente terminato"
            assert time.monotonic() < deadline, "Timeout attesa arresto per errore" if fail else "Output insufficiente"
            time.sleep(0.15)
        events = "\n".join(item["message"] for item in state["events"] if item["id"] > last_event)
        (folder / "events.log").write_text(events)
        if width:
            assert f"VapourSynth resize: 1920x1080 -> {width}x1080" in events, "Manca conferma del resize VS"
        else:
            assert state["vapoursynth"]["status"] == "disabled"
        if fail:
            assert "resize-live-intentional-error" in events, "Non è stato esercitato l'errore runtime"
            assert not state["process_alive"], "Il resize obbligatorio non si è fermato dopo l'errore"
        else:
            assert "VBV underflow" not in events
    finally:
        try:
            latest = panel.request("/api/state")
            signal_after = latest.get("signal", {})
            if expected_metadata is not None:
                (folder / "signal.json").write_text(json.dumps({"before": signal_before, "after": signal_after}, indent=2))
            (folder / "events.log").write_text("\n".join(
                item["message"] for item in latest["events"] if item["id"] > last_event))
            panel.request("/api/stop", {}, "POST")
        finally:
            if sender is not None and sender.poll() is None:
                sender.terminate()
                try:
                    sender.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    sender.kill()
                    sender.wait(timeout=3)
            sender_log.close()
            stop.set()
            reader.join(timeout=2)
            receiver.close()
    assert captured["drops"] == 0, f"Perdite nel socket di cattura: {captured['drops']}"
    result = check_media(recording, width or 1920, inverted=script == INVERT, motion=motion and not fail,
                         expected_metadata=expected_metadata)
    return {"case": name, **result, "fail_closed": fail,
            **({"signal_before": signal_before, "signal_after": signal_after} if expected_metadata is not None else {})}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--encoder", type=Path, default=ROOT / "obe-rt/obe/obecli")
    args = parser.parse_args()
    context = nullcontext(str(args.artifacts.resolve())) if args.artifacts else tempfile.TemporaryDirectory(prefix="obe-vs-resize-")
    with context as folder_name:
        directory = Path(folder_name)
        directory.mkdir(parents=True, exist_ok=True)
        engine = directory / "engine"
        engine.mkdir()
        (engine / "run.sh").write_text("#!/bin/sh\nexec " + shlex.quote(str(args.encoder.resolve())) + "\n")
        (engine / "run.sh").chmod(0o700)
        source = directory / "source.ts"
        make_source(source)
        panel, results = PanelFixture(directory, encoder_root=engine), []
        try:
            for case in (("standalone", 1440, RESIZE, True, False),
                         ("custom", 1440, INVERT, True, False),
                         ("lanczos-1280", 1280, LANCZOS, True, False),
                         ("disabled", 0, IDENTITY, False, False),
                         ("fail-closed", 1440, FAIL, True, True)):
                result = exercise(panel, directory, source, *case)
                results.append(result)
                print("PASS " + json.dumps(result), flush=True)
        finally:
            (directory / "results.json").write_text(json.dumps(results, indent=2))
            panel.close()


if __name__ == "__main__":
    main()
