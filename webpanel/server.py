#!/usr/bin/env python3
"""Console locale OBE: configurazione validata e processi isolati per canale."""

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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, quote, quote_plus, unquote, urlsplit
from preview import Preview


VERSION = "0.3.0"
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
        query = parse_qsl(plain.query, keep_blank_values=True)
        if len(query) != len({key for key, _ in query}) or any(CREDENTIAL_KEY.search(key) for key, _ in query):
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


class Run:
    def __init__(self, process, secret, action):
        self.process, self.secret, self.action = process, secret, action
        self.ready, self.started, self.exited = threading.Event(), threading.Event(), threading.Event()
        self.cancel = threading.Event()
        self.stop_lock = threading.Lock()
        self.video_id, self.audio_id = None, None
        self.signal, self.audio = None, None
        self.expected_exit = False
        self.start_sent = False
        self.start_time = time.monotonic()
        self.cpu_sample = None
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
        self.preview = Preview(self.root)
        self.closing = False
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
        return self.run is not None and (not self.run.exited.is_set() or self.phase == "stopping")

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
                now = time.monotonic()
                metrics["uptime_seconds"] = round(now - run.start_time, 1)
                try:
                    stat = Path(f"/proc/{run.process.pid}/stat").read_text().rsplit(")", 1)[1].split()
                    cpu = (int(stat[11]) + int(stat[12])) / os.sysconf("SC_CLK_TCK")
                    metrics["memory_mb"] = round(int(stat[21]) * os.sysconf("SC_PAGE_SIZE") / 1048576, 1)
                    if run.cpu_sample and now - run.cpu_sample[0] > 0:
                        metrics["cpu_percent"] = round(max(0, (cpu - run.cpu_sample[1]) / (now - run.cpu_sample[0]) * 100), 1)
                    run.cpu_sample = (now, cpu)
                except (OSError, ValueError, IndexError):
                    pass
            preview = self.preview.state()
            if self.phase != "running" and preview["status"] == "live":
                preview.update(status="stale", frame_id=None)
            return {"phase": self.phase, "message": self.message, "revision": self.revision,
                    "config": dict(self.config), "profiles": PROFILES, "process_alive": alive,
                    "encoding_started": self.encoding_started and alive,
                    "pid": run.process.pid if alive else None, "signal_state": self.signal_state,
                    "signal": dict(self.signal) if self.signal else None, "encrypted": self.encrypted and alive,
                    "metrics": metrics, "telemetry": self._telemetry_state(run, alive), "events": list(self.events),
                    "version": {"panel": VERSION, "encoder": self.encoder_version},
                    "preview": preview}

    def start_preview(self):
        with self.lock:
            if self.closing or self.phase != "running" or not self.run or self.run.process.poll() is not None:
                raise APIError(409, "encoder_not_running", "Avvia la codifica prima della preview.")
            try:
                self.preview.start(self.config["output_url"])
            except ValueError as error:
                raise APIError(422, "preview_unsupported", str(error)) from None
            except OSError:
                raise APIError(503, "preview_unavailable", "FFmpeg non è disponibile nella build del pannello.") from None
            return self.state()

    def preview_frame(self):
        with self.lock:
            frame = self.preview.jpeg() if self.phase == "running" and self.run and self.run.process.poll() is None else None
            if frame is None:
                raise APIError(503, "preview_not_ready", "Nessuna immagine aggiornata disponibile.")
            return frame

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
        if kind not in {"probe", "start"} or not isinstance(payload, dict) or not {"revision"} <= set(payload) <= {"revision", "passphrase"}:
            raise APIError(422, "invalid_request", "Richiesta di avvio o verifica non valida.")
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
            run = self.run = Run(process, secret, kind)
            self.phase, self.message = "probing", "Verifica della sorgente in corso…"
            self.signal, self.signal_state, self.encoding_started = None, "unknown", False
            self.encrypted = bool(secret)
            self._event("info", "Verifica della sorgente avviata.")
            run.collector = threading.Thread(target=self._collect, args=(run,), daemon=True)
            run.collector.start()
            threading.Thread(target=self._configure, args=(run, dict(self.config)), daemon=True).start()
            return self.state()

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
                    self._fail(run, "La sorgente non ha completato il probe. Controlla il segnale e gli eventi.")
                return
            if run.video_id is None or not run.signal:
                self._fail(run, "Il probe non ha rilevato un flusso video supportato.")
                return
            if run.action == "probe":
                self._finish(run, "Verifica completata; encoder fermo", error=False)
                return
            with self.lock:
                if run.cancel.is_set():
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
                    self._fail(run, "OBE non ha confermato l'avvio della codifica. Controlla gli eventi.")
                return
        except Exception:
            self._fail(run, "L'operazione OBE non è stata completata.")
            return
        self._monitor_telemetry(run)

    def _line(self, run, raw):
        # Machine samples contain only validated numbers/null, and are never logged.
        # Redacting a passphrase that equals a JSON key would corrupt the schema.
        telemetry = re.search(r"(?:^|obecli>\s*|\.{2,})OBE_TELEMETRY (.*)$", ANSI.sub("", raw))
        with self.lock:
            if run is not self.run:
                return
            if telemetry:
                if run.started.is_set() and not run.cancel.is_set():
                    self._telemetry_line(run, telemetry[1])
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
                run.secret = ""
                if run is self.run:
                    self.preview.stop()
                    self.encoding_started, self.encrypted = False, False
                    if not run.expected_exit:
                        self.phase, self.message = "error", "Il processo OBE è terminato. Controlla gli eventi prima di riavviare."
                        self.signal_state = "stopped"
                        self._event("error", self.message)
                run.exited.set()

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

    def _finish(self, run, message, error):
        with self.lock:
            if run is not self.run or run.cancel.is_set():
                return
            self.phase, self.message = "stopping", "Arresto del processo OBE…"
            run.expected_exit = True
        self._terminate(run)
        with self.lock:
            if run is self.run:
                self.phase, self.message = ("error" if error else "idle"), message
                self.encoding_started, self.encrypted = False, False
                if error:
                    self.signal_state = "stopped"
                self._event("error" if error else "info", message)

    def _fail(self, run, message):
        self._finish(run, message, error=True)

    def stop(self):
        with self.lock:
            self.preview.stop()
            run = self.run
            if run is None or run.exited.is_set():
                self.phase, self.message = "idle", "Encoder fermo"
                self.encoding_started, self.encrypted = False, False
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
            ports = {port}
            if source.scheme == "rtp":
                ports.add(int(options.get("localrtcpport", port + 1)))
            if not all(1 <= value <= 65535 for value in ports):
                raise ValueError()
            return ports
        except (ValueError, TypeError):
            invalid("input_url", "La porta locale di ricezione deve essere tra 1 e 65535.")

    @staticmethod
    def output_target(url):
        target = urlsplit(url)
        host = "loopback" if target.hostname in {"localhost", "127.0.0.1", "::1"} else target.hostname
        return host, target.port

    def __init__(self, root, state_dir):
        self.root, self.state_dir = Path(root), Path(state_dir)
        self.lock, self.closing = threading.RLock(), False
        self.panels = {"main": Panel(root, state_dir)}
        directory = self.state_dir / "channels"
        if directory.exists():
            for child in sorted(directory.iterdir()):
                if (len(self.panels) < self.MAX_CHANNELS and (child / "config.json").is_file() and not child.is_symlink()
                        and re.fullmatch(r"channel-[2-4]", child.name)):
                    self.panels[child.name] = Panel(root, child)

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
                          output_url=f"udp://127.0.0.1:{13000 + number * 10}")
            panel = Panel(self.root, self.state_dir / "channels" / channel)
            panel.save_config({"revision": panel.revision, "config": config})
            self.panels[channel] = panel
            return {"id": channel, "state": panel.state()}

    def action(self, channel, kind, payload):
        with self.lock:
            if self.closing:
                raise APIError(503, "panel_closing", "Il pannello si sta arrestando.")
            panel = self.get(channel)
            with panel.lock:
                ports = self.input_ports(panel.config["input_url"])
                for key, other in self.panels.items():
                    if key == channel:
                        continue
                    with other.lock:
                        if not other._busy():
                            continue
                        if ports & self.input_ports(other.config["input_url"]):
                            raise APIError(409, "input_in_use", "Usa porte di ingresso UDP/RTP/SRT listener distinte per i canali attivi.")
                        if kind == "start" and self.output_target(panel.config["output_url"]) == self.output_target(other.config["output_url"]):
                            raise APIError(409, "output_in_use", "Questa uscita è già usata da un altro canale attivo.")
                return panel.action(kind, payload)

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

    def _reply(self, status, value, mime="application/json; charset=utf-8"):
        content = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
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
            channels = self.server.channels
            if path == "/api/channels":
                if self.command == "GET":
                    self._reply(200, channels.state())
                elif self.command == "POST":
                    self._reply(201, channels.create(self._body()))
                else:
                    raise APIError(405, "method_not_allowed", "Metodo non consentito.")
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
            elif self.command == "GET" and path == "/api/preview-frame":
                self._reply(200, panel.preview_frame(), "image/jpeg")
            elif self.command == "POST" and path in {"/api/preview", "/api/preview-stop"}:
                if self._body():
                    raise APIError(422, "invalid_request", "La preview richiede un oggetto JSON vuoto.")
                if path == "/api/preview":
                    self._reply(200, panel.start_preview())
                else:
                    panel.preview.stop()
                    self._reply(200, panel.state())
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
                self._reply(202, channels.action(channel, path.rsplit("/", 1)[1], self._body()))
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
        panel = Channels(args.root, args.state_dir)
        server = ThreadingHTTPServer((args.bind, args.port), Handler)
        server.channels = panel
        server.static_dir = Path(__file__).resolve().parent / "static"

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
