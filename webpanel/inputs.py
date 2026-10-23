"""Validated input options for the existing OBE URL, DeckLink and NDI modules.

The CLI splits input options on commas and equals signs, but preserves spaces.
Keep this module independent of the HTTP server so validation never probes a
device, resolves a network source or starts an encoder.
"""

import ipaddress
from pathlib import Path
import re
import unicodedata


INPUT_DEFAULTS = {
    "input_type": "url",
    "input_card_index": 0,
    "input_video_format": "pal",
    "input_video_connection": "sdi",
    "input_audio_connection": "embedded",
    "ndi_source": "",
}
INPUT_TYPES = ("url", "decklink", "ndi")
VIDEO_FORMATS = (
    "pal", "ntsc", "720p50", "720p59.94", "720p60", "1080i50", "1080i59.94", "1080i60",
    "1080p23.98", "1080p24", "1080p25", "1080p29.97", "1080p30", "1080p50",
    "1080p59.94", "1080p60",
)
VIDEO_CONNECTIONS = ("sdi", "hdmi", "optical-sdi", "component", "composite", "s-video")
AUDIO_CONNECTIONS = ("embedded", "aes-ebu", "analogue")


class InputError(ValueError):
    def __init__(self, field, message):
        super().__init__(message)
        self.field, self.message = field, message


def _safe_text(value):
    return isinstance(value, str) and not any(
        unicodedata.category(c) in {"Cc", "Cf", "Cs", "Zl", "Zp"} or c in ",=\\\"'"
        for c in value
    )


def validate_input(config):
    """Return canonical additive input fields; URL validation belongs to server.

    Unknown top-level fields are intentionally left to the server's complete
    config schema. Inactive options stay validated so switching source type
    cannot activate previously accepted CLI separators.
    """
    if not isinstance(config, dict):
        raise InputError("input_type", "Configurazione ingresso non valida.")
    result = {key: config.get(key, value) for key, value in INPUT_DEFAULTS.items()}
    for key, choices in (
        ("input_type", INPUT_TYPES), ("input_video_format", VIDEO_FORMATS),
        ("input_video_connection", VIDEO_CONNECTIONS), ("input_audio_connection", AUDIO_CONNECTIONS),
    ):
        if not isinstance(result[key], str) or result[key] not in choices:
            raise InputError(key, "Seleziona un valore supportato dall'ingresso OBE.")
    index = result["input_card_index"]
    if type(index) is not int or not 0 <= index <= 63:
        raise InputError("input_card_index", "Usa un indice scheda intero da 0 a 63.")
    name = result["ndi_source"]
    if not _safe_text(name) or len(name.encode("utf-8")) > 255:
        raise InputError("ndi_source", "Usa un nome NDI di massimo 255 byte senza separatori o controlli.")
    name = name.strip()
    if name.startswith("@ "):
        raise InputError("ndi_source", "Inserisci il nome NDI prima dell'indirizzo di discovery.")
    if " @ " in name:
        # The native module interprets this suffix as a discovery address and
        # appends it to NDI_CONFIG_DIR. Do not let a source become a file path.
        source, address = name.rsplit(" @ ", 1)
        try:
            ipaddress.IPv4Address(address)
        except ipaddress.AddressValueError:
            raise InputError("ndi_source", "Dopo « @ » usa solo l'indirizzo IPv4 di discovery.") from None
        if not source.strip() or " @ " in source:
            raise InputError("ndi_source", "Inserisci il nome NDI prima dell'indirizzo di discovery.")
    if result["input_type"] == "ndi" and not name:
        raise InputError("ndi_source", "Inserisci il nome completo della sorgente NDI.")
    result["ndi_source"] = name
    return result


def input_commands(config):
    """Generate the input/probe CLI fragment, never using shell interpolation."""
    settings = validate_input(config)
    kind = settings["input_type"]
    if kind == "url":
        # Full URL protocol/credential validation is performed by the server.
        # Guard the CLI boundary as well, including direct uses of this helper.
        url = config.get("input_url")
        if not isinstance(url, str) or not url or any(
            c.isspace() or unicodedata.category(c) in {"Cc", "Cf", "Cs"} or c in ",\\\"'"
            for c in url
        ):
            raise InputError("input_url", "URL non valido per l'ingresso OBE.")
        options = "location=" + url
    elif kind == "decklink":
        options = ",".join((
            f"card-idx={settings['input_card_index']}",
            f"video-format={settings['input_video_format']}",
            f"video-connection={settings['input_video_connection']}",
            f"audio-connection={settings['input_audio_connection']}",
        ))
        # These rates are explicitly gated in the existing DeckLink module.
        if settings["input_video_format"] in {"1080p50", "1080p59.94", "1080p60"}:
            options += ",allow-1080p60=1"
    else:
        options = "name=" + settings["ndi_source"]
    return f"set input {kind}\nset input opts {options}\nprobe input\n"


def reservation(config):
    """Only a physical capture channel is exclusive; NDI receivers may share."""
    settings = validate_input(config)
    return ("decklink", settings["input_card_index"]) if settings["input_type"] == "decklink" else None


def _build_flags(path):
    try:
        # Read generated build metadata only; templates do not prove support.
        with path.open(encoding="utf-8", errors="replace") as source:
            data = source.read(1024 * 1024)
    except OSError:
        return None
    data = data.replace("\\\n", " ")
    return " ".join(line.split("#", 1)[0] for line in data.splitlines()
                    if re.match(r"\s*(?:DEFS|CFLAGS|CXXFLAGS|CPPFLAGS)\s*[:+?]?=", line))


def _defined(flags, macro):
    definitions = re.findall(r"(?:^|\s)-D" + macro + r"(?:=([01]))?(?=\s|$)", flags)
    return bool(definitions and definitions[-1] != "0")


def capability_report(root):
    """Report build evidence separately from unverified devices/sources.

    Reading a Makefile is not a runtime device or SDK test. No native library
    is loaded and no capture/discovery is started by this status endpoint.
    """
    root = Path(root)
    cli_flags = _build_flags(root / "obe-rt" / "obe" / "Makefile")
    flags = cli_flags if cli_flags is not None else _build_flags(root / "obe-rt" / "Makefile")
    ndi = _defined(flags, "HAVE_PROCESSING_NDI_LIB_H") if flags is not None else None
    decklink = _defined(cli_flags, "HAVE_DECKLINK") if cli_flags is not None else None
    return {
        "url": {"build_supported": True, "available": None,
                "message": "Ingresso di rete supportato; raggiungibilità da verificare con il probe."},
        "decklink": {"build_supported": decklink, "available": None,
                     "message": "Schede, driver e accesso al dispositivo non verificati; eseguire il probe."},
        "ndi": {"build_supported": ndi, "available": None,
                "message": "Runtime NDI e sorgente non verificati; eseguire il probe."},
    }
