"use strict";

const assert = require("node:assert/strict");
const { parse, build, reservedOptions } = require("../webpanel/static/input-url.js");

const udp = parse("udp://239.1.2.3:2079?localport=2080&fifo_size=100000");
assert.equal(udp.protocol, "udp");
assert.equal(udp.host, "239.1.2.3");
assert.equal(udp.localPort, "2080");
assert.equal(build(udp), "udp://239.1.2.3:2079?fifo_size=100000&localport=2080");
assert.deepEqual(reservedOptions("udp", "fifo_size=100000&localport=0"), ["localport"]);
const rtpAlias = parse("rtp://239.1.2.3:2079?localport=2080&pkt_size=1316");
assert.equal(rtpAlias.localPort, "2080");
assert.equal(build(rtpAlias), "rtp://239.1.2.3:2079?pkt_size=1316&localrtpport=2080");

const srt = parse("srt://[::1]:9000?mode=listener&transtype=live&latency=200001");
assert.equal(srt.host, "[::1]");
assert.equal(srt.mode, "listener");
assert.equal(srt.latencyMs, "200.001");
assert.equal(build(srt), "srt://[::1]:9000?transtype=live&mode=listener&latency=200001");
assert.equal(build({ ...srt, host: "example.net", mode: "caller", latencyMs: "250" }),
  "srt://example.net:9000?transtype=live&mode=caller&latency=250000");
const flags = parse("srt://media.example:9000?tsbpd&latency=200000&foo=%2F%20+&flag=");
assert.equal(flags.options, "tsbpd&foo=%2F%20+&flag=");
assert.equal(build({ ...flags, host: "new.example" }),
  "srt://new.example:9000?tsbpd&foo=%2F%20+&flag=&latency=200000");
assert.deepEqual(reservedOptions("srt", "mode=listener&latency=500000&tsbpd"), ["mode", "latency"]);

const buffered = parse("srt://media.example:20879?mode=listener&rcvbuf=67108864&ffs=65536&transtype=live&latency=200000");
assert.equal(buffered.rcvbuf, "67108864");
assert.equal(buffered.ffs, "65536");
assert.equal(buffered.options, "transtype=live");
assert.equal(build(buffered), "srt://media.example:20879?transtype=live&mode=listener&latency=200000&rcvbuf=67108864&ffs=65536");
const changed = new URL(build({ ...buffered, rcvbuf: "33554432", ffs: "32768", options: "transtype=live&rcvbuf=1&ffs=32" }));
assert.deepEqual(changed.searchParams.getAll("rcvbuf"), ["33554432"]);
assert.deepEqual(changed.searchParams.getAll("ffs"), ["32768"]);
assert.equal(new URL(build({ ...buffered, rcvbuf: "6.7108864e7" })).searchParams.get("rcvbuf"), "67108864");
const cleared = new URL(build({ ...buffered, rcvbuf: "", ffs: "" }));
assert.equal(cleared.searchParams.has("rcvbuf"), false);
assert.equal(cleared.searchParams.has("ffs"), false);
assert.deepEqual(reservedOptions("srt", "rcvbuf=67108864&%66fs=65536"), ["rcvbuf", "ffs"]);
assert.deepEqual(reservedOptions("udp", "rcvbuf=67108864&ffs=65536"), []);
assert.equal(parse("srt://media.example:9000?rcvbuf=-1&ffs=-1").rcvbuf, "");
assert.equal(parse("srt://media.example:9000?rcvbuf=bad").options, "rcvbuf=bad");
assert.equal(new URL(build({ ...udp, rcvbuf: "67108864", ffs: "65536" })).searchParams.has("rcvbuf"), false);

const rtmp = parse("rtmp://media.example:1935/live/program");
assert.equal(rtmp.path, "/live/program");
assert.equal(build(rtmp), "rtmp://media.example:1935/live/program");
assert.equal(build({ ...rtmp, path: "new/live" }), "rtmp://media.example:1935/new/live");

const http = parse("https://media.example:8443/live.ts?format=ts&fifo_size=123");
assert.equal(http.port, "8443");
assert.equal(http.options, "format=ts&fifo_size=123");
assert.equal(build(http), "https://media.example:8443/live.ts?format=ts&fifo_size=123");
assert.equal(parse("file:///tmp/video.ts"), null);
assert.equal(parse("srt://user:secret@media.example:9000"), null);

console.log("PASS input URL editor");
