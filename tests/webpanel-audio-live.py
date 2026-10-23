#!/usr/bin/env python3
"""Loopback-only multi-audio test: real encoder, independent tones and reconnect."""
import argparse
from array import array
import importlib.util
import json
import math
from pathlib import Path
import socket
import struct
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("broadcast", ROOT / "tests/webpanel-broadcast-live.py")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


def run(command):
    result = subprocess.run(command, env=base.ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=40)
    if result.returncode:
        raise AssertionError(result.stderr.decode(errors="replace")[-1800:])
    return result.stdout



def check_tone(samples, frequency):
    assert len(samples) >= 24000, "Missing decoded audio"
    rms = math.sqrt(sum(x*x for x in samples)/len(samples))
    assert 0.02 < rms < 0.2, ("Silent or clipped track", rms)
    powers = []
    for tone in range(440, 3521, 440):
        coefficient = 2 * math.cos(2 * math.pi * tone / 48000)
        a = b = 0.0
        for sample in samples:
            current = sample + coefficient * a - b
            b, a = a, current
        powers.append((a*a + b*b - coefficient*a*b, tone))
    powers.sort(reverse=True)
    assert powers[0][1] == frequency and powers[0][0] > 100 * powers[1][0], ("Tracks mixed or swapped", powers)


def make_source(path, count):
    command = ["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", "testsrc2=size=720x576:rate=50"]
    for i in range(count):
        command += ["-f", "lavfi", "-i", f"sine=frequency={440 * (i + 1)}:sample_rate=48000"]
    for i in range(count + 1):
        command += ["-map", str(i)]
    command += ["-filter_threads", "1", "-vf", "interlace=scan=tff,setsar=16/15", "-c:v", "mpeg2video", "-threads", "2",
                "-flags", "+ilme+ildct", "-q:v", "4", "-bf", "0", "-g", "12", "-c:a", "mp2", "-b:a", "192k", "-ac", "2", "-t", "30", "-f", "mpegts", str(path)]
    run(command)


def start_sender(source, endpoint, log, reverse=False, count=2):
    command = ["ffmpeg", "-v", "error", "-nostdin", "-re", "-stream_loop", "-1", "-i", str(source), "-map", "0:v:0"]
    order = list(range(count))
    if reverse: order.reverse()
    for index in order: command += ["-map", f"0:a:{index}"]
    command += ["-streamid", "0:256"]
    for out, index in enumerate(order, 1): command += ["-streamid", f"{out}:{257 + index}"]
    command += ["-c", "copy", "-f", "mpegts", endpoint + "?pkt_size=1316"]
    return subprocess.Popen(command, env=base.ENV, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log)


def stop(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)


def exercise(panel, folder, name, source, tracks, expected, reconnect=False, count=2, codec="mp2", overrides=None, duration=8):
    folder = folder / name
    folder.mkdir()
    media = folder / "output.ts"
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
    receiver.setsockopt(socket.SOL_SOCKET, 40, 1)  # SO_RXQ_OVFL (Linux)
    receiver.bind(("127.0.0.1", 0)); receiver.settimeout(0.1)
    done = threading.Event()
    capture_stats = {"dropped_datagrams": 0}
    capture_errors = []
    def capture():
        try:
            with media.open("wb", buffering=0) as target:
                while not done.is_set():
                    try:
                        data, ancillary, flags, _ = receiver.recvmsg(65536, socket.CMSG_SPACE(4))
                        assert not flags & socket.MSG_TRUNC, "Truncated UDP capture"
                        for level, kind, value in ancillary:
                            if level == socket.SOL_SOCKET and kind == 40:
                                capture_stats["dropped_datagrams"] = struct.unpack("I", value)[0]
                        target.write(data)
                    except socket.timeout: pass
        except Exception as error:
            capture_errors.append(error)
    collector = threading.Thread(target=capture); collector.start()
    endpoint = f"udp://127.0.0.1:{base.free_port()}"
    state = panel.request("/api/state")
    config = {**state["config"], "input_url": endpoint, "output_url": f"udp://127.0.0.1:{receiver.getsockname()[1]}",
              "video_bitrate_kbps": 1000, "vbv_buffer_kbits": 1000, "mux_bitrate_kbps": 3500,
              "preset": "ultrafast", "threads": 2, "bframes": 0, "lookahead_frames": 0, "gop_frames": 25,
              "audio_tracks": tracks, "audio_codec": codec, "auto_restart": False, "audio_language": "", "audio_pid": 0, "audio_type": "undefined"}
    config.update(overrides or {})
    state = panel.request("/api/config", {"revision": state["revision"], "config": config}, "PUT")
    assert state["config"]["audio_tracks"] == tracks
    sender = None
    try:
        panel.request("/api/start", {"revision": state["revision"]}, "POST")
        with (folder / "sender.log").open("wb") as log:
            sender = start_sender(source, endpoint, log, count=count)
            state = panel.wait(lambda s: s["signal_state"] == "receiving" and s["phase"] == "running", 30)
            assert len(state["signal"]["audio_tracks"]) == count
            # The mux has buffered audio: retain enough complete media to decode
            # an interior interval without reaching the final, interrupted PES.
            time.sleep(duration)
            if reconnect:
                before = media.stat().st_size
                stop(sender); sender = None
                panel.wait(lambda s: s["phase"] == "reconnecting", 10)
                snapshot = folder / "before.ts"
                snapshot.write_bytes(media.read_bytes())
                old_packets = base.ffprobe(snapshot, "-show_packets")["packets"]
                sender = start_sender(source, endpoint, log, reverse=True, count=count)
                panel.wait(lambda s: s["phase"] == "running" and s["signal_state"] == "receiving", 25)
                time.sleep(8)
                assert media.stat().st_size > before + 500000
            state = panel.request("/api/state")
    finally:
        try: panel.request("/api/stop", {}, "POST")
        finally:
            stop(sender); done.set(); collector.join(timeout=3); receiver.close()
            (folder / "events.json").write_text(json.dumps(panel.request("/api/state")["events"], indent=2))
    assert not collector.is_alive() and not capture_errors, capture_errors
    assert capture_stats["dropped_datagrams"] == 0, capture_stats
    streams = base.ffprobe(media, "-show_streams")["streams"]
    audio = [s for s in streams if s["codec_type"] == "audio"]
    assert len(audio) == len(expected), (name, len(audio), len(expected))
    for stream, (frequency, pid, language) in zip(audio, expected):
        assert stream["codec_name"] == codec and stream["channels"] == 2 and stream["sample_rate"] == "48000"
        if pid: assert int(stream["id"], 16) == pid, stream
        if language: assert stream.get("tags", {}).get("language") == language, stream
        samples = array("f")
        samples.frombytes(run(["ffmpeg", "-v", "error", "-ss", "1", "-i", str(media), "-map", f"0:{stream['index']}", "-t", "1", "-ac", "1", "-f", "f32le", "-"]))
        if frequency is not None:
            check_tone(samples, frequency)
    packets = base.ffprobe(media, "-show_packets", "-show_entries", "packet=stream_index,pts_time,dts_time,flags")["packets"]
    resumed, clock_errors = [], []
    for stream in streams:
        current = [p for p in packets if p["stream_index"] == stream["index"]]
        times = [float(p["pts_time"]) for p in current]
        # A stopped live TS can end inside its final audio PES. Earlier damaged
        # packets would indicate a real mux/transport problem and must fail.
        assert all("C" not in p["flags"] or float(p["pts_time"]) >= times[-1] - 0.15 for p in current), (name, "Corrupt interior packet", stream["index"])
        for field in ("pts_time", "dts_time"):
            clock = [float(p[field]) for p in current]
            assert len(clock) > 40 and all(b > a for a,b in zip(clock,clock[1:])), (name, stream["index"], field)
            if reconnect:
                step = 0.04 if stream["codec_type"] == "video" else 0.024
                assert all(abs(b-a-step) < 0.0001 for a,b in zip(clock,clock[1:])), (name, "Timestamp gap", stream["index"], field)
        if reconnect:
            old_count = sum(p["stream_index"] == stream["index"] for p in old_packets)
            extra = times[old_count:]
            assert len(extra) >= 60, (name, "Missing resumed track", stream["index"])
            resumed.append((extra[0], extra[-1]))
            clock_errors.append(times[-1] - times[0] - (len(times) - 1) * step)
    if reconnect:
        resumed_pts = max(start for start, end in resumed)
        assert min(end for start, end in resumed) - resumed_pts >= 2, (name, "No common resumed A/V interval", resumed)
        assert max(clock_errors) - min(clock_errors) < 0.001, (name, "Cumulative A/V clock drift", clock_errors)
        for stream in audio:
            skew = min(abs(float(p["pts_time"]) - resumed_pts) for p in packets if p["stream_index"] == stream["index"])
            assert skew <= 0.0241, (name, "A/V gap after reconnect", stream["index"], skew)
        for stream,(frequency,_,_) in zip(audio,expected):
            samples=array("f"); samples.frombytes(run(["ffmpeg","-v","error","-i",str(media),"-map",f"0:{stream['index']}","-ac","1","-f","f32le","-"]))
            check_tone(samples[-48000:], frequency)
        first_pts = min(float(p["pts_time"]) for p in packets)
        run(["ffmpeg","-v","error","-xerror","-ss",str(resumed_pts-first_pts+0.5),"-i",str(media),"-t","1","-map","0:v:0","-map","0:a?","-f","null","-"])
    run(["ffmpeg","-v","error","-xerror","-ss","1","-i",str(media),"-t","2","-map","0:v:0","-map","0:a?","-f","null","-"])
    print(f"PASS {name}: {len(audio)} tracce, PID/lingue, toni separati, PTS e decodifica" + ("; riconnessione con ordine sorgenti invertito" if reconnect else ""), flush=True)
    return media, state


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts",type=Path,required=True)
    parser.add_argument("--case",action="append",choices=("two-tracks","second-only","video-only","legacy-first","eight-tracks","two-aac"))
    args=parser.parse_args(); folder=args.artifacts; folder.mkdir(parents=True,exist_ok=False)
    engine=folder/"engine"; engine.mkdir()
    for name, source in {"obecli":ROOT/"obe-rt/obe/obecli","run.sh":ROOT/"run.sh","target-root":ROOT/"target-root","NDI":ROOT/"NDI"}.items():
        (engine/name).symlink_to(source)
    source=folder/"source.ts"; make_source(source,2)
    panel=base.PanelFixture(folder,engine)
    def track(source,pid=0,language="",enabled=True):
        return {"source":source,"pid":pid,"language":language,"enabled":enabled,"audio_type":"undefined"}
    try:
        cases = [
            ("two-tracks", [track(1,991,"ita"),track(2,992,"eng")], [(440,991,"ita"),(880,992,"eng")], True, 2, "mp2"),
            ("second-only", [track(1,991,"ita",False),track(2,992,"eng")], [(880,992,"eng")], False, 2, "mp2"),
            ("video-only", [], [], False, 2, "mp2"),
            ("legacy-first", None, [(440,None,None)], False, 2, "mp2"),
            ("eight-tracks", [track(i+1,991+i,"ita") for i in range(8)], [(440*(i+1),991+i,"ita") for i in range(8)], False, 8, "mp2"),
            ("two-aac", [track(1,991,"ita"),track(2,992,"eng")], [(440,991,"ita"),(880,992,"eng")], False, 2, "aac"),
        ]
        for name, tracks, expected, reconnect, count, codec in cases:
            if args.case and name not in args.case: continue
            if count == 8:
                current_source=folder/"source8.ts"; make_source(current_source,8)
            else: current_source=source
            exercise(panel,folder,name,current_source,tracks,expected,reconnect,count,codec)
    finally: panel.close()


if __name__ == "__main__": main()
