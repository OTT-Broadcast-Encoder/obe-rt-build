"""Additive broadcast settings and the corresponding OBE CLI commands.

Zero/auto/source defaults deliberately leave the engine's historical defaults
alone. This module validates configuration, not the codecs installed at runtime.
"""

import math
import re


X264_DEFAULTS = {
    "x264_speedcontrol": "adaptive", "qcomp": None, "ipratio": None, "pbratio": None,
    "qp_min": None, "qp_max": None, "qp_step": None, "chroma_qp_offset": None,
    "min_gop_frames": None, "b_bias": None, "open_gop": "auto", "mbtree": "auto",
    "deblock": "auto", "deblock_alpha": None, "deblock_beta": None,
    "psy": "auto", "psy_rd": None, "psy_trellis": None, "direct_pred": "preset",
    "weighted_bipred": "auto", "weighted_pred": "auto", "fast_pskip": "auto",
    "transform_8x8": "auto", "entropy_coding": "auto", "motion_estimation": "auto",
    "motion_range": None, "subme": None, "trellis": "auto", "mixed_refs": "auto",
    "partitions": "auto",
}
X264_INTEGERS = {
    "qp_min": (0, 51), "qp_max": (0, 51), "qp_step": (2, 51),
    "chroma_qp_offset": (-12, 12), "min_gop_frames": (1, 151), "b_bias": (-90, 100),
    "deblock_alpha": (-6, 6), "deblock_beta": (-6, 6),
    "motion_range": (4, 64), "subme": (0, 11),
}
X264_NUMBERS = {"qcomp": (0, 1), "ipratio": (0.01, 10), "pbratio": (0.01, 10),
                "psy_rd": (0, 10), "psy_trellis": (0, 10)}
FIXED_X264_FIELDS = ("motion_estimation", "motion_range", "subme", "trellis",
                     "mixed_refs", "partitions", "psy_rd", "psy_trellis")

EXTENDED_DEFAULTS = {
    **X264_DEFAULTS,
    "video_profile": "auto", "video_level": "auto", "video_tune": "none",
    "vbv_maxrate_kbps": 0, "reference_frames": 0, "b_pyramid": "auto",
    "b_adapt": "auto", "scenecut_threshold": None,
    "aq_mode": "auto", "aq_strength": None,
    "field_order": "source", "aspect_ratio": "source",
    "audio_codec": "mp2", "aac_profile": "aac-lc", "aac_encapsulation": "adts",
    "mp2_mode": "auto", "audio_gain_db": 0, "audio_offset_ms": 0,
    "audio_language": "", "audio_type": "undefined",
    "audio_tracks": None,
    "audio_normalize": False, "audio_loudness_lufs": -23.0,
    "audio_true_peak_dbtp": -1.0, "audio_loudness_range_lu": 7.0,
    "ts_id": 0, "program_number": 0, "pmt_pid": 0, "video_pid": 0,
    "audio_pid": 0, "pcr_pid": 0, "pcr_period_ms": 0, "pat_period_ms": 0,
    "service_name": "", "provider_name": "",
}

ENUMS = {
    "x264_speedcontrol": {"adaptive", "fixed"},
    **{field: {"auto", "0", "1"} for field in ("open_gop", "mbtree", "deblock", "psy",
        "weighted_bipred", "fast_pskip", "transform_8x8", "mixed_refs")},
    "direct_pred": {"preset", "none", "spatial", "temporal", "auto"},
    "weighted_pred": {"auto", "0", "1", "2"}, "trellis": {"auto", "0", "1", "2"},
    "entropy_coding": {"auto", "cabac", "cavlc"},
    "motion_estimation": {"auto", "dia", "hex", "umh"},
    "partitions": {"auto", "none", "most", "all"},
    "video_profile": {"auto", "baseline", "main", "high", "high422"},
    "video_level": {"auto", "1", "1b", "1.1", "1.2", "1.3", "2", "2.1", "2.2",
                    "3", "3.1", "3.2", "4", "4.1", "4.2", "5", "5.1", "5.2", "6", "6.1", "6.2"},
    "video_tune": {"none", "animation", "zerolatency", "fastdecode", "grain", "ssim", "psnr"},
    "b_pyramid": {"auto", "none", "strict", "normal"},
    "b_adapt": {"auto", "0", "1", "2"},
    "aq_mode": {"auto", "0", "1", "2", "3"},
    "field_order": {"source", "tff", "bff"},
    "aspect_ratio": {"source", "4:3", "16:9"},
    "audio_codec": {"mp2", "aac", "ac3", "e-ac3"},
    "aac_profile": {"aac-lc", "he-aac-v1", "he-aac-v2"},
    "aac_encapsulation": {"adts", "latm"},
    "mp2_mode": {"auto", "stereo", "joint-stereo", "dual-channel"},
    "audio_type": {"undefined", "clean-effects", "hearing-impaired", "visual-impaired"},
}
MP2_BITRATES = {64, 96, 128, 160, 192, 224, 256, 320, 384}
AC3_BITRATES = {64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384, 448, 512, 576, 640}
PID_FIELDS = ("pmt_pid", "video_pid", "audio_pid", "pcr_pid")
MAX_AUDIO_TRACKS = 8
AUDIO_TRACK_DEFAULTS = {"source": 1, "enabled": True, "pid": 0, "language": "", "audio_type": "undefined"}


class ConfigurationError(ValueError):
    def __init__(self, field, message):
        super().__init__(message)
        self.field, self.message = field, message


def _invalid(field, message):
    raise ConfigurationError(field, message)


def _integer(config, field, minimum, maximum):
    value = config[field]
    if type(value) is not int or not minimum <= value <= maximum:
        _invalid(field, f"Inserisci un intero tra {minimum} e {maximum}.")


def active_audio_tracks(config):
    """Missing/null keeps the historical automatic first stereo track."""
    tracks = config.get("audio_tracks")
    if tracks is None:
        return [{**AUDIO_TRACK_DEFAULTS, "pid": config.get("audio_pid", 0),
                 "language": config.get("audio_language", ""), "audio_type": config.get("audio_type", "undefined")}]
    return [track for track in tracks if track["enabled"]]


def audio_bitrate_total(config):
    return len(active_audio_tracks(config)) * config.get("audio_bitrate_kbps", 0)


def _validate_audio_tracks(config):
    tracks = config["audio_tracks"]
    if tracks is None:
        return
    if not isinstance(tracks, list) or len(tracks) > MAX_AUDIO_TRACKS:
        _invalid("audio_tracks", "Configura al massimo 8 tracce audio.")
    result, used = [], set()
    for i, value in enumerate(tracks):
        prefix = f"audio_tracks.{i}."
        if not isinstance(value, dict) or set(value) - set(AUDIO_TRACK_DEFAULTS):
            _invalid(f"audio_tracks.{i}", "Traccia audio non valida o con campi sconosciuti.")
        track = {**AUDIO_TRACK_DEFAULTS, **value}
        for field, low, high in (("source", 1, MAX_AUDIO_TRACKS), ("pid", 0, 8190)):
            if type(track[field]) is not int or not low <= track[field] <= high:
                _invalid(prefix + field, f"Usa un intero tra {low} e {high}.")
        if 0 < track["pid"] < 32:
            _invalid(prefix + "pid", "Usa 0 per automatico oppure un PID tra 32 e 8190.")
        if type(track["enabled"]) is not bool:
            _invalid(prefix + "enabled", "Usa un valore booleano.")
        language = track["language"]
        if not isinstance(language, str) or (language and not re.fullmatch(r"[a-zA-Z]{3}", language)):
            _invalid(prefix + "language", "Usa un codice lingua ISO 639 di tre lettere, oppure lascia vuoto.")
        track["language"] = language.lower()
        if not isinstance(track["audio_type"], str) or track["audio_type"] not in ENUMS["audio_type"]:
            _invalid(prefix + "audio_type", "Tipo audio non supportato.")
        if track["audio_type"] != "undefined" and not language:
            _invalid(prefix + "language", "Il tipo di audio richiede anche il codice lingua.")
        if track["enabled"]:
            if track["source"] in used:
                _invalid(prefix + "source", "Questa sorgente è già assegnata a una traccia abilitata.")
            used.add(track["source"])
        result.append(track)
    config["audio_tracks"] = result


def _validate_x264(config):
    for field, limits in X264_INTEGERS.items():
        if config[field] is not None:
            _integer(config, field, *limits)
    for field, (minimum, maximum) in X264_NUMBERS.items():
        value = config[field]
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value)
                                  or not minimum <= value <= maximum):
            _invalid(field, f"Usa un valore tra {minimum} e {maximum}, oppure lascia vuoto.")
    if config["x264_speedcontrol"] != "fixed":
        for field in FIXED_X264_FIELDS:
            if config[field] != X264_DEFAULTS[field]:
                _invalid(field, "Seleziona qualità fissa: speedcontrol sovrascrive i parametri di analisi.")
    for first, second, switch in (("deblock_alpha", "deblock_beta", "deblock"),
                                   ("psy_rd", "psy_trellis", "psy")):
        if (config[first] is None) != (config[second] is None):
            _invalid(second if config[second] is None else first, "Compila entrambi i valori oppure lasciali entrambi vuoti.")
        if config[first] is not None and config[switch] == "0":
            _invalid(first, "Svuota i valori quando la funzione è disattivata.")
    if config["qp_min"] is not None and config["qp_max"] is not None and config["qp_min"] > config["qp_max"]:
        _invalid("qp_max", "Il QP massimo deve essere almeno pari al minimo.")
    if config["min_gop_frames"] is not None and config.get("gop_frames") is not None:
        if config["min_gop_frames"] > config["gop_frames"] // 2 + 1:
            _invalid("min_gop_frames", "x264 limita il GOP minimo a metà del GOP massimo più un frame.")
    if config["mbtree"] == "1" and (config.get("lookahead_frames") == 0 or config.get("gop_frames") == 1 or config["qcomp"] == 1):
        _invalid("mbtree", "MBtree richiede lookahead, GOP maggiore di 1 e qcomp inferiore a 1.")
    if config["pbratio"] is not None and config["mbtree"] != "0":
        _invalid("pbratio", "Il rapporto P/B è applicato solo con MBtree disattivato.")
    if config.get("bframes") == 0:
        for field in ("b_bias", "weighted_bipred", "open_gop", "pbratio", "direct_pred"):
            if config[field] not in (X264_DEFAULTS[field], "0", "none"):
                _invalid(field, "Questa opzione richiede almeno un B-frame.")
    if config["b_bias"] is not None and config["b_adapt"] == "0":
        _invalid("b_bias", "La preferenza B-frame richiede B-frame adattivi.")
    if config["transform_8x8"] == "1" and config["video_profile"] in {"baseline", "main"}:
        _invalid("transform_8x8", "La trasformata 8×8 richiede un profilo High.")
    if config["entropy_coding"] == "cabac" and config["video_profile"] == "baseline":
        _invalid("entropy_coding", "Baseline supporta solo CAVLC.")
    if config["trellis"] in {"1", "2"} and (config["entropy_coding"] == "cavlc" or config["video_profile"] == "baseline"):
        _invalid("trellis", "Trellis richiede CABAC.")
    if config["subme"] is not None and config["subme"] >= 10 and (config["trellis"] != "2" or config["aq_mode"] in {"auto", "0"}):
        _invalid("subme", "Subme 10–11 richiede Trellis 2 e una modalità AQ esplicita da 1 a 3.")
    if config["motion_range"] is not None and config["motion_range"] > 16 and config["motion_estimation"] != "umh":
        _invalid("motion_range", "Per un raggio oltre 16 seleziona ricerca UMH.")
    if (config["psy_rd"] or config["psy_trellis"]) and config["psy"] == "auto" and config["video_tune"] in {"psnr", "ssim"}:
        _invalid("psy", "Il tune selezionato disattiva psy: abilitalo esplicitamente per usare queste intensità.")
    if config["psy_rd"] and config["subme"] is not None and config["subme"] < 6:
        _invalid("psy_rd", "Psy-RD richiede Subme almeno 6.")
    if config["psy_trellis"] and (config["trellis"] == "0" or config["entropy_coding"] == "cavlc" or config["video_profile"] == "baseline"):
        _invalid("psy_trellis", "Psy-Trellis richiede Trellis e CABAC.")


def validate_extended(config):
    """Return a new full config, filling missing additive fields.

    The caller remains responsible for base fields and rejecting unknown keys.
    """
    if not isinstance(config, dict):
        _invalid("config", "La configurazione deve essere un oggetto.")
    result = {**EXTENDED_DEFAULTS, **config}
    _validate_audio_tracks(result)
    if type(result["audio_normalize"]) is not bool:
        _invalid("audio_normalize", "Usa un valore booleano.")
    for field, low, high in (("audio_loudness_lufs", -70, -5), ("audio_true_peak_dbtp", -9, 0), ("audio_loudness_range_lu", 1, 50)):
        value = result[field]
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            _invalid(field, f"Inserisci un valore finito tra {low} e {high}.")
    for field, options in ENUMS.items():
        if not isinstance(result[field], str) or result[field] not in options:
            _invalid(field, "Opzione non supportata.")
    _validate_x264(result)
    for field, limits in {
        "vbv_maxrate_kbps": (0, 50000), "reference_frames": (0, 16),
        "audio_offset_ms": (-10000, 10000), "ts_id": (0, 65535),
        "program_number": (0, 65535), "pcr_period_ms": (0, 40),
        "pat_period_ms": (0, 500), **{field: (0, 8190) for field in PID_FIELDS},
    }.items():
        _integer(result, field, *limits)
    if result["scenecut_threshold"] is not None:
        _integer(result, "scenecut_threshold", 0, 100)
    strength = result["aq_strength"]
    if strength is not None and (type(strength) not in (int, float) or not math.isfinite(strength)
                                 or not 0 <= strength <= 3):
        _invalid("aq_strength", "Inserisci un valore tra 0 e 3, oppure lascia vuoto per il preset.")
    if result["aq_mode"] == "0" and strength is not None:
        _invalid("aq_strength", "Svuota la forza AQ quando la modalità AQ è disattivata.")
    if result["b_adapt"] != "auto" and result.get("bframes") == 0:
        _invalid("b_adapt", "I B-frame adattivi richiedono almeno un B-frame.")
    for field in PID_FIELDS:
        if 0 < result[field] < 32:
            _invalid(field, "Usa 0 per automatico oppure un PID tra 32 e 8190.")
    pid_fields = {field: result[field] for field in PID_FIELDS if field != "audio_pid" or result["audio_tracks"] is None}
    if result["audio_tracks"] is not None:
        pid_fields.update({f"audio_tracks.{i}.pid": track["pid"] for i, track in enumerate(result["audio_tracks"]) if track["enabled"]})
    seen_pids = {}
    for field, pid in pid_fields.items():
        for other, used_pid in seen_pids.items():
            if pid and pid == used_pid and {field, other} != {"video_pid", "pcr_pid"}:
                owner = (f"la traccia audio {int(other.split('.')[1]) + 1}" if other.startswith("audio_tracks.")
                         else {"pmt_pid": "la PMT", "video_pid": "il video", "audio_pid": "l'audio", "pcr_pid": "il PCR"}[other])
                _invalid(field, f"Il PID è già usato per {owner}.")
        seen_pids[field] = pid
    gain = result["audio_gain_db"]
    if type(gain) not in (int, float) or not math.isfinite(gain) or not -60 <= gain <= 20:
        _invalid("audio_gain_db", "Inserisci un guadagno finito tra -60 e +20 dB.")
    language = result["audio_language"]
    if not isinstance(language, str) or (language and not re.fullmatch(r"[a-z]{3}", language)):
        _invalid("audio_language", "Usa un codice lingua ISO 639 di tre lettere minuscole, oppure lascia vuoto.")
    if result["audio_type"] != "undefined" and not language:
        _invalid("audio_language", "Il tipo di audio richiede anche il codice lingua.")
    for field in ("service_name", "provider_name"):
        value = result[field]
        # OBE consumes the rest of the command verbatim. Quotes are literal;
        # comma/backslash split options. Restrict encoding to unambiguous DVB text.
        if (not isinstance(value, str) or len(value) > 63 or value != value.strip()
                or any(ord(char) < 32 or ord(char) > 126 or char in ",=\\\"'`|;<>" for char in value)):
            _invalid(field, "Usa al massimo 63 caratteri ASCII stampabili; spazi interni consentiti, separatori e virgolette esclusi.")
    bitrate = result.get("audio_bitrate_kbps")
    if bitrate is not None:
        _integer(result, "audio_bitrate_kbps", 16, 640)
        codec = result["audio_codec"]
        if codec == "mp2" and bitrate not in MP2_BITRATES:
            _invalid("audio_bitrate_kbps", "Bitrate MP2 non supportato (64–384 kb/s).")
        if codec == "ac3" and bitrate not in AC3_BITRATES:
            _invalid("audio_bitrate_kbps", "Bitrate AC-3 non supportato (64–640 kb/s).")
        if codec == "e-ac3" and bitrate < 64:
            _invalid("audio_bitrate_kbps", "Per E-AC-3 stereo usa almeno 64 kb/s.")
        if codec == "aac":
            limits = {"aac-lc": (32, 512), "he-aac-v1": (32, 128), "he-aac-v2": (16, 64)}[result["aac_profile"]]
            if not limits[0] <= bitrate <= limits[1]:
                _invalid("audio_bitrate_kbps", f"Per questo profilo AAC usa {limits[0]}–{limits[1]} kb/s.")
    maximum = result["vbv_maxrate_kbps"] or result.get("video_bitrate_kbps", 0)
    if maximum and maximum < result.get("video_bitrate_kbps", 0):
        _invalid("vbv_maxrate_kbps", "Il massimo VBV non può essere inferiore al bitrate video.")
    if "mux_bitrate_kbps" in result and maximum + audio_bitrate_total(result) + 128 > result["mux_bitrate_kbps"]:
        _invalid("mux_bitrate_kbps", "Il mux deve includere il massimo video VBV, tutte le tracce audio abilitate e almeno 128 kb/s di margine.")
    if result["video_profile"] == "baseline":
        if result.get("bframes", 0):
            _invalid("bframes", "Il profilo Baseline richiede zero B-frame.")
        if result["field_order"] != "source":
            _invalid("video_profile", "Il profilo Baseline non supporta video interlacciato.")
    return result


def effective_pids(config):
    """Allocate automatic PIDs around explicit choices, avoiding collisions.

    All-zero configuration emits no PIDs, preserving legacy engine allocation.
    """
    return _stream_pids(config)[0]


def _stream_pids(config):
    values = {field: config[field] for field in PID_FIELDS}
    tracks = active_audio_tracks(config)
    audio_pids = [track["pid"] for track in tracks]
    values["audio_pid"] = audio_pids[0] if audio_pids else 0
    if config.get("audio_tracks") is None and not any(values.values()):
        return values, audio_pids
    used = {value for value in (*values.values(), *audio_pids) if value}
    next_pid = 48  # Historical OBE MIN_PID, after DVB reserved PIDs.
    for field in ("pmt_pid", "video_pid"):
        if not values[field]:
            while next_pid in used:
                next_pid += 1
            values[field] = next_pid
            used.add(next_pid)
    for i, pid in enumerate(audio_pids):
        if not pid:
            while next_pid in used:
                next_pid += 1
            audio_pids[i] = next_pid
            used.add(next_pid)
    values["audio_pid"] = audio_pids[0] if audio_pids else 0
    values["pcr_pid"] = values["pcr_pid"] or values["video_pid"]
    return values, audio_pids


def video_options(config, video_id, signal):
    """Build one stream command, refusing interlace overrides on progressive input."""
    config = validate_extended(config)
    field_order = config["field_order"]
    if field_order != "source" and (not signal or signal.get("scan") != "i"):
        _invalid("field_order", "TFF/BFF richiede una sorgente interlacciata: questa opzione non converte il video progressivo.")
    if config["video_profile"] == "baseline" and signal and signal.get("scan") == "i":
        _invalid("video_profile", "Il profilo Baseline non supporta la sorgente interlacciata.")
    if config["weighted_pred"] in {"1", "2"} and (field_order != "source" or signal and signal.get("scan") == "i"):
        _invalid("weighted_pred", "La predizione pesata P non è supportata sulle sorgenti interlacciate.")
    options = ["format=avc", f"preset={config['preset']}", f"bitrate={config['video_bitrate_kbps']}",
               f"vbv-maxrate={config['vbv_maxrate_kbps'] or config['video_bitrate_kbps']}",
               f"vbv-bufsize={config['vbv_buffer_kbits']}", f"thread={config['threads']}",
               f"bframes={config['bframes']}", f"keyint={config['gop_frames']}",
               f"rc-lookahead={config['lookahead_frames']}"]
    for field, option, default in (("video_profile", "profile", "auto"), ("video_tune", "tune", "none"),
                                   ("aspect_ratio", "aspect-ratio", "source")):
        if config[field] != default:
            options.append(f"{option}={config[field]}")
    if config["video_level"] != "auto":
        # OBE interprets decimal levels only below 6; use integer level_idc.
        level = config["video_level"]
        options.append(f"level={9 if level == '1b' else round(float(level) * 10)}")
    if config["reference_frames"]:
        options.append(f"max-refs={config['reference_frames']}")
    if config["b_pyramid"] != "auto":
        options.append(f"b-pyramid={dict(none=0, strict=1, normal=2)[config['b_pyramid']]}")
    for field, option in (("b_adapt", "b-adapt"), ("aq_mode", "aq-mode")):
        if config[field] != "auto":
            options.append(f"{option}={config[field]}")
    if config["scenecut_threshold"] is not None:
        options.append(f"scenecut={config['scenecut_threshold']}")
    if config["aq_strength"] is not None:
        options.append(f"aq-strength={config['aq_strength']:g}")
    if config["x264_speedcontrol"] == "fixed":
        options.append("speedcontrol=0")
    for field, option in (("qcomp", "qcomp"), ("ipratio", "ipratio"), ("pbratio", "pbratio"),
                          ("qp_min", "qpmin"), ("qp_max", "qpmax"), ("qp_step", "qpstep"),
                          ("chroma_qp_offset", "chroma-qp-offset"), ("min_gop_frames", "min-keyint"),
                          ("b_bias", "b-bias"), ("motion_range", "merange"), ("subme", "subme")):
        if config[field] is not None:
            options.append(f"{option}={config[field]:g}")
    for field, option in (("mbtree", "mbtree"), ("open_gop", "open-gop"), ("psy", "psy"),
                          ("weighted_bipred", "weightb"), ("weighted_pred", "weightp"),
                          ("fast_pskip", "fast-pskip"), ("transform_8x8", "8x8dct"),
                          ("entropy_coding", "entropy"), ("motion_estimation", "me"),
                          ("trellis", "trellis"), ("mixed_refs", "mixed-refs")):
        if config[field] != "auto":
            options.append(f"{option}={config[field]}")
    if config["direct_pred"] != "preset":
        options.append(f"direct={config['direct_pred']}")
    if config["partitions"] != "auto":
        partitions = "i4x4+i8x8+p8x8+b8x8" if config["partitions"] == "most" else config["partitions"]
        options.append(f"partitions={partitions}")
    if config["deblock_alpha"] is not None:
        options.append(f"deblock={config['deblock_alpha']}:{config['deblock_beta']}")
    elif config["deblock"] != "auto":
        options.append(f"deblock={config['deblock']}")
    if config["psy_rd"] is not None:
        options.append(f"psy-rd={config['psy_rd']:g}:{config['psy_trellis']:g}")
    if field_order != "source":
        options.extend(("interlaced=1", f"tff={int(field_order == 'tff')}"))
    pid = effective_pids(config)["video_pid"]
    if pid:
        options.append(f"pid={pid}")
    return f"set stream opts {video_id}:" + ",".join(options) + "\n"


def audio_options(config, audio_id):
    if audio_id is None:
        return ""
    config = validate_extended(config)
    return _audio_options(config, audio_id, effective_pids(config)["audio_pid"],
                          config["audio_language"], config["audio_type"])


def _audio_options(config, audio_id, pid, language, audio_type):
    options = [f"format={config['audio_codec']}", f"bitrate={config['audio_bitrate_kbps']}", "channel-map=stereo"]
    if config["audio_codec"] == "aac":
        options.extend((f"aac-profile={config['aac_profile']}", f"aac-encap={config['aac_encapsulation']}"))
    if config["audio_codec"] == "mp2" and config["mp2_mode"] != "auto":
        options.append(f"mp2-mode={config['mp2_mode']}")
    if config["audio_gain_db"]:
        options.append(f"gain={config['audio_gain_db']:.6f}".rstrip("0").rstrip("."))
    if config["audio_normalize"]:
        options.extend(("audio-normalize=1", f"loudness-i={config['audio_loudness_lufs']:.3f}",
                        f"loudness-tp={config['audio_true_peak_dbtp']:.3f}", f"loudness-lra={config['audio_loudness_range_lu']:.3f}"))
    if config["audio_offset_ms"]:
        options.append(f"audio-offset={config['audio_offset_ms']}")
    if language:
        options.extend((f"lang={language}", f"audio-type={audio_type}"))
    if pid:
        options.append(f"pid={pid}")
    return f"set stream opts {audio_id}:" + ",".join(options) + "\n"


def audio_commands(config, sources):
    """Configure selected probed streams, then remove unused outputs in reverse order."""
    config = validate_extended(config)
    tracks = active_audio_tracks(config)
    if not sources and config["audio_tracks"] is None:
        return ""  # Preserve legacy video-only sources.
    pids = _stream_pids(config)[1]
    commands, selected = [], set()
    for track, pid in zip(tracks, pids):
        if track["source"] > len(sources):
            _invalid("audio_tracks", f"Sorgente audio {track['source']} assente: il probe ha rilevato {len(sources)} tracce.")
        source = sources[track["source"] - 1]
        if "digital bitstream" in source.get("description", ""):
            _invalid("audio_tracks", "Seleziona una sorgente PCM; la ricodifica del bitstream SDI non è supportata.")
        selected.add(source["id"])
        commands.append(_audio_options(config, source["id"], pid, track["language"], track["audio_type"]))
    commands.extend(f"remove stream {source['id']}\n" for source in sorted(sources, key=lambda source: source["id"], reverse=True)
                    if source["id"] not in selected)
    return "".join(commands)


def mux_options(config):
    config = validate_extended(config)
    options = [f"ts-muxrate={config['mux_bitrate_kbps'] * 1000}", "cbr=1", "ts-type=dvb"]
    for field, option in (("ts_id", "ts-id"), ("program_number", "program-num"),
                          ("pcr_period_ms", "pcr-period"), ("pat_period_ms", "pat-period"),
                          ("service_name", "service-name"), ("provider_name", "provider-name")):
        if config[field]:
            options.append(f"{option}={config[field]}")
    for field in ("pmt_pid", "pcr_pid"):
        value = effective_pids(config)[field]
        if value:
            options.append(f"{field.replace('_', '-')}={value}")
    return "set muxer opts " + ",".join(options) + "\n"
