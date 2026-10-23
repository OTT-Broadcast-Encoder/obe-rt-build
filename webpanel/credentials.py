"""Validation and runtime URLs for locally persisted source credentials."""

import base64
import re
import unicodedata
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit


FIELDS = ("passphrase", "username", "password", "query")
DEFAULT_PORTS = {"http": 80, "https": 443, "rtsp": 554}


class CredentialError(ValueError):
    def __init__(self, field, message):
        self.field = field
        super().__init__(message)


def origin(url):
    parts = urlsplit(url)
    return parts.scheme, parts.hostname, parts.port or DEFAULT_PORTS.get(parts.scheme)


def query_pairs(value):
    try:
        if re.search(r"%(?![0-9a-fA-F]{2})", value) or "#" in value or value.startswith("?"):
            raise ValueError()
        pairs = parse_qsl(value, keep_blank_values=True, strict_parsing=True, errors="strict")
        if len(pairs) > 32 or len({key for key, _ in pairs}) != len(pairs):
            raise ValueError()
        if any(not key or any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in key + val)
               for key, val in pairs):
            raise ValueError()
        return pairs
    except (ValueError, UnicodeError):
        raise CredentialError("query", "Usa parametri URL univoci, nel formato token=valore&chiave=valore.") from None


def validate(values, url):
    if not isinstance(values, dict) or set(values) - set(FIELDS):
        raise CredentialError("query", "Campi delle credenziali non validi.")
    result = dict.fromkeys(FIELDS, "")
    for field, value in values.items():
        limit = {"passphrase": 79, "username": 256, "password": 1024, "query": 2048}[field]
        if not isinstance(value, str) or any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in value) or len(value.encode("utf-8")) > limit:
            raise CredentialError(field, f"Usa al massimo {limit} byte, senza caratteri di controllo.")
        result[field] = value
    parts = urlsplit(url)
    if result["passphrase"] and (parts.scheme != "srt" or len(result["passphrase"].encode("utf-8")) < 10):
        raise CredentialError("passphrase", "La passphrase richiede un ingresso SRT e da 10 a 79 byte.")
    for field in ("username", "password", "query"):
        if result[field] and parts.scheme not in DEFAULT_PORTS:
            raise CredentialError(field, "Queste credenziali richiedono un ingresso HTTP, HTTPS o RTSP.")
    if ":" in result["username"]:
        raise CredentialError("username", "Il nome utente non può contenere due punti.")
    if result["password"] and not result["username"]:
        raise CredentialError("username", "Inserisci anche il nome utente.")
    private_pairs = query_pairs(result["query"])
    if {key for key, _ in private_pairs} & {key for key, _ in parse_qsl(parts.query, keep_blank_values=True)}:
        raise CredentialError("query", "Un parametro è già presente nell'URL di ingresso.")
    return result


def input_url(url, values):
    parts = urlsplit(url)
    netloc = parts.netloc
    if values["username"]:
        netloc = quote(values["username"], safe="") + ":" + quote(values["password"], safe="") + "@" + netloc
    extra = urlencode(query_pairs(values["query"]))
    query = "&".join(part for part in (parts.query, extra) if part)
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


def redactions(values):
    secrets = {value for value in values.values() if value}
    secrets.update(value for _, value in query_pairs(values.get("query", "")) if value)
    if values.get("username"):
        basic = (values["username"] + ":" + values.get("password", "")).encode("utf-8")
        secrets.add(base64.b64encode(basic).decode("ascii"))
    return tuple(sorted(secrets, key=len, reverse=True))
