"""Per-channel script storage, syntax validation and bounded execution tests."""

from fractions import Fraction
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import warnings

from configuration import ConfigurationError


MAX_BYTES = 8192
DEFAULT_SCRIPT = "import vapoursynth as vs\n\nclip.set_output()\n"
DEFAULTS = {"vapoursynth_enabled": False, "vapoursynth_script": DEFAULT_SCRIPT}
TEST_TIMEOUT = 60
MAX_LOG_BYTES = 16384
TEST_FRAMES = (0, 1, 2, 12, 24)
PIXEL_FORMATS = {"YUV420P8": 8, "YUV420P10": 10, "YUV422P10": 10}
# Values from VapourSynth VSConstants4.h; _ColorRange uses the legacy convention.
PROP_VALUES = {
    "_FieldBased": {0, 1, 2}, "_Matrix": {1, 2, 4, 5, 6, 7, 8, 9, 10, 12, 13, 14},
    "_Primaries": {1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 22},
    "_Transfer": {1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 13, 14, 15, 16, 17, 18},
    "_ColorRange": {0, 1}, "_Range": {0, 1}, "_ChromaLocation": set(range(6)),
}
FRAME_PROPS = (*PROP_VALUES, "_SARNum", "_SARDen", "_DurationNum", "_DurationDen", "_AbsoluteTime")
_test_lock = threading.Lock()


class TestBusy(Exception):
    pass


def validate_script(source, syntax=True):
    if not isinstance(source, str):
        raise ConfigurationError("vapoursynth_script", "Lo script deve essere testo Python.")
    try:
        size = len(source.encode("utf-8"))
    except UnicodeError:
        raise ConfigurationError("vapoursynth_script", "Lo script contiene caratteri non validi.") from None
    if size > MAX_BYTES or "\x00" in source:
        raise ConfigurationError("vapoursynth_script", "Massimo 8 KiB UTF-8; caratteri NUL non consentiti.")
    if syntax:
        if not source.strip():
            raise ConfigurationError("vapoursynth_script", "Inserisci uno script con output video 0.")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                compile(source, "channel.vpy", "exec")
        except SyntaxError as error:
            location = f"Riga {error.lineno or 1}, colonna {error.offset or 1}"
            raise ConfigurationError("vapoursynth_script", f"{location}: {error.msg}.") from None
        except (ValueError, RecursionError, OverflowError):
            raise ConfigurationError("vapoursynth_script", "Script non valido o troppo complesso.") from None
    return source


def validate(config):
    if type(config["vapoursynth_enabled"]) is not bool:
        raise ConfigurationError("vapoursynth_enabled", "Usa un valore booleano.")
    validate_script(config["vapoursynth_script"], syntax=config["vapoursynth_enabled"])


def prepare(source):
    """Private immutable snapshot for one encoder process, outside the CLI input."""
    folder = tempfile.TemporaryDirectory(prefix="obe-vapoursynth-", dir="/tmp", ignore_cleanup_errors=True)
    try:
        path = Path(folder.name) / "channel.vpy"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(source)
    except BaseException:
        folder.cleanup()
        raise
    return folder, str(path)


def test_context(source=None, video_format="1080i50", field_order="auto"):
    """Describe synthetic pixels using detected frame metadata where available."""
    formats = {"pal": (720, 576, 25, 1), "ntsc": (720, 480, 30000, 1001)}
    for size, width, height in (("720p", 1280, 720), ("1080p", 1920, 1080), ("1080i", 1920, 1080)):
        for rate, num, den in (("23.98", 24000, 1001), ("24", 24, 1), ("25", 25, 1),
                               ("29.97", 30000, 1001), ("30", 30, 1), ("50", 50, 1),
                               ("59.94", 60000, 1001), ("60", 60, 1)):
            formats[size + rate] = (width, height, num, den * (2 if size.endswith("i") else 1))
    width, height, num, den = formats.get(video_format, formats["1080i50"])
    interlaced = video_format in ("pal", "ntsc") or video_format.startswith("1080i") or video_format not in formats
    tff = field_order != "bff"
    origin = {"geometry": "simulated", "field_order": "simulated", "pixel_format": "simulated", "props": "simulated"}
    pixel_format, props = "YUV420P8", {}
    source = source if isinstance(source, dict) else {}
    native = source.get("vapoursynth_input")
    native = native if isinstance(native, dict) else {}
    candidates = [source, native]
    for candidate in candidates:
        try:
            if "fps" in candidate:
                candidate_num, candidate_den = map(int, candidate["fps"].split("/"))
            else:
                candidate_num, candidate_den = candidate["fps_num"], candidate["fps_den"]
            if (type(candidate["width"]) is not int or type(candidate["height"]) is not int
                    or not 16 <= candidate["width"] <= 4096 or not 16 <= candidate["height"] <= 2160
                    or candidate["width"] % 2 or candidate["height"] % 2
                    or type(candidate_num) is not int or type(candidate_den) is not int
                    or candidate_den <= 0 or not 1 <= candidate_num / candidate_den <= 120):
                continue
            width, height, num, den = candidate["width"], candidate["height"], candidate_num, candidate_den
            origin["geometry"] = "detected"
            if candidate.get("scan") in ("i", "p"):
                interlaced = candidate["scan"] == "i"
                if not interlaced:
                    origin["field_order"] = "detected"
            if type(candidate.get("tff")) in (bool, int) and candidate["tff"] in (0, 1):
                tff = bool(candidate["tff"])
                origin["field_order"] = "detected"
            if candidate is native:
                if candidate.get("pixel_format") in PIXEL_FORMATS:
                    pixel_format = candidate["pixel_format"]
                    origin["pixel_format"] = "detected"
                received = candidate.get("props", {})
                if isinstance(received, dict):
                    for key, values in PROP_VALUES.items():
                        if type(received.get(key)) is int and received[key] in values:
                            props[key] = received[key]
                    if all(type(received.get(key)) is int and 0 < received[key] <= 2147483647
                           for key in ("_SARNum", "_SARDen")):
                        props.update({key: received[key] for key in ("_SARNum", "_SARDen")})
                    if props:
                        origin["props"] = "detected"
                    if "_FieldBased" in props:
                        interlaced, tff = props["_FieldBased"] != 0, props["_FieldBased"] == 2
                        origin["field_order"] = "detected"
        except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError):
            continue
    props["_FieldBased"] = (2 if tff else 1) if interlaced else 0
    duration = Fraction(den, num)
    props.update(_DurationNum=duration.numerator, _DurationDen=duration.denominator)
    return {"width": width, "height": height, "fps_num": num, "fps_den": den,
            "interlaced": interlaced, "tff": interlaced and tff,
            "pixel_format": pixel_format, "props": props, "origin": origin}


def frame_metadata(node, frame):
    """Return actual properties, keeping unknown colour/aspect values unknown."""
    keys = set(frame.props.keys())  # API 4.2 aliases _ColorRange to _Range in __contains__.
    props = {key: frame.props[key] for key in FRAME_PROPS if key in keys}
    props = {key: int(value) if isinstance(value, int) and not isinstance(value, bool) else value
             for key, value in props.items()}
    field_based = props.get("_FieldBased")
    scan = {0: "p", 1: "bff", 2: "tff"}.get(field_based) if type(field_based) is int else None
    result = {"width": frame.width, "height": frame.height,
              "fps_num": node.fps_num, "fps_den": node.fps_den,
              "pixel_format": frame.format.name, "bit_depth": frame.format.bits_per_sample,
              "props": props, "scan": scan,
              "sar": None, "dar": None}
    if all(type(props.get(key)) is int and props[key] > 0 for key in ("_SARNum", "_SARDen")):
        sar = Fraction(props["_SARNum"], props["_SARDen"])
        dar = sar * Fraction(frame.width, frame.height)
        result.update(sar=f"{sar.numerator}:{sar.denominator}", dar=f"{dar.numerator}:{dar.denominator}")
    return result


def _normalized_props(props):
    normalized = dict(props)
    if "_Range" in normalized:
        value = 1 - normalized.pop("_Range")
        if "_ColorRange" in normalized and normalized["_ColorRange"] != value:
            raise ValueError("_Range e _ColorRange sono in conflitto.")
        normalized["_ColorRange"] = value
    for numerator, denominator in (("_SARNum", "_SARDen"), ("_DurationNum", "_DurationDen")):
        if numerator in normalized and denominator in normalized:
            fraction = Fraction(normalized[numerator], normalized[denominator])
            normalized[numerator], normalized[denominator] = fraction.numerator, fraction.denominator
    normalized.pop("_AbsoluteTime", None)
    return normalized


def validate_output_props(props, context, number, first_props=None):
    """Match the native bridge: one input frame produces one output frame."""
    for key, values in PROP_VALUES.items():
        if key in props and (type(props[key]) is not int or props[key] not in values):
            raise ValueError(f"Proprietà {key} non valida nel frame {number}.")
    for key in ("_FieldBased", "_DurationNum", "_DurationDen", "_AbsoluteTime"):
        if key not in props:
            raise ValueError(f"Manca {key} nel frame {number}.")
    for numerator, denominator in (("_SARNum", "_SARDen"), ("_DurationNum", "_DurationDen")):
        if numerator in props or denominator in props:
            if not all(type(props.get(key)) is int and 0 < props[key] <= 2147483647
                       for key in (numerator, denominator)):
                raise ValueError(f"Coppia {numerator}/{denominator} non valida nel frame {number}.")
    if Fraction(props["_DurationNum"], props["_DurationDen"]) != Fraction(context["fps_den"], context["fps_num"]):
        raise ValueError(f"La durata del frame {number} deve coincidere con l’ingresso.")
    if "_SARNum" in props:
        sar = Fraction(props["_SARNum"], props["_SARDen"])
        if max(sar.numerator, sar.denominator) > 65535:
            raise ValueError("SAR non rappresentabile nel flusso H.264 (massimo 65535:65535 dopo riduzione).")
    if props.get("_Primaries") == 22:
        raise ValueError("_Primaries=22 non è supportato dalla segnalazione x264.")
    if props.get("_Transfer") in (16, 18):
        raise ValueError("Output HDR non supportato dalla catena OBE.")
    timestamp = props["_AbsoluteTime"]
    expected_time = number * context["fps_den"] / context["fps_num"]
    if (type(timestamp) is not float or not math.isfinite(timestamp)
            or abs(timestamp - expected_time) > 1 / 27000000):
        raise ValueError(f"_AbsoluteTime non coerente con l’ingresso nel frame {number}; retiming non supportato.")
    normalized = _normalized_props(props)
    for key in _normalized_props(context.get("props", {})):
        if key not in normalized:
            raise ValueError(f"La proprietà sorgente {key} manca nel frame {number}.")
    if first_props is not None and normalized != _normalized_props(first_props):
        raise ValueError(f"Proprietà del segnale variabili al frame {number}: serve una configurazione costante.")


def run_test(root, source, context, timeout=TEST_TIMEOUT):
    validate_script(source, syntax=False)
    if not source.strip():
        raise ConfigurationError("vapoursynth_script", "Inserisci uno script con output video 0.")
    if not _test_lock.acquire(blocking=False):
        raise TestBusy()
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="obe-vs-test-") as folder:
            folder = Path(folder)
            script = folder / "channel.vpy"
            script.write_text(source, encoding="utf-8")
            script.chmod(0o600)
            prefix = Path(root).resolve() / "target-root/usr/local"
            site = prefix / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
            env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "PYTHONPATH", "LD_LIBRARY_PATH") if key in os.environ}
            env.update(PYTHONPATH=str(site) + os.pathsep + env.get("PYTHONPATH", ""),
                       LD_LIBRARY_PATH=f"{prefix}/lib:{site}/vapoursynth:" + env.get("LD_LIBRARY_PATH", ""),
                       PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", OMP_NUM_THREADS="2", OPENBLAS_NUM_THREADS="2")
            timed_out = False
            with (folder / "run.log").open("w+b") as output:
                try:
                    process = subprocess.Popen([sys.executable, "-u", str(Path(__file__).resolve()),
                                                str(script), json.dumps(context)], cwd=folder, env=env,
                                               stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                               start_new_session=True)
                except OSError:
                    return {"valid": False, "message": "Test non avviato", "log": "Impossibile avviare il processo di prova."}
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    timed_out = True
                finally:
                    # Also stop descendants if a script spawned a process before exiting.
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
                size = output.tell()
                output.seek(0)
                if size > MAX_LOG_BYTES:
                    raw = output.read(2048) + b"\n[... log troncato ...]\n"
                    output.seek(-(MAX_LOG_BYTES - 2112), os.SEEK_END)
                    raw += output.read(MAX_LOG_BYTES - 2112)
                else:
                    raw = output.read(MAX_LOG_BYTES)
            try:
                result = json.loads((folder / "result.json").read_text()[:16384])
            except (OSError, ValueError):
                result = {}
            valid = (not timed_out and process.returncode == 0 and result.get("valid") is True
                     and result.get("frames") == len(TEST_FRAMES) and isinstance(result.get("output"), dict))
            log = raw.decode("utf-8", errors="replace").strip()
            message = "Test non riuscito"
            if timed_out:
                log += f"\nTest interrotto: limite di {timeout:g} secondi superato."
            elif process.returncode != 0:
                if process.returncode < 0:
                    try:
                        reason = f"segnale {signal.Signals(-process.returncode).name} ({-process.returncode})"
                    except ValueError:
                        reason = f"segnale {-process.returncode}"
                else:
                    reason = f"codice {process.returncode}"
                log += f"\nProcesso di prova terminato con {reason}."
                if result.get("valid") is True:
                    message = "Render completato; errore alla chiusura del processo"
                    log += "\nI frame sono stati renderizzati, ma il test richiede anche una chiusura senza errori."
            elif not valid and not result:
                log += f"\nProcesso terminato senza completare il test (codice {process.returncode})."
            output = result.get("output") if valid else None
            return {"valid": valid, "message": f"Test riuscito · {output['width']}×{output['height']} · {len(TEST_FRAMES)} frame" if valid else message,
                    "log": log, "duration_ms": round((time.monotonic() - started) * 1000),
                    "input": result.get("input"), "output": output, "frames": result.get("frames", 0)}
    finally:
        _test_lock.release()


def _test_worker(script, context):
    import resource
    import runpy
    import traceback

    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    # CPU time is cumulative across both allowed cores; wall time is bounded by the parent.
    resource.setrlimit(resource.RLIMIT_CPU, (TEST_TIMEOUT * 2, TEST_TIMEOUT * 2 + 1))
    resource.setrlimit(resource.RLIMIT_AS, (3 * 1024**3, 3 * 1024**3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (131072, 131072))
    os.nice(10)
    os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:2])
    result = {"valid": False}
    try:
        import vapoursynth as vs

        core = vs.core
        core.num_threads = 2
        core.max_cache_size = 256
        # The pixels are synthetic; source metadata can come from OBE's actual first frame.
        pixel_format = context.get("pixel_format", "YUV420P8")
        if pixel_format not in PIXEL_FORMATS:
            raise ValueError("Formato pixel di prova non supportato da OBE.")
        clip = core.std.BlankClip(width=context["width"], height=context["height"],
                                  format=getattr(vs, pixel_format), fpsnum=context["fps_num"], fpsden=context["fps_den"],
                                  length=2147483647 // 2, keep=True).std.RemoveFrameProps()
        interlaced = bool(context.get("interlaced", False))
        tff = interlaced and bool(context.get("tff", True))
        field_based = (2 if tff else 1) if interlaced else 0
        props = dict(context.get("props", {}))
        duration = Fraction(context["fps_den"], context["fps_num"])
        props.setdefault("_FieldBased", field_based)
        props.setdefault("_DurationNum", duration.numerator)
        props.setdefault("_DurationDen", duration.denominator)
        if hasattr(vs, "Range") and "_ColorRange" in props:
            if "_Range" in props and props["_Range"] != 1 - props["_ColorRange"]:
                raise ValueError("_Range e _ColorRange sono in conflitto nell’ingresso.")
            props["_Range"] = 1 - props.pop("_ColorRange")
        clip = clip.std.SetFrameProps(**props)
        def timestamp(n, f):
            frame = f.copy()
            frame.props["_AbsoluteTime"] = n * float(duration)
            return frame
        clip = clip.std.ModifyFrame(clips=clip, selector=timestamp)
        with clip.get_frame(0) as frame:
            input_metadata = frame_metadata(clip, frame)
        input_metadata["origin"] = context.get("origin", {})
        input_metadata["synthetic"] = True
        result["input"] = input_metadata
        field_based = props["_FieldBased"]
        interlaced, tff = field_based != 0, field_based == 2
        print(f"Clip sintetico: {clip.width}×{clip.height}, {clip.format.name}, {clip.fps_num}/{clip.fps_den} fps.")
        origin = "rilevato" if context.get("origin", {}).get("field_order") == "detected" else "simulato"
        print(f"Ordine campi {origin}: {('progressivo', 'BFF', 'TFF')[field_based]}.")
        print("Props ingresso: " + json.dumps(input_metadata["props"], sort_keys=True))
        runpy.run_path(str(script), init_globals={
            "clip": clip, "OBE_INTERLACED": int(interlaced), "OBE_TFF": int(tff),
            "OBE_BFF": int(interlaced and not tff), "OBE_FIELD_BASED": field_based,
            "OBE_WIDTH": clip.width, "OBE_HEIGHT": clip.height,
            "OBE_FPS_NUM": clip.fps_num, "OBE_FPS_DEN": clip.fps_den,
            "OBE_BIT_DEPTH": clip.format.bits_per_sample,
        })
        try:
            output = vs.get_output(0)
        except KeyError:
            raise ValueError("Manca l’output video 0: usa clip.set_output().") from None
        output = getattr(output, "clip", output)
        if not isinstance(output, vs.VideoNode):
            raise ValueError("L’output 0 deve essere un video.")
        if not output.format or output.format.id != clip.format.id:
            raise ValueError("Il formato pixel dell’output deve coincidere con quello in ingresso.")
        if output.height != clip.height or not 16 <= output.width <= clip.width or output.width % 2:
            raise ValueError("Mantieni l’altezza originale e una larghezza pari tra 16 e quella in ingresso.")
        expected_size = (output.width, output.height)
        if (output.fps_num, output.fps_den) != (clip.fps_num, clip.fps_den):
            raise ValueError("Gli fps dell’output devono coincidere con quelli in ingresso.")
        if output.num_frames != clip.num_frames:
            raise ValueError("La durata dell’output deve coincidere con quella in ingresso.")
        output_metadata = None
        for number in TEST_FRAMES:
            print(f"Render frame {number}…", flush=True)
            with output.get_frame(number) as frame:
                if frame.format.id != clip.format.id or (frame.width, frame.height) != expected_size:
                    raise ValueError(f"Formato o dimensioni non compatibili nel frame {number}.")
                metadata = frame_metadata(output, frame)
                validate_output_props(metadata["props"], context, number,
                                      output_metadata["props"] if output_metadata else None)
                if output_metadata is None:
                    output_metadata = metadata
        print(f"Uscita: {output.width}×{output.height}, {output.format.name}, {output.fps_num}/{output.fps_den} fps.")
        print("Props uscita: " + json.dumps(output_metadata["props"], sort_keys=True))
        print("Output 0 compatibile con OBE; render completato.")
        result.update(valid=True, frames=len(TEST_FRAMES), output=output_metadata)
    except BaseException:
        traceback.print_exc()
    Path("result.json").write_text(json.dumps(result), encoding="utf-8")
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    sys.exit(_test_worker(Path(sys.argv[1]), json.loads(sys.argv[2])))
