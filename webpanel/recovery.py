"""Opt-in desired state persisted separately from portable channel profiles."""
import json
import os
from pathlib import Path
import stat
import tempfile

DEFAULTS = {"auto_restart": False, "resume_on_restart": False, "restart_attempts": 3, "restart_delay_seconds": 2}


def validate(config):
    for key in ("auto_restart", "resume_on_restart"):
        if type(config[key]) is not bool:
            raise ValueError(key)
    for key, bounds in {"restart_attempts": (1, 20), "restart_delay_seconds": (1, 60)}.items():
        if type(config[key]) is not int or not bounds[0] <= config[key] <= bounds[1]:
            raise ValueError(key)


def delays(config, legacy):
    if config["restart_attempts"] == 3 and config["restart_delay_seconds"] == 2:
        return legacy
    return tuple(min(60, config["restart_delay_seconds"] * 2**attempt) for attempt in range(config["restart_attempts"]))


class DesiredState:
    def __init__(self, directory):
        self.path = Path(directory) / "desired.json"

    def read(self):
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 1024:
                raise ValueError("Invalid desired state")
            value = json.load(handle)
        if (not isinstance(value, dict) or set(value) != {"schema", "running", "auto_restart"}
                or type(value["schema"]) is not int or value["schema"] != 1
                or type(value["running"]) is not bool or type(value["auto_restart"]) is not bool):
            raise ValueError("Invalid desired state")
        return value

    def write(self, running, auto_restart=False):
        fd, name = tempfile.mkstemp(prefix=".desired-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"schema": 1, "running": bool(running), "auto_restart": bool(auto_restart)}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)
