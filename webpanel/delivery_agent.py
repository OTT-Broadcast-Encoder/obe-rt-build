#!/usr/bin/env python3
"""Passive source/return observer and optional local MAIN/BACKUP selector.

Only loopback HTTP is accepted. Reach remote observers through SSH forwarding.
Configuration and token files are private; no credentials or URLs are logged.
"""
import argparse
import base64
import binascii
from collections import deque
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
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
from urllib.parse import parse_qs, urlsplit

from delivery_signal import Clock, Selector, fingerprint, SAMPLE_HZ, SAMPLE_LIMIT


def endpoint(value, output=False):
    u = urlsplit(value)
    address = ipaddress.IPv4Address(u.hostname)
    options = parse_qs(u.query, strict_parsing=True) if u.query else {}
    if (u.scheme not in (("udp",) if output else ("udp", "rtp")) or not u.port
            or u.username is not None or u.password is not None or u.fragment
            or u.path not in ("", "/") or set(options) - {"interface", "ttl"}
            or any(len(v) != 1 for v in options.values())
            or output and address.is_unspecified):
        raise ValueError("Usa un indirizzo UDP/RTP IPv4 con porta.")
    interface = str(ipaddress.IPv4Address(options.get("interface", ["0.0.0.0"])[0]))
    ttl = int(options.get("ttl", ["1"])[0])
    if not 0 <= ttl <= 255:
        raise ValueError("TTL non valido.")
    return u.scheme, str(address), u.port, interface, ttl


def ts_payload(data, rtp=False):
    if rtp:
        if len(data) < 12 or data[0] >> 6 != 2 or data[1] & 127 != 33:
            return None
        offset = 12 + (data[0] & 15) * 4
        if data[0] & 16:
            if len(data) < offset + 4:
                return None
            offset += 4 + int.from_bytes(data[offset + 2:offset + 4], "big") * 4
        if offset >= len(data):
            return None
        end = len(data)
        if data[0] & 32:
            padding = data[-1]
            if not padding or padding > end - offset:
                return None
            end -= padding
        data = data[offset:end]
    if not data or len(data) % 188 or any(data[i] != 71 for i in range(0, len(data), 188)):
        return None
    return data


class Video:
    def __init__(self, clock):
        self.clock, self.lock = clock, threading.RLock()
        self.samples = deque(maxlen=SAMPLE_LIMIT)
        self.frames, self.last_frame, self.changed = 0, None, None
        self.error = None

    def add(self, raw, stamp=None):
        now = time.monotonic()
        with self.lock:
            if not self.samples or sum(abs(x-y) for x, y in zip(raw, base64.b64decode(self.samples[-1][1]))) > 288:
                self.changed = now
            self.last_frame, self.frames, self.error = now, self.frames + 1, None
            self.samples.append([round(self.clock.now() if stamp is None else stamp, 2),
                                 base64.b64encode(raw).decode("ascii")])
            while self.samples and self.samples[0][0] < self.clock.now() - 60000:
                self.samples.popleft()

    def state(self, samples=False):
        with self.lock:
            age = None if self.last_frame is None else time.monotonic() - self.last_frame
            return {"status": "live" if age is not None and age < 2 else "unavailable",
                    "frame_age_seconds": round(age, 2) if age is not None else None,
                    "decoded_frames": self.frames, "error": self.error,
                    "unchanged_seconds": round(time.monotonic() - self.changed, 1) if self.changed else None,
                    **({"samples": list(self.samples)} if samples else {})}


class Decoder:
    def __init__(self, ffmpeg, program, video):
        self.video, self.stop_event = video, threading.Event()
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        reservation = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
        reservation.close()
        self.target = ("127.0.0.1", port)
        command = [ffmpeg, "-nostdin", "-v", "error", "-threads", "1", "-probesize", "1000000",
                   "-analyzeduration", "1000000", "-i",
                   f"udp://127.0.0.1:{port}?fifo_size=1024&overrun_nonfatal=1",
                   "-map", f"0:p:{program}:v:0" if program else "0:v:0", "-an", "-sn", "-dn",
                   "-filter_threads", "1", "-vf", "fps=5,scale=160:90:flags=area,format=gray",
                   "-threads", "1", "-flush_packets", "1", "-f", "rawvideo", "pipe:1"]
        self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL)
        self.worker = threading.Thread(target=self._read, daemon=True)
        self.worker.start()

    def _read(self):
        size, pending = 160 * 90, bytearray()
        try:
            while not self.stop_event.is_set():
                data = self.process.stdout.read(size - len(pending))
                if not data:
                    break
                pending.extend(data)
                if len(pending) == size:
                    self.video.add(fingerprint(pending))
                    pending.clear()
        finally:
            self.video.error = "Decodifica non disponibile"

    def feed(self, data):
        if self.process.poll() is None:
            self.socket.sendto(data, self.target)

    def close(self):
        self.stop_event.set()
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        self.worker.join(timeout=2)
        self.process.stdout.close()
        self.socket.close()


class Stream:
    def __init__(self, name, url, agent):
        self.name, self.agent = name, agent
        self.lock, self.stop_event = threading.RLock(), threading.Event()
        self.video = Video(agent.clock)
        protocol, host, port, interface, _ = endpoint(url)
        self.rtp = protocol == "rtp"
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        if ipaddress.ip_address(host).is_multicast:
            self.socket.bind((host, port))
            self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                   socket.inet_aton(host) + socket.inet_aton(interface))
        else:
            self.socket.bind((host, port))
        self.socket.settimeout(.1)
        self.decoder = None
        self.last = self.bad = None
        self.packets = self.bytes = self.errors = self.cc_errors = 0
        self.counters = {}
        self.rate = deque(maxlen=30)
        self.worker = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.worker.start()

    def _run(self):
        retry = 0
        while not self.stop_event.is_set():
            now = time.monotonic()
            if (self.decoder is None or self.decoder.process.poll() is not None) and now >= retry:
                if self.decoder:
                    self.decoder.close()
                    self.decoder = None
                try:
                    self.decoder = Decoder(self.agent.config["ffmpeg"], self.agent.config.get("program", 0), self.video)
                except OSError:
                    self.video.error = "FFmpeg non disponibile"
                retry = now + 5
            try:
                data = self.socket.recv(65535)
            except socket.timeout:
                self.agent.route(None, None)
                continue
            except OSError:
                break
            data = ts_payload(data, self.rtp)
            with self.lock:
                if data is None:
                    self.errors += 1
                    self.bad = now
                    continue
                self.last, self.bytes, self.packets = now, self.bytes + len(data), self.packets + len(data) // 188
                if not self.rate or now - self.rate[-1][0] >= .2:
                    self.rate.append((now, self.bytes))
                for i in range(0, len(data), 188):
                    p = data[i:i + 188]
                    pid, afc, cc = ((p[1] & 31) << 8) | p[2], (p[3] >> 4) & 3, p[3] & 15
                    if p[1] & 128 or not afc or afc & 2 and p[4] > (182 if afc == 3 else 183):
                        self.errors += 1
                        self.bad = now
                        continue
                    if pid == 8191:
                        continue
                    discontinuity = afc & 2 and p[4] and p[5] & 128
                    if afc & 1:
                        previous = self.counters.get(pid)
                        payload = binascii.crc32(p[5 + p[4]:] if afc == 3 else p[4:])
                        if (previous is not None and not discontinuity
                                and not (cc == (previous[0] + 1) % 16 or cc == previous[0] and payload == previous[1])):
                            self.cc_errors += 1
                            self.bad = now
                        self.counters[pid] = cc, payload
                    elif discontinuity:
                        self.counters.pop(pid, None)
            if self.decoder:
                try:
                    self.decoder.feed(data)
                except OSError:
                    self.video.error = "Lettura video interrotta"
            self.agent.route(self.name, data)

    def state(self, samples=False):
        with self.lock:
            now = time.monotonic()
            view = self.video.state(samples)
            fresh = self.last is not None and now - self.last < 1
            healthy = fresh and view["status"] == "live" and (self.bad is None or now - self.bad >= 1)
            bitrate = 0
            if fresh and len(self.rate) > 1:
                elapsed = self.rate[-1][0] - self.rate[0][0]
                if elapsed:
                    bitrate = (self.rate[-1][1] - self.rate[0][1]) * .008 / elapsed
            return {**view, "healthy": healthy, "status": "live" if healthy else "degraded" if fresh else "unavailable",
                    "packets": self.packets, "bytes": self.bytes, "errors": self.errors,
                    "continuity_errors": self.cc_errors, "bitrate_kbps": round(bitrate, 1)}

    def close(self):
        self.stop_event.set()
        self.socket.close()
        if self.worker.is_alive():
            self.worker.join(timeout=3)
        if self.decoder:
            self.decoder.close()


class NDISource:
    """Independent NDI reader: no changes or restart to the on-air gateway."""
    def __init__(self, config, clock):
        self.config, self.video = config, Video(clock)
        self.stop_event = threading.Event()
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    def _run(self):
        sys.path.insert(0, self.config["ndi_module_dir"])
        import ndi_sender as ndi
        C = ndi.C
        while not self.stop_event.is_set():
            reader = None
            try:
                reader = ndi.NDI(self.config["ndi_runtime"])
                reader.connect(self.config["ndi_source"], self.stop_event)
                last = 0
                while not self.stop_event.is_set():
                    v, a, m = ndi.Video(), ndi.Audio(), ndi.Metadata()
                    kind = reader.recv_capture_v2(reader.receiver, C.byref(v), C.byref(a), C.byref(m), 250)
                    if kind == 1:
                        try:
                            now = time.monotonic()
                            if now - last < 1 / SAMPLE_HZ:
                                continue
                            last = now
                            if (v.fourcc != int.from_bytes(b'UYVY', 'little') or v.format not in (0, 1)
                                    or not v.data or not 16 <= v.width <= 3840 or not 9 <= v.height <= 2160
                                    or not v.width * 2 <= v.stride <= v.width * 2 + 4096):
                                raise ValueError("Formato NDI non supportato")
                            # Stratified area sampling without copying full HD frames.
                            raw = bytes(round(sum(C.c_ubyte.from_address(v.data +
                                min(v.height - 1, int((y + (j + .5) / 8) * v.height / 9)) * v.stride +
                                min(v.width - 1, int((x + (i + .5) / 8) * v.width / 16)) * 2 + 1).value
                                for j in range(8) for i in range(8)) / 64)
                                for y in range(9) for x in range(16))
                            self.video.add(raw)
                        finally:
                            reader.recv_free_video_v2(reader.receiver, C.byref(v))
                    elif kind == 2:
                        reader.recv_free_audio_v2(reader.receiver, C.byref(a))
                    elif kind == 3:
                        reader.recv_free_metadata(reader.receiver, C.byref(m))
                    elif kind == 4:
                        raise ValueError("NDI disconnesso")
            except (OSError, ValueError, RuntimeError):
                self.video.error = "Sorgente NDI non disponibile"
            finally:
                if reader:
                    reader.close()
            self.stop_event.wait(2)

    def state(self, samples=False):
        return self.video.state(samples)

    def close(self):
        self.stop_event.set()
        self.worker.join(timeout=3)


class Agent:
    def __init__(self, config, config_path=None):
        self.config, self.clock = config, Clock()
        self.config_path = config_path
        self.lock = threading.RLock()
        self.revision = 1
        self.streams, self.source, self.output = {}, None, None
        self.output_bytes = self.output_errors = 0
        self.selector = Selector(config.get("failure_seconds", 2), config.get("recovery_seconds", 30),
                                 config.get("hold_seconds", 5))
        for value in (self.selector.failure, self.selector.recovery, self.selector.hold):
            if type(value) not in (float, int) or not 1 <= value <= 300:
                raise ValueError("Intervallo di commutazione non valido")
        self.selector.mode = config.get("mode", "auto")
        if self.selector.mode not in ("auto", "main", "backup"):
            raise ValueError("Modalità di selezione non valida")
        self.role = config["role"]
        if self.role not in ("source", "monitor", "receiver"):
            raise ValueError("Ruolo non valido")
        if self.role == "receiver":
            if not {"main", "backup", "return"} <= set(config["streams"]) or not 1 <= config.get("program", 0) <= 65535:
                raise ValueError("MAIN, BACKUP, ritorno e programma sono richiesti")
            _, host, port, _, ttl = endpoint(config["output_url"], output=True)
            if any(endpoint(url)[1:3] == (host, port) for url in config["streams"].values()):
                raise ValueError("L'uscita deve essere distinta dagli ingressi")
            self.output = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.output.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
            self.output.settimeout(.05)
            self.output.connect((host, port))
        try:
            if self.role == "source" and "ndi_source" in config:
                self.source = NDISource(config, self.clock)
            else:
                names = set(config["streams"])
                if not names or not names <= {"main", "backup", "return", "source"} or len(names) > 3:
                    raise ValueError("Ingressi non validi")
                for name, url in config["streams"].items():
                    self.streams[name] = Stream(name, url, self)
                for stream in self.streams.values():
                    stream.start()
        except BaseException:
            self.close()
            raise

    def route(self, name, data):
        if self.role != "receiver":
            return
        with self.lock:
            active = self.selector.update({key: self.streams[key].state()["healthy"] for key in ("main", "backup")})
            if data is not None and active == name:
                try:
                    self.output.send(data)
                    self.output_bytes += len(data)
                except OSError:
                    self.output_errors += 1

    def state(self):
        with self.lock:
            observed = self.source or self.streams.get("return") or self.streams.get("source")
            observation = observed.state(samples=True) if observed else {}
            return {"schema": 1, "instance": self.clock.instance, "revision": self.revision,
                    "role": self.role, "streams": {k: v.state() for k, v in self.streams.items()},
                    "observation": {k: v for k, v in observation.items() if k != "samples"},
                    "samples": observation.get("samples", []),
                    "selector": self.selector.state() if self.role == "receiver" else None,
                    "output_bytes": self.output_bytes, "output_errors": self.output_errors,
                    "clock_ms": self.clock.now()}

    def control(self, payload):
        with self.lock:
            if (self.role != "receiver" or not isinstance(payload, dict)
                    or set(payload) != {"instance", "revision", "mode"}
                    or payload["mode"] not in ("auto", "main", "backup")):
                raise ValueError("Comando non valido")
            if (payload["instance"] != self.clock.instance or type(payload["revision"]) is not int
                    or payload["revision"] != self.revision):
                raise RuntimeError("Stato del ricevitore cambiato")
            if self.config_path:
                changed = {**self.config, "mode": payload["mode"]}
                with tempfile.NamedTemporaryFile(dir=Path(self.config_path).parent, mode="w", delete=False) as handle:
                    temporary = handle.name
                    json.dump(changed, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.config_path)
                self.config = changed
            self.selector.mode = payload["mode"]
            self.revision += 1
            self.route(None, None)
            return self.state()

    def close(self):
        for stream in self.streams.values():
            stream.close()
        if self.source:
            self.source.close()
        if self.output:
            self.output.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def setup(self):
        self.request.settimeout(2)
        super().setup()

    def reply(self, status, data):
        body = json.dumps(data, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_request(self):
        if not hmac.compare_digest(self.headers.get("Authorization", "").encode(), ("Bearer " + self.server.token).encode()):
            self.reply(401, {"error": "Autenticazione richiesta"})
            return
        try:
            if self.command == "GET" and self.path == "/v1/state":
                self.reply(200, self.server.agent.state())
            elif self.command == "POST" and self.path == "/v1/control":
                length = int(self.headers.get("Content-Length", "0"))
                if not 1 <= length <= 1024 or self.headers.get("Transfer-Encoding"):
                    raise ValueError()
                result = self.server.agent.control(json.loads(self.rfile.read(length)))
                self.reply(200, result)
            else:
                self.reply(404, {"error": "Risorsa non disponibile"})
        except RuntimeError:
            self.reply(409, {"error": "Stato del ricevitore cambiato"})
        except (ValueError, TypeError, KeyError):
            self.reply(422, {"error": "Richiesta non valida"})
        except OSError:
            self.reply(503, {"error": "Salvataggio non riuscito"})

    do_GET = do_POST = handle_request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=0)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    token = Path(config["token_file"]).read_text().strip()
    if not 32 <= len(token) <= 128 or not token.isascii() or not token.isalnum():
        parser.error("Token non valido")
    agent = Agent(config, args.config)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", config.get("port", 0)), Handler)
    except BaseException:
        agent.close()
        raise
    server.daemon_threads = True
    server.agent, server.token = agent, token
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, lambda *_: stop.set())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print("OBE delivery observer ready", server.server_port, flush=True)
    try:
        stop.wait(args.seconds or None)
    finally:
        server.shutdown()
        server.server_close()
        agent.close()


if __name__ == "__main__":
    main()
