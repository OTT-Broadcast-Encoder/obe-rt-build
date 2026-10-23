/* Overview: current health and bounded history, independent of configuration. */
(() => {
  "use strict";
  const finite = value => typeof value === "number" && Number.isFinite(value);
  const fresh = value => value?.status === "live" && finite(value.age_seconds) && value.age_seconds >= 0 && value.age_seconds <= 5;
  const number = new Intl.NumberFormat("it-IT", { maximumFractionDigits: 1 });
  const clock = new Intl.DateTimeFormat("it-IT", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const format = (value, unit = "") => finite(value) ? `${number.format(value)}${unit ? (unit === "%" ? unit : ` ${unit.trim()}`) : ""}` : "—";
  const stage = (name, tone, label) => ({ name, tone, label });

  function outputRows(state, connected) {
    if (state?.transport?.independent_process) {
      const service = state.transport;
      const active = connected && service.service_available;
      return [{ id: "primary", name: "UDP primaria", protocol: "udp", enabled: true }, ...(state.config?.outputs || [])].map(config => {
        const item = (service.outputs || []).find(output => output.id === config.id);
        return { name: config.name, protocol: config.protocol.toUpperCase(),
          status: !config.enabled ? "disabled" : !connected ? "unknown" : !active ? "error" :
            !item ? "idle" : item.status === "live" && (service.input_status !== "live" || !(item.bitrate_kbps > 0)) ? "waiting" : item.status,
          bitrate: active ? item?.bitrate_kbps : null, errors: active ? item?.errors : null,
          drops: active && finite(item?.dropped_packets) ? item.dropped_packets + (item.srt_dropped_packets || 0) : null,
          rtt: active ? item?.rtt_ms : null };
      });
    }
    const live = connected && state?.process_alive && fresh(state.telemetry);
    const active = connected && state?.process_alive && fresh(state.monitoring);
    const telemetry = state?.telemetry || {};
    const primary = { name: "UDP primaria", protocol: "UDP", status: !connected ? "unknown" : !state?.process_alive ? "idle" :
      !live ? "unknown" : telemetry.output_bitrate_kbps > 0 ? "live" : "waiting",
    bitrate: live ? telemetry.output_bitrate_kbps : null, errors: live ? telemetry.output_errors : null, drops: null, rtt: null };
    return [primary, ...(state?.config?.outputs || []).map(config => {
      const item = (state?.transport?.outputs || []).find(output => output.id === config.id);
      return { name: config.name, protocol: (item?.protocol || config.protocol || "").toUpperCase(),
        status: !config.enabled ? "disabled" : !connected ? "unknown" : !state.process_alive ? "idle" :
          !active || !item ? "unknown" : item.status === "live" && !(item.bitrate_kbps > 0) ? "waiting" : item.status,
        bitrate: active ? item?.bitrate_kbps : null, errors: active ? item?.errors : null,
        drops: active && finite(item?.dropped_packets) ? item.dropped_packets + (item.srt_dropped_packets || 0) : null,
        rtt: active ? item?.rtt_ms : null };
    })];
  }

  function summarize(state, connected) {
    const blank = [stage("Ingresso", "neutral", "Da verificare"), stage("Codifica", "neutral", "Da verificare"), stage("Uscite", "neutral", "Da verificare")];
    if (!connected || !state) return { code: "offline", tone: "warning", title: "Stato non disponibile", detail: "Servizio non connesso. Riconnessione automatica in corso.", stages: blank };
    if (state.phase === "idle" && !state.process_alive) {
      const stages = blank.map(s => ({ ...s, label: "Fermo" }));
      if (state.transport?.independent_process) stages[2] = stage("Uscite", state.transport.service_available ? "neutral" : "bad",
        !state.transport.service_available ? "Servizio non disponibile" : state.transport.running ? "In attesa di segnale" : "Ferme");
      return { code: "idle", tone: "neutral", title: "Canale fermo", detail: "Nessuna codifica attiva", stages };
    }
    const monitored = fresh(state.monitoring);
    const live = fresh(state.telemetry) && state.process_alive;
    const fps = live ? state.telemetry.video_fps : null;
    const expected = state.signal_details?.frame_rate;
    const cadence = finite(expected) && finite(fps) && Math.abs(fps - expected) > Math.max(1, expected * .08);
    const rows = outputRows(state, connected).filter(row => row.status !== "disabled");
    const stages = [
      stage("Ingresso", !state.process_alive ? "neutral" : monitored && state.signal_state === "receiving" ? "good" : "warning",
        !state.process_alive ? "Non verificato" : !monitored ? "Non aggiornato" : state.signal_state === "receiving" ? "In ricezione" : state.signal_state === "reconnecting" ? "Riconnessione" : "Segnale non confermato"),
      stage("Codifica", live && state.encoding_started && fps > 0 && !cadence ? "good" : "warning",
        !live ? "Misure non aggiornate" : !state.encoding_started ? "In avvio" : fps === 0 ? "Nessun avanzamento" : cadence ? "Cadenza fuori atteso" : fps > 0 ? `${format(fps)} fps` : finite(fps) ? "Nessun avanzamento" : "In attesa di misure"),
      stage("Uscite", rows.every(row => row.status === "live") ? "good" : rows.some(row => row.status === "error") ? "bad" : "warning",
        `${rows.filter(row => row.status === "live").length}/${rows.length} in trasmissione`)
    ];
    const alarms = monitored ? state.monitoring.active_alarms || [] : [];
    for (const alarm of alarms) {
      const index = { source_missing: 0, srt_drops: 0, srt_rtt: 0, video_stalled: 1, video_queue: 1, udp_errors: 2 }[alarm.code];
      if (index === undefined) continue;
      if (stages[index].tone !== "bad") {
        stages[index].tone = alarm.level === "error" ? "bad" : "warning";
        stages[index].label = alarm.label;
      }
    }
    let title, detail, tone, code;
    const failure = alarms.find(alarm => alarm.level === "error");
    if (state.phase === "error" || (state.phase === "running" && !state.process_alive)) {
      code = "encoder_error";
      stages[1] = stage("Codifica", "bad", "Processo in errore");
      [tone, title, detail] = ["bad", "Encoder in errore", state.message || "Processo di codifica non attivo"];
    } else if (state.phase === "restart_wait") {
      code = "restart_wait";
      [tone, title, detail] = ["warning", "Riavvio automatico in attesa", state.message];
    } else if (failure) {
      code = failure.code || "alarm";
      [tone, title, detail] = ["bad", failure.label, failure.guide];
    } else if (state.phase === "reconnecting") {
      code = "source_missing";
      [tone, title, detail] = ["warning", "Ingresso in riconnessione", state.message];
    } else if (state.phase !== "running") {
      code = "preparing";
      [tone, title, detail] = ["warning", "Canale in preparazione", state.message];
    } else if (state.config?.vapoursynth_enabled && state.vapoursynth?.status !== "active") {
      code = "filter";
      stages[1] = stage("Codifica", "warning", "Filtro non attivo");
      [tone, title, detail] = ["warning", "Filtro non attivo", "Il filtro VapourSynth configurato non è confermato in uso."];
    } else if (alarms.length) {
      code = alarms[0].code || "alarm";
      [tone, title, detail] = ["warning", alarms[0].label, alarms[0].guide];
    } else if (!monitored || !live) {
      code = "telemetry_missing";
      [tone, title, detail] = ["warning", "Misure non aggiornate", "Lo stato del video non è verificabile con i dati disponibili."];
    } else if (state.signal_state !== "receiving") {
      code = "source_missing";
      [tone, title, detail] = ["warning", state.signal_state === "reconnecting" ? "Ingresso in riconnessione" : "Segnale non confermato", "Controlla il mittente e la connessione d’ingresso."];
    } else if (state.encoding_started && fps === 0) {
      code = "video_stalled";
      [tone, title, detail] = ["warning", "Il video non avanza", "0 fps misurati. Controlla gli eventi e la coda video."];
    } else if (cadence) {
      code = "cadence";
      [tone, title, detail] = ["warning", "La codifica non mantiene la cadenza", `${format(fps)} fps misurati · attesi ${format(expected)} fps. Controlla CPU, filtri e preset.`];
    } else if (rows.some(row => row.status === "error")) {
      code = "output_error";
      [tone, title, detail] = ["bad", "Invio in errore", "Controlla il servizio di trasporto e la destinazione d’uscita."];
    } else if (rows.some(row => row.status !== "live")) {
      code = "output_waiting";
      [tone, title, detail] = ["warning", "Uscita non confermata", "Una o più uscite non stanno confermando l’invio di dati."];
    } else if (stages.some(s => s.tone !== "good") || !monitored) {
      code = "unconfirmed";
      const issue = stages.find(s => s.tone !== "good");
      [tone, title, detail] = [stages.some(s => s.tone === "bad") ? "bad" : "warning", "Canale da verificare", issue ? `${issue.name}: ${issue.label}` : "Allarmi non aggiornati"];
    } else if (state.config?.input_url?.startsWith("srt://") && !fresh(state.srt)) {
      code = "srt_missing";
      [tone, title, detail] = ["warning", "Statistiche SRT non aggiornate", "Il video avanza; perdita e recupero SRT non sono verificabili."];
    } else if (state.delivery?.config?.enabled && (!fresh(state.delivery) || state.delivery.receiver?.status !== "live")) {
      code = "delivery";
      [tone, title, detail] = ["warning", "Consegna da verificare", "Ricevitore finale non disponibile o segnale degradato"];
    } else {
      code = "healthy";
      [tone, title, detail] = ["good", "Flusso regolare", "Ingresso presente · codifica in avanzamento · invio attivo"];
    }
    return { code, tone, title, detail: detail || "", stages };
  }

  function diagnose(state, connected) {
    const health = summarize(state, connected);
    const live = connected && state?.process_alive && fresh(state.telemetry);
    const monitored = connected && fresh(state?.monitoring);
    const telemetry = live ? state.telemetry : {};
    const expected = state?.signal_details?.frame_rate;
    const alarms = monitored ? state.monitoring.active_alarms || [] : null;
    const actions = {
      idle: ["Apri configurazione", "#configuration"],
      source_missing: ["Controlla ingresso", "#configuration/connections"],
      filter: ["Controlla filtro", "#configuration/filters"],
      cadence: ["Controlla codifica", "#configuration/video"],
      video_queue: ["Controlla codifica", "#configuration/video"],
      srt_drops: ["Controlla SRT", "#configuration/connections"],
      srt_rtt: ["Controlla SRT", "#configuration/connections"],
      udp_errors: ["Controlla uscite", "#configuration/outputs"],
      output_error: ["Controlla uscite", "#configuration/outputs"],
      output_waiting: ["Controlla uscite", "#configuration/outputs"],
      restart_wait: ["Controlla recupero", "#configuration/management"],
      delivery: ["Controlla consegna", "#overview"]
    };
    const [label, href] = actions[health.code] || ["Apri eventi", "#events"];
    const signal = state?.signal;
    const output = outputRows(state, connected)[0];
    const notes = [
      connected && monitored && state?.process_alive && state.signal_state === "receiving" && signal?.width ?
        `${signal.width} × ${signal.height}${signal.scan || ""} · ${format(expected, "fps")}` : "",
      live ? `${format(telemetry.video_fps)} fps misurati${finite(expected) ? ` / ${format(expected)} attesi` : ""}` : "",
      finite(output.bitrate) ? `${format(output.bitrate, "kb/s")} · target ${format(state?.config?.mux_bitrate_kbps, "kb/s")}` : ""
    ];
    let evidence = "";
    if (["cadence", "video_queue", "video_stalled"].includes(health.code) && finite(telemetry.video_queue_frames)) {
      evidence = `Coda video: ${format(telemetry.video_queue_frames)} frame${expected > 0 ? ` · ${format(telemetry.video_queue_frames / expected, "s")}` : ""}`;
    } else if (["srt_drops", "srt_rtt"].includes(health.code) && connected && fresh(state?.srt)) {
      evidence = `RTT ${format(state.srt.rtt_ms, "ms")} · latenza ${format(state.srt.latency_ms, "ms")} · scarti totali connessione ${format(state.srt.dropped_packets)}`;
    } else if (["encoder_error", "restart_wait"].includes(health.code)) {
      const exit = state.recovery?.last_exit;
      if (exit) evidence = `Ultima uscita del processo: ${exit.signal || (finite(exit.returncode) ? `codice ${exit.returncode}` : "codice non disponibile")}`;
      const recovery = state.recovery;
      if (recovery?.exhausted) evidence += `${evidence ? " · " : ""}Tentativi di recupero esauriti`;
      else if (finite(recovery?.next_retry_seconds)) evidence += `${evidence ? " · " : ""}Prossimo tentativo tra ${format(recovery.next_retry_seconds, "s")}`;
    }
    if (health.code === "healthy") {
      health.title = "Codifica e invio attivi";
      health.detail = `Ingresso presente · ${finite(expected) ? "cadenza regolare" : "codifica in avanzamento"} · invio locale attivo`;
      if (!state.delivery?.config?.enabled) evidence = "Ricezione sul decoder non verificata.";
    }
    // This is explicitly historical: a log entry alone is not an active alarm.
    const lastError = connected && ["bad", "warning"].includes(health.tone) ?
      [...(state?.events || [])].reverse().find(event => event.level === "error") : null;
    return { ...health, stages: health.stages.map((item, i) => ({ ...item, note: notes[i] })),
      evidence, action: health.code === "healthy" ? null : { label, href }, lastError, alarms,
      fps: live ? telemetry.video_fps : null,
      freshness: !connected ? "Servizio non connesso" : health.code === "idle" ? "Encoder fermo" :
        live ? `Misure encoder: ${format(telemetry.age_seconds, "s")} fa` : "Misure encoder non aggiornate" };
  }

  // Break traces at missing samples, process changes and gaps; never invent zeros.
  function trace(samples, read, key, end, seconds, scale) {
    let previous = null;
    return samples.map(sample => {
      const time = Date.parse(sample.time), value = read(sample), identity = key(sample);
      if (!finite(time) || time < end - seconds * 1000 || time > end || !finite(value)) { previous = null; return ""; }
      const joined = previous && previous.identity === identity && time > previous.time && time - previous.time <= 5000;
      const x = (time - end + seconds * 1000) / (seconds * 1000) * 600;
      const y = 110 - Math.min(1, Math.max(0, value) / scale) * 100;
      previous = { time, identity };
      return `${joined ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
    }).filter(Boolean).join(" ");
  }

  const byId = id => document.getElementById(id);
  const text = (id, value) => { const el = byId(id); if (el.textContent !== value) el.textContent = value; };
  let currentState = null, currentConnected = false, archive = null, historyError = "", networkName = "";
  const labels = { live: "In trasmissione", idle: "Ferma", disabled: "Disabilitata", connecting: "Connessione", reconnecting: "Riconnessione", error: "Errore", waiting: "In attesa di dati", unknown: "Non aggiornata" };

  function render(state, connected) {
    currentState = state; currentConnected = connected;
    const health = summarize(state, connected);
    byId("overview-health").dataset.tone = health.tone;
    text("overview-health-title", health.title);
    text("overview-health-detail", health.detail);
    byId("overview-checks").replaceChildren(...health.stages.map(item => {
      const node = document.createElement("div"), label = document.createElement("span"), value = document.createElement("strong");
      node.dataset.tone = item.tone; label.textContent = item.name; value.textContent = item.label;
      node.append(label, value); return node;
    }));
    text("overview-freshness", connected && fresh(state?.telemetry) ? `Campione ${format(state.telemetry.age_seconds)} s fa` :
      connected && state?.phase === "idle" ? "Encoder fermo" : "Misure non confermate");
    const metric = connected ? state?.metrics : null;
    const system = connected && fresh(state?.system) ? state.system : null;
    text("overview-uptime", state?.process_alive && connected ? `${format((metric?.uptime_seconds || 0) / 3600)} h` : "—");
    text("overview-process-cpu", format(metric?.cpu_percent, "%"));
    text("overview-process-memory", format(metric?.memory_mb, " MiB"));
    text("overview-disk", format(system?.disk?.used_percent, "%"));
    text("overview-limits", system ? `CPU sistema: ${format(system.cpu?.logical_cpus)} core · limite contenitore: ${format(system.limits?.cpu_quota_cores)} core / ${format(finite(system.limits?.memory_limit_bytes) ? system.limits.memory_limit_bytes / 1073741824 : null)} GiB` : "Risorse non aggiornate");
    const alarms = connected && fresh(state?.monitoring) ? state.monitoring.active_alarms || [] : null;
    text("overview-alarm-count", alarms ? `${alarms.length} attivi` : "Non aggiornati");
    const items = alarms?.length ? alarms.map(a => ({ time: a.time, level: a.level, text: a.label, guide: a.guide })) :
      (state?.events || []).filter(e => ["warning", "error"].includes(e.level)).slice(-3).reverse();
    byId("overview-incidents").replaceChildren(...(items.length ? items : [{ text: alarms ? "Nessun problema recente" : "Stato degli allarmi non disponibile" }]).map(item => {
      const li = document.createElement("li"), title = document.createElement("strong"), detail = document.createElement("span");
      li.dataset.tone = item.level === "error" ? "bad" : item.level === "warning" ? "warning" : "neutral";
      title.textContent = item.text || item.message;
      detail.textContent = [item.time ? new Date(item.time).toLocaleString("it-IT") : "", item.guide || ""].filter(Boolean).join(" · ");
      li.append(title, detail); return li;
    }));
    byId("overview-outputs").replaceChildren(...outputRows(state, connected).map(item => {
      const row = document.createElement("tr");
      [item.name, item.protocol, labels[item.status] || "Non aggiornata", format(item.bitrate / 1000), format(item.errors), format(item.drops), format(item.rtt)].forEach((value, index) => {
        const cell = document.createElement("td");
        cell.textContent = index === 3 && !finite(item.bitrate) ? "—" : value;
        if (index === 2) cell.dataset.tone = item.status === "live" ? "good" : item.status === "error" ? "bad" : ["idle", "disabled"].includes(item.status) ? "neutral" : "warning";
        row.append(cell);
      }); return row;
    }));
    const interfaces = state?.system?.network || [];
    const select = byId("overview-interface");
    const signature = interfaces.map(i => i.interface).join("/");
    if (select.dataset.interfaces !== signature) {
      if (!interfaces.some(i => i.interface === networkName)) networkName =
        interfaces.find(i => i.state === "up" && !/^(lo|veth|docker|br-|tun|wg)/.test(i.interface))?.interface || interfaces.find(i => i.state === "up")?.interface || interfaces[0]?.interface || "";
      select.replaceChildren(...interfaces.map(i => new Option(i.interface, i.interface)));
      select.value = networkName; select.dataset.interfaces = signature;
    }
    select.disabled = !system || !interfaces.length;
    renderGraphs();
  }

  function chart(id, series, samples, end, seconds, floor, unit, key, fixed = false) {
    const values = samples.flatMap(s => series.map(line => line.read(s))).filter(finite);
    const scale = fixed ? floor : Math.max(floor, ...values) * 1.1;
    series.forEach((line, index) => byId(`overview-${id}-line-${index}`).setAttribute("d", trace(samples, line.read, key, end, seconds, scale)));
    const state = currentState;
    const readings = series.map(line => `${line.label} ${format(line.current(state), unit)}`).join(" · ");
    text(`overview-${id}-value`, readings);
    text(`overview-${id}-scale`, values.length ? `0–${format(scale)} ${unit}` : "Nessun campione");
    text(`overview-${id}-title`, `${byId(`overview-${id}-heading`).textContent}: ${values.length ? readings : "nessun campione"}`);
  }

  function renderGraphs() {
    const seconds = Number(byId("overview-window").value);
    const end = Date.parse(archive?.generated_at) || Date.now();
    const samples = (archive?.samples || []).filter(s => Date.parse(s.time) >= end - seconds * 1000 && Date.parse(s.time) <= end);
    const live = currentConnected && currentState?.process_alive && fresh(currentState.telemetry);
    const sys = currentConnected && fresh(currentState?.system);
    const video = (field, divisor = 1) => ({ label: field === "video_fps" ? "Codifica" : field === "video_bitrate_kbps" ? "AVC" : "UDP",
      read: s => s.telemetry?.status === "live" && finite(s.telemetry[field]) ? s.telemetry[field] / divisor : null,
      current: s => live && finite(s?.telemetry?.[field]) ? s.telemetry[field] / divisor : null });
    const resource = (section, field, label, divisor = 1) => ({ label,
      read: s => s.system?.status === "live" && finite(s.system?.[section]?.[field]) ? s.system[section][field] / divisor : null,
      current: s => sys && finite(s?.system?.[section]?.[field]) ? s.system[section][field] / divisor : null });
    chart("bitrate", [video("video_bitrate_kbps", 1000), video("output_bitrate_kbps", 1000)], samples, end, seconds, 1, "Mbit/s", s => s.run_id);
    chart("fps", [video("video_fps")], samples, end, seconds, 30, "fps", s => s.run_id);
    chart("cpu", [resource("cpu", "usage_percent", "Sistema")], samples, end, seconds, 100, "%", s => s.system?.instance_id, true);
    chart("memory", [resource("memory", "used_bytes", "In uso", 1073741824)], samples, end, seconds, 1, "GiB", s => s.system?.instance_id);
    chart("load", [resource("cpu", "load_1m", "1 min"), resource("cpu", "load_5m", "5 min"), resource("cpu", "load_15m", "15 min")], samples, end, seconds, 1, "", s => s.system?.instance_id);
    const rate = (system, field) => { const value = system?.network?.find(i => i.interface === networkName)?.[field]; return finite(value) ? value / 1000 : null; };
    chart("network", ["rx", "tx"].map(direction => ({ label: direction.toUpperCase(),
      read: s => s.system?.status === "live" ? rate(s.system, `${direction}_bitrate_kbps`) : null,
      current: s => sys ? rate(s.system, `${direction}_bitrate_kbps`) : null })), samples, end, seconds, 1, "Mbit/s", s => s.system?.instance_id);
    text("overview-history-status", historyError || (!currentConnected ? "Storico non aggiornato · servizio non connesso" : archive ? `Storico letto alle ${clock.format(new Date(end))} · ${samples.length} campioni` : "Caricamento dello storico…"));
    text("overview-history-start", clock.format(new Date(end - seconds * 1000)));
    text("overview-history-end", clock.format(new Date(end)));
  }

  const api = { summarize, diagnose, fresh, outputRows, trace, render,
    history(data) { archive = data; historyError = ""; renderGraphs(); },
    historyError(message) { historyError = message; renderGraphs(); },
    selectInterface() { networkName = byId("overview-interface").value; renderGraphs(); } };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else window.OBEOverview = api;
})();
