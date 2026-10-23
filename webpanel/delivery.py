"""Per-channel remote delivery monitoring, bounded polling and clock calibration."""
from collections import deque
import copy
import http.client
import json
import math
import os
from pathlib import Path
import statistics
import tempfile
import threading
import time
from urllib.parse import urlsplit

from delivery_signal import measure_latency, samples_decode

DEFAULTS = {"enabled": False, "source_url": "", "receiver_url": ""}


class DeliveryError(ValueError):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def endpoint(url):
    try:
        u = urlsplit(url)
        if (u.scheme != "http" or u.hostname != "127.0.0.1" or not u.port
                or u.path not in ("", "/") or u.query or u.fragment or u.username is not None):
            raise ValueError()
    except (ValueError, TypeError):
        raise DeliveryError("Usa http://127.0.0.1:porta tramite inoltro SSH.") from None
    return u


def request(url, token, payload=None):
    u = endpoint(url)
    connection = http.client.HTTPConnection(u.hostname, u.port, timeout=1)
    start = time.monotonic() * 1000
    try:
        body = None if payload is None else json.dumps(payload)
        connection.request("GET" if payload is None else "POST",
                           "/v1/state" if payload is None else "/v1/control", body,
                           {"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        response = connection.getresponse()
        raw = bytearray()
        deadline = time.monotonic() + 2
        while len(raw) <= 131072:
            if time.monotonic() >= deadline:
                raise OSError("Read deadline")
            chunk = response.read1(min(16384, 131073 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        end = time.monotonic() * 1000
        if response.status != 200 or len(raw) > 131072:
            raise DeliveryError("Osservatore non disponibile o accesso rifiutato.", 503)
        state = json.loads(raw)
        validate_state(state)
        return state, state["clock_ms"] - (start + end) / 2, (end - start) / 2
    except (OSError, http.client.HTTPException, ValueError, TypeError, KeyError):
        raise DeliveryError("Osservatore non disponibile o risposta non valida.", 503) from None
    finally:
        connection.close()


def finite(value, minimum=0, maximum=1e16):
    return type(value) in (float, int) and math.isfinite(value) and minimum <= value <= maximum


def validate_state(state):
    if (not isinstance(state, dict) or state.get("schema") != 1
            or state.get("role") not in ("source", "monitor", "receiver")
            or not isinstance(state.get("instance"), str) or len(state["instance"]) != 32
            or type(state.get("revision")) is not int or state["revision"] < 1
            or not finite(state.get("clock_ms")) or not isinstance(state.get("streams"), dict)
            or set(state["streams"]) - {"main", "backup", "return", "source"}):
        raise ValueError("Invalid observer state")
    for stream in [state.get("observation"), *state["streams"].values()]:
        if not isinstance(stream, dict) or stream.get("status") not in ("live", "unavailable", "degraded"):
            raise ValueError("Invalid stream state")
        for key in ("decoded_frames", "bytes", "packets", "errors", "continuity_errors", "bitrate_kbps",
                    "unchanged_seconds", "frame_age_seconds"):
            if stream.get(key) is not None and not finite(stream[key]):
                raise ValueError("Invalid stream metric")
    samples_decode(state.get("samples", []), state["clock_ms"])
    selection = state.get("selector")
    if state["role"] == "receiver":
        if (not isinstance(selection, dict) or selection.get("active") not in (None, "main", "backup")
                or selection.get("mode") not in ("auto", "main", "backup")
                or not finite(selection.get("switches"))):
            raise ValueError("Invalid selector")


def public_stream(value):
    return {key: value.get(key) for key in ("status", "frame_age_seconds", "decoded_frames", "bitrate_kbps",
             "packets", "errors", "continuity_errors", "unchanged_seconds")}


class Delivery:
    def __init__(self, directory):
        self.path = Path(directory) / "delivery.json"
        self.lock, self.operation = threading.RLock(), threading.Lock()
        self.stop, self.wake = threading.Event(), threading.Event()
        self.config, self.tokens, self.revision = dict(DEFAULTS), {}, 1
        self.received, self.snapshot, self.remote = None, {}, None
        self.matches = deque(maxlen=120)
        self.last_match = None
        self.error = None
        if self.path.exists():
            try:
                if self.path.stat().st_size > 8192 or self.path.stat().st_mode & 0o077:
                    raise ValueError()
                saved = json.loads(self.path.read_text())
                self.config, self.tokens = self._validate(saved["config"], saved["tokens"])
                self.revision = saved["revision"]
                if type(self.revision) is not int or self.revision < 1:
                    raise ValueError()
            except (OSError, ValueError, KeyError, TypeError):
                self.config, self.tokens = dict(DEFAULTS), {}
                self.error = "Configurazione del monitoraggio non valida"
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    def _validate(self, config, tokens):
        if (not isinstance(config, dict) or set(config) != set(DEFAULTS)
                or type(config["enabled"]) is not bool or not isinstance(tokens, dict)
                or set(tokens) - {"source", "receiver"}):
            raise DeliveryError("Configurazione del monitoraggio non valida.")
        for role in ("source", "receiver"):
            value = config[role + "_url"]
            if not isinstance(value, str) or len(value) > 80:
                raise DeliveryError("Indirizzo osservatore non valido.")
            if value:
                endpoint(value)
            token = tokens.get(role, "")
            if not isinstance(token, str) or token and not (32 <= len(token) <= 128 and token.isascii() and token.isalnum()):
                raise DeliveryError("Il token deve contenere 32–128 caratteri alfanumerici.")
            if config["enabled"] and (value and not token or role == "receiver" and not value):
                raise DeliveryError("Configura l'osservatore e il relativo token.")
        return dict(config), dict(tokens)

    def configure(self, payload):
        with self.operation, self.lock:
            if self.stop.is_set():
                raise DeliveryError("Il canale è in chiusura.", 409)
            if not isinstance(payload, dict) or set(payload) - {"revision", "config", "tokens"}:
                raise DeliveryError("Richiesta non valida.")
            if type(payload.get("revision")) is not int or payload["revision"] != self.revision:
                raise DeliveryError("Configurazione cambiata: ricarica prima di salvare.", 409)
            config, patch = payload.get("config"), payload.get("tokens", {})
            if not isinstance(config, dict) or not isinstance(patch, dict):
                raise DeliveryError("Configurazione non valida.")
            tokens = {role: value for role, value in self.tokens.items()
                      if config.get(role + "_url") == self.config[role + "_url"]}
            tokens.update(patch)
            config, tokens = self._validate(config, tokens)
            saved = {"config": config, "tokens": tokens, "revision": self.revision + 1}
            try:
                with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False, mode="w") as handle:
                    temporary = Path(handle.name)
                    os.fchmod(handle.fileno(), 0o600)
                    json.dump(saved, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
            except OSError:
                raise DeliveryError("Salvataggio del monitoraggio non riuscito.", 503) from None
            self.config, self.tokens, self.revision = config, tokens, saved["revision"]
            self.received, self.remote, self.snapshot, self.error = None, None, {}, None
            self.matches.clear()
            self.last_match = None
            self.wake.set()
            return self.state()

    def _poll(self):
        with self.lock:
            config, tokens, revision = dict(self.config), dict(self.tokens), self.revision
        if not config["enabled"]:
            return
        try:
            receiver, roffset, rerror = request(config["receiver_url"], tokens["receiver"])
            if receiver["role"] not in ("monitor", "receiver"):
                raise DeliveryError("Il ricevitore configurato ha un ruolo diverso.", 503)
            latency = {"status": "unavailable", "reason": "Osservatore playout non configurato",
                       "latency_ms": None, "uncertainty_ms": None}
            source = None
            if config["source_url"]:
                try:
                    source, soffset, serror = request(config["source_url"], tokens["source"])
                    if source["role"] != "source":
                        raise DeliveryError("Ruolo sorgente non valido.", 503)
                    latency = measure_latency(source, receiver, soffset, roffset, serror + rerror)
                except DeliveryError:
                    latency["reason"] = "Osservatore playout non disponibile"
            selection = receiver.get("selector")
            with self.lock:
                if revision != self.revision or self.stop.is_set():
                    return
                sample_id = (source["instance"] if source else None, receiver["instance"],
                             receiver["samples"][-1][0] if receiver["samples"] else None)
                if self.last_match and sample_id[:2] != self.last_match[:2]:
                    self.matches.clear()
                if latency["status"] == "measured" and sample_id != self.last_match:
                    self.matches.append(latency["latency_ms"])
                    self.last_match = sample_id
                elif latency["status"] != "measured":
                    self.matches.clear()
                    self.last_match = None
                # Require two independent sequence matches before publishing a live value.
                if latency["status"] == "measured" and (len(self.matches) < 2 or abs(self.matches[-1] - self.matches[-2]) > 500):
                    latency = {**latency, "status": "waiting", "latency_ms": None,
                               "reason": "Conferma della corrispondenza video"}
                if self.matches and latency["status"] == "measured":
                    values = sorted(self.matches)
                    latency.update(samples=len(values), p50_ms=round(statistics.median(values)),
                                   p95_ms=values[max(0, math.ceil(len(values) * .95) - 1)])
                self.snapshot = {"receiver": public_stream(receiver["observation"]),
                    "streams": {k: public_stream(v) for k, v in receiver["streams"].items()},
                    "selector": {k: selection[k] for k in ("mode", "active", "switches")} if selection else None,
                    "latency": latency, "role": receiver["role"]}
                self.remote = {"instance": receiver["instance"], "revision": receiver["revision"]}
                self.received, self.error = time.monotonic(), None
        except (DeliveryError, ValueError):
            with self.lock:
                if self.stop.is_set():
                    return
                if revision == self.revision:
                    self.error = "Ricevitore non raggiungibile o dati non validi"
                    self.received, self.remote = None, None
                    self.matches.clear()

    def control(self, payload):
        with self.operation:
            with self.lock:
                if self.stop.is_set():
                    raise DeliveryError("Il canale è in chiusura.", 409)
                if (not isinstance(payload, dict) or set(payload) != {"revision", "mode"}
                        or payload["mode"] not in ("auto", "main", "backup")):
                    raise DeliveryError("Scegli Automatico, MAIN o BACKUP.")
                if type(payload["revision"]) is not int or payload["revision"] != self.revision:
                    raise DeliveryError("Configurazione cambiata.", 409)
                if (not self.remote or not self.received or time.monotonic() - self.received > 5
                        or self.snapshot.get("role") != "receiver"):
                    raise DeliveryError("Selettore non disponibile.", 409)
                config, token, remote = dict(self.config), self.tokens["receiver"], dict(self.remote)
            request(config["receiver_url"], token, {**remote, "mode": payload["mode"]})
            self._poll()
            return self.state()

    def state(self):
        with self.lock:
            live = self.received is not None and time.monotonic() - self.received <= 5
            return {"config": dict(self.config), "revision": self.revision,
                    "tokens": {role: bool(self.tokens.get(role)) for role in ("source", "receiver")},
                    "status": "disabled" if not self.config["enabled"] else "live" if live else "unavailable",
                    "age_seconds": round(time.monotonic() - self.received, 2) if self.received else None,
                    "error": self.error, **(copy.deepcopy(self.snapshot) if live else {})}

    def _run(self):
        while not self.stop.is_set():
            self.wake.clear()
            self._poll()
            self.wake.wait(2)

    def close(self):
        self.stop.set()
        self.wake.set()
        self.worker.join(timeout=4)
