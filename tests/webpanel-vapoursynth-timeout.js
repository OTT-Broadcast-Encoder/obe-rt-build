"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../webpanel/static/app.js"), "utf8");
const start = source.indexOf("  async function api(");
const end = source.indexOf("\n  }\n", start) + "\n  }".length;
assert.ok(start >= 0 && end > start);

function request(endpoint, responseDelay = null) {
  let now = 0, sequence = 0, outcome;
  const timers = new Map();
  const clock = {
    setTimeout(callback, delay) {
      timers.set(++sequence, { at: now + delay, callback });
      return sequence;
    },
    clearTimeout(id) { timers.delete(id); }
  };
  const api = vm.runInNewContext(`(${source.slice(start, end)})`, {
    AbortController, window: clock, selectedChannel: "main", requireLogin() {},
    fetch(url, options) {
      return new Promise((resolve, reject) => {
        options.signal.addEventListener("abort", () => reject(Object.assign(new Error("abort"), { name: "AbortError" })), { once: true });
        if (responseDelay !== null) clock.setTimeout(() => resolve({ ok: true, json: async () => ({ valid: true }) }), responseDelay);
      });
    }
  });
  api(endpoint, "POST", { script: "clip.set_output()" }).then(value => { outcome = { value }; }, error => { outcome = { error }; });
  return {
    async advance(milliseconds) {
      const target = now + milliseconds;
      for (;;) {
        const next = [...timers].filter(([, timer]) => timer.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
        if (!next) break;
        now = next[1].at;
        timers.delete(next[0]);
        next[1].callback();
        await new Promise(setImmediate);
      }
      now = target;
      await new Promise(setImmediate);
      return outcome;
    },
    timers
  };
}

(async () => {
  const slow = request("vapoursynth-test", 20000);
  assert.equal(await slow.advance(16000), undefined, "The script test must survive the ordinary request timeout");
  assert.equal((await slow.advance(4000)).value.valid, true);
  assert.equal(slow.timers.size, 0, "Successful requests must cancel their abort timer");
  const stalled = request("vapoursynth-test");
  assert.equal(await stalled.advance(60000), undefined, "Allow the backend to return its timeout diagnostic");
  assert.match((await stalled.advance(15000)).error.message, /75 secondi/);
  const ordinary = request("state");
  assert.match((await ordinary.advance(15000)).error.message, /15 secondi/);
  console.log("PASS: slow VapourSynth response, bounded request, ordinary timeout and timer cleanup");
})().catch(error => { console.error(error); process.exitCode = 1; });
