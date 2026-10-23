#!/usr/bin/env python3
"""Loopback-only video filtering test; run in the existing build container."""

from pathlib import Path
import argparse
from contextlib import nullcontext
import json
import runpy
import shlex
import socket
import subprocess
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
helpers = runpy.run_path(str(ROOT / "tests/webpanel-broadcast-live.py"))
ENV, free_port, PanelFixture = (helpers[key] for key in ("ENV", "free_port", "PanelFixture"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--video-format", choices=("pal", "1080i"), default="pal")
    args = parser.parse_args()
    width, height = (1920, 1080) if args.video_format == "1080i" else (720, 576)
    context = nullcontext(str(args.artifacts.resolve())) if args.artifacts else tempfile.TemporaryDirectory(prefix="obe-vs-live-")
    with context as folder_name:
        folder = Path(folder_name)
        folder.mkdir(parents=True, exist_ok=True)
        root = folder / "encoder"
        root.mkdir(exist_ok=True)
        # Test the newly built binary before installing it in the active panel.
        (root / "run.sh").write_text("#!/bin/sh\nexec " + shlex.quote(str(ROOT / "obe-rt/obe/obecli")) + '\n')
        (root / "run.sh").chmod(0o700)
        panel = PanelFixture(folder, encoder_root=root)
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(0.1)
        source = f"udp://127.0.0.1:{free_port()}"
        stop = threading.Event()
        packets = []

        def capture():
            while not stop.is_set():
                try:
                    packets.append(receiver.recv(65536))
                except socket.timeout:
                    pass

        reader = threading.Thread(target=capture)
        reader.start()
        sender = None
        try:
            state = panel.request("/api/state")
            config = {**state["config"], "input_url": source,
                      "output_url": f"udp://127.0.0.1:{receiver.getsockname()[1]}",
                      "preset": "ultrafast", "threads": 2, "bframes": 0, "lookahead_frames": 0,
                      "gop_frames": 25, "vapoursynth_enabled": True,
                      "vapoursynth_script": "import vapoursynth as vs\n\nif clip.width > 0:\n    clip = vs.core.std.Invert(clip)\n\nclip.set_output()\n"}
            state = panel.request("/api/config", {"revision": state["revision"], "config": config}, "PUT")
            panel.request("/api/start", {"revision": state["revision"]}, "POST")
            sender = subprocess.Popen(
                ["ffmpeg", "-v", "error", "-nostdin", "-re", "-f", "lavfi", "-i", f"color=black:size={width}x{height}:rate=50",
                 "-f", "lavfi", "-i", "sine=frequency=997:sample_rate=48000", "-filter_threads", "1",
                 "-vf", "interlace=scan=tff:lowpass=0", "-c:v", "mpeg2video", "-threads", "2",
                 "-flags", "+ilme+ildct", "-q:v", "4", "-bf", "0", "-g", "12",
                 "-c:a", "mp2", "-b:a", "192k", "-ac", "2", "-muxrate", "12000000",
                 "-f", "mpegts", source + "?pkt_size=1316"],
                env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            panel.wait(lambda state: state["phase"] == "running" and state["vapoursynth"]["status"] == "active")
            panel.wait(lambda state: (state["telemetry"].get("video_frames") or 0) >= 150
                       and (state["telemetry"].get("output_bytes") or 0) >= 2000000)
            recording = folder / "filtered.ts"
            recording.write_bytes(b"".join(packets))
            assert recording.stat().st_size > 500000, "Missing MPEG-TS output"
            result = subprocess.run(
                ["ffmpeg", "-v", "error", "-i", str(recording), "-an", "-vf", "scale=32:32",
                 "-frames:v", "10", "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"],
                env=ENV, capture_output=True, check=True, timeout=20)
            assert len(result.stdout) == 10240, f"Missing decoded frames: {len(result.stdout)} bytes; {result.stderr.decode()}"
            assert sum(result.stdout) / len(result.stdout) > 220, "VapourSynth did not invert the black source"
            probe = helpers["ffprobe"](recording, "-show_streams")
            video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
            assert (video["width"], video["height"], video["avg_frame_rate"], video["field_order"]) == (width, height, "25/1", "tt")
            assert any(stream["codec_type"] == "audio" for stream in probe["streams"])

            def apply(script, enabled=True):
                current = panel.request("/api/state")
                return panel.request("/api/apply", {"revision": current["revision"], "instance_id": current["instance_id"],
                    "config": {**current["config"], "vapoursynth_script": script, "vapoursynth_enabled": enabled}}, "POST")

            apply("# No output\npass\n")
            panel.wait(lambda state: state["phase"] == "running" and state["vapoursynth"]["status"] == "error")
            apply(config["vapoursynth_script"], enabled=False)
            panel.wait(lambda state: state["phase"] == "running" and state["vapoursynth"]["status"] == "disabled")
            print(f"PASS real OBE: multiline filtering, decoded output, {args.video_format} TFF/audio, apply/restart, missing-output error and disable")
        except BaseException:
            (folder / "failure.json").write_text(json.dumps(panel.request("/api/state"), indent=2))
            raise
        finally:
            panel.close()
            if sender:
                sender.terminate()
                try:
                    sender.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    sender.kill()
                    sender.communicate(timeout=3)
            stop.set()
            reader.join(timeout=3)
            receiver.close()


if __name__ == "__main__":
    main()
