"""Bounded MPEG-TS fan-out with independently supervised UDP, RTP and SRT outputs.

No transcoding occurs here. The encoder sends TS to one loopback socket. Each
destination has its own bounded queue; an unavailable link cannot stall another.
SRT uses the bundled native helper so secrets never appear in process arguments.
"""

import ipaddress
import json
import math
import os
from pathlib import Path
import queue
import re
import secrets
import select
import socket
import struct
import subprocess
import sys
import threading
import time
import unicodedata
from urllib.parse import parse_qsl, urlsplit

from srt_options import SRT_DEFAULTS, SRT_PARAMETERS, option_error, valid_option


MAX_OUTPUTS = 8
QUEUE_PACKETS = 128
TS_PACKET = 188
DATAGRAM_SIZE = 7 * TS_PACKET
MAX_PACKET_AGE = 0.5
RETRY_SECONDS = 1
OUTPUT_DEFAULTS = {"enabled": True, **SRT_DEFAULTS}
OUTPUT_KEYS = {"id", "name", "protocol", "url", *OUTPUT_DEFAULTS}


class TransportError(ValueError):
    def __init__(self, field, message):
        super().__init__(message)
        self.field, self.message = field, message


def _invalid(field, message):
    raise TransportError(field, message)


def _control(text):
    return any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in text)


def validate_outputs(outputs):
    """Validate public configuration; all credentials belong in a separate map."""
    if not isinstance(outputs, list) or len(outputs) > MAX_OUTPUTS:
        _invalid("outputs", f"Configura al massimo {MAX_OUTPUTS} destinazioni.")
    result, identifiers, listeners = [], set(), set()
    for index, supplied in enumerate(outputs):
        field = f"outputs.{index}"
        if not isinstance(supplied, dict) or set(supplied) - OUTPUT_KEYS or not {"id", "name", "protocol", "url"} <= set(supplied):
            _invalid(field, "Destinazione incompleta o con campi sconosciuti.")
        output = {**OUTPUT_DEFAULTS, **supplied}
        identifier = output["id"]
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", identifier) or identifier == "primary" or identifier in identifiers:
            _invalid(f"{field}.id", "Usa un identificatore univoco; primary è riservato.")
        identifiers.add(identifier)
        name = output["name"]
        if not isinstance(name, str) or not name.strip() or len(name) > 64 or _control(name):
            _invalid(f"{field}.name", "Usa un nome da 1 a 64 caratteri senza controlli.")
        if type(output["enabled"]) is not bool:
            _invalid(f"{field}.enabled", "Indica se la destinazione è abilitata.")
        if output["protocol"] not in ("udp", "rtp", "srt"):
            _invalid(f"{field}.protocol", "Scegli UDP, RTP o SRT.")
        for name, spec in SRT_PARAMETERS.items():
            if not valid_option(output[name], spec):
                _invalid(f"{field}.{name}", option_error(spec))
        value = output["url"]
        try:
            if not isinstance(value, str) or not 1 <= len(value) <= 800 or any(c.isspace() for c in value) or _control(value):
                raise ValueError()
            parts = urlsplit(value)
            if (parts.scheme != output["protocol"] or not parts.hostname or parts.path not in ("", "/")
                    or parts.username is not None or parts.password is not None or parts.query or parts.fragment
                    or not parts.port or not 1 <= parts.port <= 65535
                    or any(c in value for c in "%\\\"'<>`|,;")):
                raise ValueError()
            # Resolve DNS in the worker, never while validating/saving a profile.
            try:
                address = ipaddress.ip_address(parts.hostname)
            except ValueError:
                if not re.fullmatch(r"(?=.{1,253}$)[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", parts.hostname):
                    raise ValueError()
            else:
                if parts.scheme == "srt" and address.is_multicast:
                    raise ValueError()
                if address.is_unspecified and not (parts.scheme == "srt" and output["srt_mode"] == "listener"):
                    raise ValueError()
        except (TypeError, ValueError):
            _invalid(f"{field}.url", "Usa protocollo://host:porta senza query o credenziali.")
        if output["enabled"] and output["protocol"] == "srt" and output["srt_mode"] == "listener":
            # Two listeners may not claim the same port, even with alias hosts.
            if parts.port in listeners:
                _invalid(f"{field}.url", "La porta SRT listener è già usata da un'altra destinazione.")
            listeners.add(parts.port)
        result.append(output)
    return result


def _endpoint(output):
    parts = urlsplit(output["url"])
    return output["protocol"], parts.hostname, parts.port, output["srt_mode"]


def merge_credentials(old_outputs, new_outputs, saved, patch=None):
    """Keep keys only for the same endpoint; an explicit empty value removes one."""
    patch = {} if patch is None else patch
    if not isinstance(patch, dict) or not isinstance(saved, dict):
        _invalid("output_credentials", "Le credenziali delle uscite devono essere un oggetto.")
    previous = {item["id"]: item for item in validate_outputs(old_outputs)}
    current = {item["id"]: item for item in validate_outputs(new_outputs)}
    if set(patch) - set(current):
        _invalid("output_credentials", "Credenziale riferita a una destinazione inesistente.")
    result = {}
    for identifier, output in current.items():
        key = ""
        old = previous.get(identifier)
        if old and _endpoint(old) == _endpoint(output):
            key = saved.get(identifier, "")
        if identifier in patch:
            key = patch[identifier]
        if not isinstance(key, str) or _control(key) or (key and not 10 <= len(key.encode("utf-8")) <= 79):
            _invalid(f"output_credentials.{identifier}", "La passphrase SRT deve contenere da 10 a 79 byte senza controlli.")
        if key and output["protocol"] != "srt":
            _invalid(f"output_credentials.{identifier}", "La passphrase è disponibile soltanto per SRT.")
        if key:
            result[identifier] = key
    return result


def public_credentials(outputs, saved):
    return {item["id"]: bool(saved.get(item["id"])) for item in outputs}


def primary_output(url):
    """The primary UDP output follows the same lifecycle as other destinations."""
    try:
        parts = urlsplit(url)
        options = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
        if (len(options) > 1 or any(key != "ttl" or not value.isascii() or not value.isdecimal()
                                  or not 0 <= int(value) <= 255 for key, value in options)):
            raise ValueError()
        output = validate_outputs([{"id": "monitor", "name": "Uscita principale", "protocol": "udp",
                                    "url": parts._replace(query="").geturl()}])[0]
    except (TypeError, ValueError):
        _invalid("output_url", "Usa udp://host:porta; è ammesso il parametro ttl da 0 a 255.")
    return {**output, "id": "primary", "url": url}


def helper_path(root):
    local = Path(root) / "webpanel/bin/obe-srt-output"
    return local if local.is_file() else Path(root) / "target-root/usr/local/bin/obe-srt-output"


def capabilities(root):
    helper = helper_path(root)
    return {"udp": True, "rtp": True, "srt": helper.is_file() and os.access(helper, os.X_OK),
            "live_update": True, "srt_options": True, "srt_parameters": SRT_PARAMETERS}


class _DatagramSink:
    def __init__(self, output, root, key):
        parts = urlsplit(output["url"])
        try:
            ipaddress.ip_address(parts.hostname)
        except ValueError:
            # A stuck DNS resolver must not prevent channel stop. Resolve once
            # in a bounded child using the same Python runtime, not a daemon
            # thread which would remain behind after shutdown.
            code = "import json,socket,sys; print(json.dumps(socket.getaddrinfo(sys.argv[1],int(sys.argv[2]),type=socket.SOCK_DGRAM)))"
            resolved = subprocess.run([sys.executable, "-I", "-c", code, parts.hostname, str(parts.port)],
                                      capture_output=True, text=True, timeout=1, check=True)
            addresses = json.loads(resolved.stdout)
        else:
            addresses = socket.getaddrinfo(parts.hostname, parts.port, type=socket.SOCK_DGRAM, flags=socket.AI_NUMERICHOST)
        family, kind, protocol, _, address = addresses[0]
        self.socket = socket.socket(family, kind, protocol)
        try:
            if ipaddress.ip_address(address[0]).is_multicast:
                ttl = int(dict(parse_qsl(parts.query)).get("ttl", "1"))
                if family == socket.AF_INET:
                    self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
                elif family == socket.AF_INET6:
                    self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_HOPS, ttl)
            self.socket.settimeout(0.05)
            self.socket.connect(tuple(address))
        except BaseException:
            self.socket.close()
            raise
        self.rtp = output["protocol"] == "rtp"
        self.sequence = secrets.randbits(16)
        self.ssrc = secrets.randbits(32)

    def send(self, data):
        if self.rtp:
            # RFC 2250: PT 33 for MPEG-TS, 90 kHz clock for transmission time.
            timestamp = int(time.monotonic() * 90000) & 0xffffffff
            data = struct.pack("!BBHII", 0x80, 33, self.sequence, timestamp, self.ssrc) + data
            self.sequence = (self.sequence + 1) & 0xffff
        return self.socket.send(data) == len(data)

    def metrics(self):
        return {"status": "live"}

    def close(self):
        self.socket.close()


class _SRTSink:
    def __init__(self, output, root, key):
        parts = urlsplit(output["url"])
        env = {**os.environ, "OBE_OUTPUT_SRT_PASSPHRASE": key,
               "OBE_OUTPUT_PARENT_PID": str(os.getpid())}
        self.process = subprocess.Popen(
            [str(helper_path(root)), parts.hostname, str(parts.port), output["srt_mode"], str(output["latency_ms"]),
             str(output["srt_key_length"]), str(output["srt_max_bandwidth_kbps"]),
             str(output["srt_connect_timeout_ms"]), str(output["srt_send_timeout_ms"]),
             "1" if output["srt_drop_late_packets"] else "0"],
            env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            start_new_session=True)
        os.set_blocking(self.process.stdin.fileno(), False)
        os.set_blocking(self.process.stdout.fileno(), False)
        self.pending = b""
        self.latest = {"status": "connecting", "sent_bytes": 0, "sent_packets": 0}

    def send(self, data):
        if self.process.poll() is not None:
            raise OSError("Gateway SRT terminato")
        if self.latest["status"] != "live":
            return False
        frame = struct.pack("!H", len(data)) + data
        # All writes are below PIPE_BUF, so EAGAIN never leaves half a frame.
        ready = select.select([], [self.process.stdin], [], 0.05)[1]
        if not ready:
            return False
        try:
            return os.write(self.process.stdin.fileno(), frame) == len(frame)
        except BlockingIOError:
            return False

    def metrics(self):
        while True:
            try:
                chunk = os.read(self.process.stdout.fileno(), 16384)
            except BlockingIOError:
                break
            if not chunk:
                break
            self.pending += chunk
            while b"\n" in self.pending:
                line, self.pending = self.pending.split(b"\n", 1)
                try:
                    values = json.loads(line)
                    # Read only numeric counters and known state values.
                    for name in ("sent_bytes", "sent_packets", "rtt_ms", "lost_packets", "retransmitted_packets", "srt_dropped_packets"):
                        if type(values.get(name)) in (int, float) and math.isfinite(values[name]) and values[name] >= 0:
                            self.latest[name] = values[name]
                    if values.get("status") in ("connecting", "live"):
                        self.latest["status"] = values["status"]
                except (ValueError, TypeError, AttributeError):
                    pass
            if len(self.pending) > 8192:
                self.pending = b""
        if self.process.poll() is not None:
            raise OSError("Gateway SRT terminato")
        return dict(self.latest)

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=1)
        self.process.stdin.close()
        self.process.stdout.close()


def _sink(output, root, key):
    return (_SRTSink if output["protocol"] == "srt" else _DatagramSink)(output, root, key)


class _Output:
    def __init__(self, config, root, key, sink_factory, active=True):
        self.config, self.root, self.key, self.sink_factory = config, root, key, sink_factory
        self.queue = queue.Queue(maxsize=QUEUE_PACKETS)
        self.finished, self.lock = threading.Event(), threading.Lock()
        self.activated = threading.Event()
        if active:
            self.activated.set()
        self.stats = {"id": config["id"], "name": config["name"], "protocol": config["protocol"],
                      "status": "connecting", "sent_bytes": 0, "sent_packets": 0,
                      "dropped_packets": 0, "errors": 0, "restarts": 0,
                      "last_error": None,
                      "encrypted": bool(key), "bitrate_kbps": 0,
                      "rtt_ms": None, "lost_packets": 0, "retransmitted_packets": 0, "srt_dropped_packets": 0}
        self.thread = threading.Thread(target=self._run, name=f"output-{config['id']}", daemon=True)
        self.thread.start()

    def offer(self, data):
        if self.finished.is_set():
            return
        try:
            self.queue.put_nowait((time.monotonic(), data))
        except queue.Full:
            # Keep the latest signal instead of allowing latency to grow forever.
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
            with self.lock:
                self.stats["dropped_packets"] += 1
            try:
                self.queue.put_nowait((time.monotonic(), data))
            except queue.Full:
                with self.lock:
                    self.stats["dropped_packets"] += 1

    def _run(self):
        # Staged workers cannot bind/connect until the profile is persisted.
        self.activated.wait()
        sink, attempts = None, 0
        baseline_bytes = baseline_packets = 0
        rate_time, rate_bytes = time.monotonic(), 0
        while not self.finished.is_set():
            try:
                if sink is None:
                    with self.lock:
                        self.stats.update(status="connecting", restarts=attempts, rtt_ms=None)
                        baseline_bytes, baseline_packets = self.stats["sent_bytes"], self.stats["sent_packets"]
                    attempts += 1
                    sink = self.sink_factory(self.config, self.root, self.key)
                try:
                    received, data = self.queue.get(timeout=0.1)
                except queue.Empty:
                    data = None
                if data is not None:
                    if time.monotonic() - received > MAX_PACKET_AGE or not sink.send(data):
                        with self.lock:
                            self.stats["dropped_packets"] += 1
                    elif self.config["protocol"] != "srt":
                        with self.lock:
                            self.stats["sent_bytes"] += len(data)
                            self.stats["sent_packets"] += 1
                metrics = sink.metrics()
                with self.lock:
                    if metrics.get("status") == "live":
                        self.stats["last_error"] = None
                    for name, value in metrics.items():
                        if name in ("sent_bytes", "sent_packets"):
                            self.stats[name] = value + (baseline_bytes if name == "sent_bytes" else baseline_packets)
                        elif name in self.stats:
                            self.stats[name] = value
                    now = time.monotonic()
                    if now - rate_time >= 0.5:
                        self.stats["bitrate_kbps"] = round((self.stats["sent_bytes"] - rate_bytes) * 8 / (now - rate_time) / 1000, 1)
                        rate_time, rate_bytes = now, self.stats["sent_bytes"]
            except (OSError, ValueError, subprocess.SubprocessError):
                with self.lock:
                    self.stats["errors"] += 1
                    self.stats.update(status="reconnecting", bitrate_kbps=0, rtt_ms=None,
                                      last_error="Destinazione non raggiungibile o collegamento interrotto.")
                if sink is not None:
                    sink.close()
                    sink = None
                self.finished.wait(RETRY_SECONDS)
        if sink is not None:
            sink.close()
        with self.lock:
            self.stats.update(status="idle", bitrate_kbps=0, rtt_ms=None)
        self.key = ""

    def state(self):
        with self.lock:
            return {**self.stats, "queued_packets": self.queue.qsize()}

    def stop(self):
        self.finished.set()
        self.activated.set()


class Transport:
    """Stable encoder ingress; destinations can be reconciled independently."""
    def __init__(self, root, sink_factory=None):
        self.root, self.sink_factory = Path(root), sink_factory or _sink
        self.lifecycle, self.lock = threading.RLock(), threading.Lock()
        self.finished = threading.Event()
        self.receiver = self.thread = None
        self.outputs, self.disabled = [], []
        self.retired = []
        self.input_bytes = self.input_packets = self.invalid_packets = 0
        self.last_input = None

    def _configuration(self, outputs, credentials, primary_url):
        outputs = validate_outputs(outputs)
        credentials = merge_credentials(outputs, outputs, credentials or {})
        enabled = [item for item in outputs if item["enabled"]]
        if any(item["protocol"] == "srt" for item in enabled) and self.sink_factory is _sink and not capabilities(self.root)["srt"]:
            raise TransportError("outputs", "Gateway SRT non compilato: esegui webpanel/build-srt-output.sh.")
        if primary_url:
            enabled.insert(0, primary_output(primary_url))
        disabled = [{"id": item["id"], "name": item["name"], "protocol": item["protocol"], "status": "disabled",
                     "encrypted": bool(credentials.get(item["id"]))} for item in outputs if not item["enabled"]]
        return enabled, disabled, credentials

    def start(self, outputs, credentials=None, primary_url=None, input_port=0, commit=None):
        enabled, disabled, credentials = self._configuration(outputs, credentials, primary_url)
        with self.lifecycle:
            self.stop()
            self.outputs = []
            self.input_bytes = self.input_packets = self.invalid_packets = 0
            self.last_input = None
            self.disabled = disabled
            if not enabled:
                return None
            receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                receiver.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2 * 1024 * 1024)
                receiver.bind(("127.0.0.1", input_port))
                receiver.settimeout(0.1)
                self.finished = threading.Event()
                self.receiver = receiver
                for item in enabled:
                    self.outputs.append(_Output(item, self.root, credentials.get(item["id"], ""), self.sink_factory, active=False))
                url = f"udp://127.0.0.1:{receiver.getsockname()[1]}"
                if commit:
                    commit(url)
                for output in self.outputs:
                    output.activated.set()
                self.thread = threading.Thread(target=self._receive, name="output-ingest", daemon=True)
                self.thread.start()
                return url
            except BaseException:
                receiver.close()
                self.stop()
                raise

    def reconfigure(self, outputs, credentials=None, primary_url=None, commit=None):
        """Keep unchanged workers, counters and ingress; persist before switching."""
        enabled, disabled, credentials = self._configuration(outputs, credentials, primary_url)
        with self.lifecycle:
            if not self.thread or not self.thread.is_alive() or self.finished.is_set():
                raise TransportError("outputs", "Il distributore delle uscite non è attivo.")
            previous = {item.config["id"]: item for item in self.outputs}
            current, staged = [], []
            try:
                for config in enabled:
                    old = previous.get(config["id"])
                    key = credentials.get(config["id"], "")
                    if (old and old.thread.is_alive() and not old.finished.is_set() and old.key == key
                            and all(old.config[name] == value for name, value in config.items() if name != "name")):
                        current.append(old)
                    else:
                        output = _Output(config, self.root, key, self.sink_factory, active=False)
                        staged.append(output)
                        current.append(output)
                if commit:
                    commit()
            except BaseException:
                for output in staged:
                    output.stop()
                    output.thread.join(timeout=1)
                raise
            with self.lock:
                self.retired = [item for item in self.retired if item.thread.is_alive()]
                self.retired.extend(item for item in self.outputs if item not in current)
                for output, config in zip(current, enabled):
                    with output.lock:
                        output.config = config
                        output.stats["name"] = config["name"]
                self.outputs, self.disabled = current, disabled
            # Only replaced/removed destinations stop. Reception and retained
            # outputs keep running while a listener releases its socket.
            for output in self.retired:
                output.stop()
            deadline = time.monotonic() + 2
            for output in self.retired:
                output.thread.join(timeout=max(0, deadline - time.monotonic()))
            for output in staged:
                output.activated.set()

    def _receive(self):
        while not self.finished.is_set():
            try:
                data = self.receiver.recv(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data or len(data) % TS_PACKET or any(data[index] != 0x47 for index in range(0, len(data), TS_PACKET)):
                with self.lock:
                    self.invalid_packets += 1
                continue
            with self.lock:
                self.input_bytes += len(data)
                self.input_packets += 1
                self.last_input = time.monotonic()
                outputs = tuple(self.outputs)
            for offset in range(0, len(data), DATAGRAM_SIZE):
                packet = data[offset:offset + DATAGRAM_SIZE]
                for output in outputs:
                    output.offer(packet)

    def stop(self):
        with self.lifecycle:
            self.finished.set()
            if self.receiver is not None:
                self.receiver.close()
            outputs = self.outputs + self.retired
            for output in outputs:
                output.stop()
            if self.thread is not None:
                self.thread.join(timeout=1)
            deadline = time.monotonic() + 2
            for output in outputs:
                output.thread.join(timeout=max(0, deadline - time.monotonic()))
            if any(output.thread.is_alive() for output in outputs):
                raise RuntimeError("Una destinazione non ha completato l'arresto.")
            self.receiver = self.thread = None
            self.retired = []

    def state(self):
        with self.lock:
            age = None if self.last_input is None else max(0, time.monotonic() - self.last_input)
            return {"running": bool(self.thread and self.thread.is_alive()),
                    "input_status": "waiting" if age is None else "live" if age < 2 else "stale",
                    "input_age_seconds": round(age, 2) if age is not None else None,
                    "input_bytes": self.input_bytes, "input_packets": self.input_packets,
                    "invalid_packets": self.invalid_packets,
                    "outputs": [item.state() for item in self.outputs] + list(self.disabled)}
