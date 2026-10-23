"""Portable public profiles and one private, atomic local rollback snapshot."""

import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
from urllib.parse import parse_qsl, unquote, urlsplit


FORMAT = "obe-webpanel-profile"
BACKUP_FORMAT = "obe-webpanel-rollback"
VERSION = 1
MAX_BYTES = 65536
MAX_DEPTH = 8
MAX_ITEMS = 4096
PUBLIC_KEYS = {"format", "version", "config"}
BACKUP_KEYS = PUBLIC_KEYS | {"credentials", "output_credentials"}
SENSITIVE = re.compile(r"pass(?:word|phrase)?|secret|token|auth|credential|crypto|encrypt|api.?key|pbkeylen|keylen|user(?:name)?|login|^key$", re.I)
CREDENTIAL_LIMITS = {"passphrase": 79, "username": 256, "password": 1024, "query": 2048}


class ProfileError(ValueError):
    def __init__(self, field, message):
        super().__init__(message)
        self.field, self.message = field, message


def _invalid(message):
    raise ProfileError("profile", message)


def _shape(value, depth=0, budget=None):
    """Accept only finite JSON data, with bounds before serializing a document."""
    budget = [MAX_ITEMS] if budget is None else budget
    budget[0] -= 1
    if depth > MAX_DEPTH or budget[0] < 0:
        _invalid("Profilo troppo complesso o annidato.")
    if isinstance(value, dict):
        if len(value) > MAX_ITEMS or any(not isinstance(key, str) or len(key) > 256 for key in value):
            _invalid("Chiavi del profilo non valide.")
        for item in value.values():
            _shape(item, depth + 1, budget)
    elif isinstance(value, list):
        if len(value) > MAX_ITEMS:
            _invalid("Profilo troppo grande.")
        for item in value:
            _shape(item, depth + 1, budget)
    elif isinstance(value, str):
        if len(value) > MAX_BYTES or any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            _invalid("Testo del profilo non valido o troppo grande.")
    elif value is None or type(value) in (int, bool):
        if type(value) is int and abs(value) > 2**53 - 1:
            _invalid("Numero del profilo fuori intervallo.")
    elif type(value) is float:
        if not math.isfinite(value):
            _invalid("Il profilo contiene un numero non finito.")
    else:
        _invalid("Il profilo deve contenere soltanto valori JSON.")


def _encode(value):
    _shape(value)
    data = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(data) > MAX_BYTES:
        _invalid("Il profilo supera il limite di 64 KiB.")
    return data


def _clone(value):
    return json.loads(_encode(value))


def _check_public(value):
    """Refuse private data rather than silently exporting a changed endpoint."""
    if isinstance(value, dict):
        for key, item in value.items():
            if SENSITIVE.search(key):
                _invalid("Le credenziali devono restare separate dal profilo esportabile.")
            _check_public(item)
    elif isinstance(value, list):
        for item in value:
            _check_public(item)
    elif isinstance(value, str) and "://" in value:
        decoded = value
        for _ in range(8):
            plain = unquote(decoded)
            if plain == decoded:
                break
            decoded = plain
        else:
            _invalid("URL con codifica eccessivamente annidata.")
        try:
            parts = urlsplit(decoded)
            if parts.username is not None or parts.password is not None or any(SENSITIVE.search(key) for key, _ in parse_qsl(parts.query, keep_blank_values=True)):
                _invalid("Rimuovi le credenziali dagli URL prima di esportare il profilo.")
        except ValueError:
            _invalid("Il profilo contiene un URL non valido o con credenziali.")


def _public_config(config):
    if not isinstance(config, dict):
        _invalid("La configurazione del profilo deve essere un oggetto.")
    result = _clone(config)
    _check_public(result)
    return result


def _check_document(document, private=False):
    allowed = BACKUP_KEYS if private else PUBLIC_KEYS
    expected_format = BACKUP_FORMAT if private else FORMAT
    if not isinstance(document, dict) or set(document) != allowed:
        _invalid("Documento incompleto o con campi sconosciuti.")
    if document["format"] != expected_format or type(document["version"]) is not int or document["version"] != VERSION:
        _invalid("Formato o versione del profilo non supportati.")
    _public_config(document["config"])


def _private_credentials(values, outputs=False):
    if not isinstance(values, dict) or (len(values) > 8 if outputs else set(values) - set(CREDENTIAL_LIMITS)):
        _invalid("Credenziali dello snapshot non valide.")
    for name, value in values.items():
        if outputs and (not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", name)):
            _invalid("Destinazione delle credenziali non valida.")
        limit = 79 if outputs else CREDENTIAL_LIMITS[name]
        if not isinstance(value, str) or len(value.encode("utf-8")) > limit or any(ord(c) < 32 or 0x7f <= ord(c) <= 0x9f for c in value):
            _invalid("Credenziali dello snapshot non valide.")
        if (outputs or name == "passphrase") and value and len(value.encode("utf-8")) < 10:
            _invalid("Passphrase dello snapshot non valida.")


def _no_duplicates(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            _invalid("Chiavi duplicate nello snapshot.")
        result[name] = value
    return result


class Profiles:
    def __init__(self, state_dir):
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / "previous.json"
        self.lock = threading.RLock()

    def export(self, config):
        """Return a detached JSON document containing public configuration only."""
        document = {"format": FORMAT, "version": VERSION, "config": _public_config(config)}
        return _clone(document)

    def parse(self, document, validator):
        """Validate the envelope then delegate actual configuration semantics."""
        document = _clone(document)
        _check_document(document)
        config = validator(document["config"])
        # Validators may normalize defaults. Recheck the result before use.
        return _public_config(config)

    def backup(self, config, credentials, output_credentials):
        """Replace the previous snapshot atomically; never return private values."""
        document = _clone({"format": BACKUP_FORMAT, "version": VERSION,
                           "config": _public_config(config), "credentials": credentials,
                           "output_credentials": output_credentials})
        _private_credentials(document["credentials"])
        _private_credentials(document["output_credentials"], outputs=True)
        encoded = _encode(document)
        with self.lock:
            if self.state_dir.is_symlink():
                _invalid("La directory degli snapshot non può essere un collegamento simbolico.")
            self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.state_dir.chmod(0o700)
            fd, name = tempfile.mkstemp(prefix=".previous-", suffix=".json", dir=self.state_dir)
            temporary = Path(name)
            try:
                with os.fdopen(fd, "wb") as handle:
                    os.fchmod(handle.fileno(), 0o600)
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
                directory = os.open(self.state_dir, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                temporary.unlink(missing_ok=True)

    def previous(self):
        """Return a private snapshot for internal restore, never an API response."""
        with self.lock:
            if self.state_dir.is_symlink():
                _invalid("La directory degli snapshot non può essere un collegamento simbolico.")
            try:
                fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except FileNotFoundError:
                return None
            except OSError:
                _invalid("Snapshot locale non accessibile o non sicuro.")
            with os.fdopen(fd, "rb") as handle:
                details = os.fstat(handle.fileno())
                if not stat.S_ISREG(details.st_mode) or details.st_size > MAX_BYTES:
                    _invalid("Snapshot locale non valido o troppo grande.")
                os.fchmod(handle.fileno(), 0o600)
                raw = handle.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                _invalid("Snapshot locale troppo grande.")
            try:
                document = json.loads(raw, object_pairs_hook=_no_duplicates,
                                      parse_constant=lambda _: _invalid("Snapshot con numero non finito."))
            except (ValueError, UnicodeError, RecursionError):
                _invalid("Snapshot locale danneggiato o non valido.")
            _encode(document)
            _check_document(document, private=True)
            _private_credentials(document["credentials"])
            _private_credentials(document["output_credentials"], outputs=True)
            return document
