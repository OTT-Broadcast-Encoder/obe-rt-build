"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { execFileSync } = require("node:child_process");

const source = fs.readFileSync(path.join(__dirname, "../webpanel/static/app.js"), "utf8");
function implementation(name, next) {
  const locate = (value, from = 0) => ["", "async "].map(prefix => source.indexOf(`  ${prefix}function ${value}(`, from))
    .filter(index => index >= 0).sort((a, b) => a - b)[0] ?? -1;
  const start = locate(name);
  const end = locate(next, start + 1);
  assert.ok(start >= 0 && end > start, `${name} must exist`);
  return source.slice(start, end);
}
const defaults = { srt_mode: "caller", latency_ms: 200, srt_key_length: 32,
  srt_max_bandwidth_kbps: 0, srt_connect_timeout_ms: 1000, srt_send_timeout_ms: 1000,
  srt_drop_late_packets: true };
function card(protocol) {
  const values = { id: "main", name: "MAIN", enabled: true, protocol,
    url: `${protocol}://127.0.0.1:19000`, ...defaults, passphrase: "", "remove-key": false };
  const controls = Object.fromEntries(Object.entries(values).map(([name, value]) => [name,
    { value: String(value), checked: value === true, disabled: false, required: false }]));
  const wrappers = Object.keys(defaults).concat("passphrase", "remove-key").map(name => ({
    dataset: { srtOptionsOnly: ["srt_mode", "latency_ms", "passphrase", "remove-key"].includes(name) ? undefined : "true" },
    hidden: false, querySelectorAll: () => [controls[name]]
  }));
  return { controls, wrappers, dataset: { outputId: "main" }, querySelectorAll: () => wrappers };
}
function harness(protocol, previous, supported = true, parameters) {
  const current = card(protocol);
  const state = { capabilities: { outputs: { srt_options: supported, srt_parameters: parameters } }, config: { outputs: previous ? [previous] : [] } };
  const context = { state, outputCards: () => [current], outputField: (item, key) => item.controls[key] };
  const api = vm.runInNewContext(`${implementation("outputSrtParameters", "readOutputCredentials")}\n({ readOutputs, outputSrtParameters })`, context);
  return { current, read: () => JSON.parse(JSON.stringify(api.readOutputs()[0])), parameters: () => JSON.parse(JSON.stringify(api.outputSrtParameters())) };
}

for (const protocol of ["udp", "rtp"]) {
  const saved = { id: "main", ...defaults, srt_connect_timeout_ms: 3000 };
  const { current, read } = harness(protocol, saved);
  current.controls.srt_connect_timeout_ms.value = "50";
  current.controls.srt_send_timeout_ms.value = "";
  current.controls.latency_ms.value = "-1";
  current.controls.srt_key_length.value = "";
  current.controls.srt_max_bandwidth_kbps.value = "10";
  assert.equal(read().srt_connect_timeout_ms, 3000, "Inactive SRT drafts must not replace saved options");
  assert.equal(read().srt_send_timeout_ms, 1000);
  assert.equal(read().latency_ms, 200);
  assert.equal(read().srt_key_length, 32);
  assert.equal(read().srt_max_bandwidth_kbps, 0);
  current.controls.protocol.value = "srt";
  assert.equal(read().srt_connect_timeout_ms, 50, "Returning to SRT must preserve its editable draft");
  const fresh = harness(protocol);
  fresh.current.controls.srt_connect_timeout_ms.value = "";
  assert.equal(fresh.read().srt_connect_timeout_ms, 1000, "New non-SRT outputs use valid defaults");
}
const legacy = harness("udp", null, false).read();
assert.deepEqual(Object.keys(legacy).sort(), ["id", "name", "enabled", "protocol", "url", "srt_mode", "latency_ms"].sort());
const parameters = JSON.parse(execFileSync("python3", ["-B", "-c",
  "import json,sys; sys.path.insert(0,'webpanel'); from srt_options import SRT_PARAMETERS; print(json.dumps(SRT_PARAMETERS))"],
  { cwd: path.join(__dirname, ".."), encoding: "utf8" }));
assert.deepEqual(harness("srt").parameters(), parameters, "Legacy contract must remain compatible with this release");
parameters.srt_connect_timeout_ms.default = 2500;
parameters.srt_connect_timeout_ms.max = 30000;
const advertised = harness("udp", null, true, parameters);
assert.equal(advertised.read().srt_connect_timeout_ms, 2500, "Frontend defaults must use backend metadata");
assert.equal(advertised.parameters().srt_connect_timeout_ms.max, 30000);

const visibility = vm.runInNewContext(`(${implementation("updateOutputProtocolFields", "updateBroadcastControls")})`, {
  outputField: (item, key) => item.controls[key]
});
for (const protocol of ["udp", "rtp", "srt"]) {
  for (const blocked of [false, true]) for (const supported of [false, true]) {
    const current = card(protocol);
    visibility(current, blocked, supported);
    for (const wrapper of current.wrappers) {
      const inactive = protocol !== "srt" || (wrapper.dataset.srtOptionsOnly === "true" && !supported);
      assert.equal(wrapper.hidden, inactive);
      assert.equal(wrapper.querySelectorAll()[0].disabled, blocked || inactive,
        "Hidden SRT fields must not participate in HTML validation");
    }
  }
}
console.log("PASS: SRT → UDP/RTP drafts, saved options, legacy payload and hidden-field validation");

function credentialsHarness() {
  const current = card("srt");
  current.isConnected = true;
  current.querySelector = () => ({ textContent: "" });
  let resolve, calls = 0;
  const context = { URL, selectedChannel: "main", outputKeyGeneration: 0,
    authRequired: true, authBlocked: false, loggingOut: false, connected: true, pending: null,
    conflict: () => false, updateControls() {}, outputCards: () => [current],
    outputField: (item, key) => item.controls[key], savedOutputKeys: new WeakMap(),
    outputKeyLoads: new WeakSet(), outputKeyFailures: new WeakSet(), outputKeyTouched: new WeakSet(),
    state: { revision: 3, instance_id: "fixture", output_credentials: { main: true },
      capabilities: { output_passphrase_reveal: true },
      config: { outputs: [{ id: "main", protocol: "srt", url: current.controls.url.value, ...defaults }] } },
    api() { calls += 1; return new Promise(done => { resolve = done; }); }
  };
  const functions = [implementation("savedOutputEndpoint", "clearMovedOutputKey"),
    implementation("clearMovedOutputKey", "clearOutputKeys"),
    implementation("credentialOrigin", "availableCredentialKeys"),
    implementation("readOutputCredentials", "loadSavedOutputKey"),
    implementation("loadSavedOutputKey", "outputErrorField")].join("\n");
  const api = vm.runInNewContext(`${functions}\n({ savedOutputEndpoint, clearMovedOutputKey, readOutputCredentials, loadSavedOutputKey })`, context);
  return { current, context, api, resolve: value => resolve(value), calls: () => calls,
    read: () => JSON.parse(JSON.stringify(api.readOutputCredentials())) };
}

(async () => {
  const saved = credentialsHarness();
  const secret = "fixture-output-passphrase";
  saved.current.controls.passphrase.value = secret;
  saved.context.savedOutputKeys.set(saved.current, { value: secret, endpoint: saved.api.savedOutputEndpoint(saved.current) });
  assert.deepEqual(saved.read(), {}, "An unchanged visible key must not create an unsaved draft");
  saved.current.controls.passphrase.value = "";
  assert.deepEqual(saved.read(), { main: "" }, "Clearing a loaded key must request removal");
  saved.current.controls.passphrase.value = secret;
  saved.current.controls.url.value = "srt://127.0.0.1:19001";
  saved.api.clearMovedOutputKey(saved.current);
  assert.equal(saved.current.controls.passphrase.value, "", "A saved key must not follow an endpoint change");
  assert.deepEqual(saved.read(), {});

  for (const scenario of ["loaded", "endpoint", "mode", "channel", "revision", "logout", "edited", "removed-card"]) {
    const h = credentialsHarness();
    const loading = h.api.loadSavedOutputKey(h.current);
    assert.equal(h.calls(), 1);
    if (scenario === "endpoint") h.current.controls.url.value = "srt://127.0.0.1:19001";
    if (scenario === "mode") h.current.controls.srt_mode.value = "listener";
    if (scenario === "channel") h.context.selectedChannel = "channel-2";
    if (scenario === "revision") h.context.state.revision += 1;
    if (scenario === "logout") { h.context.loggingOut = true; h.context.outputKeyGeneration += 1; }
    if (scenario === "edited") { h.context.outputKeyTouched.add(h.current); h.current.controls.passphrase.value = "fixture-manual-key"; }
    if (scenario === "removed-card") h.current.isConnected = false;
    h.resolve({ passphrase: secret });
    await loading;
    assert.equal(h.current.controls.passphrase.value,
      scenario === "loaded" ? secret : scenario === "edited" ? "fixture-manual-key" : "", scenario);
    assert.equal(h.context.outputKeyLoads.has(h.current), false);
    if (scenario === "loaded") assert.deepEqual(h.read(), {});
  }
  console.log("PASS: passphrase persistence/removal and asynchronous endpoint/channel/logout safeguards");
})().catch(error => { console.error(error); process.exitCode = 1; });
