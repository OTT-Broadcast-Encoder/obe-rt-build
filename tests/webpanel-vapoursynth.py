#!/usr/bin/env python3
"""Script validation, persistence and application with an isolated fake encoder."""

import importlib.util
import json
from pathlib import Path
import runpy
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webpanel"))
import vapoursynth_script as scripts
from configuration import ConfigurationError

helpers = runpy.run_path(str(ROOT / "tests/webpanel.py"))
fixture = helpers["PanelTests"]
SCRIPT = "import vapoursynth as vs\n\nif clip.width > 0:\n    clip = vs.core.std.Invert(clip)\n\nclip.set_output()\n"
HAS_RUNTIME = importlib.util.find_spec("vapoursynth") is not None


class ScriptTests(unittest.TestCase):
    def test_test_context_uses_detected_rate_and_falls_back(self):
        context = scripts.test_context({"width": 720, "height": 576, "fps": "25/1"})
        self.assertEqual({key: context[key] for key in ("width", "height", "fps_num", "fps_den", "interlaced", "tff")},
                         {"width": 720, "height": 576, "fps_num": 25, "fps_den": 1,
                          "interlaced": True, "tff": True})
        self.assertEqual(context["origin"]["geometry"], "detected")
        self.assertEqual(context["origin"]["field_order"], "simulated")
        self.assertNotIn("_Matrix", context["props"])
        self.assertNotIn("_SARNum", context["props"])
        self.assertEqual(scripts.test_context(None, "1080i59.94")["fps_den"], 2002)
        self.assertEqual(scripts.test_context({"width": 0, "height": 0, "fps": "0/0"}), scripts.test_context())

    def test_context_field_order_uses_signal_then_simulation_settings(self):
        signal = {"width": 1920, "height": 1080, "fps": "25/1", "scan": "p"}
        progressive = scripts.test_context(signal, field_order="tff")
        self.assertFalse(progressive["interlaced"])
        self.assertFalse(progressive["tff"])
        self.assertFalse(scripts.test_context(None, "1080p25")["interlaced"])
        bff = scripts.test_context({**signal, "scan": "i"}, field_order="bff")
        self.assertTrue(bff["interlaced"])
        self.assertFalse(bff["tff"])
        detected = scripts.test_context({**signal, "scan": "i", "tff": False}, field_order="tff")
        self.assertFalse(detected["tff"])

    def test_context_prefers_actual_filter_input_and_does_not_invent_colour(self):
        props = {"_FieldBased": 1, "_SARNum": 64, "_SARDen": 45, "_Matrix": 5,
                 "_Primaries": 5, "_Transfer": 5, "_Range": 0, "_ChromaLocation": 0}
        signal = {"width": 1920, "height": 1080, "fps": "25/1", "scan": "i", "vapoursynth_input": {
            "width": 720, "height": 576, "fps_num": 25, "fps_den": 1, "pixel_format": "YUV420P10", "props": props}}
        context = scripts.test_context(signal, field_order="tff")
        self.assertEqual((context["width"], context["height"], context["pixel_format"]), (720, 576, "YUV420P10"))
        self.assertFalse(context["tff"])
        self.assertTrue(all(value == "detected" for value in context["origin"].values()))
        self.assertEqual(context["props"], {**props, "_DurationNum": 1, "_DurationDen": 25})
        signal["vapoursynth_input"]["props"] = {"_Matrix": 999, "_SARNum": 1, "_ColorRange": True}
        context = scripts.test_context(signal)
        self.assertNotIn("_Matrix", context["props"])
        self.assertNotIn("_SARNum", context["props"])
        self.assertNotIn("_ColorRange", context["props"])

    def test_property_contract_matches_native_timing_and_decoder_limits(self):
        context = scripts.test_context()
        valid = {**context["props"], "_AbsoluteTime": 0.0, "_SARNum": 4, "_SARDen": 3}
        scripts.validate_output_props(valid, context, 0)
        scripts.validate_output_props({**valid, "_SARNum": 8, "_SARDen": 6}, context, 0, valid)
        for change in ({"_DurationNum": 2}, {"_AbsoluteTime": 0.5}, {"_AbsoluteTime": 0}, {"_SARNum": 65537, "_SARDen": 1},
                       {"_Transfer": 16}, {"_Transfer": 18}, {"_Primaries": 22}, {"_Matrix": 999},
                       {"_ColorRange": 1, "_Range": 1}, {"_FieldBased": "tff"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                scripts.validate_output_props({**valid, **change}, context, 0)
        for key in ("_FieldBased", "_DurationNum", "_DurationDen", "_AbsoluteTime"):
            with self.subTest(missing=key), self.assertRaises(ValueError):
                scripts.validate_output_props({k: value for k, value in valid.items() if k != key}, context, 0)
        with self.assertRaises(ValueError):
            scripts.validate_output_props({**valid, "_FieldBased": 0}, context, 0, valid)

    def test_validation_does_not_execute_python(self):
        self.assertEqual(scripts.validate_script(SCRIPT), SCRIPT)
        scripts.validate_script("raise RuntimeError('must not execute')\n")
        for source in (None, 4, "", " \n", "clip =\n", "clip\x00", "\ud800", "#" * 8193):
            with self.subTest(source=repr(source)), self.assertRaises(ConfigurationError):
                scripts.validate_script(source)
        with self.assertRaises(ConfigurationError) as caught:
            scripts.validate_script("import vapoursynth as vs\nclip =\n")
        self.assertIn("Riga 2", str(caught.exception))
        self.assertNotIn("clip =", str(caught.exception))

    def test_disabled_draft_and_private_snapshot(self):
        scripts.validate({"vapoursynth_enabled": False, "vapoursynth_script": "clip ="})
        with self.assertRaises(ConfigurationError):
            scripts.validate({"vapoursynth_enabled": 1, "vapoursynth_script": SCRIPT})
        first, path = scripts.prepare(SCRIPT)
        second, other = scripts.prepare("clip.set_output()\n")
        try:
            self.assertNotEqual(path, other)
            self.assertEqual(Path(path).read_text(), SCRIPT)
            self.assertEqual(Path(path).stat().st_mode & 0o777, 0o600)
            self.assertEqual(Path(path).parent.stat().st_mode & 0o777, 0o700)
        finally:
            first.cleanup()
            second.cleanup()
        self.assertFalse(Path(path).exists())


@unittest.skipUnless(HAS_RUNTIME, "Eseguire nel container con VapourSynth installato")
class RuntimeScriptTests(unittest.TestCase):
    context = {"width": 320, "height": 240, "fps_num": 25, "fps_den": 1}

    def test_injected_input_globals_and_automatic_field_roundtrip(self):
        for field_based in (0, 1, 2):
            with self.subTest(field_based=field_based):
                context = {**self.context, "fps_num": 30000, "fps_den": 1001,
                           "interlaced": field_based != 0, "tff": field_based != 1}
                source = (
                    "from __future__ import annotations\n"
                    f"assert OBE_FIELD_BASED == {field_based}\n"
                    "assert OBE_INTERLACED == (OBE_FIELD_BASED != 0)\n"
                    "assert OBE_TFF == (OBE_FIELD_BASED == 2)\n"
                    "assert OBE_BFF == (OBE_FIELD_BASED == 1)\n"
                    "assert (OBE_WIDTH, OBE_HEIGHT) == (clip.width, clip.height) == (320, 240)\n"
                    "assert (OBE_FPS_NUM, OBE_FPS_DEN) == (clip.fps_num, clip.fps_den) == (30000, 1001)\n"
                    "assert OBE_BIT_DEPTH == clip.format.bits_per_sample == 8\n"
                    "if OBE_INTERLACED:\n"
                    "    clip = clip.std.SeparateFields(tff=OBE_TFF)\n"
                    "    clip = clip.std.DoubleWeave(tff=OBE_TFF).std.SelectEvery(cycle=2, offsets=0)\n"
                    "clip.set_output()\n"
                )
                result = scripts.run_test(ROOT, source, context)
                self.assertTrue(result["valid"], result["log"])
                self.assertIn(("progressivo", "BFF", "TFF")[field_based], result["log"])

    def test_runs_plugins_and_renders_output(self):
        result = scripts.run_test(ROOT, "print('output della prova')\n" + SCRIPT, self.context)
        self.assertTrue(result["valid"], result["log"])
        self.assertIn("output della prova", result["log"])
        self.assertIn("Render frame 24", result["log"])

    def test_allows_slow_script_initialization(self):
        result = scripts.run_test(ROOT, "import time\ntime.sleep(13)\nclip.set_output()\n", self.context)
        self.assertTrue(result["valid"], result["log"])

    def test_cpu_budget_allows_initialization_beyond_ten_seconds(self):
        source = "import time\nwhile time.process_time() < 12:\n    pass\nclip.set_output()\n"
        result = scripts.run_test(ROOT, source, self.context)
        self.assertTrue(result["valid"], result["log"])

    def test_script_horizontal_resize_and_output_geometry(self):
        context = scripts.test_context()
        for kernel in ("Spline36", "Lanczos"):
            with self.subTest(kernel=kernel):
                source = "from __future__ import annotations\nimport vapoursynth as vs\n"
                source += f"clip.std.Invert().resize.{kernel}(width=1440).set_output()\n"
                result = scripts.run_test(ROOT, source, context)
                self.assertTrue(result["valid"], result["log"])
                self.assertIn("Uscita: 1440×1080", result["log"])
                self.assertEqual({key: result["output"][key] for key in ("width", "height", "fps_num", "fps_den", "pixel_format")},
                                 {"width": 1440, "height": 1080, "fps_num": 25, "fps_den": 1, "pixel_format": "YUV420P8"})
                self.assertEqual(result["output"]["props"]["_FieldBased"], 2)
        result = scripts.run_test(ROOT, "clip.resize.Spline36(width=160).set_output()\n", self.context)
        self.assertTrue(result["valid"], result["log"])
        self.assertEqual((result["output"]["width"], result["output"]["height"]), (160, 240))
        for source, message in (("clip.resize.Point(width=400).set_output()\n", "larghezza pari"),
                                ("clip.resize.Point(width=8).set_output()\n", "larghezza pari"),
                                ("clip.resize.Point(height=200).set_output()\n", "altezza originale")):
            result = scripts.run_test(ROOT, source, self.context)
            self.assertFalse(result["valid"])
            self.assertIn(message, result["log"])

    def test_dynamic_props_resize_colour_and_bit_depth(self):
        context = {**self.context, "pixel_format": "YUV420P10", "interlaced": True, "tff": False,
                   "props": {"_FieldBased": 1, "_SARNum": 1, "_SARDen": 1, "_Matrix": 1,
                             "_Primaries": 1, "_Transfer": 1, "_ColorRange": 1, "_ChromaLocation": 0}}
        source = ("assert OBE_BIT_DEPTH == 10\n"
                  "with clip.get_frame(0) as f:\n"
                  "    assert f.props['_FieldBased'] == 1 and f.props['_Matrix'] == 1\n"
                  "clip.resize.Bicubic(width=160, matrix=5, primaries=5, transfer=5, range=1).set_output()\n")
        result = scripts.run_test(ROOT, source, context)
        self.assertTrue(result["valid"], result["log"])
        self.assertEqual(result["input"]["sar"], "1:1")
        self.assertEqual(result["output"]["sar"], "2:1")
        self.assertEqual(result["input"]["dar"], result["output"]["dar"])
        self.assertEqual(result["output"]["scan"], "bff")
        self.assertEqual(result["output"]["bit_depth"], 10)
        self.assertEqual(result["output"]["props"]["_Matrix"], 5)
        self.assertEqual(scripts._normalized_props(result["output"]["props"])["_ColorRange"], 0)
        for source, error in (
            ("clip.std.RemoveFrameProps(props=['_FieldBased']).set_output()\n", "_FieldBased"),
            ("clip.std.SetFrameProps(_DurationNum=2).set_output()\n", "durata"),
            ("clip.std.RemoveFrameProps(props=['_SARNum']).set_output()\n", "_SARNum"),
            ("clip.std.RemoveFrameProps(props=['_Matrix']).set_output()\n", "_Matrix"),
            ("clip.std.SetFrameProps(_Transfer=16).set_output()\n", "HDR"),
            ("clip.std.SetFrameProps(_AbsoluteTime=0.0).set_output()\n", "retiming"),
            ("def change(n, f):\n    f=f.copy()\n    f.props['_FieldBased']=n%3\n    return f\n"
             "clip.std.ModifyFrame(clips=clip, selector=change).set_output()\n", "variabili"),
        ):
            with self.subTest(source=source):
                result = scripts.run_test(ROOT, source, context)
                self.assertFalse(result["valid"], result["log"])
                self.assertIn(error, result["log"])
                self.assertIsNotNone(result["input"])

    def test_reports_script_and_output_errors(self):
        cases = (
            ("if True\n", "SyntaxError"),
            ("raise RuntimeError('errore di utilizzo')\n", "errore di utilizzo"),
            ("import vapoursynth as vs\nvs.core.plugin_inesistente.Filter(clip)\n", "plugin_inesistente"),
            ("clip.std.Invert(argomento_inesistente=1).set_output()\n", "argomento_inesistente"),
            ("pass\n", "output video 0"),
            ("clip.std.CropRel(bottom=2).set_output()\n", "altezza originale"),
            ("clip.std.AssumeFPS(fpsnum=30).set_output()\n", "fps"),
            ("clip[:10].set_output()\n", "durata"),
            ("import vapoursynth as vs\nclip.resize.Point(format=vs.YUV444P8).set_output()\n", "formato pixel"),
            ("raise SystemExit(0)\n", "SystemExit"),
        )
        for source, error in cases:
            with self.subTest(error=error):
                result = scripts.run_test(ROOT, source, self.context)
                self.assertFalse(result["valid"])
                self.assertIn(error, result["log"])

    def test_reports_lazy_frame_error(self):
        source = "def render(n, f):\n    if n == 2: raise ValueError('errore frame 2')\n    return f\nclip.std.ModifyFrame(clips=clip, selector=render).set_output()\n"
        result = scripts.run_test(ROOT, source, self.context)
        self.assertFalse(result["valid"])
        self.assertIn("errore frame 2", result["log"])

    def test_reports_exit_failure_after_successful_render(self):
        source = "import atexit, os\natexit.register(os._exit, 7)\nclip.set_output()\n"
        result = scripts.run_test(ROOT, source, self.context)
        self.assertFalse(result["valid"])
        self.assertIsNone(result["output"])
        self.assertIn("render completato", result["log"])
        self.assertIn("codice 7", result["log"])
        self.assertIn("chiusura", result["message"])

    def test_reports_signal_after_successful_render(self):
        source = ("import atexit, os, signal\n"
                  "atexit.register(os.kill, os.getpid(), signal.SIGTERM)\nclip.set_output()\n")
        result = scripts.run_test(ROOT, source, self.context)
        self.assertFalse(result["valid"])
        self.assertIn("render completato", result["log"])
        self.assertIn("SIGTERM", result["log"])
        self.assertIn("chiusura", result["message"])

    def test_timeout_and_bounded_log(self):
        result = scripts.run_test(ROOT, "import time\ntime.sleep(60)\n", self.context, timeout=2)
        self.assertFalse(result["valid"])
        self.assertIn("limite di 2 secondi", result["log"])
        result = scripts.run_test(ROOT, "print('x' * 20000)\nclip.set_output()\n", self.context)
        self.assertTrue(result["valid"], result["log"])
        self.assertIn("log troncato", result["log"])
        self.assertLessEqual(len(result["log"].encode()), scripts.MAX_LOG_BYTES)


class PanelScriptTests(unittest.TestCase):
    setUp = fixture.setUp
    tearDown = fixture.tearDown
    start_server = fixture.start_server
    stop_server = fixture.stop_server
    request = fixture.request
    state = fixture.state
    wait_phase = fixture.wait_phase
    save = fixture.save
    start_encoder = fixture.start_encoder

    @unittest.skipUnless(HAS_RUNTIME, "Eseguire nel container con VapourSynth installato")
    def test_runtime_endpoint_simulates_configured_bff(self):
        self.assertEqual(self.save(field_order="bff")[0], 200)
        status, result = self.request("POST", "/api/vapoursynth-test", {
            "script": "assert OBE_INTERLACED == OBE_BFF == OBE_FIELD_BASED == 1\n"
                      "assert OBE_TFF == 0\nclip.set_output()\n"})
        self.assertEqual(status, 200)
        self.assertTrue(result["valid"], result["log"])

    def test_runtime_endpoint_rejects_invalid_requests(self):
        self.assertTrue(self.state()["capabilities"]["vapoursynth_test"])
        for payload in ({}, {"script": SCRIPT, "extra": True}, {"script": None}, {"script": ""},
                        {"script": SCRIPT, "vapoursynth_width": 1280},
                        {"script": SCRIPT, "vapoursynth_enabled": 1},
                        {"script": SCRIPT, "vapoursynth_resize_kernel": "unknown"}):
            with self.subTest(payload=payload):
                self.assertEqual(self.request("POST", "/api/vapoursynth-test", payload)[0], 422)

    @unittest.skipUnless(HAS_RUNTIME, "Eseguire nel container con VapourSynth installato")
    def test_runtime_draft_preserves_live_channel_and_redacts_log(self):
        before = self.start_encoder()
        status, result = self.request("POST", "/api/vapoursynth-test", {"script": SCRIPT})
        self.assertEqual(status, 200)
        self.assertTrue(result["valid"], result["log"])
        after = self.state()
        self.assertEqual((after["pid"], after["revision"], after["config"]),
                         (before["pid"], before["revision"], before["config"]))
        self.assertTrue(after["process_alive"])
        status, result = self.request("POST", "/api/vapoursynth-test", {
            "script": "clip.resize.Spline36(width=1440).set_output()\n"})
        self.assertEqual(status, 200)
        self.assertTrue(result["valid"], result["log"])
        self.assertIn("Uscita: 1440×1080", result["log"])
        self.assertEqual(self.state()["revision"], before["revision"])
        status, result = self.request("POST", "/api/vapoursynth-test", {
            "script": "raise RuntimeError('password=fixture-secret')\n"})
        self.assertEqual(status, 200)
        self.assertFalse(result["valid"])
        self.assertNotIn("fixture-secret", result["log"])
        self.assertIn("[rimosso]", result["log"])

    def record_scripts(self):
        fake = helpers["FAKE"].replace("command = line.strip()", """command = line.strip()
    if ',vs-script=' in command:
        path = command.split(',vs-script=', 1)[1]
        Path('script-' + str(os.getpid()) + '.json').write_text(json.dumps({
            'path': path, 'source': Path(path).read_text(), 'command': command}))""")
        (self.root / "run.sh").write_text(fake)

    def test_validation_endpoint_and_persistence(self):
        status, body = self.request("POST", "/api/vapoursynth-validate", {"script": SCRIPT})
        self.assertEqual((status, body), (200, {"valid": True}))
        status, body = self.request("POST", "/api/vapoursynth-validate", {"script": "if True\n"})
        self.assertEqual(status, 422)
        self.assertIn("vapoursynth_script", body["error"]["fields"])
        self.assertEqual(self.save(vapoursynth_enabled=True, vapoursynth_script=SCRIPT)[0], 200)
        self.stop_server()
        self.start_server()
        self.assertEqual(self.state()["config"]["vapoursynth_script"], SCRIPT)
        status, exported = self.request("GET", "/api/profile")
        self.assertEqual(status, 200)
        self.assertEqual(exported["config"]["vapoursynth_script"], SCRIPT)
        self.assertEqual(self.request("POST", "/api/profile-validate", exported)[0], 200)

    def test_apply_snapshot_rejects_bad_syntax_before_stopping(self):
        self.record_scripts()
        self.assertEqual(self.save(vapoursynth_enabled=True, vapoursynth_script=SCRIPT)[0], 200)
        self.start_encoder()
        running = self.wait_phase({"running"})
        first = json.loads(next(self.root.glob("script-*.json")).read_text())
        payload = {"revision": running["revision"], "instance_id": running["instance_id"],
                   "config": {**running["config"], "vapoursynth_script": "if True\n"}}
        self.assertEqual(self.request("POST", "/api/apply", payload)[0], 422)
        self.assertEqual(self.state()["revision"], running["revision"])
        self.assertTrue(self.state()["process_alive"])
        self.assertEqual(Path(first["path"]).read_text(), SCRIPT)
        payload["config"]["vapoursynth_script"] = "# New revision\nclip.set_output()\n"
        status, state = self.request("POST", "/api/apply", payload)
        self.assertEqual(status, 202, state)
        self.wait_phase({"running"})
        records = [json.loads(path.read_text()) for path in self.root.glob("script-*.json")]
        self.assertEqual(len(records), 2)
        second = next(record for record in records if record["path"] != first["path"])
        self.assertEqual(second["source"], payload["config"]["vapoursynth_script"])
        self.assertFalse(Path(first["path"]).exists())
        self.assertNotIn("clip.set_output", second["command"])
        self.request("POST", "/api/stop", {})
        self.wait_phase({"idle"})
        self.assertFalse(Path(second["path"]).exists())

    def test_disabled_and_probe_do_not_pass_a_script(self):
        self.record_scripts()
        self.assertEqual(self.save(vapoursynth_script="unfinished =")[0], 200)
        self.start_encoder()
        self.wait_phase({"running"})
        self.assertFalse(list(self.root.glob("script-*.json")))
        self.request("POST", "/api/stop", {})
        self.wait_phase({"idle"})
        self.save(vapoursynth_enabled=True, vapoursynth_script=SCRIPT)
        self.request("POST", "/api/probe", {"revision": self.state()["revision"]})
        self.wait_phase({"idle"})
        self.assertFalse(list(self.root.glob("script-*.json")))

    def test_resize_script_is_passed_unchanged_without_size_options(self):
        self.record_scripts()
        script = "clip.resize.Spline36(width=1440).set_output()\n"
        self.assertEqual(self.save(vapoursynth_enabled=True, vapoursynth_script=script)[0], 200)
        self.start_encoder()
        self.wait_phase({"running"})
        record = json.loads(next(self.root.glob("script-*.json")).read_text())
        self.assertNotIn("width=", record["command"])
        self.assertNotIn("vs-resize", record["command"])
        self.assertEqual(record["source"], script)
        self.assertEqual(self.state()["config"]["vapoursynth_script"], script)
        self.request("POST", "/api/stop", {})
        self.wait_phase({"idle"})
        self.assertEqual(self.save(vapoursynth_enabled=False)[0], 200)
        self.start_encoder()
        self.wait_phase({"running"})
        self.assertEqual(len(list(self.root.glob("script-*.json"))), 1)

    def test_retired_resize_fields_do_not_invalidate_existing_profiles(self):
        state = self.state()
        config = {**state["config"], "vapoursynth_width": 0, "vapoursynth_resize_kernel": "spline36"}
        status, saved = self.request("PUT", "/api/config", {"revision": state["revision"], "config": config})
        self.assertEqual(status, 200)
        self.assertEqual(saved["config"], state["config"])
        self.assertNotIn("vapoursynth_width", saved["config"])
        self.assertNotIn("vapoursynth_resize_kernel", saved["config"])

    def test_legacy_profiles_and_channel_isolation(self):
        state = self.state()
        config = {key: value for key, value in state["config"].items() if key not in scripts.DEFAULTS}
        self.assertEqual(self.request("PUT", "/api/config", {"revision": state["revision"], "config": config})[0], 200)
        self.assertFalse(self.state()["config"]["vapoursynth_enabled"])
        self.assertEqual(self.request("POST", "/api/channels", {"name": "Secondo"})[0], 201)
        self.save(vapoursynth_enabled=True, vapoursynth_script=SCRIPT)
        status, other = self.request("GET", "/api/channels/channel-2/state")
        self.assertEqual(status, 200)
        self.assertFalse(other["config"]["vapoursynth_enabled"])
        self.assertEqual(other["config"]["vapoursynth_script"], scripts.DEFAULT_SCRIPT)


del fixture

if __name__ == "__main__":
    unittest.main()
