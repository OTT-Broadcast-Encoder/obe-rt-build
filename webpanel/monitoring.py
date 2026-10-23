"""Bounded local monitoring. No URLs, logs or credentials enter the archive."""

from collections import deque
import csv
from datetime import datetime, timezone
import io
import json
import os
import sqlite3
import threading
import time
import uuid


SAMPLE_LIMIT = 1800
ALARM_LIMIT = 200
MAX_AGE = 5
SRT_COUNTERS = ("received_packets", "received_bytes", "lost_packets",
                "retransmitted_packets", "dropped_packets")
SRT_FIELDS = {*SRT_COUNTERS, "sample_us", "rtt_us", "buffer_bytes", "buffer_ms",
              "buffer_available_bytes", "latency_ms"}
PROFILE_FIELDS = ("name", "video_bitrate_kbps", "mux_bitrate_kbps", "vbv_buffer_kbits",
                  "preset", "gop_frames", "bframes", "lookahead_frames", "threads", "audio_bitrate_kbps")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def system_sample(state, instance_id):
    """Archive only numeric resource measurements and interface names."""
    result = {"status": state.get("status"), "instance_id": instance_id}
    if result["status"] != "live":
        return result
    fields = {"cpu": ("usage_percent", "load_1m", "load_5m", "load_15m"),
              "memory": ("used_bytes", "total_bytes", "used_percent")}
    for section, names in fields.items():
        result[section] = {name: state.get(section, {}).get(name) for name in names}
    result["network"] = [{name: interface.get(name) for name in
                          ("interface", "rx_bitrate_kbps", "tx_bitrate_kbps")}
                         for interface in state.get("network", [])]
    return result


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


class SRTStats:
    def __init__(self):
        self.sample, self.received, self.connection = None, None, None
        self.rates = {}

    def accept(self, payload, now):
        envelope = json.loads(payload, object_pairs_hook=unique_object)
        if not isinstance(envelope, dict) or set(envelope) != {"schema", "connection", "stats"}:
            raise ValueError("invalid SRT envelope")
        if type(envelope["schema"]) is not int or envelope["schema"] != 1:
            raise ValueError("unsupported SRT schema")
        connection, sample = envelope["connection"], envelope["stats"]
        if type(connection) is not int or not 1 <= connection < 2**32:
            raise ValueError("invalid SRT connection")
        if not isinstance(sample, dict) or set(sample) != SRT_FIELDS:
            raise ValueError("invalid SRT fields")
        if any(type(value) is not int or not 0 <= value < 2**64 for value in sample.values()):
            raise ValueError("invalid SRT counter")
        previous = self.sample if connection == self.connection else None
        if self.connection is not None and connection < self.connection:
            raise ValueError("old SRT connection")
        rates = {}
        if previous:
            # Re-reading a cached AVOption must not renew its freshness.
            if sample == previous:
                return
            elapsed = (sample["sample_us"] - previous["sample_us"]) / 1e6
            if elapsed <= 0 or any(sample[key] < previous[key] for key in SRT_COUNTERS):
                raise ValueError("non-monotonic SRT sample")
            rates = {"receive_bitrate_kbps": round((sample["received_bytes"] - previous["received_bytes"]) * .008 / elapsed, 2),
                     "interval_seconds": round(elapsed, 3)}
        self.sample, self.connection, self.received, self.rates = sample, connection, now, rates

    def view(self, applicable, alive, now, started_at):
        result = {key: None for key in (*SRT_FIELDS, "connection", "rtt_ms", "age_seconds",
                                       "receive_bitrate_kbps", "interval_seconds")}
        result["status"] = "not_applicable" if not applicable else "idle"
        if not applicable or not alive:
            return result
        result["status"] = "waiting" if now - started_at <= MAX_AGE else "unavailable"
        if self.sample is not None:
            age = max(0, now - self.received)
            result["age_seconds"] = round(age, 2)
            result["status"] = "live" if age <= MAX_AGE else "stale"
            if result["status"] == "live":
                result.update(self.sample)
                result.update(self.rates)
                result.update(connection=self.connection, rtt_ms=round(self.sample["rtt_us"] / 1000, 3))
        return result


# Trigger/clear delays are seconds of continuous condition. Event counters use
# a 10-second quiet window; cumulative totals alone never keep an alarm active.
RULES = {
    "encoder_error": {"label": "Errore encoder", "level": "error", "trigger_seconds": 0, "clear_seconds": 3,
                      "guide": "Controlla codice di uscita, diagnostica e tentativi di recupero."},
    "source_missing": {"label": "Sorgente assente", "level": "error", "trigger_seconds": 3, "clear_seconds": 3,
                       "guide": "Controlla il mittente e la raggiungibilità dell’ingresso."},
    "telemetry_missing": {"label": "Telemetria encoder assente", "level": "warning", "trigger_seconds": 3, "clear_seconds": 3,
                          "guide": "Controlla gli eventi e la versione del motore; assenza di misure non prova uno stop del video."},
    "srt_missing": {"label": "Statistiche SRT assenti", "level": "warning", "trigger_seconds": 3, "clear_seconds": 3,
                    "guide": "Controlla la sorgente e la build FFmpeg con il ponte delle statistiche SRT."},
    "udp_errors": {"label": "Errori di invio UDP", "level": "error", "trigger_seconds": 0, "clear_seconds": 3,
                   "guide": "Errori locali negli ultimi 10 s: controlla socket, destinazione e interfaccia di uscita."},
    "srt_drops": {"label": "Pacchetti SRT scartati", "level": "error", "trigger_seconds": 0, "clear_seconds": 3,
                  "guide": "Scarti negli ultimi 10 s: controlla banda e latenza SRT. Le perdite recuperate non attivano questo allarme."},
    "srt_rtt": {"label": "RTT vicino alla latenza SRT", "level": "warning", "trigger_seconds": 5, "clear_seconds": 5,
                "guide": "RTT oltre l’80% della latenza negoziata; rientro sotto il 60%. Controlla la rete e il margine di recupero."},
    "video_queue": {"label": "Coda video elevata", "level": "warning", "trigger_seconds": 5, "clear_seconds": 5,
                    "guide": "Coda oltre 2 s di video; rientro sotto 1 s. Controlla CPU, preset e bitrate."},
    "video_stalled": {"label": "Codifica senza avanzamento", "level": "error", "trigger_seconds": 0, "clear_seconds": 3,
                      "guide": "Nessun frame nuovo da 5 s con sorgente in ricezione e telemetria valida. Controlla motore e code."},
}


class Alarms:
    def __init__(self):
        self.active, self.pending, self.clearing = {}, {}, {}
        self.recent = deque(maxlen=ALARM_LIMIT)
        self.run_id, self.previous = None, None
        self.counters = {}
        self.increments, self.frame_changed = {}, None

    def _transition(self, code, state, snapshot, reason):
        event = {"id": uuid.uuid4().hex, "time": snapshot["time"], "code": code,
                 "state": state, "reason": reason, "run_id": snapshot["run_id"],
                 "session_id": snapshot["session_id"], **RULES[code]}
        if state == "active":
            self.active[code] = event
        else:
            original = self.active.pop(code)
            event.update(run_id=original["run_id"], session_id=original["session_id"], since=original["time"])
        self.recent.append(event)
        return event

    def evaluate(self, snapshot, now):
        events = []
        changed = snapshot["run_id"] != self.run_id
        stopped = snapshot["phase"] in {"idle", "stopping"}
        if changed or stopped:
            for code in list(self.active):
                events.append(self._transition(code, "resolved", snapshot, "process_changed" if changed else "encoder_stopped"))
            self.pending.clear()
            self.clearing.clear()
            self.previous, self.increments, self.frame_changed = None, {}, None
            self.counters.clear()
            self.run_id = snapshot["run_id"]
        if stopped:
            return events
        telemetry, srt = snapshot["telemetry"], snapshot["srt"]
        operating = snapshot["encoding_started"] and snapshot["process_alive"]
        live = operating and telemetry["status"] == "live"
        srt_live = operating and srt["status"] == "live"
        previous = self.previous
        for code, current, generation in (
                ("udp_errors", telemetry.get("output_errors") if live else None, self.run_id),
                ("srt_drops", srt.get("dropped_packets") if srt_live else None, srt.get("connection"))):
            old_generation, old = self.counters.get(code, (generation, 0))
            if old_generation != generation:
                old = 0
            if current is not None and current > old:
                self.increments[code] = now
            if current is not None:
                self.counters[code] = (generation, current)
        receiving = live and snapshot["signal_state"] == "receiving" and telemetry.get("video_frames") is not None
        if not receiving:
            self.frame_changed = None
        elif self.frame_changed is None or not previous or telemetry["video_frames"] != previous["telemetry"].get("video_frames"):
            self.frame_changed = now
        fps = snapshot.get("source_fps")
        queue = telemetry.get("video_queue_frames")
        rtt, latency = srt.get("rtt_ms"), srt.get("latency_ms")
        conditions = {
            "encoder_error": snapshot["phase"] in {"error", "restart_wait"},
            "source_missing": operating and snapshot["signal_state"] == "reconnecting",
            "telemetry_missing": operating and telemetry["status"] in {"stale", "unavailable"},
            "srt_missing": operating and srt["status"] in {"stale", "unavailable"},
            "udp_errors": operating and now - self.increments.get("udp_errors", -float("inf")) < 10,
            "srt_drops": operating and now - self.increments.get("srt_drops", -float("inf")) < 10,
            "srt_rtt": bool(srt_live and latency and rtt is not None and rtt > latency * (.6 if "srt_rtt" in self.active else .8)),
            "video_queue": bool(live and fps and queue is not None and queue > fps * (1 if "video_queue" in self.active else 2)),
            "video_stalled": receiving and now - self.frame_changed >= 5,
        }
        for code, condition in conditions.items():
            # Missing measurements are unknown, not evidence of recovery.
            known = {"udp_errors": live, "srt_drops": srt_live, "srt_rtt": srt_live,
                     "video_queue": live, "video_stalled": receiving}.get(code, True)
            if operating and not known:
                self.pending.pop(code, None)
                self.clearing.pop(code, None)
                continue
            if condition:
                self.clearing.pop(code, None)
                since = self.pending.setdefault(code, now)
                if code not in self.active and now - since >= RULES[code]["trigger_seconds"]:
                    events.append(self._transition(code, "active", snapshot, "threshold"))
            else:
                self.pending.pop(code, None)
                since = self.clearing.setdefault(code, now)
                if code in self.active and now - since >= RULES[code]["clear_seconds"]:
                    events.append(self._transition(code, "resolved", snapshot, "recovered"))
        self.previous = snapshot
        return events


class History:
    """Serialized SQLite access with row and page limits; failures never stop OBE."""
    def __init__(self, path, sample_limit=SAMPLE_LIMIT, alarm_limit=ALARM_LIMIT):
        self.path, self.sample_limit, self.alarm_limit = path, sample_limit, alarm_limit
        self.lock = threading.RLock()
        self.db, self.available, self.count, self.retry_at = None, False, 0, 0
        self._open()

    def _open(self):
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
            os.fchmod(fd, 0o600)
            os.close(fd)
            self.db = sqlite3.connect(self.path, timeout=.1, check_same_thread=False)
            page_size = self.db.execute("PRAGMA page_size").fetchone()[0]
            self.db.execute(f"PRAGMA max_page_count={16 * 1024 * 1024 // page_size}")
            for table in ("samples", "alarms"):
                self.db.execute(f"CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
            self.db.commit()
            self.count = self.db.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
            self.available = True
        except (OSError, sqlite3.Error):
            self._failed()

    def _failed(self):
        self.available, self.retry_at = False, time.monotonic() + 10
        if self.db:
            try:
                self.db.close()
            except sqlite3.Error:
                pass
        self.db = None

    def append(self, sample, alarms):
        with self.lock:
            if self.db is None and time.monotonic() >= self.retry_at:
                self._open()
            if self.db is None:
                return False
            try:
                with self.db:
                    if sample is not None:
                        self.db.execute("INSERT INTO samples(payload) VALUES(?)", (json.dumps(sample, ensure_ascii=False, allow_nan=False),))
                    self.db.executemany("INSERT INTO alarms(payload) VALUES(?)", [(json.dumps(alarm, ensure_ascii=False),) for alarm in alarms])
                    for table, limit in (("samples", self.sample_limit), ("alarms", self.alarm_limit)):
                        self.db.execute(f"DELETE FROM {table} WHERE id IN (SELECT id FROM {table} ORDER BY id DESC LIMIT -1 OFFSET ?)", (limit,))
                self.count = self.db.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
                self.available = True
                return True
            except (sqlite3.Error, OSError):
                self._failed()
                return False

    def read(self, limit):
        with self.lock:
            if self.db is None:
                raise OSError("history unavailable")
            try:
                result = {"schema": 1, "generated_at": utc_now(), "sample_limit": self.sample_limit,
                          "alarm_limit": self.alarm_limit, "total_samples": self.count}
                for table, count in (("samples", limit), ("alarms", self.alarm_limit)):
                    rows = self.db.execute(f"SELECT payload FROM {table} ORDER BY id DESC LIMIT ?", (count,)).fetchall()
                    result[table] = [json.loads(row[0]) for row in reversed(rows)]
                return result
            except (sqlite3.Error, ValueError):
                self._failed()
                raise OSError("history unavailable") from None

    def close(self):
        with self.lock:
            if self.db:
                self.db.close()
                self.db = None
            self.available = False


def history_csv(data):
    # Text columns are formula-escaped; the archive contains no URL or raw log.
    columns = ["time", "session_id", "run_id", "revision", "phase", "signal_state",
               "context.name", "context.input_protocol", "context.video_bitrate_kbps",
               "context.mux_bitrate_kbps", "context.vbv_buffer_kbits", "context.preset",
               "context.gop_frames", "context.bframes", "context.lookahead_frames", "context.threads",
               "context.audio_bitrate_kbps", "version.panel", "version.encoder", "source_fps",
               "metrics.cpu_percent", "metrics.memory_mb", "telemetry.status",
               "telemetry.video_bitrate_kbps", "telemetry.output_bitrate_kbps", "telemetry.video_fps",
               "telemetry.video_frames", "telemetry.video_queue_frames", "telemetry.output_errors",
               "srt.status", "srt.connection", "srt.rtt_ms", "srt.receive_bitrate_kbps",
               *[f"srt.{key}" for key in SRT_COUNTERS], "srt.buffer_bytes", "srt.buffer_ms",
               "srt.buffer_available_bytes", "srt.latency_ms",
               "system.cpu.usage_percent", "system.cpu.load_1m", "system.cpu.load_5m",
               "system.cpu.load_15m", "system.memory.used_bytes", "system.memory.used_percent"]
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(columns)
    for sample in data["samples"]:
        row = []
        for column in columns:
            value = sample
            for key in column.split("."):
                value = value.get(key) if isinstance(value, dict) else None
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                value = "'" + value
            row.append(value)
        writer.writerow(row)
    return output.getvalue().encode("utf-8-sig")
