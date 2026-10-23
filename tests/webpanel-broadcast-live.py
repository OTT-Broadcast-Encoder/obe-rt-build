#!/usr/bin/env python3
"""Exercise broadcast settings against an isolated panel and real OBE encoder.

Run inside the existing builder image with this staging checkout at /work and
the original build tree mounted read-only at its absolute path for symlinks.
All endpoints are loopback, profiles are temporary and no existing panel is used.
No packages are installed. Logs and media are retained only with --artifacts.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
PREFIX = ROOT / "target-root/usr/local"
SITE = PREFIX / "lib/python3.12/site-packages"
ENV = {**os.environ, "PATH": f"{PREFIX}/bin:{SITE}/bin:" + os.environ.get("PATH", ""),
       "PYTHONPATH": str(SITE), "PYTHONDONTWRITEBYTECODE": "1",
       "LD_LIBRARY_PATH": f"{PREFIX}/lib:{SITE}/vapoursynth:{ROOT}/NDI/sdk/lib/x86_64-linux-gnu"}
CASES = {
    "pal-mp2-tff": (720, 576, "mp2", "tff"),
    "pal-aac-bff": (720, 576, "aac", "bff"),
    "pal-ac3-tff": (720, 576, "ac3", "tff"),
    "pal-eac3-bff": (720, 576, "e-ac3", "bff"),
    "1080i-mp2-tff": (1920, 1080, "mp2", "tff"),
    "1080i-aac-bff": (1920, 1080, "aac", "bff"),
    "1080i-ac3-bff": (1920, 1080, "ac3", "bff"),
    "1080i-eac3-tff": (1920, 1080, "e-ac3", "tff"),
}


def free_port(kind=socket.SOCK_DGRAM):
    with socket.socket(socket.AF_INET, kind) as reservation:
        reservation.bind(("127.0.0.1", 0))
        return reservation.getsockname()[1]


class PanelFixture:
    def __init__(self, folder, encoder_root=None):
        self.url = f"http://127.0.0.1:{free_port(socket.SOCK_STREAM)}"
        self.log = (folder / "panel.log").open("wb")
        self.process = subprocess.Popen(
            [sys.executable, "-u", str(ROOT / "webpanel/server.py"), "--root", str(encoder_root or ROOT),
             "--state-dir", str(folder / "state"), "--port", self.url.rsplit(":", 1)[1]],
            env=ENV, cwd=ROOT, stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise AssertionError("Il server isolato non è partito; controllare panel.log")
                try:
                    self.request("/api/state")
                    return
                except (URLError, ConnectionError):
                    time.sleep(0.1)
            raise AssertionError("Avvio del server isolato scaduto")
        except BaseException:
            if self.process.poll() is None:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=3)
            self.log.close()
            raise

    def request(self, path, body=None, method=None):
        data = None if body is None else json.dumps(body).encode()
        request = Request(self.url + path, data=data, method=method,
                          headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=20) as response:
                return json.load(response)
        except HTTPError as error:
            # All profiles and endpoints in this fixture are generated locally.
            raise AssertionError(f"HTTP {error.code}: {error.read().decode()}") from None

    def wait(self, predicate, timeout=25):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.request("/api/state")
            if predicate(state):
                return state
            if state["phase"] == "error":
                events = [item["message"] for item in state.get("events", [])[-8:]]
                raise AssertionError(f"Encoder: {state['message']}; {events}")
            time.sleep(0.15)
        raise AssertionError(f"Timeout encoder: {state['phase']}; {state['message']}")

    def close(self):
        try:
            if self.process.poll() is None:
                self.request("/api/stop", {}, "POST")
        finally:
            if self.process.poll() is None:
                os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(timeout=3)
            self.log.close()


def ffprobe(path, *args):
    result = subprocess.run(["ffprobe", "-v", "error", *args, "-of", "json", str(path)],
                            env=ENV, capture_output=True, text=True, timeout=20, check=True)
    if result.stderr:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


def check_transport_tables(path, expected_ts_id):
    data = path.read_bytes()
    found, continuity = False, {}
    for offset in range(0, len(data) - 187, 188):
        packet = data[offset:offset + 188]
        if packet[0] != 0x47:
            raise AssertionError("TS sync byte non valido")
        pid = ((packet[1] & 31) << 8) | packet[2]
        if pid != 8191 and packet[3] & 0x10:
            counter = packet[3] & 15
            discontinuity = packet[3] & 0x20 and packet[4] and packet[5] & 0x80
            if pid in continuity and not discontinuity and counter != (continuity[pid] + 1) % 16:
                raise AssertionError(f"Discontinuità TS al byte {offset}, PID {pid}")
            continuity[pid] = counter
        if pid or not packet[1] & 0x40 or not packet[3] & 0x10:
            continue
        cursor = 4
        if packet[3] & 0x20:
            cursor += 1 + packet[4]
        if cursor >= 188:
            continue
        cursor += 1 + packet[cursor]
        if cursor + 5 >= 188 or packet[cursor] != 0:
            continue
        ts_id = (packet[cursor + 3] << 8) | packet[cursor + 4]
        if ts_id != expected_ts_id:
            raise AssertionError(f"Transport stream ID errato: {ts_id}")
        found = True
    if not found:
        raise AssertionError("Nessuna PAT leggibile")


def exercise(panel, name, directory, overrides=None):
    width, height, codec, order = CASES[name]
    folder = directory / name
    folder.mkdir()
    media = folder / "output.ts"
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
    # Linux reports receiver queue overflow separately from encoder corruption.
    receiver.setsockopt(socket.SOL_SOCKET, 40, 1)  # SO_RXQ_OVFL
    receiver.bind(("127.0.0.1", 0))
    receiver.settimeout(0.1)
    completed = threading.Event()
    capture_stats = {"bytes": 0, "dropped_datagrams": 0}
    def capture():
        with media.open("wb", buffering=1024 * 1024) as handle:
            while not completed.is_set():
                try:
                    data, ancillary, _, _ = receiver.recvmsg(65535, socket.CMSG_SPACE(4))
                    for level, kind, value in ancillary:
                        if level == socket.SOL_SOCKET and kind == 40:
                            capture_stats["dropped_datagrams"] = struct.unpack("I", value)[0]
                    handle.write(data)
                    capture_stats["bytes"] += len(data)
                except socket.timeout:
                    pass
    collector = threading.Thread(target=capture, daemon=True)
    collector.start()
    source_port = free_port()
    source = f"udp://127.0.0.1:{source_port}"
    state = panel.request("/api/state")
    config = {**state["config"], "name": f"Collaudo {name}", "input_type": "url", "input_url": source,
              "output_url": f"udp://127.0.0.1:{receiver.getsockname()[1]}", "outputs": [],
              "preset": "ultrafast", "video_bitrate_kbps": 8000 if height == 1080 else 5000,
              "mux_bitrate_kbps": 9500 if height == 1080 else 6500,
              "vbv_buffer_kbits": 8000 if height == 1080 else 5000,
              "vbv_maxrate_kbps": 0, "gop_frames": 25, "bframes": 0, "lookahead_frames": 0,
              "threads": 2, "field_order": order, "aspect_ratio": "source", "video_profile": "high",
              "audio_codec": codec, "audio_bitrate_kbps": 192, "audio_language": "ita",
              "audio_type": "clean-effects", "ts_id": 107, "program_number": 42,
              "pmt_pid": 4096, "video_pid": 256, "audio_pid": 257, "pcr_pid": 256,
              "pcr_period_ms": 20, "pat_period_ms": 100,
              "service_name": "Test Broadcast", "provider_name": "OBE Local"}
    config.update(overrides or {})
    sender, sender_log = None, (folder / "sender.log").open("wb")
    try:
        state = panel.request("/api/config", {"revision": state["revision"], "config": config}, "PUT")
        panel.request("/api/start", {"revision": state["revision"]}, "POST")
        colors = "bt709" if height == 1080 else "smpte170m"
        sender = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-nostdin", "-re", "-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate=50",
             "-f", "lavfi", "-i", "sine=frequency=997:sample_rate=48000", "-filter_threads", "1",
             "-vf", f"interlace=scan={order}:lowpass=0,setsar={'1/1' if height == 1080 else '16/15'}",
             "-c:v", "mpeg2video", "-threads", "2", "-flags", "+ilme+ildct", "-q:v", "4", "-bf", "0", "-g", "12",
             "-color_primaries", colors, "-color_trc", colors, "-colorspace", colors,
             "-c:a", "mp2", "-b:a", "192k", "-ac", "2", "-f", "mpegts", source + "?pkt_size=1316"],
            env=ENV, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=sender_log)
        panel.wait(lambda current: current["phase"] == "running" and current.get("encoding_started"))
        needed = config["mux_bitrate_kbps"] * 1000 // 8 * 5
        deadline = time.monotonic() + 20
        while capture_stats["bytes"] < needed:
            if sender.poll() is not None:
                raise AssertionError("Generatore del segnale terminato; controllare sender.log")
            if time.monotonic() > deadline:
                raise AssertionError("L'encoder non ha prodotto abbastanza MPEG-TS")
            panel.wait(lambda current: current["phase"] == "running", timeout=8)
            time.sleep(0.1)
    finally:
        try:
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
            completed.set()
            collector.join(timeout=2)
            receiver.close()
    assert capture_stats["dropped_datagrams"] == 0, f"Perdite nel socket di cattura: {capture_stats['dropped_datagrams']}"
    streams = ffprobe(media, "-show_streams")["streams"]
    video = next(stream for stream in streams if stream["codec_type"] == "video")
    audio = next(stream for stream in streams if stream["codec_type"] == "audio")
    assert video["codec_name"] == "h264"
    assert (video["width"], video["height"]) == (width, height)
    assert video["r_frame_rate"] == "25/1"
    assert video["field_order"] in (("tt", "tb") if order == "tff" else ("bb", "bt")), video["field_order"]
    assert int(video["id"], 16) == 256
    assert audio["codec_name"] == {"e-ac3": "eac3"}.get(codec, codec), audio["codec_name"]
    assert int(audio["id"], 16) == 257
    assert (int(audio["sample_rate"]), audio["channels"]) == (48000, 2)
    assert audio.get("tags", {}).get("language") == "ita"
    programs = ffprobe(media, "-show_programs")["programs"]
    assert len(programs) == 1
    program = programs[0]
    assert (program["program_id"], program["pmt_pid"], program["pcr_pid"]) == (42, 4096, 256)
    assert program.get("tags", {}).get("service_name") == "Test Broadcast"
    assert program.get("tags", {}).get("service_provider") == "OBE Local"
    check_transport_tables(media, 107)
    frames = ffprobe(media, "-select_streams", "v:0", "-read_intervals", "%+2", "-show_frames",
                     "-show_entries", "frame=interlaced_frame,top_field_first")["frames"]
    assert len(frames) >= 25
    assert all(frame["interlaced_frame"] == 1 and frame["top_field_first"] == (order == "tff") for frame in frames)
    decode = subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(media), "-t", "2",
                             "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"],
                            env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=20)
    assert decode.returncode == 0, decode.stderr.decode()
    return {"case": name, "codec": audio["codec_name"], "field_order": video["field_order"],
            "video_pid": 256, "audio_pid": 257, "pmt_pid": program["pmt_pid"], "program": 42,
            "bytes": media.stat().st_size, "capture_drops": capture_stats["dropped_datagrams"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", choices=CASES)
    parser.add_argument("--artifacts", type=Path, help="Directory nuova in cui conservare media e log")
    args = parser.parse_args()
    temporary = None
    if args.artifacts:
        directory = args.artifacts
        directory.mkdir(parents=True, exist_ok=False)
    else:
        temporary = tempfile.TemporaryDirectory(prefix="obe-broadcast-live-")
        directory = Path(temporary.name)
    panel, results, failures = None, [], []
    try:
        panel = PanelFixture(directory)
        for name in args.case or CASES:
            try:
                result = exercise(panel, name, directory)
                results.append(result)
                print("PASS " + json.dumps(result), flush=True)
            except Exception as error:
                failure = {"case": name, "error": str(error)}
                failures.append(failure)
                print("FAIL " + json.dumps(failure), flush=True)
        (directory / "results.json").write_text(json.dumps({"passed": results, "failed": failures}, indent=2))
    finally:
        if panel:
            panel.close()
        if temporary:
            temporary.cleanup()
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
