#!/usr/bin/env python3
"""Console locale OBE: configurazione validata e un solo processo posseduto."""

import argparse
from collections import deque
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import unicodedata
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, quote, quote_plus, unquote, urlsplit

from monitoring import Alarms, History, PROFILE_FIELDS, RULES, SAMPLE_LIMIT, SRTStats, history_csv, utc_now


VERSION = "0.4.0"
RESTART_DELAYS = (2, 4, 8)
TELEMETRY_COUNTERS = ("video_frames", "video_bytes", "output_bytes", "output_datagrams", "output_errors")
TELEMETRY_FIELDS = {"schema", "monotonic_ns", *TELEMETRY_COUNTERS, "video_queue_frames"}
TELEMETRY_MAX_AGE = 5
DEFAULT_CONFIG = {
    "name": "Banco 1080i", "input_url": "udp://127.0.0.1:12000",
    "output_url": "udp://127.0.0.1:13000", "video_bitrate_kbps": 8000,
    "mux_bitrate_kbps": 9500, "vbv_buffer_kbits": 8000, "preset": "veryfast",
    "gop_frames": 50, "bframes": 2, "lookahead_frames": 20, "threads": 4,
    "audio_bitrate_kbps": 192,
}
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
AUDIO = re.compile(r"^Input-stream-id: (\d+) - Audio: (.*?)(?: - SDI audio pair:.*)?$")
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
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
        schemes = {"udp"} if output else {"udp", "rtp", "http", "https", "rtsp", "tcp", "srt"}
        if parts.scheme not in schemes or not parts.hostname or not plain.hostname:
            raise ValueError()
        if parts.username is not None or plain.username is not None or parts.fragment or plain.fragment:
            raise ValueError()
        if parts.port is not None and not 1 <= parts.port <= 65535:
            raise ValueError()
        if parts.scheme in {"udp", "rtp", "tcp", "srt"} and parts.port is None:
            raise ValueError()
        if any(CREDENTIAL_KEY.search(key) for key, _ in parse_qsl(plain.query, keep_blank_values=True)):
            raise ValueError()
        if re.search(r"[%;]", plain.netloc):
            raise ValueError()
    except ValueError:
        invalid(field, "Protocollo, indirizzo o porta non validi; credenziali e query crypto non ammesse.")
    return value


def validate_config(config):
    if not isinstance(config, dict) or set(config) != set(DEFAULT_CONFIG):
        raise APIError(422, "invalid_config", "Configurazione incompleta o con campi sconosciuti.")
    name = config["name"]
    if not isinstance(name, str) or not name.strip() or len(name) > 64 or any(control(c) for c in name):
        invalid("name", "Usa un nome da 1 a 64 caratteri senza controlli.")
    validate_url(config["input_url"], "input_url")
    validate_url(config["output_url"], "output_url", output=True)
    for field, limits in {
        "video_bitrate_kbps": (128, 50000), "mux_bitrate_kbps": (128, 100000),
        "vbv_buffer_kbits": (128, 100000), "gop_frames": (1, 300), "bframes": (0, 8),
        "lookahead_frames": (0, 100), "threads": (1, 32), "audio_bitrate_kbps": (64, 384),
    }.items():
        value = config[field]
        if type(value) is not int or not limits[0] <= value <= limits[1]:
            invalid(field, f"Inserisci un intero tra {limits[0]} e {limits[1]}.")
    if config["audio_bitrate_kbps"] not in {64, 96, 128, 160, 192, 224, 256, 320, 384}:
        invalid("audio_bitrate_kbps", "Bitrate MP2 non supportato.")
    if config["mux_bitrate_kbps"] < config["video_bitrate_kbps"] + config["audio_bitrate_kbps"] + 128:
        invalid("mux_bitrate_kbps", "Il mux deve includere video, audio e almeno 128 kb/s di margine.")
    if not isinstance(config["preset"], str) or config["preset"] not in PRESETS:
        invalid("preset", "Preset AVC non supportato.")
    return dict(config)


def clean_log(message, secret=""):
    if secret:
        for representation in {secret, quote(secret, safe=""), quote_plus(secret, safe="")}:
            message = message.replace(representation, "[chiave rimossa]")
    message = ANSI.sub("", message)
    message = "".join(c for c in message if not control(c))
    if secret:
        message = message.replace(secret, "[chiave rimossa]")
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
        self.session = session
        self.restart_attempt = session.attempts if session else 0
        self.exit_reason = None
        self.ready, self.started, self.exited = threading.Event(), threading.Event(), threading.Event()
        self.cancel = threading.Event()
        self.stop_lock = threading.Lock()
        self.video_id, self.audio_id = None, None
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
    def __init__(self, root, state_dir):
        self.root, self.state_dir = Path(root).resolve(), Path(state_dir).resolve()
        self.lock = threading.RLock()
        self.run = None
        self.session = None
        self.exit_history = deque(maxlen=10)
        self.closing = False
        self.alarms = Alarms()
        self.history = None
        self.monitor_cancel = threading.Event()
        self.monitor_thread = None
        self.monitor_received = None
        self.monitor_failed = False
        self.history_failed = False
        self.history_signature = None
        self.config, self.revision = dict(DEFAULT_CONFIG), 1
        self.phase, self.message = "idle", "Encoder fermo"
        self.signal_state, self.signal, self.encoding_started = "unknown", None, False
        self.encoder_version, self.encrypted = None, False
        self.events, self.event_id = deque(maxlen=200), 0
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.config_path = self.state_dir / "config.json"
        if self.config_path.exists():
            try:
                if self.config_path.stat().st_size > 16384:
                    raise ValueError()
                saved = json.loads(self.config_path.read_text(encoding="utf-8"))
                if set(saved) != {"revision", "config"} or type(saved["revision"]) is not int or saved["revision"] < 1:
                    raise ValueError()
                self.config, self.revision = validate_config(saved["config"]), saved["revision"]
            except (OSError, ValueError, TypeError, APIError):
                self.phase, self.message = "error", "Profilo salvato non valido: correggi e salva la configurazione."
        self._event("info", "Pannello pronto; nessun encoder avviato automaticamente.")

    def _event(self, level, message):
        self.event_id += 1
        self.events.append({"id": self.event_id, "time": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                            "level": level, "message": message})

    def _busy(self):
        return bool((self.session and self.session.active) or
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
            return {"phase": self.phase, "message": self.message, "revision": self.revision,
                    "config": dict(self.config), "profiles": PROFILES, "process_alive": alive,
                    "encoding_started": self.encoding_started and alive,
                    "pid": run.process.pid if alive else None, "signal_state": self.signal_state,
                    "signal": dict(self.signal) if self.signal else None, "encrypted": self.encrypted and alive,
                    "metrics": metrics, "telemetry": self._telemetry_state(run, alive),
                    "srt": self._srt_state(run, alive), "monitoring": self._monitoring_state(),
                    "recovery": self._recovery_state(), "events": list(self.events),
                    "version": {"panel": VERSION, "encoder": self.encoder_version}}

    def _srt_state(self, run, alive):
        return (run.srt if run else SRTStats()).view(
            urlsplit(self.config["input_url"]).scheme == "srt", alive and run.started.is_set(),
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
        with self.lock:
            now, run = time.monotonic(), self.run
            if run and run.process.poll() is None:
                self._process_metrics(run, now)
            self.monitor_received = now
            state = self.state()
            context = {key: self.config[key] for key in PROFILE_FIELDS}
            context["name"] = clean_log(context["name"], run.secret if run else "")
            context["input_protocol"] = urlsplit(self.config["input_url"]).scheme
            fps = None
            if self.signal:
                numerator, denominator = map(int, self.signal["fps"].split("/"))
                fps = numerator / denominator if denominator else None
            sample = {key: state[key] for key in ("phase", "process_alive", "encoding_started", "signal_state",
                      "revision", "version", "metrics", "telemetry", "srt")}
            sample.update(time=utc_now(), run_id=run.id if run else None,
                          session_id=run.session.id if run and run.session else None,
                          context=context, source_fps=fps)
            transitions = self.alarms.evaluate(sample, now)
            sample["active_alarms"] = list(self.alarms.active)
            for event in transitions:
                self._event(event["level"] if event["state"] == "active" else "info",
                            f"{'Allarme' if event['state'] == 'active' else 'Chiusura allarme'}: {event['label']}.")
            signature = (sample["run_id"], sample["phase"], sample["revision"])
            save = state["process_alive"] or state["phase"] == "restart_wait" or signature != self.history_signature or transitions
        # No disk I/O under the encoder lock: a busy/full disk cannot block Stop.
        okay = self.history.append(sample if save else None, transitions)
        with self.lock:
            if okay:
                self.history_signature = signature
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
            if self.history is None:
                raise OSError()
            data = self.history.read(int(raw_limit))
        except OSError:
            raise APIError(503, "history_unavailable", "Lo storico sul disco non è disponibile. La codifica continua.") from None
        return data, params.get("format")

    def _recovery_state(self):
        session = self.session
        remaining = None if not session or session.retry_at is None else round(max(0, session.retry_at - time.monotonic()), 1)
        history = [dict(entry) for entry in self.exit_history]
        return {"enabled": bool(session and session.enabled), "active": bool(session and session.active),
                "attempts": session.attempts if session else 0, "max_attempts": len(RESTART_DELAYS),
                "next_retry_seconds": remaining, "exhausted": bool(session and session.exhausted),
                "last_exit": history[-1] if history else None, "history": history}

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
        if session.attempts >= len(RESTART_DELAYS):
            session.exhausted = True
            self._cancel_session()
            self.phase, self.message = "error", "Recupero interrotto: esauriti i 3 tentativi. Controlla la diagnostica."
            self._event("error", self.message)
            return
        delay = RESTART_DELAYS[session.attempts]
        session.retry_at = time.monotonic() + delay
        self.phase = "restart_wait"
        self.message = f"Processo terminato; riavvio {session.attempts + 1}/{len(RESTART_DELAYS)} tra {delay} secondi."
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
            self._event("warning", f"Avvio automatico {session.attempts}/{len(RESTART_DELAYS)}.")
            try:
                self._launch("start", session.config, session.secret, session)
            except APIError as error:
                self.phase, self.message = "error", error.message
                self._record_exit(None, error.code)
                self._event("error", error.message)
                self._schedule_restart()

    def _telemetry_state(self, run, alive):
        result = {key: None for key in (*TELEMETRY_COUNTERS, "video_queue_frames",
                  "video_bitrate_kbps", "output_bitrate_kbps", "video_fps", "interval_seconds", "age_seconds")}
        result["status"] = "idle"
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
            result.update(run.telemetry_rates)
        return result

    def _telemetry_line(self, run, payload):
        run.telemetry_response.set()
        try:
            sample = json.loads(payload, object_pairs_hook=reject_duplicates)
            if not isinstance(sample, dict) or set(sample) != TELEMETRY_FIELDS or type(sample["schema"]) is not int or sample["schema"] != 1:
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

    def save_config(self, payload):
        if not isinstance(payload, dict) or set(payload) != {"revision", "config"}:
            raise APIError(422, "invalid_request", "Sono richiesti revision e config, senza altri campi.")
        config = validate_config(payload["config"])
        with self.lock:
            self._revision(payload)
            if self._busy():
                raise APIError(409, "encoder_busy", "Ferma l'encoder prima di modificare il profilo.")
            path = None
            try:
                fd, path = tempfile.mkstemp(prefix=".config-", dir=self.state_dir)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump({"revision": self.revision + 1, "config": config}, handle, ensure_ascii=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(path, self.config_path)
                path = None
            except OSError:
                raise APIError(500, "save_failed", "Impossibile salvare il profilo sul disco.") from None
            finally:
                if path:
                    Path(path).unlink(missing_ok=True)
            self.config, self.revision = config, self.revision + 1
            self.phase, self.message = "idle", "Profilo salvato"
            self.signal, self.signal_state = None, "unknown"
            self._event("info", "Configurazione salvata.")
            return self.state()

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
            if secret and urlsplit(self.config["input_url"]).scheme != "srt":
                raise APIError(422, "invalid_passphrase", "La chiave è utilizzabile soltanto con un ingresso SRT.")
            self.session = Session(self.config, secret, payload.get("auto_restart", False)) if kind == "start" else None
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
        if secret:
            env["OBE_SRT_PASSPHRASE"] = secret
        try:
            process = subprocess.Popen([stdbuf, "-oL", "-eL", str(launcher)], cwd=self.root, env=env,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       start_new_session=True)
        except OSError:
            raise APIError(500, "spawn_failed", "Impossibile avviare il processo OBE.") from None
        run = self.run = Run(process, secret, kind, session)
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
            commands = ("set obe opts system-type=generic,max-probe-time=8\nset input url\n"
                        f"set input opts location={config['input_url']}\nprobe input\n")
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
            video = (f"set stream opts {run.video_id}:format=avc,preset={config['preset']},"
                     f"bitrate={config['video_bitrate_kbps']},vbv-maxrate={config['video_bitrate_kbps']},"
                     f"vbv-bufsize={config['vbv_buffer_kbits']},thread={config['threads']},"
                     f"bframes={config['bframes']},keyint={config['gop_frames']},rc-lookahead={config['lookahead_frames']}\n")
            audio = "" if run.audio_id is None else f"set stream opts {run.audio_id}:format=mp2,bitrate={config['audio_bitrate_kbps']},channel-map=stereo\n"
            commands = (video + audio + "set muxer mpegts\n"
                        f"set muxer opts ts-muxrate={config['mux_bitrate_kbps'] * 1000},cbr=1,ts-type=dvb\n"
                        f"set outputs 1\nset output opts 0:type=udp,target={config['output_url']}\nstart\n")
            if not self._send(run, commands) or not self._await(run, run.started, 20):
                if not run.cancel.is_set():
                    self._fail(run, "OBE non ha confermato l'avvio della codifica. Controlla gli eventi.", "start_timeout")
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
        with self.lock:
            if run is not self.run:
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
            line = clean_log(raw, run.secret)
            if not line:
                return
            self._event("warning" if re.search(r"error|failed|warning|invalid|retrying|connection lost", line, re.I) else "info", line)
            if line.startswith("Version "):
                self.encoder_version = line[:160]
            video, audio = VIDEO.match(line), AUDIO.match(line)
            if video and run.video_id is None:
                run.video_id = int(video[1])
                run.signal = {"width": int(video[2]), "height": int(video[3]), "scan": video[4], "fps": video[5], "audio": run.audio}
                self.signal = dict(run.signal)
            if audio and run.audio_id is None:
                run.audio_id, run.audio = int(audio[1]), audio[2]
                if run.signal:
                    run.signal["audio"] = run.audio
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
            with self.lock:
                run.cancel.set()
                run.secret = ""
                if run is self.run:
                    entry = self._record_exit(run)
                    self.encoding_started, self.encrypted = False, False
                    if not run.expected_exit:
                        self.phase, self.message = "error", "Il processo OBE è terminato. Controlla gli eventi prima di riavviare."
                        self.signal_state = "stopped"
                        self._event("error", f"Processo OBE terminato: {entry['signal'] or 'codice ' + str(entry['returncode'])}.")
                run.exited.set()
                if run is self.run and not run.expected_exit:
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
            waiting = bool(self.session and self.session.retry_at is not None)
            self._cancel_session()
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
        self.stop()
        self.monitor_cancel.set()
        if self.monitor_thread:
            self.monitor_thread.join(timeout=3)
        if self.history:
            try:
                self._sample_monitoring()
            finally:
                self.history.close()


def reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


class Handler(BaseHTTPRequestHandler):
    server_version = "OBEPanel/0.1"

    def log_message(self, *args):
        pass  # I percorsi e i corpi HTTP non vengono registrati.

    def _guard(self):
        if len(self.headers.get_all("Host", [])) != 1:
            raise APIError(403, "invalid_host", "Host non consentito.")
        host = self.headers["Host"]
        try:
            parts = urlsplit("http://" + host)
            if parts.hostname not in {"localhost", "127.0.0.1", "::1"} or parts.username is not None or parts.path or parts.query or parts.fragment:
                raise ValueError()
            port = parts.port or 80
        except ValueError:
            raise APIError(403, "invalid_host", "Host non consentito.") from None
        origins = self.headers.get_all("Origin", [])
        if len(origins) > 1:
            raise APIError(403, "invalid_origin", "Origine non consentita.")
        if origins:
            try:
                origin = urlsplit(origins[0])
                if origin.scheme != "http" or origin.hostname != parts.hostname or (origin.port or 80) != port or origin.username is not None or origin.path or origin.query or origin.fragment:
                    raise ValueError()
            except ValueError:
                raise APIError(403, "invalid_origin", "Origine non consentita.") from None
        if self.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
            raise APIError(403, "cross_site", "Richiesta cross-site non consentita.")

    def _body(self):
        if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) != 1:
            raise APIError(400, "invalid_body", "Lunghezza del corpo richiesta.")
        if self.headers.get_content_type() != "application/json":
            raise APIError(400, "invalid_content_type", "È richiesto un corpo application/json.")
        try:
            size = int(self.headers["Content-Length"])
            if not 0 < size <= 16384:
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
            raise APIError(400, "invalid_json", "Corpo JSON non valido o superiore a 16 KiB.") from None

    def _reply(self, status, value, mime="application/json; charset=utf-8", filename=None):
        content = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self.wfile.write(content)

    def _dispatch(self):
        try:
            self._guard()
            path = urlsplit(self.path).path
            panel = self.server.panel
            if self.command == "GET" and path == "/api/state":
                self._reply(200, panel.state())
            elif self.command == "GET" and path == "/api/history":
                data, format_name = panel.read_history(urlsplit(self.path).query)
                if format_name == "csv":
                    self._reply(200, history_csv(data), "text/csv; charset=utf-8", "obe-history.csv")
                else:
                    self._reply(200, data, filename="obe-history.json" if format_name else None)
            elif self.command == "GET" and path in ASSETS:
                filename, mime = ASSETS[path]
                try:
                    content = (self.server.static_dir / filename).read_bytes()
                except OSError:
                    raise APIError(404, "not_found", "Risorsa non disponibile.") from None
                self._reply(200, content, mime)
            elif self.command == "PUT" and path == "/api/config":
                self._reply(200, panel.save_config(self._body()))
            elif self.command == "POST" and path in {"/api/probe", "/api/start"}:
                self._reply(202, panel.action(path.rsplit("/", 1)[1], self._body()))
            elif self.command == "POST" and path == "/api/stop":
                if self._body():
                    raise APIError(422, "invalid_request", "L'arresto richiede un oggetto JSON vuoto.")
                self._reply(200, panel.stop())
            else:
                raise APIError(404, "not_found", "Risorsa non disponibile.")
        except APIError as error:
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
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8088)
    args = parser.parse_args()
    panel = None
    server = None
    try:
        panel = Panel(args.root, args.state_dir)
        server = ThreadingHTTPServer((args.bind, args.port), Handler)
        server.panel = panel
        server.static_dir = Path(__file__).resolve().parent / "static"
        panel.start_monitoring()

        def shutdown(signum, frame):
            raise KeyboardInterrupt()

        signal.signal(signal.SIGTERM, shutdown)
        print(f"Webpanel pronto: http://127.0.0.1:{server.server_port}", flush=True)
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    except OSError:
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
