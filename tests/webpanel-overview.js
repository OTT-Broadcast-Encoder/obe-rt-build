"use strict";
const assert = require("node:assert/strict");
const { summarize, diagnose, outputRows, trace } = require("../webpanel/static/overview.js");

function running() {
  return { phase: "running", process_alive: true, encoding_started: true, signal_state: "receiving",
    signal_details: { frame_rate: 25 }, config: { outputs: [] },
    telemetry: { status: "live", age_seconds: 1, video_fps: 25, output_bitrate_kbps: 9000, output_errors: 7 },
    monitoring: { status: "live", age_seconds: 1, active_alarms: [] } };
}

assert.equal(summarize(running(), true).tone, "good"); // Historical error totals do not imply an active fault.
const delivery = running();
delivery.delivery = { config: { enabled: true }, status: "unavailable" };
assert.equal(summarize(delivery, true).title, "Consegna da verificare");
delivery.delivery = { config: { enabled: true }, status: "live", age_seconds: 1, receiver: { status: "live" } };
assert.equal(summarize(delivery, true).tone, "good");
delivery.delivery.age_seconds = 6;
assert.equal(summarize(delivery, true).tone, "warning");
assert.notEqual(summarize(running(), false).tone, "good");
assert.equal(summarize({ phase: "idle", process_alive: false }, true).tone, "neutral");
for (const change of [s => s.telemetry.status = "stale", s => s.telemetry.age_seconds = 6,
  s => s.monitoring.status = "unavailable", s => s.telemetry.video_fps = null,
  s => s.telemetry.video_fps = 0, s => s.telemetry.video_fps = 12,
  s => s.telemetry.output_bitrate_kbps = 0, s => s.signal_state = "reconnecting",
  s => s.encoding_started = false, s => { s.config.vapoursynth_enabled = true; s.vapoursynth = { status: "error" }; },
  s => { s.config.input_url = "srt://test.invalid:1234"; s.srt = { status: "stale" }; }]) {
  const state = running(); change(state);
  assert.notEqual(summarize(state, true).tone, "good");
}
const alarm = running();
alarm.monitoring.active_alarms = [{ code: "udp_errors", level: "error", label: "Invio non riuscito", guide: "Controlla l’uscita" }];
assert.equal(summarize(alarm, true).tone, "bad");
assert.equal(summarize(alarm, true).title, "Invio non riuscito");
const output = running();
output.config.outputs = [{ id: "extra", name: "SRT main", protocol: "srt", enabled: true }];
assert.notEqual(summarize(output, true).tone, "good");
output.transport = { outputs: [{ id: "extra", status: "error", bitrate_kbps: 0 }] };
assert.equal(summarize(output, true).tone, "bad");
output.config.outputs[0].enabled = false;
assert.equal(summarize(output, true).tone, "good");
assert.equal(outputRows(output, false)[0].bitrate, null);
assert.equal(outputRows(output, false)[1].status, "disabled");

const independent = { phase: "idle", process_alive: false, config: { outputs: [] },
  transport: { independent_process: true, service_available: true, running: true, input_status: "waiting",
    outputs: [{ id: "primary", status: "live", bitrate_kbps: 0, errors: 0, dropped_packets: 0 }] } };
assert.equal(outputRows(independent, true)[0].status, "waiting");
assert.equal(summarize(independent, true).stages[2].label, "In attesa di segnale");
independent.transport.service_available = false;
assert.equal(outputRows(independent, true)[0].status, "error");
assert.equal(summarize(independent, true).stages[2].label, "Servizio non disponibile");

const end = Date.parse("2026-10-23T12:00:00Z");
const samples = [[-10, 5, "a"], [-9, 6, "a"], [-8, null, "a"], [-7, 0, "a"], [-6, 2, "b"], [0, 3, "b"]]
  .map(([seconds, value, run]) => ({ time: new Date(end + seconds * 1000).toISOString(), value, run }));
const path = trace(samples, s => s.value, s => s.run, end, 120, 10);
assert.equal((path.match(/M/g) || []).length, 4); // Null, restart and sampling gap break the line.
assert.equal((path.match(/L/g) || []).length, 1);
assert.ok(path.includes(",110.0")); // A measured zero is plotted.
assert.equal(trace(samples, () => null, s => s.run, end, 120, 10), "");
assert.ok(!path.includes("NaN"));

// Diagnosis must separate a stopped process, missing measurements and measured faults.
assert.equal(diagnose(null, false).code, "offline");
const idle = diagnose({ phase: "idle", process_alive: false, signal_state: "unknown" }, true);
assert.equal(idle.title, "Canale fermo");
assert.equal(idle.action.href, "#configuration");
assert.equal(idle.stages[0].label, "Fermo");
assert.equal(idle.fps, null);
const healthy = running();
healthy.events = [{ level: "error", message: "Vecchio errore di invio" }];
assert.equal(diagnose(healthy, true).code, "healthy");
assert.equal(diagnose(healthy, true).lastError, null);
assert.equal(diagnose(healthy, true).action, null);
assert.match(diagnose(healthy, true).evidence, /decoder non verificata/);
const slow = running();
slow.telemetry.video_fps = 20;
slow.telemetry.video_queue_frames = 75;
assert.equal(diagnose(slow, true).code, "cadence");
assert.equal(diagnose(slow, true).stages[1].tone, "warning");
assert.match(diagnose(slow, true).detail, /20 fps misurati · attesi 25/);
assert.match(diagnose(slow, true).evidence, /75 frame · 3 s/);
assert.equal(diagnose(slow, true).action.href, "#configuration/video");
slow.telemetry.video_fps = 0;
assert.equal(diagnose(slow, true).code, "video_stalled");
slow.telemetry.video_fps = null;
assert.notEqual(diagnose(slow, true).code, "video_stalled");
slow.telemetry.age_seconds = 6;
assert.equal(diagnose(slow, true).code, "telemetry_missing");
assert.equal(diagnose(slow, true).fps, null);
assert.equal(diagnose(slow, true).stages[1].note, "");
assert.equal(diagnose(slow, false).evidence, "");
const staleAlarms = running();
staleAlarms.monitoring = { status: "live", age_seconds: 6, active_alarms: [{ code: "udp_errors", level: "error" }] };
assert.equal(diagnose(staleAlarms, true).alarms, null);
assert.equal(diagnose(staleAlarms, true).code, "telemetry_missing");
const crash = running();
Object.assign(crash, { phase: "error", process_alive: false, message: "Avvio encoder fallito",
  recovery: { last_exit: { returncode: -9, signal: "SIGKILL" }, exhausted: true }, events: healthy.events });
assert.equal(diagnose(crash, true).tone, "bad");
assert.match(diagnose(crash, true).evidence, /SIGKILL.*esauriti/);
assert.equal(diagnose(crash, true).lastError.message, "Vecchio errore di invio"); // Explicitly labelled as historical by the renderer.
crash.phase = "restart_wait";
crash.recovery = { next_retry_seconds: 4, last_exit: { returncode: 1 } };
assert.equal(diagnose(crash, true).code, "restart_wait");
assert.match(diagnose(crash, true).evidence, /codice 1.*tra 4 s/);
for (const [code, index, href] of [["udp_errors", 2, "#configuration/outputs"], ["srt_drops", 0, "#configuration/connections"], ["video_stalled", 1, "#events"]]) {
  const active = running();
  active.monitoring.active_alarms = [{ code, label: "Problema rilevato", level: "error", guide: "Azione suggerita" }];
  const diagnosis = diagnose(active, true);
  assert.equal(diagnosis.stages[index].tone, "bad");
  assert.equal(diagnosis.action.href, href);
  assert.equal(diagnosis.detail, "Azione suggerita");
}
const filter = running();
filter.config.vapoursynth_enabled = true;
filter.vapoursynth = { status: "error" };
assert.equal(diagnose(filter, true).action.href, "#configuration/filters");
assert.equal(diagnose(filter, true).stages[1].tone, "warning");
const reconnecting = running();
reconnecting.signal_state = "reconnecting";
assert.equal(diagnose(reconnecting, true).code, "source_missing");
console.log("Diagnostica: fermo, errore, recupero, cadenza, filtro, dati scaduti e contatori storici OK");
console.log("Overview: stati sani/degradati/offline, uscite e discontinuità dei grafici OK");
