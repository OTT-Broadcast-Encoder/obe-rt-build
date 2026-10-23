"""Passive video timing and receiver-side selection; no encoder dependencies."""
from collections import deque
import base64
import math
import statistics
import time
import uuid

WIDTH, HEIGHT, SAMPLE_HZ, SAMPLE_LIMIT = 16, 9, 5, 300


class Clock:
    def __init__(self):
        self.instance = uuid.uuid4().hex
        self.epoch = time.time() * 1000 - time.monotonic() * 1000

    def now(self):
        # One stable clock domain for frames and HTTP calibration, even after NTP steps.
        return time.monotonic() * 1000 + self.epoch


def fingerprint(gray, width=160, height=90):
    if len(gray) != width * height or width % WIDTH or height % HEIGHT:
        raise ValueError("Formato della lettura video non valido.")
    dx, dy = width // WIDTH, height // HEIGHT
    return bytes(round(sum(gray[(y * dy + j) * width + x * dx + i]
                           for j in range(dy) for i in range(dx)) / (dx * dy))
                 for y in range(HEIGHT) for x in range(WIDTH))


def normalized(data):
    mean = sum(data) / len(data)
    spread = math.sqrt(sum((v - mean) ** 2 for v in data) / len(data))
    return [(v - mean) / max(12, spread) for v in data]


def samples_decode(items, clock_ms):
    if not isinstance(items, list) or len(items) > SAMPLE_LIMIT:
        raise ValueError("Serie video non valida.")
    result = []
    for item in items:
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError("Campione video non valido.")
        stamp, encoded = item
        if (type(stamp) not in (int, float) or not math.isfinite(stamp)
                or not clock_ms - 65000 <= stamp <= clock_ms + 10
                or result and stamp <= result[-1][0] or not isinstance(encoded, str) or len(encoded) != 192):
            raise ValueError("Temporizzazione video non valida.")
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) != WIDTH * HEIGHT:
            raise ValueError("Impronta video non valida.")
        result.append((stamp, normalized(raw)))
    return result


def measure_latency(source, receiver, source_offset, receiver_offset, uncertainty_ms):
    """Match a moving sequence, rejecting static, repeated or unrelated content.

    Offsets map agent clocks to the controller monotonic clock. Measurement
    endpoints are NDI/video observation and final decoded return, not the display.
    """
    empty = {"status": "unavailable", "latency_ms": None, "uncertainty_ms": None,
             "method": "video_correlation", "reason": "Campioni insufficienti"}
    if uncertainty_ms > 250:
        return {**empty, "reason": "Incertezza degli orologi superiore a 250 ms"}
    a = samples_decode(source.get("samples", []), source["clock_ms"])
    b = samples_decode(receiver.get("samples", []), receiver["clock_ms"])
    if len(a) < 16 or len(b) < 12:
        return empty
    if source["clock_ms"] - a[-1][0] > 1500 or receiver["clock_ms"] - b[-1][0] > 1500:
        return {**empty, "reason": "Video non aggiornato"}
    window = b[-12:]
    if any(not 0 < window[i][0] - window[i-1][0] <= 1500 for i in range(1, len(window))):
        return {**empty, "reason": "Campionamento discontinuo"}
    motion = sum(sum((x - y) ** 2 for x, y in zip(window[i][1], window[i-1][1])) / (WIDTH * HEIGHT)
                 for i in range(1, len(window))) / (len(window) - 1)
    if motion < .012:
        return {**empty, "reason": "Immagine statica: misura ambigua"}
    candidates = []
    # Compare real sample times, rather than assuming identical capture cadence.
    for end in range(11, len(a)):
        delta = window[-1][0] - receiver_offset - (a[end][0] - source_offset)
        if not 0 <= delta <= 55000:
            continue
        score, differences, valid = 0, [], True
        for position, (stamp, vector) in enumerate(window):
            # The return decoder emits a 5 Hz media sequence. Pipe/network bursts
            # affect observation time, not the content cadence of those frames.
            target = a[end][0] - (len(window) - 1 - position) * 1000 / SAMPLE_HZ
            index = min(range(max(0, end - 16), end + 1), key=lambda k: abs(a[k][0] - target))
            if abs(a[index][0] - target) > 130:
                valid = False
                break
            score += sum((x - y) ** 2 for x, y in zip(vector, a[index][1])) / (WIDTH * HEIGHT)
            differences.append(stamp - receiver_offset - (a[index][0] - source_offset))
        if valid:
            median = statistics.median(differences)
            jitter = max(abs(value - median) for value in differences)
            if jitter <= 750:
                candidates.append((score / len(window), median, jitter))
    candidates.sort()
    if not candidates or candidates[0][0] > .16:
        return {**empty, "reason": "Contenuti non corrispondenti"}
    score, delay, jitter = candidates[0]
    other = [v for v in candidates[1:] if abs(v[1] - delay) > 600]
    if other and min(v[0] for v in other) < max(score * 1.8, score + .035):
        return {**empty, "reason": "Sequenza ripetuta: misura ambigua"}
    return {"status": "measured", "method": "video_correlation", "latency_ms": round(delay),
            "uncertainty_ms": math.ceil(uncertainty_ms + 200 + jitter), "match_error": round(score, 4),
            "observation_jitter_ms": round(jitter),
            "window_ms": round(window[-1][0] - window[0][0]),
            "reason": "Playout → ritorno decodificato"}


class Selector:
    def __init__(self, failure_seconds=2, recovery_seconds=30, hold_seconds=5):
        self.failure, self.recovery, self.hold = failure_seconds, recovery_seconds, hold_seconds
        self.mode, self.active = "auto", None
        self.good_since = dict.fromkeys(("main", "backup"))
        self.bad_since = dict.fromkeys(("main", "backup"))
        self.last_switch, self.switches = None, 0
        self.events = deque(maxlen=50)

    def update(self, health, now=None):
        now = time.monotonic() if now is None else now
        for name in self.good_since:
            if health.get(name) is True:
                self.good_since[name] = now if self.good_since[name] is None else self.good_since[name]
                self.bad_since[name] = None
            else:
                self.bad_since[name] = now if self.bad_since[name] is None else self.bad_since[name]
                self.good_since[name] = None
        stable = lambda name, seconds: self.good_since[name] is not None and now - self.good_since[name] >= seconds
        target = self.active
        if self.mode != "auto":
            target = self.mode if stable(self.mode, 1) else None
        elif self.active is None:
            target = next((n for n in ("main", "backup") if stable(n, 2)), None)
        else:
            other = "backup" if self.active == "main" else "main"
            failed = self.bad_since[self.active]
            held = self.last_switch is None or now - self.last_switch >= self.hold
            if failed is not None and now - failed >= self.failure and stable(other, 2) and held:
                target = other
            elif self.active == "backup" and stable("main", self.recovery) and held:
                target = "main"
        if target != self.active:
            self.events.append({"from": self.active, "to": target, "mode": self.mode})
            self.active, self.last_switch = target, now
            self.switches += 1
        # Both unhealthy: forward nothing; don't oscillate or certify a dead branch.
        return self.active if health.get(self.active) is True else None

    def state(self):
        return {"mode": self.mode, "active": self.active, "switches": self.switches,
                "events": list(self.events), "failure_seconds": self.failure,
                "recovery_seconds": self.recovery, "hold_seconds": self.hold}
