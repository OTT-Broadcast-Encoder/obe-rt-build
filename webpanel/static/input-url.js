"use strict";

// The backend persists input_url. These helpers only edit that existing value.
(() => {
  const protocols = new Set(["udp", "rtp", "srt", "rtmp", "http", "https", "rtsp", "tcp"]);
  const modes = new Set(["caller", "listener", "rendezvous"]);
  const optionName = (part) => new URLSearchParams(part).keys().next().value;
  const dedicatedOptions = { srt: new Set(["mode", "latency", "rcvbuf", "ffs"]), udp: new Set(["localport"]),
    rtp: new Set(["localport", "localrtpport"]) };

  function reservedOptions(protocol, options) {
    const reserved = dedicatedOptions[protocol];
    return reserved ? [...new Set(options.trim().replace(/^\?/, "").split("&").filter(Boolean)
      .map(optionName).filter(name => reserved.has(name)))] : [];
  }

  function parse(value) {
    try {
      const url = new URL(value);
      const protocol = url.protocol.slice(0, -1);
      if (!protocols.has(protocol) || !url.hostname || url.username || url.password || url.hash) return null;
      const rawOptions = url.search.slice(1).split("&").filter(Boolean);
      const params = new URLSearchParams(url.search);
      const consumed = new Set();
      let mode = "", latencyMs = "", localPort = "";
      const buffers = { rcvbuf: "", ffs: "" };
      if (protocol === "srt") {
        if (modes.has(params.get("mode"))) { mode = params.get("mode"); consumed.add("mode"); }
        const latency = params.get("latency");
        if (latency && /^\d+$/.test(latency)) { latencyMs = String(Number(latency) / 1000); consumed.add("latency"); }
        for (const key of Object.keys(buffers)) {
          const value = params.get(key);
          if (value === "-1" || (value && /^\d+$/.test(value))) {
            buffers[key] = value === "-1" ? "" : value;
            consumed.add(key);
          }
        }
      }
      if (protocol === "udp" || protocol === "rtp") {
        const key = protocol === "udp" || !params.has("localrtpport") ? "localport" : "localrtpport";
        const port = params.get(key);
        if (port && /^\d+$/.test(port)) { localPort = port; consumed.add(key); }
      }
      return { protocol, host: url.hostname, port: url.port, path: url.pathname,
        options: rawOptions.filter(part => !consumed.has(optionName(part))).join("&"), mode, latencyMs, localPort, ...buffers };
    } catch { return null; }
  }

  function build({ protocol, host, port, path, options, mode, latencyMs, localPort, rcvbuf = "", ffs = "" }) {
    const address = host.trim();
    const authority = address.includes(":") && !address.startsWith("[") ? `[${address}]` : address;
    const query = options.trim().replace(/^\?/, "").split("&").filter(Boolean);
    if (protocol === "srt") {
      for (let index = query.length - 1; index >= 0; index--) if (dedicatedOptions.srt.has(optionName(query[index]))) query.splice(index, 1);
      if (mode) query.push(`mode=${encodeURIComponent(mode)}`);
      if (latencyMs !== "") query.push(`latency=${Math.round(Number(latencyMs) * 1000)}`);
      if (rcvbuf !== "") query.push(`rcvbuf=${Number(rcvbuf)}`);
      if (ffs !== "") query.push(`ffs=${Number(ffs)}`);
    }
    if (protocol === "udp" || protocol === "rtp") {
      const key = protocol === "udp" ? "localport" : "localrtpport";
      for (let index = query.length - 1; index >= 0; index--) if (dedicatedOptions[protocol].has(optionName(query[index]))) query.splice(index, 1);
      if (localPort !== "") query.push(`${key}=${encodeURIComponent(localPort)}`);
    }
    const suffix = path.trim() ? (path.trim().startsWith("/") ? path.trim() : `/${path.trim()}`) : "";
    return `${protocol}://${authority}${port ? `:${port}` : ""}${suffix}${query.length ? `?${query.join("&")}` : ""}`;
  }

  const api = Object.freeze({ parse, build, reservedOptions });
  globalThis.obeInputUrl = api;
  if (typeof module !== "undefined") module.exports = api;
})();
