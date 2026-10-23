#!/usr/bin/env python3
"""Console locale OBE: configurazione validata e processi isolati per canale."""

import argparse
import ipaddress
from collections import deque
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import ssl
from http.cookies import SimpleCookie, CookieError
import subprocess
import tempfile
import threading
import time
import unicodedata
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, quote, quote_plus, unquote, urlsplit
from preview import Preview
from live_preview import LivePreview
import credentials
import configuration
import vapoursynth_script
import inputs
import transport
from transport_client import Client as TransportClient
import profiles
import recovery
import delivery
from auth import Auth, AuthError, COOKIE_NAME
from diagnostics import Diagnostics, signal_diagnostics

from monitoring import Alarms, History, PROFILE_FIELDS, RULES, SAMPLE_LIMIT, SRTStats, history_csv, system_sample, utc_now


VERSION = "0.9.1"
RESTART_DELAYS = (2, 4, 8)
TELEMETRY_COUNTERS = ("video_frames", "video_bytes", "output_bytes", "output_datagrams", "output_errors")
TELEMETRY_FIELDS = {"schema", "monotonic_ns", *TELEMETRY_COUNTERS, "video_queue_frames"}
TELEMETRY_MAX_AGE = 5
DEFAULT_CONFIG = {
    "name": "Banco 1080i", "input_url": "udp://127.0.0.1:12000",
    "output_url": "udp://239.255.0.1:13000", "video_bitrate_kbps": 8000,
    "mux_bitrate_kbps": 9500, "vbv_buffer_kbits": 8000, "preset": "veryfast",
    "gop_frames": 50, "bframes": 2, "lookahead_frames": 20, "threads": 4,
    "audio_bitrate_kbps": 192,
}
BASE_FIELDS = set(DEFAULT_CONFIG)
DEFAULT_CONFIG.update(configuration.EXTENDED_DEFAULTS)
DEFAULT_CONFIG.update(vapoursynth_script.DEFAULTS)
DEFAULT_CONFIG.update(inputs.INPUT_DEFAULTS)
DEFAULT_CONFIG["outputs"] = []
DEFAULT_CONFIG.update(recovery.DEFAULTS)
PROFILES = [
    {"id": "hd", "label": "HD · 9,5 Mbit/s", "config": dict(DEFAULT_CONFIG)},
    {"id": "sd", "label": "SD · 6,5 Mbit/s", "config": {
        **DEFAULT_CONFIG, "name": "Banco SD", "video_bitrate_kbps": 5000,
        "mux_bitrate_kbps": 6500, "vbv_buffer_kbits": 5000, "gop_frames": 25,
    }},
    {"id": "low-latency", "label": "Bassa latenza · 6,5 Mbit/s", "config": {
        **DEFAULT_CONFIG, "name": "Bassa latenza", "video_bitrate_kbps": 5000,
        "mux_bitrate_kbps": 6500, "vbv_buffer_kbits": 5000, "gop_frames": 25,
        "preset": "ultrafast", "bframes": 0, "lookahead_frames": 0, "threads": 2,
    }},
]
PRESETS = {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium",
           "slow", "slower", "veryslow"}
CREDENTIAL_KEY = re.compile(r"pass(?:word|phrase)?|secret|token|auth|credential|crypto|encrypt|api.?key|pbkeylen|keylen|user(?:name)?|login|^key$", re.I)
ANSI = re.compile(r"\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]")
VIDEO = re.compile(r"^Input-stream-id: (\d+) - Video: .*? (\d+)x(\d+)([ip]) (\d+/\d+)fps\b")
AUDIO = re.compile(r"^Input-stream-id: (\d+) - Audio: (.*?)(?: - SDI audio pair: (\d+))?$")
VS_INPUT = re.compile(r"(?:^|obecli>\s*|\.{2,}|\[filter_vapoursynth\]:\s*)OBE_VS_INPUT (.*)$")
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/input-url.js": ("input-url.js", "text/javascript; charset=utf-8"),
    "/overview.js": ("overview.js", "text/javascript; charset=utf-8"),
    "/delivery.js": ("delivery.js", "text/javascript; charset=utf-8"),
    "/live-preview.js": ("live-preview.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/assets/plex-sans.woff2": ("assets/plex-sans.woff2", "font/woff2"),
    "/assets/plex-sans-semibold.woff2": ("assets/plex-sans-semibold.woff2", "font/woff2"),
    "/assets/plex-mono.woff2": ("assets/plex-mono.woff2", "font/woff2"),
    "/assets/FONT-LICENSE.txt": ("assets/FONT-LICENSE.txt", "text/plain; charset=utf-8"),
}


class APIError(Exception):
    def __init__(self, status, code, message, fields=None):
        super().__init__(message)
        self.status, self.code, self.message, self.fields = status, code, message, fields

    def body(self):
        error = {"code": self.code, "message": self.message}
        if self.fields:
            error["fields"] = self.fields
        return {"error": error}


def invalid(field, message):
    raise APIError(422, "invalid_config", "Controlla i campi della configurazione.", {field: message})


def control(character):
    return unicodedata.category(character) in {"Cc", "Cf", "Cs"}


def validate_url(value, field, output=False):
    if not isinstance(value, str) or not 1 <= len(value) <= 800:
        invalid(field, "Inserisci un URL di rete valido (massimo 800 caratteri).")
    decoded = value
    # Controllare anche separatori e chiavi percent-encoded, senza trasformare l'URL usato.
    for _ in range(8):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    else:
        invalid(field, "Codifica URL eccessivamente annidata.")
    if any(c.isspace() or control(c) for c in decoded) or any(c in decoded for c in ",\\\"'<>`|"):
        invalid(field, "L'URL contiene separatori o caratteri non consentiti.")
    try:
        parts = urlsplit(value)
        plain = urlsplit(decoded)
        schemes = {"udp"} if output else {"udp", "rtp", "http", "https", "rtsp", "tcp", "srt", "rtmp"}
        if parts.scheme not in schemes or not parts.hostname or not plain.hostname:
            raise ValueError()
        if parts.username is not None or plain.username is not None or parts.fragment or plain.fragment:
            raise ValueError()
        if parts.port is not None and not 1 <= parts.port <= 65535:
            raise ValueError()
        if parts.scheme in {"udp", "rtp", "tcp", "srt"} and parts.port is None:
            raise ValueError()
        if parts.scheme == "rtmp" and len(parts.path.strip("/").split("/")) < 2:
            raise ValueError()
        query = parse_qsl(plain.query, keep_blank_values=True)
        if len(query) != len({key for key, _ in query}) or any(CREDENTIAL_KEY.search(key) for key, _ in query):
            raise ValueError()
        if re.search(r"[%;]", plain.netloc):
            raise ValueError()
    except ValueError:
        invalid(field, "Protocollo, indirizzo o porta non validi; credenziali e query crypto non ammesse.")
    return parts.scheme + ":" + value.split(":", 1)[1]


def udp_output_target(url):
    """Keep multicast on the local LAN unless the profile specifies a TTL."""
    parts = urlsplit(url)
    try:
        multicast = parts.scheme == "udp" and ipaddress.ip_address(parts.hostname).is_multicast
    except (TypeError, ValueError):
        multicast = False
    if multicast and not any(key == "ttl" for key, _ in parse_qsl(parts.query, keep_blank_values=True)):
        return url + ("&" if parts.query else "?") + "ttl=1"
    return url


def validate_name(name):
    if not isinstance(name, str) or not name.strip() or len(name) > 64 or any(control(c) for c in name):
        invalid("name", "Usa un nome da 1 a 64 caratteri senza controlli.")
    return name


def validate_config(config):
    if isinstance(config, dict):
        config = {key: value for key, value in config.items()
                  if key not in {"vapoursynth_width", "vapoursynth_resize_kernel"}}
    if not isinstance(config, dict) or not BASE_FIELDS <= set(config) <= set(DEFAULT_CONFIG):
        raise APIError(422, "invalid_config", "Configurazione incompleta o con campi sconosciuti.")
    config = dict(DEFAULT_CONFIG, **config)
    validate_name(config["name"])
    config["input_url"] = validate_url(config["input_url"], "input_url")
    config["output_url"] = validate_url(config["output_url"], "output_url", output=True)
    for field, limits in {
        "video_bitrate_kbps": (128, 50000), "mux_bitrate_kbps": (128, 100000),
        "vbv_buffer_kbits": (128, 100000), "gop_frames": (1, 300), "bframes": (0, 8),
        "lookahead_frames": (0, 100), "threads": (1, 32), "audio_bitrate_kbps": (16, 640),
    }.items():
        value = config[field]
        if type(value) is not int or not limits[0] <= value <= limits[1]:
            invalid(field, f"Inserisci un intero tra {limits[0]} e {limits[1]}.")
    if not isinstance(config["preset"], str) or config["preset"] not in PRESETS:
        invalid("preset", "Preset AVC non supportato.")
    try:
        config = configuration.validate_extended(config)
        vapoursynth_script.validate(config)
        config.update(inputs.validate_input(config))
        config["outputs"] = transport.validate_outputs(config["outputs"])
        transport.primary_output(config["output_url"])
        if any(item["enabled"] for item in config["outputs"]):
            destinations = {Channels.output_target(config["output_url"])}
            for index, item in enumerate(config["outputs"]):
                if not item["enabled"]:
                    continue
                target = Channels.output_target(item["url"])
                if target in destinations:
                    invalid(f"outputs.{index}.url", "Ogni destinazione deve usare un endpoint distinto, incluso il monitor UDP.")
                destinations.add(target)
    except (configuration.ConfigurationError, inputs.InputError, transport.TransportError) as error:
        invalid(error.field, error.message)
    try:
        recovery.validate(config)
    except ValueError as error:
        invalid(str(error), "Impostazione di recupero non valida.")
    return config


def source_identity(config):
    kind = config.get("input_type", "url")
    return (kind, credentials.origin(config["input_url"])) if kind == "url" else (kind,)


def clean_log(message, secret=""):
    secrets = (secret,) if isinstance(secret, str) else secret
    representations = {representation for value in secrets if value
                       for representation in (value, quote(value, safe=""), quote_plus(value, safe=""), unquote(value))}
    for representation in sorted(representations, key=len, reverse=True):
        message = message.replace(representation, "[chiave rimossa]")
    message = ANSI.sub("", message)
    message = "".join(c for c in message if not control(c))
    for representation in sorted(representations, key=len, reverse=True):
        message = message.replace(representation, "[chiave rimossa]")
    message = re.sub(r"(?i)((?:passphrase|password|secret|token|api[_-]?key)\s*[=:]\s*)[^\s&,;]+", r"\1[rimosso]", message)
    message = re.sub(r"([a-z]+://)[^\s/@]+:[^\s/@]+@", r"\1[credenziali rimosse]@", message)
    return message[:1000].strip()


class Session:
    def __init__(self, config, secret, auto_restart):
        self.id = uuid.uuid4().hex
        self.config, self.secret, self.enabled = dict(config), secret, auto_restart
        self.active, self.armed, self.exhausted = True, False, False
        self.attempts, self.retry_at = 0, None
        self.cancel = threading.Event()


class Run:
    def __init__(self, process, secret, action, session=None):
        self.id = uuid.uuid4().hex
        self.process, self.secret, self.action = process, secret, action
        self.redactions = ()
        self.stream_tokens = ()
        self.transport = None
        self.output_url = None
        self.script_folder = None
        self.script_path = None
        self.vapoursynth_status = "disabled"
        self.session = session
        self.restart_attempt = session.attempts if session else 0
        self.exit_reason = None
        self.ready, self.started, self.exited = threading.Event(), threading.Event(), threading.Event()
        self.cancel = threading.Event()
        self.stop_lock = threading.Lock()
        self.video_id, self.audio_id = None, None
        self.audio_streams = []
        self.signal, self.audio = None, None
        self.expected_exit = False
        self.start_sent = False
        self.start_time = time.monotonic()
        self.cpu_sample = None
        self.metrics = {"cpu_percent": None, "memory_mb": None}
        self.encoding_time = None
        self.srt = SRTStats()
        self.srt_warned = False
        self.collector = None
        self.telemetry_response = threading.Event()
        self.telemetry_sample = None
        self.telemetry_received = None
        self.telemetry_rates = {}
        self.telemetry_failed = False
        self.telemetry_warned = False


class Panel:
    def __init__(self, root, state_dir, transport_socket=None, channel="main"):
        self.root, self.state_dir = Path(root).resolve(), Path(state_dir).resolve()
        self.instance_id = uuid.uuid4().hex
        self.lock = threading.RLock()
        self.run = None
        self.output_transport = TransportClient(transport_socket, channel) if transport_socket else None
        self.session = None
        self.apply_operation = None
        self.exit_history = deque(maxlen=10)
        self.preview = Preview(self.root)
        self.live_preview = LivePreview(self.root)
        self.preview_mode = "snapshot"
        self.closing = False
        self.alarms = Alarms()
        self.history = None
        self.monitor_cancel = threading.Event()
        self.monitor_thread = None
        self.monitor_received = None
        self.monitor_failed = False
        self.history_failed = False
        self.config, self.revision = dict(DEFAULT_CONFIG), 1
        self.credentials = dict.fromkeys(credentials.FIELDS, "")
        self.output_credentials = {}
        self.phase, self.message = "idle", "Encoder fermo"
        self.signal_state, self.signal, self.encoding_started = "unknown", None, False
        self.encoder_version, self.encrypted = None, False
        self.events, self.event_id = deque(maxlen=200), 0
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_dir.chmod(0o700)
        self.config_path = self.state_dir / "config.json"
        self.input_capabilities = inputs.capability_report(self.root)
        self.profiles = profiles.Profiles(self.state_dir)
        self.desired = recovery.DesiredState(self.state_dir)
        self.diagnostics = Diagnostics(self.root)
        self.delivery = delivery.Delivery(self.state_dir)
        if self.config_path.exists():
            try:
                if self.config_path.stat().st_size > 65536:
                    raise ValueError()
                saved = json.loads(self.config_path.read_text(encoding="utf-8"))
                if not {"revision", "config"} <= set(saved) <= {"revision", "config", "credentials", "output_credentials"} or type(saved["revision"]) is not int or saved["revision"] < 1:
                    raise ValueError()
                config = validate_config(saved["config"])
                saved_credentials = credentials.validate(saved.get("credentials", {}), config["input_url"])
                self.output_credentials = transport.merge_credentials(config["outputs"], config["outputs"], saved.get("output_credentials", {}))
                self.config_path.chmod(0o600)
                self.config, self.revision, self.credentials = config, saved["revision"], saved_credentials
            except (OSError, ValueError, TypeError, APIError):
                self.phase, self.message = "error", "Profilo salvato non valido: correggi e salva la configurazione."
        self._event("info", "Pannello pronto; ripresa automatica solo se configurata.")

    def _event(self, level, message):
        self.event_id += 1
        self.events.append({"id": self.event_id, "time": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                            "level": level, "message": message})

    def _busy(self):
        return bool(self.apply_operation or (self.session and self.session.active) or
                    (self.run is not None and (not self.run.exited.is_set() or self.phase == "stopping")))

    def _revision(self, payload):
        if type(payload.get("revision")) is not int:
            raise APIError(422, "invalid_revision", "La revisione deve essere un intero.")
        if payload["revision"] != self.revision:
            raise APIError(409, "stale_revision", "Il profilo è cambiato: ricarica la configurazione.")

    def state(self):
        with self.lock:
            run = self.run
            alive = run is not None and run.process.poll() is None
            metrics = {"cpu_percent": None, "memory_mb": None, "uptime_seconds": 0}
            if alive:
                metrics["uptime_seconds"] = round(time.monotonic() - run.start_time, 1)
                if self.monitor_received is not None and time.monotonic() - self.monitor_received <= TELEMETRY_MAX_AGE:
                    metrics.update(run.metrics)
            preview = self.live_preview.state() if self.preview_mode == "video" else dict(self.preview.state(), mode="snapshot")
            if self.phase != "running" and preview["status"] == "live":
                preview.update(status="stale", frame_id=None)
            return {"phase": self.phase, "message": self.message, "revision": self.revision,
                    "instance_id": self.instance_id,
                    "applying": self.apply_operation is not None,
                    "cleanup_pending": bool(run and not alive and not run.exited.is_set()),
                    "profile_backup": (self.state_dir / "previous.json").is_file(),
                    "capabilities": {"inputs": self.input_capabilities, "outputs": {**transport.capabilities(self.root),
                                     "independent_process": self.output_transport is not None},
                                     "passphrase_reveal": True, "output_passphrase_reveal": True,
                                     "vapoursynth_editor": True, "vapoursynth_test": True,
                                     "audio_tracks": configuration.MAX_AUDIO_TRACKS, "audio_processing": True},
                    "vapoursynth": {"status": run.vapoursynth_status if alive else "stopped"},
                    "config": dict(self.config), "profiles": PROFILES, "process_alive": alive,
                    "credentials": {field: bool(value) for field, value in self.credentials.items()},
                    "output_credentials": transport.public_credentials(self.config["outputs"], self.output_credentials),
                    "transport": (self.output_transport.state() if self.output_transport else
                                  run.transport.state() if run and run.transport else {"running": False, "outputs": []}),
                    "delivery": self.delivery.state(),
                    "encoding_started": self.encoding_started and alive,
                    "pid": run.process.pid if alive else None, "signal_state": self.signal_state,
                    "signal": dict(self.signal) if self.signal else None,
                    "signal_details": signal_diagnostics(self.signal), "system": self.diagnostics.state(), "encrypted": self.encrypted and alive,
                    "metrics": metrics, "telemetry": self._telemetry_state(run, alive),
                    "srt": self._srt_state(run, alive), "monitoring": self._monitoring_state(),
                    "recovery": self._recovery_state(), "events": list(self.events),
                    "version": {"panel": VERSION, "encoder": self.encoder_version}, "preview": preview}

    def start_preview(self, mode="snapshot"):
        with self.lock:
            if self.closing or self.phase != "running" or not self.run or self.run.process.poll() is not None:
                raise APIError(409, "encoder_not_running", "Avvia la codifica prima della preview.")
            if mode not in {"snapshot", "video"}:
                raise APIError(422, "invalid_request", "Scegli la preview video o snapshot.")
            try:
                if mode != self.preview_mode:
                    self._stop_preview()
                self.preview_mode = mode
                if mode == "video":
                    self.live_preview.start(self.config["output_url"], bool(self.signal and self.signal.get("audio")))
                else:
                    self.preview.start(self.config["output_url"])
            except ValueError as error:
                raise APIError(422, "preview_unsupported", str(error)) from None
            except OSError:
                raise APIError(503, "preview_unavailable", "FFmpeg non è disponibile nella build del pannello.") from None
            return self.state()

    def _stop_preview(self):
        self.preview.stop()
        self.live_preview.stop()

    def preview_media(self, query, segment=False):
        try:
            values = dict(reject_duplicates(parse_qsl(query, max_num_fields=2)))
            if set(values) != ({"session", "sequence"} if segment else {"session"}):
                raise ValueError()
            if not re.fullmatch(r"[0-9a-f]{32}", values["session"]):
                raise ValueError()
            sequence = int(values["sequence"]) if segment else None
            if sequence is not None and not 1 <= sequence < 2**31:
                raise ValueError()
        except (ValueError, KeyError):
            raise APIError(422, "invalid_preview_request", "Sessione o segmento preview non validi.") from None
        with self.lock:
            if self.preview_mode != "video" or self.phase != "running":
                raise APIError(409, "preview_not_ready", "Preview video non disponibile.")
            try:
                return self.live_preview.media(values["session"], sequence)
            except ValueError:
                raise APIError(409, "preview_expired", "Sessione preview scaduta: ricarica il monitor.") from None

    def preview_frame(self):
        with self.lock:
            frame = self.preview.jpeg() if self.phase == "running" and self.run and self.run.process.poll() is None else None
            if frame is None:
                raise APIError(503, "preview_not_ready", "Nessuna immagine aggiornata disponibile.")
            return frame

    def _srt_state(self, run, alive):
        return (run.srt if run else SRTStats()).view(
            self.config["input_type"] == "url" and urlsplit(self.config["input_url"]).scheme == "srt", alive and run.started.is_set(),
            time.monotonic(), (run.encoding_time or run.start_time) if run else 0)

    def _monitoring_state(self):
        age = None if self.monitor_received is None else max(0, time.monotonic() - self.monitor_received)
        return {"status": "live" if age is not None and age <= TELEMETRY_MAX_AGE and not self.monitor_failed else "unavailable",
                "age_seconds": round(age, 2) if age is not None else None,
                "sample_interval_seconds": 1, "sample_limit": SAMPLE_LIMIT,
                "history_available": bool(self.history and self.history.available),
                "samples": self.history.count if self.history else 0,
                "active_alarms": list(self.alarms.active.values()), "rules": RULES}

    def start_monitoring(self):
        self.history = History(self.state_dir / "monitoring.sqlite3")
        # A previous process is not being observed after a panel restart. Close
        # its open incidents explicitly, without claiming the signal recovered.
        try:
            opened = {}
            for event in self.history.read(1)["alarms"]:
                if event["state"] == "active":
                    opened[event["code"]] = event
                else:
                    opened.pop(event["code"], None)
            self.history.append(None, [{**event, "id": uuid.uuid4().hex, "time": utc_now(),
                                       "state": "resolved", "reason": "monitor_restarted", "since": event["time"]}
                                      for event in opened.values()])
        except OSError:
            pass
        self.monitor_thread = threading.Thread(target=self._monitor, daemon=True)
        self.monitor_thread.start()

    def _process_metrics(self, run, now):
        run.metrics = {"cpu_percent": None, "memory_mb": None}
        try:
            stat = Path(f"/proc/{run.process.pid}/stat").read_text().rsplit(")", 1)[1].split()
            cpu = (int(stat[11]) + int(stat[12])) / os.sysconf("SC_CLK_TCK")
            run.metrics["memory_mb"] = round(int(stat[21]) * os.sysconf("SC_PAGE_SIZE") / 1048576, 1)
            if run.cpu_sample and now > run.cpu_sample[0]:
                run.metrics["cpu_percent"] = round(max(0, (cpu - run.cpu_sample[1]) / (now - run.cpu_sample[0]) * 100), 1)
            run.cpu_sample = (now, cpu)
        except (OSError, ValueError, IndexError):
            pass

    def _sample_monitoring(self):
        self.diagnostics.sample()
        with self.lock:
            now, run = time.monotonic(), self.run
            if run and run.process.poll() is None:
                self._process_metrics(run, now)
            self.monitor_received = now
            state = self.state()
            context = {key: self.config[key] for key in PROFILE_FIELDS}
            context["name"] = clean_log(context["name"], self._secrets(run))
            context["input_protocol"] = (urlsplit(self.config["input_url"]).scheme
                                         if self.config["input_type"] == "url" else self.config["input_type"])
            fps = None
            if self.signal:
                numerator, denominator = map(int, self.signal["fps"].split("/"))
                fps = numerator / denominator if denominator else None
            sample = {key: state[key] for key in ("phase", "process_alive", "encoding_started", "signal_state",
                      "revision", "version", "metrics", "telemetry", "srt")}
            sample.update(time=utc_now(), run_id=run.id if run else None,
                          session_id=run.session.id if run and run.session else None,
                          context=context, source_fps=fps,
                          system=system_sample(state["system"], self.instance_id))
            transitions = self.alarms.evaluate(sample, now)
            sample["active_alarms"] = list(self.alarms.active)
            for event in transitions:
                self._event(event["level"] if event["state"] == "active" else "info",
                            f"{'Allarme' if event['state'] == 'active' else 'Chiusura allarme'}: {event['label']}.")
        # No disk I/O under the encoder lock: a busy/full disk cannot block Stop.
        okay = self.history.append(sample, transitions)
        with self.lock:
            if not okay and not self.history_failed:
                self._event("warning", "Storico non disponibile sul disco; codifica e allarmi locali continuano. Nuovo tentativo tra 10 s.")
            elif okay and self.history_failed:
                self._event("info", "Scrittura dello storico ripristinata; eventuali campioni mancanti non sono ricostruiti.")
            self.history_failed = not okay

    def _monitor(self):
        while not self.monitor_cancel.is_set():
            next_sample = time.monotonic() + 1
            try:
                self._sample_monitoring()
                self.monitor_failed = False
            except Exception:
                with self.lock:
                    if not self.monitor_failed:
                        self._event("warning", "Campionamento locale non riuscito; le misure scadono dopo 5 s. La codifica continua.")
                    self.monitor_failed = True
            if self.monitor_cancel.wait(max(0, next_sample - time.monotonic())):
                break

    def read_history(self, query):
        try:
            params = dict(reject_duplicates(parse_qsl(query, keep_blank_values=True, max_num_fields=2)))
            if set(params) - {"limit", "format"} or params.get("format", "json") not in {"json", "csv"}:
                raise ValueError()
            raw_limit = params.get("limit", "120")
            if not re.fullmatch(r"[0-9]{1,4}", raw_limit) or not 1 <= int(raw_limit) <= SAMPLE_LIMIT:
                raise ValueError()
        except ValueError:
            raise APIError(422, "invalid_history_query", "Usa limit tra 1 e 1800 e format json oppure csv.") from None
        try:
            history = self.history
            if history is None:
                raise OSError()
            data = history.read(int(raw_limit))
        except OSError:
            raise APIError(503, "history_unavailable", "Lo storico sul disco non è disponibile. La codifica continua.") from None
        return data, params.get("format")

    def _recovery_state(self):
        session = self.session
        remaining = None if not session or session.retry_at is None else round(max(0, session.retry_at - time.monotonic()), 1)
        history = [dict(entry) for entry in self.exit_history]
        return {"enabled": bool(session and session.enabled), "active": bool(session and session.active),
                "attempts": session.attempts if session else 0, "max_attempts": len(self._restart_delays()),
                "next_retry_seconds": remaining, "exhausted": bool(session and session.exhausted),
                "last_exit": history[-1] if history else None, "history": history}

    def _restart_delays(self):
        return recovery.delays(self.session.config if self.session else self.config, RESTART_DELAYS)

    def _remember(self, running):
        try:
            self.desired.write(running and self.config["resume_on_restart"], bool(self.session and self.session.enabled))
        except (OSError, ValueError):
            self._event("error", "Impossibile salvare lo stato desiderato: ripresa dopo riavvio del pannello non garantita.")

    def _cancel_session(self):
        if self.session:
            self.session.active = False
            self.session.cancel.set()
            self.session.retry_at, self.session.secret = None, ""

    def _record_exit(self, run, reason=None, action="start"):
        code = run.process.returncode if run else None
        signame = None
        if code is not None and code < 0:
            try:
                signame = signal.Signals(-code).name
            except ValueError:
                signame = f"SIG{-code}"
        entry = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                 "pid": run.process.pid if run else None, "action": run.action if run else action,
                 "returncode": code, "signal": signame, "expected": bool(run and run.expected_exit),
                 "reason": reason or (run.exit_reason if run else None) or ("process_signal" if signame else "process_exit"),
                 "uptime_seconds": round(time.monotonic() - run.start_time, 1) if run else 0,
                 "restart_attempt": run.restart_attempt if run else self.session.attempts if self.session else 0}
        self.exit_history.append(entry)
        return entry

    def _schedule_restart(self):
        session = self.session
        if not session or not session.active or not session.enabled or not session.armed or self.closing:
            self._cancel_session()
            return
        if session.attempts >= len(self._restart_delays()):
            session.exhausted = True
            self._cancel_session()
            self.phase, self.message = "error", "Recupero interrotto: esauriti i tentativi configurati. Controlla la diagnostica."
            self._event("error", self.message)
            return
        delay = self._restart_delays()[session.attempts]
        session.retry_at = time.monotonic() + delay
        self.phase = "restart_wait"
        self.message = f"Processo terminato; riavvio {session.attempts + 1}/{len(self._restart_delays())} tra {delay} secondi."
        self._event("warning", self.message)
        threading.Thread(target=self._restart, args=(session, delay), daemon=True).start()

    def _restart(self, session, delay):
        if session.cancel.wait(delay):
            return
        with self.lock:
            if session is not self.session or not session.active or self.closing:
                return
            session.retry_at = None
            session.attempts += 1
            self._event("warning", f"Avvio automatico {session.attempts}/{len(self._restart_delays())}.")
            try:
                self._launch("start", session.config, session.secret, session)
            except APIError as error:
                self.phase, self.message = "error", error.message
                self._record_exit(None, error.code)
                self._event("error", error.message)
                self._schedule_restart()

    def _telemetry_state(self, run, alive):
        result = {key: None for key in (*TELEMETRY_COUNTERS, "video_queue_frames",
                  "video_bitrate_kbps", "output_bitrate_kbps", "video_fps", "video_qp_aq", "video_pvmaf", "interval_seconds", "age_seconds")}
        result["status"] = "idle"
        result["audio"] = None
        if not alive:
            return result
        result["status"] = "unavailable" if run.telemetry_failed else "waiting"
        if run.telemetry_sample is None:
            return result
        age = max(0, time.monotonic() - run.telemetry_received)
        result["age_seconds"] = round(age, 2)
        result["status"] = "stale" if age > TELEMETRY_MAX_AGE else "live"
        if result["status"] == "live":
            result.update({key: run.telemetry_sample[key] for key in (*TELEMETRY_COUNTERS, "video_queue_frames")})
            result["video_qp_aq"] = run.telemetry_sample.get("video_qp_aq")
            result["video_pvmaf"] = run.telemetry_sample.get("video_pvmaf")
            if "audio" in run.telemetry_sample:
                result["audio"] = []
                for meter in run.telemetry_sample["audio"]:
                    current = {**meter, "age_ms": meter["age_ms"] + round(age * 1000)}
                    current["status"] = "live" if current["age_ms"] <= 1500 else "stale"
                    if current["status"] == "stale":
                        current.update(peak_dbfs=None, rms_dbfs=None, clipped=None)
                    result["audio"].append(current)
            result.update(run.telemetry_rates)
        return result

    def _telemetry_line(self, run, payload):
        run.telemetry_response.set()
        try:
            sample = json.loads(payload, object_pairs_hook=reject_duplicates)
            if not isinstance(sample, dict) or type(sample.get("schema")) is not int or sample["schema"] not in (1, 2, 3):
                raise ValueError()
            fields = TELEMETRY_FIELDS | ({"video_qp_aq", "video_pvmaf"} if sample["schema"] >= 2 else set())
            if sample["schema"] == 3:
                fields |= {"audio"}
            if set(sample) != fields:
                raise ValueError()
            if sample["schema"] == 3:
                meters = sample["audio"]
                if not isinstance(meters, list) or len(meters) > configuration.MAX_AUDIO_TRACKS:
                    raise ValueError()
                used = set()
                for meter in meters:
                    if not isinstance(meter, dict) or set(meter) != {"output_id", "input_id", "peak_dbfs", "rms_dbfs", "clipped", "age_ms", "normalizing"}:
                        raise ValueError()
                    for field, low, high in (("output_id", 1, 8), ("input_id", 0, 2**31 - 1), ("age_ms", 0, 2**64 - 1)):
                        if type(meter[field]) is not int or not low <= meter[field] <= high:
                            raise ValueError()
                    if meter["output_id"] in used or type(meter["normalizing"]) is not bool:
                        raise ValueError()
                    used.add(meter["output_id"])
                    for field in ("peak_dbfs", "rms_dbfs"):
                        if not isinstance(meter[field], list) or len(meter[field]) != 2 or any(type(value) not in (int, float) or not math.isfinite(value) or not -120 <= value <= 0 for value in meter[field]):
                            raise ValueError()
                    if not isinstance(meter["clipped"], list) or len(meter["clipped"]) != 2 or any(type(value) is not bool for value in meter["clipped"]):
                        raise ValueError()
            for key, limit in (("video_qp_aq", 51), ("video_pvmaf", 100)):
                value = sample.get(key)
                if value is not None and (type(value) not in (int, float) or not 0 <= value <= limit or sample["video_frames"] is None):
                    raise ValueError()
            video_keys = ("video_frames", "video_bytes", "video_queue_frames")
            video_missing = all(sample[key] is None for key in video_keys)
            for key in TELEMETRY_FIELDS - {"schema"}:
                if video_missing and key in video_keys:
                    continue
                if type(sample[key]) is not int or not 0 <= sample[key] < 2**64:
                    raise ValueError()
            previous = run.telemetry_sample
            rates = {}
            if previous:
                elapsed = (sample["monotonic_ns"] - previous["monotonic_ns"]) / 1e9
                if elapsed <= 0:
                    raise ValueError()
                for key in TELEMETRY_COUNTERS:
                    if previous[key] is not None and sample[key] is not None and sample[key] < previous[key]:
                        raise ValueError()
                rates["interval_seconds"] = round(elapsed, 3)
                for counter, metric, factor in (("video_bytes", "video_bitrate_kbps", .008),
                                                ("output_bytes", "output_bitrate_kbps", .008),
                                                ("video_frames", "video_fps", 1)):
                    if sample[counter] is not None and previous[counter] is not None:
                        rates[metric] = round((sample[counter] - previous[counter]) * factor / elapsed, 2)
            run.telemetry_sample, run.telemetry_rates = sample, rates
            run.telemetry_received, run.telemetry_failed = time.monotonic(), False
        except (ValueError, TypeError, KeyError, RecursionError):
            run.telemetry_failed = True
            if not run.telemetry_warned:
                self._event("warning", "Campione di telemetria non valido; le misure precedenti scadono dopo 5 secondi.")
                run.telemetry_warned = True

    def _monitor_telemetry(self, run):
        # One request at a time: a stalled or older CLI cannot fill the stdin pipe.
        while not run.cancel.is_set() and not run.exited.is_set():
            run.telemetry_response.clear()
            if not self._send(run, "show telemetry\n"):
                return
            if not self._await(run, run.telemetry_response, TELEMETRY_MAX_AGE):
                with self.lock:
                    if run is self.run and not run.cancel.is_set() and not run.exited.is_set():
                        run.telemetry_failed = True
                        self._event("warning", "Telemetria del flusso non disponibile: OBE non ha risposto. La codifica continua.")
                return
            if run.cancel.wait(1):
                return

    def _prepare_config(self, payload, applying=False):
        # Caller holds self.lock through validation and persistence.
        required = {"revision", "config", "instance_id"} if applying else {"revision", "config"}
        if not isinstance(payload, dict) or not required <= set(payload) <= required | {"credentials", "output_credentials"}:
            raise APIError(422, "invalid_request", "Sono richiesti " + ", ".join(sorted(required)) + "; credentials è facoltativo.")
        config = validate_config(payload["config"])
        if self.closing:
            raise APIError(503, "panel_closing", "Il canale si sta arrestando o è stato eliminato.")
        self._revision(payload)
        if applying:
            if not isinstance(payload["instance_id"], str):
                raise APIError(422, "invalid_request", "instance_id deve essere una stringa.")
            if payload["instance_id"] != self.instance_id:
                raise APIError(409, "stale_channel", "Il canale è cambiato: ricarica prima di applicare.")
        saved_credentials = self.credentials if source_identity(config) == source_identity(self.config) else {}
        updates = payload.get("credentials", {})
        try:
            if not isinstance(updates, dict) or set(updates) - set(credentials.FIELDS):
                raise credentials.CredentialError("query", "Campi delle credenziali non validi.")
            saved_credentials = credentials.validate(dict(saved_credentials, **updates), config["input_url"])
            if config["input_type"] != "url" and any(saved_credentials.values()):
                raise credentials.CredentialError("query", "Le credenziali di rete richiedono un ingresso URL.")
        except credentials.CredentialError as error:
            invalid("credentials." + error.field, str(error))
        input_fields = ("input_url", *inputs.INPUT_DEFAULTS)
        if self.signal and all(config[key] == self.config[key] for key in input_fields):
            try:
                configuration.video_options(config, 0, self.signal)
            except configuration.ConfigurationError as error:
                invalid(error.field, error.message)
        try:
            saved_outputs = transport.merge_credentials(self.config["outputs"], config["outputs"], self.output_credentials, payload.get("output_credentials"))
        except transport.TransportError as error:
            invalid(error.field, error.message)
        if any(item["enabled"] and item["protocol"] == "srt" for item in config["outputs"]) and not transport.capabilities(self.root)["srt"]:
            invalid("outputs", "Gateway SRT non compilato: esegui webpanel/build-srt-output.sh.")
        return config, saved_credentials, saved_outputs

    def _persist_config(self, config, saved_credentials, saved_outputs, backup=True):
        path = None
        try:
            if backup:
                self.profiles.backup(self.config, self.credentials, self.output_credentials)
            fd, path = tempfile.mkstemp(prefix=".config-", dir=self.state_dir)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"revision": self.revision + 1, "config": config, "credentials": saved_credentials, "output_credentials": saved_outputs}, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(path, self.config_path)
            path = None
        except (OSError, profiles.ProfileError):
            raise APIError(500, "save_failed", "Impossibile salvare il profilo sul disco.") from None
        finally:
            if path:
                Path(path).unlink(missing_ok=True)
        self.config, self.revision = config, self.revision + 1
        self.credentials = saved_credentials
        self.output_credentials = saved_outputs
        self._event("info", "Configurazione salvata." if backup else "Canale rinominato.")

    def save_config(self, payload):
        with self.lock:
            config, saved_credentials, saved_outputs = self._prepare_config(payload)
            if self._busy():
                raise APIError(409, "encoder_busy", "Ferma l'encoder prima di modificare il profilo.")
            self._persist_with_transport(config, saved_credentials, saved_outputs)
            self.phase, self.message = "idle", "Profilo salvato"
            self.signal, self.signal_state = None, "unknown"
            return self.state()

    def apply_config(self, payload):
        with self.lock:
            config, saved_credentials, saved_outputs = self._prepare_config(payload, applying=True)
            restart = bool(self.session and self.session.active)
            if self.apply_operation or self.phase == "stopping" or (self._busy() and not restart):
                raise APIError(409, "encoder_busy", "Attendi la fine dell'operazione prima di applicare le modifiche.")
            output_only = (saved_credentials == self.credentials
                           and all(config[key] == value for key, value in self.config.items()
                                   if key not in {"outputs", "output_url"}))
            if output_only and (restart or self.output_transport):
                return self._apply_outputs(config, saved_credentials, saved_outputs)
            operation = threading.Event()
            secret = saved_credentials["passphrase"]
            # Keep a legacy session override only when its source is unchanged
            # and the operator has not replaced/removed the saved SRT key.
            if (restart and "passphrase" not in payload.get("credentials", {})
                    and source_identity(config) == source_identity(self.config)):
                secret = self.session.secret
            # Preserve an explicit start override unless the saved policy changes.
            auto_restart = config["auto_restart"]
            if restart and auto_restart == self.config["auto_restart"]:
                auto_restart = self.session.enabled
            session = Session(config, secret, auto_restart) if restart else None
            # A rejected profile or failed write never interrupts the old run.
            self._persist_with_transport(config, saved_credentials, saved_outputs)
            if not restart:
                self.phase, self.message = "idle", "Profilo salvato; encoder fermo"
                self.signal, self.signal_state = None, "unknown"
                return self.state()
            self.apply_operation = operation
            self._cancel_session()
            run = self.run
            if run:
                run.expected_exit, run.exit_reason = True, "config_apply"
                run.cancel.set()
            self.phase, self.message = "stopping", "Profilo salvato; arresto e riavvio con le nuove impostazioni…"
            self._event("info", self.message)
            threading.Thread(target=self._apply_restart, args=(operation, run, session), daemon=True).start()
            return self.state()

    def _persist_with_transport(self, config, saved_credentials, saved_outputs, gateway=None):
        gateway = gateway or self.output_transport
        def persist():
            self._persist_config(config, saved_credentials, saved_outputs)
        if not gateway:
            persist()
            return
        revision = self.revision
        try:
            gateway.reconfigure(config["outputs"], saved_outputs, config["output_url"], commit=persist)
        except (transport.TransportError, OSError, RuntimeError):
            if self.revision != revision:
                message = "Profilo salvato; conferma delle uscite non ricevuta. Verifica il distributore e riapplica."
            else:
                message = "Impossibile aggiornare le uscite; configurazione precedente conservata."
            raise APIError(503, "output_update_failed", message) from None

    def _apply_outputs(self, config, saved_credentials, saved_outputs):
        # The channel lock and Channels port checks cover the entire change.
        run = self.run
        primary_changed = config["output_url"] != self.config["output_url"]
        gateway = self.output_transport or (run.transport if run and not run.exited.is_set() else None)
        if gateway:
            self._persist_with_transport(config, saved_credentials, saved_outputs, gateway)
        elif run and not run.exited.is_set():
            raise APIError(409, "output_update_unavailable", "Il distributore delle uscite non è attivo.")
        else:
            # Keep the existing retry timer/policy if the source is recovering.
            self._persist_config(config, saved_credentials, saved_outputs)
        if self.session:
            self.session.config = dict(config)
        if primary_changed:
            preview = self.live_preview.state() if self.preview_mode == "video" else self.preview.state()
            was_active = bool(preview.get("pid"))
            self._stop_preview()
            if was_active and self.phase == "running":
                try:
                    self.start_preview(self.preview_mode)
                except APIError:
                    self._event("warning", "Uscite aggiornate; riattiva l’anteprima sulla nuova uscita primaria.")
        self._event("info", "Uscite aggiornate senza riavviare la codifica.")
        return {**self.state(), "apply_mode": "outputs"}

    def _apply_restart(self, operation, run, session):
        try:
            self._stop_preview()
            if run:
                self._terminate(run)
            with self.lock:
                if operation.is_set() or self.closing:
                    return
                if run and not run.exited.is_set():
                    raise APIError(500, "apply_stop_failed", "Profilo salvato, ma arresto incompleto: riavvio annullato.")
                self.session = session
                self._launch("start", self.config, session.secret, session)
                self._event("info", "Riavvio con la nuova configurazione avviato.")
        except (APIError, OSError, subprocess.TimeoutExpired) as error:
            with self.lock:
                code = error.code if isinstance(error, APIError) else "apply_stop_failed"
                self.phase, self.message = "error", "Profilo salvato, ma riavvio non riuscito. Controlla la diagnostica e riprova."
                self._record_exit(None, code)
                self._event("error", self.message)
                if isinstance(error, APIError):
                    self._event("error", error.message)
                self._cancel_session()
        finally:
            with self.lock:
                if self.session is not session:
                    session.secret = ""
                if operation.is_set() and not self.closing:
                    self._event("info", "Riavvio annullato dall'operatore; la configurazione salvata è conservata.")
                self.apply_operation = None

    def _secrets(self, run=None):
        values = (*credentials.redactions(self.credentials), *self.output_credentials.values())
        return (*values, run.secret, *run.redactions) if run else values

    def action(self, kind, payload):
        allowed = {"revision", "passphrase", "auto_restart"} if kind == "start" else {"revision", "passphrase"}
        if kind not in {"probe", "start"} or not isinstance(payload, dict) or not {"revision"} <= set(payload) <= allowed:
            raise APIError(422, "invalid_request", "Richiesta di avvio o verifica non valida.")
        if type(payload.get("auto_restart", False)) is not bool:
            raise APIError(422, "invalid_request", "auto_restart deve essere un valore booleano.")
        secret = payload.get("passphrase", "")
        if not isinstance(secret, str) or any(control(c) for c in secret) or (secret and not 10 <= len(secret.encode("utf-8")) <= 79):
            raise APIError(422, "invalid_passphrase", "La chiave SRT deve contenere 10–79 byte senza caratteri di controllo.")
        with self.lock:
            if self.closing:
                raise APIError(503, "panel_closing", "Il pannello si sta arrestando; riprova dopo il riavvio.")
            self._revision(payload)
            if self._busy():
                raise APIError(409, "encoder_busy", "È già in corso un'operazione sull'encoder.")
            secret = payload.get("passphrase", self.credentials["passphrase"])
            if secret and (self.config["input_type"] != "url" or urlsplit(self.config["input_url"]).scheme != "srt"):
                raise APIError(422, "invalid_passphrase", "La chiave è utilizzabile soltanto con un ingresso SRT.")
            self.session = Session(self.config, secret, payload.get("auto_restart", self.config["auto_restart"])) if kind == "start" else None
            try:
                self._launch(kind, self.config, secret, self.session)
            except APIError as error:
                self.phase, self.message = "error", error.message
                self._record_exit(None, error.code, kind)
                self._event("error", error.message)
                self._cancel_session()
                raise
            return self.state()

    def _launch(self, kind, config, secret, session):
        # Called under the panel lock, including retries; Stop cannot miss a new child.
        launcher, stdbuf = self.root / "run.sh", shutil.which("stdbuf")
        if not launcher.is_file() or not os.access(launcher, os.X_OK) or not stdbuf:
            raise APIError(500, "encoder_unavailable", "run.sh eseguibile e stdbuf sono necessari per avviare OBE.")
        env = dict(os.environ)
        env.pop("OBE_SRT_PASSPHRASE", None)
        env.pop("OBE_INPUT_URL", None)
        source_url = credentials.input_url(config["input_url"], self.credentials) if config["input_type"] == "url" else config["input_url"]
        is_rtmp = config["input_type"] == "url" and urlsplit(source_url).scheme == "rtmp"
        if source_url != config["input_url"] or is_rtmp:
            env["OBE_INPUT_URL"] = source_url
        if secret:
            env["OBE_SRT_PASSPHRASE"] = secret
        gateway = self.output_transport or transport.Transport(self.root)
        try:
            output_url = gateway.start(config["outputs"], self.output_credentials, config["output_url"]) if kind == "start" else None
        except (transport.TransportError, OSError, RuntimeError):
            raise APIError(503, "output_unavailable", "Impossibile predisporre le uscite configurate.") from None
        script_folder, script_path = None, None
        try:
            if kind == "start" and config["vapoursynth_enabled"]:
                script_folder, script_path = vapoursynth_script.prepare(config["vapoursynth_script"])
            process = subprocess.Popen([stdbuf, "-oL", "-eL", str(launcher)], cwd=self.root, env=env,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       start_new_session=True)
        except OSError:
            if gateway is not self.output_transport:
                gateway.stop()
            if script_folder:
                script_folder.cleanup()
            raise APIError(500, "spawn_failed", "Impossibile avviare il processo OBE.") from None
        run = self.run = Run(process, secret, kind, session)
        run.script_folder, run.script_path = script_folder, script_path
        run.vapoursynth_status = "waiting" if script_path else "disabled"
        run.transport, run.output_url = gateway, output_url or config["output_url"]
        run.redactions = (*credentials.redactions(self.credentials), config["input_url"], source_url) if config["input_type"] == "url" else credentials.redactions(self.credentials)
        if is_rtmp:
            stream = urlsplit(source_url).path.rstrip("/").rsplit("/", 1)[-1]
            run.stream_tokens = tuple(sorted({stream, unquote(stream)} - {""}, key=len, reverse=True))
        self.phase, self.message = "probing", "Verifica della sorgente in corso…"
        self.signal, self.signal_state, self.encoding_started = None, "unknown", False
        self.encrypted = bool(secret)
        self._event("info", "Verifica della sorgente avviata.")
        run.collector = threading.Thread(target=self._collect, args=(run,), daemon=True)
        run.collector.start()
        threading.Thread(target=self._configure, args=(run, dict(config)), daemon=True).start()

    def _send(self, run, text):
        with self.lock:
            if run is not self.run or run.cancel.is_set() or run.process.poll() is not None:
                return False
            try:
                run.process.stdin.write(text.encode("utf-8"))
                run.process.stdin.flush()
                return True
            except (BrokenPipeError, OSError):
                return False

    def _await(self, run, event, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if run.cancel.is_set() or run.exited.is_set():
                return False
            if event.wait(0.1):
                return True
        return False

    def _configure(self, run, config):
        try:
            command_config = (dict(config, input_url="rtmp://127.0.0.1/live")
                              if config["input_type"] == "url" and urlsplit(config["input_url"]).scheme == "rtmp" else config)
            commands = "set obe opts system-type=generic,max-probe-time=8\n" + inputs.input_commands(command_config)
            if not self._send(run, commands) or not self._await(run, run.ready, 20):
                if not run.cancel.is_set():
                    self._fail(run, "La sorgente non ha completato il probe. Controlla il segnale e gli eventi.", "probe_timeout")
                return
            if run.video_id is None or not run.signal:
                self._fail(run, "Il probe non ha rilevato un flusso video supportato.", "unsupported_input")
                return
            if run.action == "probe":
                self._finish(run, "Verifica completata; encoder fermo", error=False)
                return
            with self.lock:
                if run is not self.run or run.cancel.is_set():
                    return
                self.phase, self.message = "starting", "Avvio della codifica…"
                run.start_sent = True
            video = configuration.video_options(config, run.video_id, run.signal)
            if run.script_path:
                video = video.rstrip("\n") + f",vs-script={run.script_path}\n"
            audio = configuration.audio_commands(config, run.audio_streams)
            commands = (video + audio + "set muxer mpegts\n"
                        + configuration.mux_options(config) +
                        f"set outputs 1\nset output opts 0:type=udp,target={udp_output_target(run.output_url)}\nstart\n")
            if not self._send(run, commands) or not self._await(run, run.started, 20):
                if not run.cancel.is_set():
                    self._fail(run, "OBE non ha confermato l'avvio della codifica. Controlla gli eventi.", "start_timeout")
                return
        except (configuration.ConfigurationError, inputs.InputError, transport.TransportError) as error:
            self._fail(run, error.message, "invalid_config")
            return
        except Exception:
            self._fail(run, "L'operazione OBE non è stata completata.")
            return
        self._monitor_telemetry(run)

    def _line(self, run, raw):
        # Machine samples contain only validated numbers/null, and are never logged.
        # Redacting a passphrase that equals a JSON key would corrupt the schema.
        telemetry = re.search(r"(?:^|obecli>\s*|\.{2,})OBE_TELEMETRY (.*)$", ANSI.sub("", raw))
        srt = re.search(r"(?:^|obecli>\s*|\.{2,})OBE_SRT (.*)$", ANSI.sub("", raw))
        vs_input = VS_INPUT.search(ANSI.sub("", raw))
        with self.lock:
            if run is not self.run:
                return
            if vs_input:
                # Never retain arbitrary script output as metadata or expose it in logs.
                try:
                    value = json.loads(vs_input[1]) if len(vs_input[1]) <= 4096 else None
                    limits = {"width": (16, 4096), "height": (16, 2160),
                              "fps_num": (1, 1000000), "fps_den": (1, 1000000)}
                    props = value["props"]
                    allowed = {"_FieldBased": (0, 2), "_Matrix": (0, 14), "_Primaries": (1, 22),
                               "_Transfer": (1, 18), "_ColorRange": (0, 1), "_Range": (0, 1), "_ChromaLocation": (0, 5),
                               "_SARNum": (1, 2147483647), "_SARDen": (1, 2147483647),
                               "_DurationNum": (1, 2147483647), "_DurationDen": (1, 2147483647)}
                    if (set(value) != {*limits, "pixel_format", "props"}
                            or not all(type(value[k]) is int and low <= value[k] <= high
                                       for k, (low, high) in limits.items())
                            or value["width"] % 2 or value["height"] % 2
                            or not 1 <= value["fps_num"] / value["fps_den"] <= 120
                            or value["pixel_format"] not in ("YUV420P8", "YUV420P10", "YUV422P10")
                            or not isinstance(props, dict) or set(props) - allowed.keys()
                            or not all(type(v) is int and allowed[k][0] <= v <= allowed[k][1]
                                       for k, v in props.items())):
                        raise ValueError("invalid frame metadata")
                    if run.signal and not run.cancel.is_set():
                        run.signal["vapoursynth_input"] = value
                        self.signal = dict(run.signal)
                except (ValueError, TypeError, KeyError, RecursionError):
                    pass
                return
            if telemetry:
                if run.started.is_set() and not run.cancel.is_set():
                    self._telemetry_line(run, telemetry[1])
                return
            if srt:
                if run.started.is_set() and not run.cancel.is_set():
                    try:
                        run.srt.accept(srt[1], time.monotonic())
                    except (ValueError, TypeError, KeyError, RecursionError):
                        if not run.srt_warned:
                            self._event("warning", "Campione SRT non valido; le misure precedenti scadono dopo 5 secondi.")
                            run.srt_warned = True
                return
            line = clean_log(raw, self._secrets(run))
            for token in run.stream_tokens:
                line = re.sub(r"(?<![\w-])" + re.escape(token) + r"(?![\w-])", "[chiave rimossa]", line)
            if not line:
                return
            if run.script_path and "[filter_vapoursynth]:" in line:
                if "Script loaded." in line:
                    run.vapoursynth_status = "active"
                elif re.search(r"error|failed|could not", line, re.I):
                    run.vapoursynth_status = "error"
            self._event("warning" if re.search(r"error|failed|warning|invalid|retrying|connection lost", line, re.I) else "info", line)
            if line.startswith("Version "):
                self.encoder_version = line[:160]
            video, audio = VIDEO.match(line), AUDIO.match(line)
            if video and run.video_id is None:
                run.video_id = int(video[1])
                run.signal = {"width": int(video[2]), "height": int(video[3]), "scan": video[4], "fps": video[5],
                              "audio": run.audio, "audio_tracks": list(run.audio_streams)}
                self.signal = dict(run.signal)
            if audio and not any(stream["id"] == int(audio[1]) for stream in run.audio_streams):
                run.audio_streams.append({"id": int(audio[1]), "description": audio[2],
                                          "sdi_pair": int(audio[3]) if audio[3] else None})
                if run.audio_id is None:
                    run.audio_id, run.audio = int(audio[1]), audio[2]
                if run.signal:
                    run.signal["audio"] = run.audio
                    run.signal["audio_tracks"] = list(run.audio_streams)
                    self.signal = dict(run.signal)
            if line == "Encoder outputs:":
                run.ready.set()
            if run.cancel.is_set():
                return
            if line == "Encoding started" and run.start_sent:
                run.encoding_time = time.monotonic()
                if run.session is self.session and run.session and run.session.active:
                    run.session.armed = True
                self.encoding_started = True
                self._remember(True)
                if self.signal_state != "stopped":
                    self.phase = "reconnecting" if self.signal_state == "reconnecting" else "running"
                    self.message = "Codifica avviata" if self.phase == "running" else "Sorgente assente; riconnessione in corso…"
                run.started.set()
            # readline's prompt and encoder progress dots may share this line.
            input_event = re.search(r"(?:^|obecli>\s*|\.{2,})(URL input:.*)$", line)
            if input_event:
                line = input_event[1]
                if "receiving video" in line:
                    self.signal_state = "receiving"
                    if self.encoding_started:
                        self.phase, self.message = "running", "Codifica avviata; sorgente in ricezione"
                elif "retrying in" in line:
                    self.signal_state = "reconnecting"
                    if self.encoding_started:
                        self.phase, self.message = "reconnecting", "Sorgente assente; riconnessione in corso…"
                elif "stopped (" in line:
                    self._cancel_session()
                    run.exit_reason = "unsupported_input"
                    self.signal_state = "stopped"
                    self.phase, self.message = "error", "Il formato della sorgente richiede un nuovo probe. Ferma l'encoder."

    def _collect(self, run):
        pending, oversized = b"", False
        try:
            while True:
                chunk = os.read(run.process.stdout.fileno(), 4096)
                if not chunk:
                    break
                pending += chunk
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    if not oversized and len(line) <= 65536:
                        self._line(run, line.decode("utf-8", errors="replace"))
                    oversized = False
                if len(pending) > 65536:
                    pending, oversized = b"", True
            if pending and not oversized:
                self._line(run, pending.decode("utf-8", errors="replace"))
        except (OSError, ValueError):
            pass
        finally:
            run.process.wait()
            run.process.stdout.close()
            run.process.stdin.close()
            if run.script_folder:
                run.script_folder.cleanup()
            transport_stopped = True
            try:
                if run.transport and run.transport is not self.output_transport:
                    run.transport.stop()
            except RuntimeError:
                transport_stopped = False
            with self.lock:
                run.cancel.set()
                run.secret = ""
                run.redactions = ()
                run.stream_tokens = ()
                if run is self.run:
                    entry = self._record_exit(run)
                    self._stop_preview()
                    self.encoding_started, self.encrypted = False, False
                    if not run.expected_exit:
                        self.phase, self.message = "error", "Il processo OBE è terminato. Controlla gli eventi prima di riavviare."
                        self.signal_state = "stopped"
                        self._event("error", f"Processo OBE terminato: {entry['signal'] or 'codice ' + str(entry['returncode'])}.")
                if transport_stopped:
                    run.exited.set()
                else:
                    self._cancel_session()
                    self.phase, self.message = "error", "Uscita ancora in arresto; premi Arresta prima di riavviare."
                if transport_stopped and run is self.run and not run.expected_exit:
                    self._schedule_restart()

    def _terminate(self, run):
        with run.stop_lock:
            run.expected_exit = True
            run.cancel.set()
            if run.process.poll() is None:
                try:
                    run.process.stdin.write(b"quit\n")
                    run.process.stdin.flush()
                except (BrokenPipeError, OSError, ValueError):
                    pass
                try:
                    run.process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    run.process.terminate()
                    try:
                        run.process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        run.process.kill()
                        run.process.wait(timeout=2)
            if run.collector is not threading.current_thread():
                run.exited.wait(2)
            if not run.exited.is_set() and run.process.poll() is not None:
                if run.transport and run.transport is not self.output_transport:
                    try:
                        run.transport.stop()
                    except RuntimeError:
                        raise APIError(503, "output_stop_failed", "Uscita ancora in arresto; riprova Arresta.") from None
                run.exited.set()

    def _finish(self, run, message, error, reason=None):
        with self.lock:
            if run is not self.run or run.cancel.is_set():
                return
            self.phase, self.message = "stopping", "Arresto del processo OBE…"
            run.expected_exit = True
            run.exit_reason = reason or ("operation_failed" if error else "probe_complete")
            completion_reason = run.exit_reason
            self._cancel_session()
        self._terminate(run)
        with self.lock:
            if run is self.run and run.exit_reason == completion_reason:
                self.phase, self.message = ("error" if error else "idle"), message
                self.encoding_started, self.encrypted = False, False
                if error:
                    self.signal_state = "stopped"
                self._event("error" if error else "info", message)

    def _fail(self, run, message, reason="operation_failed"):
        self._finish(run, message, error=True, reason=reason)

    def stop(self):
        with self.lock:
            if not self.closing:
                self._remember(False)
            if self.apply_operation:
                self.apply_operation.set()
            waiting = bool(self.session and self.session.retry_at is not None)
            self._cancel_session()
            self._stop_preview()
            run = self.run
            if run is not None:
                run.exit_reason = "panel_shutdown" if self.closing else "operator_stop"
            if run is None or run.exited.is_set():
                self.phase, self.message, self.signal_state = "idle", "Encoder fermo", "stopped"
                self.encoding_started, self.encrypted = False, False
                if waiting:
                    self._event("info", "Riavvio automatico annullato; encoder fermo.")
                return self.state()
            self.phase, self.message = "stopping", "Arresto del processo OBE…"
            run.expected_exit = True
            run.cancel.set()
        self._terminate(run)
        with self.lock:
            if run is self.run:
                self.phase, self.message, self.signal_state = "idle", "Encoder fermo", "stopped"
                self.encoding_started, self.encrypted = False, False
                self._event("info", "Encoder arrestato.")
            return self.state()

    def close(self):
        with self.lock:
            self.closing = True
        self.delivery.close()
        self.stop()
        self.monitor_cancel.set()
        if self.monitor_thread:
            self.monitor_thread.join(timeout=3)
        if self.history:
            try:
                self._sample_monitoring()
            finally:
                self.history.close()
                with self.lock:
                    self.history = None


def reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


class Channels:
    """Independent processes; the original API continues to address main."""
    MAX_CHANNELS = 4

    @staticmethod
    def input_ports(url):
        source = urlsplit(url)
        options = dict(parse_qsl(source.query))
        if source.scheme not in {"udp", "rtp"} and not (source.scheme == "srt" and options.get("mode") in {"listener", "rendezvous"}):
            return set()
        try:
            port = source.port if source.scheme == "srt" else int(options.get("localport", source.port))
            if source.scheme == "rtp":
                port = int(options.get("localrtpport", port))
            ports = {port} if port else set()
            if source.scheme == "rtp":
                rtcp_port = int(options.get("localrtcpport", port + 1 if port else 0))
                if rtcp_port:
                    ports.add(rtcp_port)
            if not all(1 <= value <= 65535 for value in ports):
                raise ValueError()
            return ports
        except (ValueError, TypeError):
            invalid("input_url", "La porta locale di ricezione deve essere tra 0 e 65535.")

    @staticmethod
    def output_target(url):
        target = urlsplit(url)
        host = "loopback" if target.hostname in {"localhost", "127.0.0.1", "::1"} else target.hostname
        return host, target.port

    def __init__(self, root, state_dir, transport_socket=None):
        self.root, self.state_dir = Path(root), Path(state_dir)
        self.transport_socket = transport_socket
        self.lock, self.closing = threading.RLock(), False
        self.monitoring_started = False
        self.panels = {"main": Panel(root, state_dir, transport_socket)}
        directory = self.state_dir / "channels"
        if directory.exists():
            for child in sorted(directory.iterdir()):
                if (len(self.panels) < self.MAX_CHANNELS and (child / "config.json").is_file() and not child.is_symlink()
                        and re.fullmatch(r"channel-[2-4]", child.name)):
                    self.panels[child.name] = Panel(root, child, transport_socket, child.name)

    def start_monitoring(self):
        with self.lock:
            if self.closing or self.monitoring_started:
                return
            for panel in self.panels.values():
                panel.start_monitoring()
            self.monitoring_started = True

    def resume(self):
        for channel, panel in self.panels.items():
            if not panel.config["resume_on_restart"] or panel.phase == "error":
                continue
            try:
                desired = panel.desired.read()
                if desired and desired["running"]:
                    self.action(channel, "start", {"revision": panel.revision, "auto_restart": desired["auto_restart"]})
                    panel._event("info", "Ripresa richiesta dal profilo dopo il riavvio del pannello.")
            except (APIError, OSError, ValueError):
                panel._event("error", "Ripresa automatica non riuscita; verifica configurazione, risorse e stato desiderato.")

    def get(self, channel):
        with self.lock:
            if channel not in self.panels:
                raise APIError(404, "channel_not_found", "Canale non trovato.")
            return self.panels[channel]

    def state(self):
        with self.lock:
            result = []
            for key, panel in self.panels.items():
                with panel.lock:
                    result.append({"id": key, "name": panel.config["name"], "phase": panel.phase,
                                   "process_alive": panel.run is not None and panel.run.process.poll() is None})
            return {"limit": self.MAX_CHANNELS, "channels": result}

    def create(self, payload):
        if not isinstance(payload, dict) or set(payload) != {"name"}:
            raise APIError(422, "invalid_request", "Indica soltanto il nome del nuovo canale.")
        config = validate_config(dict(DEFAULT_CONFIG, name=payload["name"]))
        with self.lock:
            if self.closing:
                raise APIError(503, "panel_closing", "Il pannello si sta arrestando.")
            if len(self.panels) >= self.MAX_CHANNELS:
                raise APIError(409, "channel_limit", "Questo banco gestisce fino a quattro canali.")
            number = next(i for i in range(2, 5) if f"channel-{i}" not in self.panels)
            channel = f"channel-{number}"
            config.update(input_url=f"udp://127.0.0.1:{12000 + number * 10}",
                          output_url=f"udp://239.255.0.1:{13000 + number * 10}")
            panel = Panel(self.root, self.state_dir / "channels" / channel, self.transport_socket, channel)
            # A reused slot must reject old saves/starts even from API clients
            # without instance_id. Stay within JavaScript's exact integer range.
            panel.revision = uuid.uuid4().int >> 76
            panel.save_config({"revision": panel.revision, "config": config})
            if self.monitoring_started:
                panel.start_monitoring()
            self.panels[channel] = panel
            return {"id": channel, "state": panel.state()}

    def rename(self, channel, payload):
        if (not isinstance(payload, dict) or set(payload) != {"name", "revision", "instance_id"}
                or not isinstance(payload["instance_id"], str)):
            raise APIError(422, "invalid_request", "Indica nome, revision e instance_id del canale.")
        name = validate_name(payload["name"]).strip()
        with self.lock:
            if self.closing:
                raise APIError(503, "panel_closing", "Il pannello si sta arrestando.")
            panel = self.get(channel)
            with panel.lock:
                panel._revision(payload)
                if payload["instance_id"] != panel.instance_id:
                    raise APIError(409, "stale_channel", "Il canale è cambiato: ricarica prima di rinominare.")
                if panel.closing or panel.apply_operation:
                    raise APIError(409, "encoder_busy", "Attendi il termine dell'operazione prima di rinominare.")
                if name != panel.config["name"]:
                    panel._persist_config(dict(panel.config, name=name), panel.credentials, panel.output_credentials, backup=False)
                    if panel.session:
                        panel.session.config["name"] = name
                return panel.state()

    @classmethod
    def reserved_ports(cls, config):
        ports = cls.input_ports(config["input_url"]) if config.get("input_type", "url") == "url" else set()
        for item in config.get("outputs", []):
            if item["enabled"] and item["protocol"] == "srt" and item["srt_mode"] == "listener":
                ports.add(urlsplit(item["url"]).port)
        return ports

    @classmethod
    def output_targets(cls, config):
        targets = {cls.output_target(config["output_url"])}
        for item in config.get("outputs", []):
            if item["enabled"] and not (item["protocol"] == "srt" and item["srt_mode"] == "listener"):
                targets.add(cls.output_target(item["url"]))
        return targets

    def _check_ports(self, channel, config, kind):
        # Called under Channels.lock and the target Panel.lock. An apply keeps
        # both old and new ports reserved until the old process has exited.
        ports = self.reserved_ports(config)
        input_ports = self.input_ports(config["input_url"]) if config["input_type"] == "url" else set()
        if kind == "outputs":
            ports = {urlsplit(item["url"]).port for item in config["outputs"]
                     if item["enabled"] and item["protocol"] == "srt" and item["srt_mode"] == "listener"}
            input_ports = set()
        if any(item["enabled"] and item["protocol"] == "srt" and item["srt_mode"] == "listener"
               and urlsplit(item["url"]).port in input_ports for item in config["outputs"]):
            raise APIError(409, "input_in_use", "La porta SRT di uscita è già riservata all'ingresso del canale.")
        device = inputs.reservation(config) if kind != "outputs" else None
        for key, other in self.panels.items():
            if key == channel:
                continue
            with other.lock:
                if not other._busy():
                    if other.output_transport:
                        # An idle encoder still owns its independent outputs.
                        listeners = {urlsplit(item["url"]).port for item in other.config["outputs"]
                                     if item["enabled"] and item["protocol"] == "srt" and item["srt_mode"] == "listener"}
                        if ports & listeners:
                            raise APIError(409, "input_in_use", "La porta è riservata alle uscite di un altro canale.")
                    continue
                configs = [other.config]
                if other.run and not other.run.exited.is_set() and other.run.session:
                    configs.append(other.run.session.config)
                for other_config in configs:
                    if device is not None and device == inputs.reservation(other_config):
                        raise APIError(409, "input_in_use", "Questa scheda DeckLink è già usata da un altro canale attivo.")
                    if ports & self.reserved_ports(other_config):
                        raise APIError(409, "input_in_use", "Usa porte di ingresso UDP/RTP/SRT listener distinte per i canali attivi.")
                    if kind in {"start", "outputs"} and self.output_targets(config) & self.output_targets(other_config):
                        raise APIError(409, "output_in_use", "Questa uscita è già usata da un altro canale attivo.")

    def save(self, channel, payload):
        with self.lock:
            panel = self.get(channel)
            with panel.lock:
                if panel.output_transport:
                    config, _, _ = panel._prepare_config(payload)
                    self._check_ports(channel, config, "outputs")
                return panel.save_config(payload)

    def restore(self, channel, payload):
        if not isinstance(payload, dict) or set(payload) != {"revision", "instance_id"}:
            raise APIError(422, "invalid_request", "Conferma il ripristino con revisione e istanza del canale.")
        with self.lock:
            panel = self.get(channel)
            with panel.lock:
                try:
                    previous = panel.profiles.previous()
                except profiles.ProfileError as error:
                    raise APIError(422, "invalid_backup", error.message) from None
                if previous is None:
                    raise APIError(404, "backup_missing", "Nessuna configurazione precedente disponibile.")
                patch = {**payload, "config": previous["config"],
                         "credentials": {key: previous["credentials"].get(key, "") for key in credentials.FIELDS},
                         "output_credentials": {item["id"]: previous["output_credentials"].get(item["id"], "")
                                                for item in previous["config"].get("outputs", [])}}
                return self.apply(channel, patch)

    def apply(self, channel, payload):
        with self.lock:
            if self.closing:
                raise APIError(503, "panel_closing", "Il pannello si sta arrestando.")
            panel = self.get(channel)
            with panel.lock:
                config, _, _ = panel._prepare_config(payload, applying=True)
                if panel.session and panel.session.active:
                    self._check_ports(channel, config, "start")
                elif panel.output_transport:
                    self._check_ports(channel, config, "outputs")
                return panel.apply_config(payload)

    def action(self, channel, kind, payload):
        with self.lock:
            if self.closing:
                raise APIError(503, "panel_closing", "Il pannello si sta arrestando.")
            panel = self.get(channel)
            with panel.lock:
                self._check_ports(channel, panel.config, kind)
                return panel.action(kind, payload)

    def delete(self, channel, payload):
        if not isinstance(payload, dict) or set(payload) != {"revision", "instance_id"} or not isinstance(payload["instance_id"], str):
            raise APIError(422, "invalid_request", "Conferma il canale con revision e instance_id.")
        with self.lock:
            if self.closing:
                raise APIError(503, "panel_closing", "Il pannello si sta arrestando.")
            if channel == "main":
                raise APIError(409, "main_channel_protected", "Il canale principale non può essere eliminato.")
            panel = self.get(channel)
            directory = self.state_dir.resolve() / "channels"
            path = directory / channel
            with panel.lock:
                panel._revision(payload)
                if payload["instance_id"] != panel.instance_id:
                    raise APIError(409, "stale_channel", "Il canale è cambiato: ricarica prima di confermare.")
                if panel._busy():
                    raise APIError(409, "encoder_busy", "Arresta il canale e annulla i riavvii prima di eliminarlo.")
                if panel.closing:
                    raise APIError(503, "panel_closing", "Il canale si sta arrestando.")
                if (directory.is_symlink() or path.is_symlink() or not path.is_dir()
                        or path.resolve().parent != directory or panel.state_dir != path.resolve()
                        or panel.config_path.is_symlink() or not panel.config_path.is_file()):
                    raise APIError(409, "unsafe_channel_path", "La directory del canale non è valida per l'eliminazione.")
                identity = path.stat()
                parent_identity = directory.stat()
                if panel.output_transport:
                    try:
                        panel.output_transport.remove()
                    except (OSError, ValueError, RuntimeError):
                        raise APIError(503, "output_stop_failed", "Impossibile rilasciare le uscite del canale; nessun dato eliminato.") from None
                panel.closing = True
            # Close collectors and SQLite before removing their files. Do not
            # hold panel.lock while joining the monitoring thread.
            panel.close()
            if panel.monitor_thread and panel.monitor_thread.is_alive():
                raise APIError(503, "channel_closing", "Chiusura del monitoraggio incompleta; nessun dato eliminato.")
            removed = directory / (".deleted-" + channel + "-" + panel.instance_id)
            try:
                current = path.lstat()
                parent = directory.lstat()
                if (directory.is_symlink() or path.is_symlink()
                        or (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino)
                        or (parent.st_dev, parent.st_ino) != (parent_identity.st_dev, parent_identity.st_ino)):
                    raise OSError("channel directory changed")
            except OSError:
                # Keep the channel closed: a changed path must not be reopened
                # by monitoring or used for a subsequent configuration save.
                raise APIError(409, "unsafe_channel_path", "La directory del canale è cambiata; nessun dato eliminato. Riavvia il pannello dopo aver verificato i percorsi.") from None
            try:
                path.rename(removed)
            except OSError:
                with panel.lock:
                    panel.closing = False
                    panel.monitor_cancel.clear()
                    if self.monitoring_started:
                        panel.start_monitoring()
                raise APIError(500, "delete_failed", "Impossibile eliminare il canale; i dati sono conservati.") from None
            del self.panels[channel]
            panel.credentials = dict.fromkeys(credentials.FIELDS, "")
            panel.output_credentials = {}
            try:
                shutil.rmtree(removed)
            except OSError:
                # The private tombstone cannot be loaded as a channel after a
                # restart, even when disk cleanup fails part-way through.
                raise APIError(500, "channel_cleanup_failed", "Canale rimosso, ma la pulizia dei dati sul disco è incompleta.") from None
            return {"deleted": channel, **self.state()}

    def close(self):
        with self.lock:
            self.closing = True
            panels = list(self.panels.values())
            for panel in panels:
                with panel.lock:
                    panel.closing = True
        workers = [threading.Thread(target=panel.close) for panel in panels]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()


class Handler(BaseHTTPRequestHandler):
    server_version = "OBEPanel/0.1"

    def setup(self):
        # TLS negotiation runs in this worker, never in the accept loop.
        self.request.settimeout(5)
        super().setup()

    def log_message(self, *args):
        pass  # I percorsi e i corpi HTTP non vengono registrati.

    def _guard(self):
        if len(self.headers.get_all("Host", [])) != 1:
            raise APIError(403, "invalid_host", "Host non consentito.")
        host = self.headers["Host"]
        scheme = "https" if isinstance(self.connection, ssl.SSLSocket) else "http"
        default_port = 443 if scheme == "https" else 80
        try:
            parts = urlsplit(scheme + "://" + host)
            if parts.hostname not in self.server.allowed_hosts or parts.username is not None or parts.path or parts.query or parts.fragment:
                raise ValueError()
            port = parts.port or default_port
        except ValueError:
            raise APIError(403, "invalid_host", "Host non consentito.") from None
        origins = self.headers.get_all("Origin", [])
        if len(origins) > 1:
            raise APIError(403, "invalid_origin", "Origine non consentita.")
        if origins:
            try:
                origin = urlsplit(origins[0])
                if origin.scheme != scheme or origin.hostname != parts.hostname or (origin.port or default_port) != port or origin.username is not None or origin.path or origin.query or origin.fragment:
                    raise ValueError()
            except ValueError:
                raise APIError(403, "invalid_origin", "Origine non consentita.") from None
        if self.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
            raise APIError(403, "cross_site", "Richiesta cross-site non consentita.")

    def _token(self):
        try:
            raw = self.headers.get_all("Cookie", [])
            if len(raw) > 1 or (raw and len(raw[0]) > 8192):
                return ""
            cookie = SimpleCookie(raw[0] if raw else "")
            return cookie[COOKIE_NAME].value if COOKIE_NAME in cookie else ""
        except CookieError:
            return ""

    def _authentication(self, path):
        auth = self.server.auth
        enabled = auth.enabled
        secure = isinstance(self.connection, ssl.SSLSocket)
        if path == "/api/session" and self.command == "GET":
            self._reply(200, {"auth_required": enabled, "authenticated": not enabled or auth.verify(self._token()), "secure": secure})
            return True
        if path == "/api/login" and self.command == "POST":
            body = self._body()
            if set(body) != {"username", "password"}:
                raise APIError(422, "invalid_request", "Sono richiesti utente e password.")
            if not enabled:
                raise APIError(409, "auth_disabled", "Configura un account prima di effettuare l'accesso.")
            token = auth.login(body["username"], body["password"], self.client_address[0])
            self._reply(200, {"auth_required": True, "authenticated": True}, cookie=auth.cookie(token, secure))
            return True
        if path == "/api/logout" and self.command == "POST":
            if self._body():
                raise APIError(422, "invalid_request", "Il logout richiede un oggetto vuoto.")
            auth.logout(self._token())
            self._reply(200, {"authenticated": False}, cookie=auth.clear_cookie(secure))
            return True
        if path.startswith("/api/") and enabled and not auth.verify(self._token()):
            raise APIError(401, "login_required", "Accedi al pannello per continuare.")
        return False

    def _body(self):
        if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) != 1:
            raise APIError(400, "invalid_body", "Lunghezza del corpo richiesta.")
        if self.headers.get_content_type() != "application/json":
            raise APIError(400, "invalid_content_type", "È richiesto un corpo application/json.")
        try:
            size = int(self.headers["Content-Length"])
            if not 0 < size <= 65536:
                raise ValueError()
            self.connection.settimeout(5)
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise ValueError()
            body = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates,
                              parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
            if not isinstance(body, dict):
                raise ValueError()
            return body
        except (ValueError, OSError, RecursionError):
            raise APIError(400, "invalid_json", "Corpo JSON non valido o superiore a 64 KiB.") from None

    def _reply(self, status, value, mime="application/json; charset=utf-8", filename=None, cookie=None):
        content = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self.wfile.write(content)

    def _dispatch(self):
        try:
            self._guard()
            path = urlsplit(self.path).path
            if self._authentication(path):
                return
            channels = self.server.channels
            if path == "/api/channels":
                if self.command == "GET":
                    self._reply(200, channels.state())
                elif self.command == "POST":
                    self._reply(201, channels.create(self._body()))
                else:
                    raise APIError(405, "method_not_allowed", "Metodo non consentito.")
                return
            match = re.fullmatch(r"/api/channels/(main|channel-[2-4])", path)
            if match and self.command in {"DELETE", "PATCH"}:
                if self.command == "DELETE":
                    self._reply(200, channels.delete(match[1], self._body()))
                else:
                    self._reply(200, channels.rename(match[1], self._body()))
                return
            channel = "main"
            if path.startswith("/api/channels/"):
                match = re.fullmatch(r"/api/channels/(main|channel-[2-4])/([a-z-]+)", path)
                if not match:
                    raise APIError(404, "not_found", "Risorsa non disponibile.")
                channel, resource = match.groups()
                path = "/api/" + resource
            panel = channels.get(channel)
            if self.command == "GET" and path == "/api/state":
                self._reply(200, panel.state())
            elif path == "/api/delivery" and self.command in {"GET", "PUT"}:
                if self.command == "GET":
                    self._reply(200, panel.delivery.state())
                else:
                    body = self._body()
                    if body.pop("instance_id", None) != panel.instance_id:
                        raise APIError(409, "stale_channel", "Il canale è cambiato: ricarica prima di continuare.")
                    self._reply(200, panel.delivery.configure(body))
            elif path == "/api/delivery-control" and self.command == "POST":
                body = self._body()
                if body.pop("instance_id", None) != panel.instance_id:
                    raise APIError(409, "stale_channel", "Il canale è cambiato: ricarica prima di continuare.")
                self._reply(200, panel.delivery.control(body))
            elif self.command == "POST" and path == "/api/reveal-passphrase":
                if not self.server.auth.enabled:
                    raise APIError(403, "auth_required", "Configura il login per visualizzare la passphrase.")
                body = self._body()
                if set(body) != {"revision", "instance_id"} or not isinstance(body["instance_id"], str):
                    raise APIError(422, "invalid_request", "Sono richiesti revision e instance_id.")
                with panel.lock:
                    panel._revision(body)
                    if body["instance_id"] != panel.instance_id:
                        raise APIError(409, "stale_channel", "Il canale è cambiato: ricarica prima di continuare.")
                    secret = panel.credentials.get("passphrase", "")
                    if panel.config["input_type"] != "url" or urlsplit(panel.config["input_url"]).scheme != "srt" or not secret:
                        raise APIError(404, "credential_not_saved", "Nessuna passphrase SRT salvata per questo canale.")
                    self._reply(200, {"passphrase": secret})
            elif self.command == "POST" and path == "/api/reveal-output-passphrase":
                if not self.server.auth.enabled:
                    raise APIError(403, "auth_required", "Configura il login per visualizzare la passphrase.")
                body = self._body()
                if (set(body) != {"revision", "instance_id", "output_id"}
                        or not isinstance(body["instance_id"], str) or not isinstance(body["output_id"], str)):
                    raise APIError(422, "invalid_request", "Sono richiesti revision, instance_id e output_id.")
                with panel.lock:
                    panel._revision(body)
                    if body["instance_id"] != panel.instance_id:
                        raise APIError(409, "stale_channel", "Il canale è cambiato: ricarica prima di continuare.")
                    output = next((item for item in panel.config["outputs"] if item["id"] == body["output_id"]), None)
                    secret = panel.output_credentials.get(body["output_id"], "")
                    if not output or output["protocol"] != "srt" or not secret:
                        raise APIError(404, "credential_not_saved", "Nessuna passphrase SRT salvata per questa uscita.")
                    self._reply(200, {"passphrase": secret})
            elif self.command == "GET" and path == "/api/profile":
                with panel.lock:
                    self._reply(200, panel.profiles.export(panel.config), filename="obe-profile.json")
            elif self.command == "POST" and path == "/api/profile-validate":
                self._reply(200, {"config": panel.profiles.parse(self._body(), validate_config)})
            elif self.command == "POST" and path == "/api/vapoursynth-validate":
                body = self._body()
                if set(body) != {"script"}:
                    raise APIError(422, "invalid_request", "È richiesto soltanto il campo script.")
                try:
                    vapoursynth_script.validate_script(body["script"])
                except configuration.ConfigurationError as error:
                    invalid(error.field, error.message)
                self._reply(200, {"valid": True})
            elif self.command == "POST" and path == "/api/vapoursynth-test":
                body = self._body()
                if set(body) != {"script"}:
                    raise APIError(422, "invalid_request", "È richiesto soltanto il campo script.")
                with panel.lock:
                    context = vapoursynth_script.test_context(panel.signal,
                        panel.config["input_video_format"] if panel.config["input_type"] == "decklink" else "1080i50",
                        field_order=panel.config["field_order"])
                    secrets = panel._secrets(panel.run)
                try:
                    result = vapoursynth_script.run_test(panel.root, body["script"], context)
                except configuration.ConfigurationError as error:
                    invalid(error.field, error.message)
                except vapoursynth_script.TestBusy:
                    raise APIError(409, "test_busy", "Un test VapourSynth è già in esecuzione.") from None
                result["log"] = "\n".join(clean_log(line, secrets) for line in result["log"].splitlines())
                self._reply(200, result)
            elif self.command == "POST" and path == "/api/restore":
                state = channels.restore(channel, self._body())
                self._reply(202 if state["applying"] else 200, state)
            elif self.command == "GET" and path == "/api/history":
                data, format_name = panel.read_history(urlsplit(self.path).query)
                if format_name == "csv":
                    self._reply(200, history_csv(data), "text/csv; charset=utf-8", "obe-history.csv")
                else:
                    self._reply(200, data, filename="obe-history.json" if format_name else None)
            elif self.command == "GET" and path in {"/api/preview-init", "/api/preview-segment"}:
                data = panel.preview_media(urlsplit(self.path).query, path == "/api/preview-segment")
                self._reply(200 if data is not None else 204, data or b"", "video/mp4")
            elif self.command == "GET" and path == "/api/preview-frame":
                self._reply(200, panel.preview_frame(), "image/jpeg")
            elif self.command == "POST" and path in {"/api/preview", "/api/preview-stop"}:
                body = self._body()
                if set(body) - ({"mode"} if path == "/api/preview" else set()) or ("mode" in body and body["mode"] not in ("snapshot", "video")):
                    raise APIError(422, "invalid_request", "La preview accetta soltanto mode: video o snapshot.")
                if path == "/api/preview":
                    self._reply(200, panel.start_preview(body.get("mode", "snapshot")))
                else:
                    panel._stop_preview()
                    self._reply(200, panel.state())
            elif self.command == "GET" and path in ASSETS:
                filename, mime = ASSETS[path]
                try:
                    content = (self.server.static_dir / filename).read_bytes()
                except OSError:
                    raise APIError(404, "not_found", "Risorsa non disponibile.") from None
                self._reply(200, content, mime)
            elif self.command == "PUT" and path == "/api/config":
                self._reply(200, channels.save(channel, self._body()))
            elif self.command == "POST" and path == "/api/apply":
                state = channels.apply(channel, self._body())
                self._reply(202 if state["applying"] else 200, state)
            elif self.command == "POST" and path in {"/api/probe", "/api/start"}:
                self._reply(202, channels.action(channel, path.rsplit("/", 1)[1], self._body()))
            elif self.command == "POST" and path == "/api/stop":
                if self._body():
                    raise APIError(422, "invalid_request", "L'arresto richiede un oggetto JSON vuoto.")
                self._reply(200, panel.stop())
            else:
                raise APIError(404, "not_found", "Risorsa non disponibile.")
        except delivery.DeliveryError as error:
            self._reply(error.status, APIError(error.status, "delivery_error", str(error)).body())
        except profiles.ProfileError as error:
            self._reply(422, APIError(422, "invalid_profile", error.message).body())
        except (APIError, AuthError) as error:
            self._reply(error.status, error.body())
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self._reply(500, {"error": {"code": "internal_error", "message": "Operazione non completata. Controlla lo stato dell'encoder."}})

    do_GET = do_PUT = do_POST = do_OPTIONS = do_DELETE = do_PATCH = _dispatch


def main():
    parser = argparse.ArgumentParser(description="Webpanel OBE locale")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--state-dir", type=Path, default=Path(__file__).resolve().parent / ".state")
    parser.add_argument("--transport-socket", type=Path, help="Socket Unix del servizio indipendente delle uscite")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--allow-host", type=ipaddress.ip_address, action="append", default=[],
                        help="Indirizzo IP aggiuntivo ammesso nell'header Host (ripetibile)")
    parser.add_argument("--auth-file", type=Path, help="File account locale; default: state-dir/auth.json")
    parser.add_argument("--tls-cert", type=Path)
    parser.add_argument("--tls-key", type=Path)
    args = parser.parse_args()
    if bool(args.tls_cert) != bool(args.tls_key):
        parser.error("--tls-cert e --tls-key devono essere specificati insieme")
    panel = None
    server = None
    try:
        panel = Channels(args.root, args.state_dir, args.transport_socket)
        server = ThreadingHTTPServer((args.bind, args.port), Handler)
        server.auth = Auth(args.auth_file or args.state_dir / "auth.json")
        if args.tls_cert:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(args.tls_cert, args.tls_key)
            server.socket = context.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
        server.allowed_hosts = {"localhost", "127.0.0.1", "::1", *(str(host) for host in args.allow_host)}
        server.channels = panel
        server.static_dir = Path(__file__).resolve().parent / "static"
        panel.start_monitoring()
        panel.resume()

        def shutdown(signum, frame):
            raise KeyboardInterrupt()

        signal.signal(signal.SIGTERM, shutdown)
        print(f"Webpanel pronto: {'https' if args.tls_cert else 'http'}://127.0.0.1:{server.server_port}", flush=True)
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    except (OSError, AuthError):
        print("Avvio non riuscito: controlla porta e directory di stato.", flush=True)
        return 1
    finally:
        if panel:
            panel.close()
        if server:
            server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
