"""Read-only host diagnostics from Linux proc/sys; no commands or addresses."""

import copy
from datetime import datetime, timezone
import math
from pathlib import Path
import re
import shutil
import threading
import time

from inputs import capability_report


MAX_INTERFACES = 128
MAX_AGE = 5
COUNTERS = {"rx_bytes": 0, "rx_packets": 1, "rx_errors": 2, "rx_dropped": 3,
            "tx_bytes": 8, "tx_packets": 9, "tx_errors": 10, "tx_dropped": 11}


def _read(path):
    try:
        with path.open(encoding="ascii", errors="replace") as handle:
            return handle.read(262144)
    except OSError:
        return ""


def _integer(value):
    try:
        number = int(value.strip())
        return number if 0 <= number < 2**64 else None
    except ValueError:
        return None


def _float(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except ValueError:
        return None


def _memory(text):
    fields = {}
    for line in text.splitlines():
        match = re.fullmatch(r"([A-Za-z_()]+):\s+(\d+) kB", line)
        if match:
            fields[match[1]] = int(match[2]) * 1024
    total = fields.get("MemTotal")
    available = fields.get("MemAvailable")
    if available is None and all(key in fields for key in ("MemFree", "Buffers", "Cached")):
        available = fields["MemFree"] + fields["Buffers"] + fields["Cached"]
        available += fields.get("SReclaimable", 0) - fields.get("Shmem", 0)
    if not total or available is None:
        return {"total_bytes": total, "available_bytes": None, "used_bytes": None, "used_percent": None}
    available = max(0, min(total, available))
    used = total - available
    return {"total_bytes": total, "available_bytes": available, "used_bytes": used,
            "used_percent": round(100 * used / total, 1)}


def _cpu(text):
    sample, cores = None, 0
    for line in text.splitlines():
        fields = line.split()
        if not fields:
            continue
        if re.fullmatch(r"cpu[0-9]+", fields[0]):
            cores += 1
        elif fields[0] == "cpu" and len(fields) >= 5:
            numbers = [_integer(value) for value in fields[1:9]]
            if all(value is not None for value in numbers):
                # guest/guest_nice (columns 9/10) are already counted in user/nice.
                sample = sum(numbers), numbers[3] + (numbers[4] if len(numbers) > 4 else 0)
    return sample, cores or None


def _network(text):
    devices = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        name, values = line.rsplit(":", 1)
        name, values = name.strip(), values.split()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", name) or name in {".", ".."} or len(values) < 16:
            continue
        counters = {key: _integer(values[index]) for key, index in COUNTERS.items()}
        if any(value is None for value in counters.values()):
            continue
        devices[name] = counters
        if len(devices) == MAX_INTERFACES:
            break
    return devices


def signal_diagnostics(signal):
    """Describe probed format without claiming field order or color detection."""
    source = signal if isinstance(signal, dict) else {}
    result = {"width": None, "height": None, "scan": None, "frame_rate": None,
              "field_rate": None, "field_order": None, "color_primaries": None,
              "color_transfer": None, "color_matrix": None, "color_range": None,
              "field_motion_verified": False, "audio_video_sync_verified": False}
    for key in ("width", "height"):
        value = source.get(key)
        if type(value) is int and 1 <= value <= 16384:
            result[key] = value
    if source.get("scan") in ("i", "p"):
        result["scan"] = source["scan"]
    rate = source.get("fps")
    if isinstance(rate, str) and re.fullmatch(r"[0-9]{1,9}/[0-9]{1,9}", rate):
        numerator, denominator = map(int, rate.split("/"))
        if denominator and 0 < numerator / denominator <= 240:
            result["frame_rate"] = round(numerator / denominator, 5)
            if result["scan"] == "i":
                # Nominal format only: it does not prove 50 distinct field motions.
                result["field_rate"] = round(numerator / denominator * 2, 5)
    return result


class Diagnostics:
    """Keep one instance per server; snapshot rates need two distinct samples.

    proc/sys values describe what this process can see. Cgroup limits are
    reported separately, so a container does not present host memory as its
    own allocation. Failed/missing metrics remain null rather than fake zero.
    """
    def __init__(self, root, proc_dir="/proc", sys_dir="/sys"):
        self.root, self.proc, self.sys = Path(root), Path(proc_dir), Path(sys_dir)
        self.lock = threading.Lock()
        self._previous = None
        self._cached, self._received = None, None

    def _limits(self):
        group = self.sys / "fs" / "cgroup"
        quota = _read(group / "cpu.max").split()
        cores = None
        if len(quota) == 2:
            budget, period = _integer(quota[0]), _integer(quota[1])
            if budget is not None and period:
                cores = round(budget / period, 3)
        return {"cpu_quota_cores": cores,
                "memory_limit_bytes": _integer(_read(group / "memory.max")),
                "memory_current_bytes": _integer(_read(group / "memory.current"))}

    def sample(self):
        with self.lock:
            now = time.monotonic()
            cpu_text = _read(self.proc / "stat")
            cpu, cores = _cpu(cpu_text)
            net = _network(_read(self.proc / "net" / "dev"))
            elapsed = now - self._previous[0] if self._previous else None
            previous_cpu = self._previous[1] if self._previous else None
            previous_net = self._previous[2] if self._previous else {}
            usage = None
            if cpu and previous_cpu and elapsed is not None and elapsed > 0:
                total, idle = cpu[0] - previous_cpu[0], cpu[1] - previous_cpu[1]
                if total > 0 and 0 <= idle <= total:
                    usage = round(100 * (total - idle) / total, 1)
            interfaces = []
            for name, counters in sorted(net.items()):
                device = self.sys / "class" / "net" / name
                state = _read(device / "operstate").strip()
                if state not in {"up", "down", "unknown", "dormant", "notpresent", "lowerlayerdown", "testing"}:
                    state = "unknown"
                speed = _integer(_read(device / "speed"))
                interface = {"interface": name, "state": state,
                             "link_speed_mbps": speed if speed else None,
                             "mtu": _integer(_read(device / "mtu")), **counters}
                previous = previous_net.get(name)
                for direction in ("rx", "tx"):
                    rate = None
                    key = direction + "_bytes"
                    if previous and elapsed is not None and elapsed > 0 and counters[key] >= previous[key]:
                        rate = round((counters[key] - previous[key]) * .008 / elapsed, 2)
                    interface[direction + "_bitrate_kbps"] = rate
                interfaces.append(interface)
            self._previous = now, cpu, net
            loads = _read(self.proc / "loadavg").split()[:3]
            load = [_float(value) for value in loads] if len(loads) == 3 else [None, None, None]
            uptime = _read(self.proc / "uptime").split()
            disk = {"total_bytes": None, "used_bytes": None, "available_bytes": None, "used_percent": None}
            try:
                total, used, free = shutil.disk_usage(self.root)
                disk.update(total_bytes=total, used_bytes=used, available_bytes=free,
                            used_percent=round(100 * used / total, 1) if total else None)
            except OSError:
                pass
            state = {
                "time": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                "scope": "proc_visible", "uptime_seconds": _float(uptime[0]) if uptime else None,
                "interval_seconds": round(elapsed, 3) if elapsed is not None and elapsed > 0 else None,
                "cpu": {"usage_percent": usage, "logical_cpus": cores,
                        "load_1m": load[0], "load_5m": load[1], "load_15m": load[2]},
                "memory": _memory(_read(self.proc / "meminfo")), "disk": disk, "limits": self._limits(),
                "network": interfaces, "capabilities": {"inputs": capability_report(self.root)},
            }
            self._cached, self._received = state, now
            return copy.deepcopy(state)

    def state(self):
        """Read cached measurements only; never perform proc/sys/disk I/O here."""
        with self.lock:
            if self._cached is None:
                return {"status": "pending", "age_seconds": None}
            state = copy.deepcopy(self._cached)
            age = max(0, time.monotonic() - self._received)
            status = "live" if age <= MAX_AGE else "stale"
            if not (state["cpu"]["logical_cpus"] or state["memory"]["total_bytes"] or state["network"]):
                status = "unavailable"
            if status != "live":
                state["cpu"]["usage_percent"] = None
                for interface in state["network"]:
                    interface["rx_bitrate_kbps"] = interface["tx_bitrate_kbps"] = None
            state.update(status=status, age_seconds=round(age, 3))
            return state

    # A direct read is useful for command-line diagnostics. HTTP handlers use
    # state(), while the server's existing monitor calls sample() once a second.
    snapshot = sample
