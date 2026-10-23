"""Optional local authentication; no plaintext password or bearer token on disk."""

from collections import OrderedDict, deque
from contextlib import contextmanager
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import threading
import time


COOKIE_NAME = "obe_session"
SESSION_TTL = 7 * 24 * 60 * 60
MAX_SESSIONS = 128
PBKDF2_ITERATIONS = 600_000
CLIENT_WINDOW, CLIENT_ATTEMPTS = 900, 5
GLOBAL_WINDOW, GLOBAL_ATTEMPTS = 60, 30
MAX_CLIENTS, MAX_HASHING = 512, 2
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
USERNAME = re.compile(r"[A-Za-z0-9_.@-]{1,64}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class AuthError(Exception):
    def __init__(self, status, code, message, retry_after=None):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.retry_after = retry_after

    def body(self):
        error = {"code": self.code, "message": self.message}
        if self.retry_after is not None:
            error["retry_after"] = self.retry_after
        return {"error": error}


def _unavailable():
    return AuthError(503, "auth_unavailable", "Autenticazione non disponibile: verificare configurazione e permessi.")


def _identity(info):
    return info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size


def _valid_config(data):
    try:
        password = data["password"]
        return (
            type(data) is dict and set(data) == {"schema", "username", "password", "generation", "sessions"}
            and type(data["schema"]) is int and data["schema"] == 1
            and isinstance(data["username"], str) and USERNAME.fullmatch(data["username"])
            and isinstance(data["generation"], str) and re.fullmatch(r"[0-9a-f]{32}", data["generation"])
            and type(password) is dict and set(password) == {"algorithm", "iterations", "salt", "digest"}
            and password["algorithm"] == "pbkdf2_sha256"
            and type(password["iterations"]) is int and 600_000 <= password["iterations"] <= 2_000_000
            and isinstance(password["salt"], str) and HEX64.fullmatch(password["salt"])
            and isinstance(password["digest"], str) and HEX64.fullmatch(password["digest"])
            and type(data["sessions"]) is dict and len(data["sessions"]) <= MAX_SESSIONS
            and all(isinstance(key, str) and HEX64.fullmatch(key) and type(expiry) is int
                    and 0 < expiry < 2**53 for key, expiry in data["sessions"].items())
        )
    except (KeyError, TypeError, AttributeError):
        return False


def _read(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
                raise _unavailable()
            raw = handle.read(131073)
            if len(raw) > 131072:
                raise _unavailable()
            data = json.loads(raw)
        if not _valid_config(data):
            raise _unavailable()
        return data, _identity(info)
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError, UnicodeError):
        raise _unavailable() from None


@contextmanager
def _file_lock(path):
    fd = None
    try:
        fd = os.open(str(path) + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    except OSError:
        raise _unavailable() from None
    finally:
        if fd is not None:
            os.close(fd)


def _write(path, data):
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".auth-", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(data, handle, ensure_ascii=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        raise _unavailable() from None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def setup_auth(path, username, password, overwrite=False):
    """Create/replace the account atomically; replacing revokes every session."""
    if not isinstance(username, str) or not USERNAME.fullmatch(username):
        raise ValueError("Nome utente: 1–64 lettere, numeri o caratteri . _ @ -.")
    try:
        encoded = password.encode("utf-8")
    except (AttributeError, UnicodeError):
        raise ValueError("Password non valida.") from None
    if len(password) < 12 or len(encoded) > 1024:
        raise ValueError("Usa una password di almeno 12 caratteri e massimo 1024 byte.")
    salt = secrets.token_bytes(32)
    digest = hashlib.pbkdf2_hmac("sha256", encoded, salt, PBKDF2_ITERATIONS)
    data = {"schema": 1, "username": username, "generation": secrets.token_hex(16), "sessions": {},
            "password": {"algorithm": "pbkdf2_sha256", "iterations": PBKDF2_ITERATIONS,
                         "salt": salt.hex(), "digest": digest.hex()}}
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    except OSError:
        raise _unavailable() from None
    with _file_lock(path):
        if path.is_symlink():
            raise _unavailable()
        if path.exists() and not overwrite:
            raise ValueError("Autenticazione già configurata; usa --replace per sostituire l'account e revocare le sessioni.")
        _write(path, data)


class Auth:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self._data, self._identity = _read(self.path)
        self._was_enabled = self._data is not None
        self._clients, self._global, self._hashing = OrderedDict(), deque(), 0

    def _refresh(self, force=False):
        try:
            identity = _identity(self.path.lstat())
        except FileNotFoundError:
            identity = None
        except OSError:
            raise _unavailable() from None
        if force or identity != self._identity:
            data, identity = _read(self.path)
            if data is None and self._was_enabled:
                # Deleting a configured file must never silently open a live panel.
                raise _unavailable()
            self._data, self._identity = data, identity
            self._was_enabled = self._was_enabled or data is not None

    @property
    def enabled(self):
        with self.lock:
            self._refresh()
            return self._data is not None

    def _rate_limit(self, client_id):
        now = time.monotonic()
        key = hashlib.sha256(str(client_id)[:256].encode("utf-8", errors="replace")).digest()
        while self._global and self._global[0] <= now - GLOBAL_WINDOW:
            self._global.popleft()
        attempts = self._clients.pop(key, deque())
        while attempts and attempts[0] <= now - CLIENT_WINDOW:
            attempts.popleft()
        self._clients[key] = attempts
        while len(self._clients) > MAX_CLIENTS:
            self._clients.popitem(last=False)
        wait = 0
        if len(attempts) >= CLIENT_ATTEMPTS:
            wait = max(wait, int(attempts[0] + CLIENT_WINDOW - now) + 1)
        if len(self._global) >= GLOBAL_ATTEMPTS:
            wait = max(wait, int(self._global[0] + GLOBAL_WINDOW - now) + 1)
        if self._hashing >= MAX_HASHING:
            wait = max(wait, 1)
        if wait:
            raise AuthError(429, "login_rate_limited", "Troppi tentativi. Attendi prima di riprovare.", wait)
        attempts.append(now)
        self._global.append(now)
        return key

    def login(self, username, password, client_id):
        with self.lock:
            self._refresh()
            if self._data is None:
                raise AuthError(403, "auth_disabled", "Autenticazione non configurata.")
            client_key = self._rate_limit(client_id)
            account = self._data
            self._hashing += 1
        try:
            try:
                value = password.encode("utf-8") if isinstance(password, str) else b""
                user = username.encode("utf-8") if isinstance(username, str) else b""
            except UnicodeError:
                value, user = b"", b""
            valid_size = len(value) <= 1024 and len(user) <= 256
            record = account["password"]
            digest = hashlib.pbkdf2_hmac("sha256", value if valid_size else b"", bytes.fromhex(record["salt"]),
                                         record["iterations"])
            valid = hmac.compare_digest(digest, bytes.fromhex(record["digest"]))
            valid_user = hmac.compare_digest(hashlib.sha256(user).digest(),
                                             hashlib.sha256(account["username"].encode("utf-8")).digest())
        finally:
            with self.lock:
                self._hashing -= 1
        if not valid_size or not valid or not valid_user:
            raise AuthError(401, "invalid_credentials", "Nome utente o password non validi.")
        with self.lock, _file_lock(self.path):
            self._refresh(force=True)
            if self._data is None or self._data["generation"] != account["generation"]:
                raise AuthError(401, "invalid_credentials", "Nome utente o password non validi.")
            now = int(time.time())
            sessions = {key: expiry for key, expiry in self._data["sessions"].items() if expiry > now}
            while len(sessions) >= MAX_SESSIONS:
                del sessions[min(sessions, key=sessions.get)]
            token = secrets.token_urlsafe(32)
            sessions[hashlib.sha256(token.encode("ascii")).hexdigest()] = now + SESSION_TTL
            data = {**self._data, "sessions": sessions}
            _write(self.path, data)
            self._refresh(force=True)
            self._clients.pop(client_key, None)
            return token

    def verify(self, token):
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            return False
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        with self.lock:
            self._refresh()
            if self._data is None:
                return False
            now, valid = time.time(), False
            for saved, expiry in self._data["sessions"].items():
                matched = hmac.compare_digest(digest, saved)
                valid = (matched and expiry > now) or valid
            return valid

    def logout(self, token):
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            return
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        with self.lock:
            self._refresh()
            if self._data is None:
                return
            with _file_lock(self.path):
                self._refresh(force=True)
                if digest in self._data["sessions"]:
                    data = {**self._data, "sessions": {key: value for key, value in self._data["sessions"].items()
                                                       if key != digest}}
                    _write(self.path, data)
                    self._refresh(force=True)

    @staticmethod
    def cookie(token, secure):
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            raise ValueError("Token di sessione non valido.")
        return (f"{COOKIE_NAME}={token}; Path=/; Max-Age={SESSION_TTL}; HttpOnly; SameSite=Strict"
                + ("; Secure" if secure else ""))

    @staticmethod
    def clear_cookie(secure):
        return (f"{COOKIE_NAME}=; Path=/; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT; HttpOnly; SameSite=Strict"
                + ("; Secure" if secure else ""))
