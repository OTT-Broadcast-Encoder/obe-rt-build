"""Public SRT output contract, also used to generate the native helper's limits."""

SRT_PARAMETERS = {
    "srt_mode": {"type": "string", "default": "caller", "choices": ["caller", "listener"]},
    "latency_ms": {"type": "integer", "default": 200, "min": 20, "max": 8000},
    "srt_key_length": {"type": "integer", "default": 32, "choices": [16, 24, 32]},
    "srt_max_bandwidth_kbps": {"type": "integer", "default": 0, "min": 320,
                               "max": 1000000, "allow_zero": True},
    "srt_connect_timeout_ms": {"type": "integer", "default": 1000, "min": 100, "max": 60000},
    "srt_send_timeout_ms": {"type": "integer", "default": 1000, "min": 100, "max": 60000},
    "srt_drop_late_packets": {"type": "boolean", "default": True},
}
SRT_DEFAULTS = {name: spec["default"] for name, spec in SRT_PARAMETERS.items()}


def valid_option(value, spec):
    if type(value) is not {"string": str, "integer": int, "boolean": bool}[spec["type"]]:
        return False
    if "choices" in spec:
        return value in spec["choices"]
    if spec["type"] == "boolean":
        return True
    return spec.get("allow_zero", False) and value == 0 or spec["min"] <= value <= spec["max"]


def option_error(spec):
    if "choices" in spec:
        return "Valori ammessi: " + ", ".join(map(str, spec["choices"])) + "."
    if spec["type"] == "boolean":
        return "Indica se scartare i pacchetti SRT tardivi."
    prefix = "Usa 0 (automatico) oppure " if spec.get("allow_zero") else "Inserisci un valore tra "
    return f"{prefix}{spec['min']} e {spec['max']}."


def native_header():
    lines = ["/* Generated from srt_options.py; do not edit. */"]
    for name, spec in SRT_PARAMETERS.items():
        if spec["type"] == "string":
            continue
        symbol = "OBE_" + name.upper()
        lines.append(f"#define {symbol}_DEFAULT {int(spec['default'])}")
        choices = [0, 1] if spec["type"] == "boolean" else spec.get("choices")
        if choices:
            condition = " || ".join(f"(value) == {value}" for value in choices)
        else:
            condition = f"((value) >= {spec['min']} && (value) <= {spec['max']})"
            if spec.get("allow_zero"):
                condition = "(value) == 0 || " + condition
        lines.append(f"#define {symbol}_VALID(value) ({condition})")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    print(native_header(), end="")
