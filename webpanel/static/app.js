"use strict";

(() => {
  const byId = (id) => document.getElementById(id);
  const form = byId("config-form");
  const fields = ["name", "input_url", "output_url", "video_bitrate_kbps", "mux_bitrate_kbps", "vbv_buffer_kbits", "preset", "gop_frames", "bframes", "lookahead_frames", "threads", "audio_bitrate_kbps"];
  const numericFields = new Set(["video_bitrate_kbps", "mux_bitrate_kbps", "vbv_buffer_kbits", "gop_frames", "bframes", "lookahead_frames", "threads", "audio_bitrate_kbps"]);
  const phaseLabels = { idle: "Fermo", probing: "Verifica ingresso", starting: "Avvio in corso", running: "In codifica", reconnecting: "Riconnessione", stopping: "Arresto in corso", error: "Errore" };
  const phaseTitles = { idle: "Encoder fermo", probing: "Verifica della sorgente", starting: "Avvio dell’encoder", running: "Codifica in corso", reconnecting: "Sorgente in riconnessione", stopping: "Arresto dell’encoder", error: "Intervento richiesto" };
  const viewTitles = { overview: "Panoramica", configuration: "Configurazione", events: "Registro eventi" };
  const timeFormat = new Intl.DateTimeFormat("it-IT", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const shortTimeFormat = new Intl.DateTimeFormat("it-IT", { hour: "2-digit", minute: "2-digit" });
  const dateFormat = new Intl.DateTimeFormat("it-IT", { day: "2-digit", month: "short" });
  const numberFormat = new Intl.NumberFormat("it-IT", { maximumFractionDigits: 1 });
  let state = null;
  let connected = false;
  let dirty = false;
  let baseConfig = null;
  let draftRevision = null;
  let pending = null;
  let mutationEpoch = 0;
  let profileSignature = "";
  let eventSignature = "";
  let cpuSamples = [];
  let chartPid = null;
  let lastSampleAt = 0;
  let selectedChannel = new URLSearchParams(location.search).get("channel") || "main";
  let channelSignature = "";

  const text = (id, value) => { byId(id).textContent = value; };
  const isFiniteNumber = (value) => typeof value === "number" && Number.isFinite(value);
  const activeProcess = () => state && (state.process_alive || ["probing", "starting", "running", "reconnecting", "stopping"].includes(state.phase));
  const canConfigure = () => state && !activeProcess() && ["idle", "error"].includes(state.phase);
  const conflict = () => Boolean(state && draftRevision !== null && state.revision !== draftRevision);

  function setNotice(message, kind = "info") {
    const notice = byId("action-notice");
    notice.textContent = message;
    notice.className = `notice${kind === "error" ? " notice-error" : kind === "warning" ? " notice-warning" : ""}`;
    notice.hidden = !message;
    notice.setAttribute("role", kind === "error" ? "alert" : "status");
  }

  function setView() {
    const requested = window.location.hash.slice(1);
    if (requested === "main-content") {
      byId("main-content").focus();
      return;
    }
    const view = Object.hasOwn(viewTitles, requested) ? requested : "overview";
    for (const name of Object.keys(viewTitles)) byId(`view-${name}`).hidden = name !== view;
    document.querySelectorAll("[data-view]").forEach((link) => {
      if (link.dataset.view === view) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
    text("breadcrumb-current", viewTitles[view]);
    document.title = `OBE · ${viewTitles[view]}`;
  }

  function readDraft() {
    const config = {};
    for (const name of fields) {
      const value = form.elements.namedItem(name).value;
      config[name] = numericFields.has(name) ? Number(value) : value.trim();
    }
    return config;
  }

  function configsEqual(left, right) {
    return left && right && fields.every((key) => left[key] === right[key]);
  }

  function populateForm(config, revision) {
    for (const name of fields) form.elements.namedItem(name).value = config[name] ?? "";
    baseConfig = { ...config };
    draftRevision = revision;
    dirty = false;
    byId("profile-select").value = "";
    clearFieldErrors();
    updateSrt();
  }

  function updateSrt() {
    const input = form.elements.namedItem("input_url").value.trim();
    const isSrt = /^srt:\/\//i.test(input);
    byId("srt-section").hidden = !isSrt;
    if (!isSrt) byId("srt-passphrase").value = "";
  }

  function clearFieldErrors() {
    form.querySelectorAll("[aria-invalid]").forEach((element) => element.removeAttribute("aria-invalid"));
    form.querySelectorAll(".field-error").forEach((element) => element.remove());
    for (const name of fields) form.elements.namedItem(name).setCustomValidity("");
  }

  function markFieldErrors(errors) {
    if (!errors || typeof errors !== "object") return;
    for (const [key, message] of Object.entries(errors)) {
      if (!fields.includes(key)) continue;
      const element = form.elements.namedItem(key);
      element.setAttribute("aria-invalid", "true");
      const hint = document.createElement("small");
      hint.className = "field-error";
      hint.textContent = String(message);
      element.closest(".field").append(hint);
    }
  }

  function updateDraft() {
    dirty = !configsEqual(readDraft(), baseConfig);
    updateSrt();
    updateControls();
  }

  function updateControls() {
    const stopped = canConfigure();
    const active = activeProcess();
    const ready = connected && !pending && stopped && !dirty && !conflict();
    byId("channel-select").disabled = Boolean(pending);
    byId("new-channel").disabled = !connected || Boolean(pending) || dirty || byId("channel-select").options.length >= 4;
    byId("preview-toggle").disabled = !connected || Boolean(pending) || (!state?.preview?.pid && state?.phase !== "running");
    byId("preview-toggle").textContent = state?.preview?.pid ? "Spegni preview" : "Attiva preview";
    document.querySelectorAll('[data-action="start"]').forEach((button) => {
      button.disabled = !ready;
      if (button.closest(".session-actions")) button.hidden = Boolean(active);
      button.title = dirty ? "Salva prima le modifiche alla configurazione" : "";
    });
    document.querySelectorAll('[data-action="probe"]').forEach((button) => {
      button.disabled = !ready;
      button.title = dirty ? "Salva prima le modifiche alla configurazione" : "";
    });
    document.querySelectorAll('[data-action="stop"]').forEach((button) => {
      button.hidden = !active;
      button.disabled = !connected || Boolean(pending) || state?.phase === "stopping";
    });
    byId("save-button").disabled = !connected || Boolean(pending) || !stopped || !dirty || conflict();
    byId("save-button").textContent = pending === "config" ? "Salvataggio…" : "Salva configurazione";
    byId("discard-button").disabled = !state || Boolean(pending) || (!dirty && !conflict());
    byId("profile-select").disabled = !state || Boolean(pending);
    for (const name of fields) form.elements.namedItem(name).disabled = !state || Boolean(pending);
    byId("srt-passphrase").disabled = !state || Boolean(pending) || !stopped;
    byId("overview-draft").hidden = !dirty;
    const tag = byId("draft-status");
    tag.textContent = !state ? "Caricamento" : conflict() ? "Revisione aggiornata" : dirty ? "Modifiche non salvate" : "Profilo salvato";
    tag.className = `status-tag${dirty || conflict() ? " warning" : ""}`;
    text("config-revision", state ? `rev. ${state.revision}` : "—");
    let status = "Configurazione salvata";
    let hint = "Pronta per la verifica dell’ingresso.";
    if (!state) { status = "Caricamento del profilo…"; hint = "Attendi la connessione al servizio."; }
    else if (!connected) { status = "Servizio non raggiungibile"; hint = "La bozza resta disponibile in questa pagina."; }
    else if (conflict()) { status = "Il profilo salvato è cambiato"; hint = "Ripristina per caricare la revisione aggiornata. La tua bozza è ancora qui."; }
    else if (!stopped) { status = dirty ? "Bozza conservata" : "Profilo in uso"; hint = "Arresta l’encoder prima di salvare o verificare."; }
    else if (dirty) { status = "Ci sono modifiche da salvare"; hint = "Salva il profilo prima di verificare o avviare."; }
    text("save-status", status);
    text("save-hint", hint);
  }

  function formatRate(kbps) {
    return isFiniteNumber(kbps) ? `${numberFormat.format(kbps / 1000)} Mbit/s` : "—";
  }

  function formatUptime(seconds) {
    if (!isFiniteNumber(seconds) || seconds < 0) return "—";
    const whole = Math.floor(seconds);
    const hours = Math.floor(whole / 3600);
    const minutes = Math.floor((whole % 3600) / 60);
    return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(whole % 60).padStart(2, "0")}`;
  }

  function renderProfiles(profiles) {
    const signature = JSON.stringify(profiles || []);
    if (profileSignature === signature) return;
    profileSignature = signature;
    const select = byId("profile-select");
    const selected = select.value;
    select.replaceChildren(new Option("Seleziona un profilo…", ""));
    for (const profile of profiles || []) select.add(new Option(profile.label, profile.id));
    select.value = selected;
  }

  function renderSignal(config) {
    const signal = state.signal;
    const signalLabels = { unknown: "Da verificare", receiving: "Sorgente in ricezione", reconnecting: "Sorgente in riconnessione", stopped: "Sorgente ferma" };
    text("signal-state", signalLabels[state.signal_state] || "Stato sconosciuto");
    text("input-url", config.input_url || "—");
    text("output-url", config.output_url || "—");
    text("input-protocol", (config.input_url || "").split(":")[0].toUpperCase() || "—");
    text("video-settings", `${formatRate(config.video_bitrate_kbps)} configurati · ${config.preset || "—"}`);
    text("mux-settings", `Bitrate configurato: ${formatRate(config.mux_bitrate_kbps)}`);
    byId("signal-dot").className = `status-dot ${state.signal_state === "receiving" ? "good" : state.signal_state === "reconnecting" ? "" : "neutral"}`;
    if (signal && signal.width && signal.height) {
      text("input-format", `${signal.width} × ${signal.height}${signal.scan || ""}`);
      const rate = String(signal.fps || "").split("/").map(Number);
      const fps = rate.length === 2 && rate[1] ? rate[0] / rate[1] : rate[0];
      text("input-detail", `${Number.isFinite(fps) && fps > 0 ? `${numberFormat.format(fps)} fps` : "Cadenza non disponibile"}${state.encrypted ? " · SRT protetto" : ""}`);
      text("audio-format", signal.audio ? `MP2 stereo · ${config.audio_bitrate_kbps} kb/s configurati` : "Sorgente senza audio rilevato");
    } else {
      text("input-format", "In attesa della sorgente");
      text("input-detail", "Formato non ancora rilevato");
      text("audio-format", "Audio MP2 se presente");
    }
  }

  function renderMetrics(sample) {
    const metrics = state.metrics || {};
    const alive = Boolean(state.process_alive);
    const cpu = alive && isFiniteNumber(metrics.cpu_percent) ? metrics.cpu_percent : null;
    text("cpu-value", cpu === null ? "—" : numberFormat.format(cpu));
    text("memory-value", alive && isFiniteNumber(metrics.memory_mb) ? numberFormat.format(metrics.memory_mb) : "—");
    text("uptime-value", alive ? formatUptime(metrics.uptime_seconds) : "—");
    text("uptime-hint", alive ? "Dall’avvio del processo" : "Nessun processo attivo");
    byId("sampling-dot").className = `status-dot ${cpu !== null ? "good" : "neutral"}`;
    if (chartPid !== state.pid) {
      cpuSamples = [];
      chartPid = state.pid;
      lastSampleAt = 0;
    }
    const now = Date.now();
    if (sample && cpu !== null && now - lastSampleAt >= 1000) {
      cpuSamples.push({ time: now, value: cpu });
      cpuSamples = cpuSamples.filter((point) => now - point.time <= 120000).slice(-60);
      lastSampleAt = now;
    }
    if (!alive) cpuSamples = [];
    renderChart();
  }

  function renderTelemetry() {
    const telemetry = state?.telemetry || {};
    const status = !connected ? "offline" : !state?.process_alive ? "idle" : telemetry.status || "unavailable";
    const live = status === "live";
    const labels = { live: "Misure aggiornate", waiting: "In attesa", stale: "Campione scaduto", unavailable: "Non disponibile", idle: "Encoder fermo", offline: "Servizio non connesso" };
    const tag = byId("stream-telemetry-status");
    tag.textContent = labels[status] || labels.unavailable;
    tag.className = `status-tag${live ? " good" : ["stale", "offline"].includes(status) ? " warning" : ""}`;
    const readings = {
      "measured-video-bitrate": telemetry.video_bitrate_kbps,
      "measured-output-bitrate": telemetry.output_bitrate_kbps,
      "measured-video-fps": telemetry.video_fps,
      "measured-video-frames": telemetry.video_frames,
      "measured-video-queue": telemetry.video_queue_frames,
      "measured-output-errors": telemetry.output_errors
    };
    for (const [id, value] of Object.entries(readings)) text(id, live && isFiniteNumber(value) ? numberFormat.format(value) : "—");
    byId("measured-output-errors").classList.toggle("has-errors", live && telemetry.output_errors > 0);
    let detail = "Le misure saranno disponibili durante la codifica.";
    if (status === "live") {
      const pendingRates = [telemetry.video_bitrate_kbps, telemetry.output_bitrate_kbps, telemetry.video_fps].some((value) => !isFiniteNumber(value));
      detail = pendingRates ? "Primo campione ricevuto: attendo il successivo per calcolare bitrate e fps." : `Misure sull’ultimo intervallo${isFiniteNumber(telemetry.interval_seconds) ? ` di ${numberFormat.format(telemetry.interval_seconds)} s` : ""}.`;
    } else if (status === "waiting") detail = "L’encoder sta preparando i primi campioni.";
    else if (status === "stale") detail = `Nessun campione recente${isFiniteNumber(telemetry.age_seconds) ? `: l’ultimo risale a ${numberFormat.format(telemetry.age_seconds)} s fa` : ""}. Misure sospese.`;
    else if (status === "unavailable" || !Object.hasOwn(labels, status)) detail = "La telemetria del flusso non è disponibile per questo processo.";
    else if (status === "offline") detail = "Misure sospese fino alla riconnessione al servizio.";
    text("stream-telemetry-detail", detail);
  }

  function renderChart() {
    const count = cpuSamples.length;
    byId("chart-empty").hidden = count > 1;
    text("chart-empty", count === 1 ? "Raccolta dei primi campioni CPU…" : "Il grafico si popola con il processo attivo");
    if (count < 2) {
      byId("cpu-line").setAttribute("d", "");
      byId("cpu-area").setAttribute("d", "");
      text("chart-title", "CPU encoder: campioni insufficienti per tracciare il grafico");
      text("chart-range", count ? "CPU · 1 campione" : "CPU · nessun campione");
      text("chart-scale", "Solo misure reali");
      return;
    }
    const max = Math.max(100, Math.ceil(Math.max(...cpuSamples.map((point) => point.value)) / 100) * 100);
    const elapsed = cpuSamples[count - 1].time - cpuSamples[0].time;
    const points = cpuSamples.map((point) => ({ x: 600 * (point.time - cpuSamples[0].time) / Math.max(elapsed, 1), y: 80 - 68 * Math.max(0, point.value) / max }));
    const line = points.map((point, index) => `${index ? "L" : "M"}${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(" ");
    byId("cpu-line").setAttribute("d", line);
    byId("cpu-area").setAttribute("d", `${line} L600,80 L0,80 Z`);
    text("chart-range", `CPU · ${count} campioni · ${Math.round(elapsed / 1000)} s`);
    text("chart-scale", `Scala 0–${max}%`);
    text("chart-title", `CPU encoder: ${count} campioni reali, ultimo ${numberFormat.format(cpuSamples[count - 1].value)}%, scala da 0 a ${max}%`);
  }

  function eventLevel(level) {
    if (["warn", "warning"].includes(level)) return "warning";
    if (["error", "critical", "fatal"].includes(level)) return "error";
    return "info";
  }

  function buildEvent(event, full) {
    const level = eventLevel(event.level);
    const item = document.createElement("li");
    item.className = `event-item ${level}`;
    const time = document.createElement("time");
    time.className = "event-time";
    const date = new Date(event.time);
    if (Number.isFinite(date.getTime())) {
      time.dateTime = date.toISOString();
      time.textContent = (full ? timeFormat : shortTimeFormat).format(date);
      time.title = date.toLocaleString("it-IT");
      if (full) {
        const day = document.createElement("span");
        day.className = "event-date";
        day.textContent = dateFormat.format(date);
        time.append(day);
      }
    } else time.textContent = "—";
    const content = document.createElement("div");
    content.className = "event-content";
    const tag = document.createElement("span");
    tag.className = "event-level";
    const dot = document.createElement("span");
    dot.className = `status-dot ${level === "error" ? "bad" : level === "info" ? "neutral" : ""}`;
    dot.setAttribute("aria-hidden", "true");
    tag.append(dot, document.createTextNode({ info: "Informazione", warning: "Avviso", error: "Errore" }[level]));
    const message = document.createElement("p");
    message.className = "event-message";
    message.textContent = event.message || "Evento senza descrizione";
    content.append(tag, message);
    item.append(time, content);
    return item;
  }

  function emptyEvent(message) {
    const item = document.createElement("li");
    item.className = "empty-events";
    item.textContent = message;
    return item;
  }

  function renderEvents(force = false) {
    const events = state?.events || [];
    const filter = byId("event-filter").value;
    const signature = JSON.stringify([filter, events]);
    if (!force && signature === eventSignature) return;
    eventSignature = signature;
    const newest = [...events].reverse();
    const recent = byId("recent-events");
    recent.replaceChildren(...newest.slice(0, 3).map((event) => buildEvent(event, false)));
    if (!newest.length) recent.append(emptyEvent("Nessuna attività registrata."));
    const filtered = newest.filter((event) => filter === "all" || eventLevel(event.level) === filter);
    const all = byId("all-events");
    all.replaceChildren(...filtered.map((event) => buildEvent(event, true)));
    if (!filtered.length) all.append(emptyEvent(filter === "all" ? "Nessuna attività registrata." : "Nessun evento per questo filtro."));
    text("event-count", String(events.length));
    text("event-summary", `${filtered.length} ${filtered.length === 1 ? "evento" : "eventi"}${filter !== "all" ? ` su ${events.length}` : ""}`);
  }

  function applyState(next, options = {}) {
    if (!next || typeof next !== "object" || !next.config || !Number.isInteger(next.revision)) throw new Error("Il servizio ha restituito uno stato non valido.");
    if (["probing", "starting"].includes(state?.phase) && ["idle", "running", "reconnecting", "error"].includes(next.phase)) setNotice("");
    state = next;
    connected = true;
    byId("api-error").hidden = true;
    text("api-status", "Servizio connesso");
    byId("api-dot").className = "status-dot good";
    text("panel-version", next.version?.panel ? `v${next.version.panel}` : "—");
    if (options.saved || !baseConfig || (!dirty && draftRevision !== next.revision && !form.contains(document.activeElement))) populateForm(next.config, next.revision);
    renderProfiles(next.profiles);
    text("session-name", next.config.name || "Profilo encoder");
    text("phase-tag", phaseLabels[next.phase] || "Stato sconosciuto");
    const warning = ["probing", "starting", "reconnecting", "stopping"].includes(next.phase);
    const tone = next.phase === "running" ? "good" : next.phase === "error" ? "bad" : warning ? "warning" : "";
    byId("phase-tag").className = `status-tag ${tone}`;
    document.querySelector(".session-panel").dataset.phase = next.phase;
    byId("encoder-indicator").className = `encoder-indicator ${tone === "good" ? "active" : tone}`;
    text("session-heading", phaseTitles[next.phase] || "Stato dell’encoder");
    text("session-message", next.message || "Nessun dettaglio disponibile");
    text("session-pid", `PID ${next.pid ?? "—"}`);
    text("session-detail", next.encoding_started ? "Codifica confermata dal processo" : next.process_alive ? "Processo avviato · codifica non ancora confermata" : "Nessun processo di codifica attivo");
    renderSignal(next.config);
    renderMetrics(options.sample !== false);
    renderTelemetry();
    renderPreview();
    renderEvents();
    text("last-update", `Aggiornato alle ${timeFormat.format(new Date())}`);
    updateControls();
    if (!activeProcess() && byId("stop-dialog").open) byId("stop-dialog").close();
  }

  function markDisconnected(error) {
    connected = false;
    renderPreview();
    const banner = byId("api-error");
    banner.textContent = `Connessione al servizio interrotta. ${error.message} Nuovo tentativo automatico tra 2 secondi.`;
    banner.hidden = false;
    text("api-status", "Servizio non connesso");
    byId("api-dot").className = "status-dot bad";
    text("phase-tag", "Stato non disponibile");
    byId("phase-tag").className = "status-tag warning";
    text("session-heading", "Connessione interrotta");
    text("session-message", "Impossibile verificare lo stato corrente. Le azioni saranno disponibili alla riconnessione.");
    text("session-detail", "Ultimo stato ricevuto non aggiornato");
    text("signal-state", "Stato non disponibile");
    byId("signal-dot").className = "status-dot neutral";
    byId("sampling-dot").className = "status-dot neutral";
    byId("encoder-indicator").className = "encoder-indicator warning";
    text("cpu-value", "—");
    text("memory-value", "—");
    text("uptime-value", "—");
    text("uptime-hint", "Misure non disponibili");
    renderTelemetry();
    cpuSamples = [];
    renderChart();
    text("chart-empty", "Grafico sospeso: servizio non connesso");
    updateControls();
  }

  async function api(path, method = "GET", body) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 15000);
    try {
      const endpoint = path === "channels" ? "/api/channels" : `/api/channels/${encodeURIComponent(selectedChannel)}/${path}`;
      const response = await fetch(endpoint, {
        method,
        headers: body === undefined ? { Accept: "application/json" } : { Accept: "application/json", "Content-Type": "application/json" },
        credentials: "same-origin",
        cache: "no-store",
        signal: controller.signal,
        ...(body === undefined ? {} : { body: JSON.stringify(body) })
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        const error = new Error(data?.error?.message || `Richiesta non riuscita (HTTP ${response.status}).`);
        error.fields = data?.error?.fields;
        error.status = response.status;
        throw error;
      }
      if (!data) throw new Error("Risposta del servizio non leggibile.");
      return data;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("Il servizio non ha risposto entro 15 secondi.");
      if (error instanceof TypeError) throw new Error("Verifica che il servizio del pannello sia attivo.");
      throw error;
    } finally { window.clearTimeout(timeout); }
  }

  async function poll() {
    const epoch = mutationEpoch;
    try {
      if (!pending) {
        const [next, channels] = await Promise.all([api("state"), api("channels")]);
        if (epoch === mutationEpoch && !pending) { renderChannels(channels); applyState(next); }
      }
    } catch (error) {
      if (epoch === mutationEpoch && !pending) {
        if (error.status === 404 && selectedChannel !== "main") {
          await selectChannel("main");
          setNotice("Il canale richiesto non è disponibile. Aperto il canale principale.", "warning");
        } else markDisconnected(error);
      }
    } finally { window.setTimeout(poll, 2000); }
  }

  function renderChannels(data) {
    const signature = JSON.stringify(data);
    if (signature === channelSignature) return;
    channelSignature = signature;
    const select = byId("channel-select");
    select.replaceChildren(...data.channels.map((channel) => {
      const option = document.createElement("option");
      option.value = channel.id;
      option.textContent = `${channel.name} · ${phaseLabels[channel.phase] || channel.phase}`;
      return option;
    }));
    select.value = selectedChannel;
    text("channel-summary", `${data.channels.filter(channel => channel.process_alive).length} attivi · ${data.channels.length}/${data.limit} canali`);
  }

  async function selectChannel(id) {
    if (dirty || pending) { byId("channel-select").value = selectedChannel; setNotice("Salva o ripristina la bozza prima di cambiare canale.", "warning"); return; }
    selectedChannel = id;
    mutationEpoch += 1;
    pending = "channel";
    state = null; baseConfig = null; draftRevision = null; cpuSamples = []; chartPid = null;
    renderPreview();
    byId("srt-passphrase").value = "";
    setNotice(""); updateControls();
    const url = new URL(location.href);
    url.searchParams.set("channel", id);
    history.replaceState(null, "", url);
    try { const [next, channels] = await Promise.all([api("state"), api("channels")]); channelSignature = ""; renderChannels(channels); applyState(next); }
    catch (error) { markDisconnected(error); }
    finally { pending = null; updateControls(); }
  }

  byId("channel-select").addEventListener("change", event => void selectChannel(event.target.value));
  byId("new-channel").addEventListener("click", () => byId("channel-dialog").showModal());
  byId("cancel-channel").addEventListener("click", () => byId("channel-dialog").close());
  byId("channel-form").addEventListener("submit", async event => {
    event.preventDefault();
    if (pending) return;
    pending = "create-channel"; mutationEpoch += 1; updateControls();
    let created;
    try { created = await api("channels", "POST", { name: byId("channel-name").value.trim() }); byId("channel-dialog").close(); byId("channel-form").reset(); }
    catch (error) { byId("channel-dialog").close(); setNotice(error.message, "error"); }
    finally { pending = null; updateControls(); }
    if (created) await selectChannel(created.id);
  });

  async function runAction(action, payload) {
    if (pending || !connected) return;
    pending = action;
    mutationEpoch += 1;
    setNotice("");
    clearFieldErrors();
    updateControls();
    try {
      const next = await api(action, action === "config" ? "PUT" : "POST", payload);
      applyState(next, { saved: action === "config", sample: false });
      const confirmations = { config: "Configurazione salvata. Ora puoi verificare l’ingresso o avviare l’encoder.", probe: "Verifica dell’ingresso avviata. L’esito comparirà nello stato e nel registro eventi.", start: "Richiesta di avvio accettata. Attendi la conferma del processo.", stop: next.process_alive ? "Richiesta di arresto accettata." : "Encoder arrestato.", preview: "Preview avviata: in attesa del primo fotogramma.", "preview-stop": "Preview spenta." };
      setNotice(confirmations[action]);
    } catch (error) {
      setNotice(error.message, "error");
      markFieldErrors(error.fields);
      if (error.status === 409) {
        try { applyState(await api("state"), { sample: false }); } catch (refreshError) { markDisconnected(refreshError); }
      } else if (!error.status) markDisconnected(error);
    } finally {
      if (payload && Object.hasOwn(payload, "passphrase")) payload.passphrase = "";
      if (action === "probe" || action === "start") byId("srt-passphrase").value = "";
      pending = null;
      updateControls();
    }
  }

  function prepareAction(action) {
    if (!state || !connected || pending) return;
    if (action === "stop") { byId("stop-dialog").showModal(); return; }
    if (dirty || !canConfigure() || conflict()) return;
    const payload = { revision: state.revision };
    const passphraseInput = byId("srt-passphrase");
    if (/^srt:\/\//i.test(state.config.input_url) && passphraseInput.value) {
      const byteLength = new TextEncoder().encode(passphraseInput.value).length;
      if (byteLength < 10 || byteLength > 79) {
        setNotice("La passphrase SRT deve contenere da 10 a 79 byte.", "error");
        window.location.hash = "configuration";
        passphraseInput.focus();
        return;
      }
      payload.passphrase = passphraseInput.value;
    }
    passphraseInput.value = "";
    void runAction(action, payload);
  }

  form.addEventListener("input", (event) => {
    if (!fields.includes(event.target.name)) return;
    clearFieldErrors();
    updateDraft();
  });
  form.addEventListener("change", (event) => {
    if (fields.includes(event.target.name)) { clearFieldErrors(); updateDraft(); }
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!connected || pending || !canConfigure() || !dirty || conflict()) return;
    clearFieldErrors();
    const config = readDraft();
    const mux = form.elements.namedItem("mux_bitrate_kbps");
    if (config.mux_bitrate_kbps < config.video_bitrate_kbps + config.audio_bitrate_kbps + 128) mux.setCustomValidity("Il multiplex deve essere almeno pari al bitrate video + audio + 128 kb/s.");
    if (!form.reportValidity()) return;
    void runAction("config", { revision: draftRevision, config });
  });
  byId("profile-select").addEventListener("change", (event) => {
    const profile = state?.profiles?.find((entry) => entry.id === event.target.value);
    if (!profile?.config) return;
    for (const name of fields) {
      if (Object.hasOwn(profile.config, name)) form.elements.namedItem(name).value = profile.config[name];
    }
    clearFieldErrors();
    updateDraft();
    setNotice(`Profilo “${profile.label}” caricato nella bozza. Salva per applicarlo.`);
  });
  byId("discard-button").addEventListener("click", () => {
    if (!state || pending) return;
    populateForm(state.config, state.revision);
    byId("srt-passphrase").value = "";
    updateControls();
    setNotice("Ripristinata la configurazione salvata.");
  });
  document.querySelectorAll("[data-action]").forEach((button) => button.addEventListener("click", () => prepareAction(button.dataset.action)));
  byId("cancel-stop").addEventListener("click", () => byId("stop-dialog").close());
  byId("confirm-stop").addEventListener("click", () => {
    byId("stop-dialog").close();
    if (activeProcess()) void runAction("stop", {});
  });
  byId("event-filter").addEventListener("change", () => renderEvents(true));
  let previewImageUrl = null;
  function renderPreview() {
    const slot = byId("preview-image");
    const preview = connected ? state?.preview : null;
    const status = preview?.status || "idle";
    const labels = { idle: "Preview spenta", waiting: "In attesa dell’immagine…", stale: "Immagine non aggiornata", live: "Preview aggiornata", error: "Preview interrotta: verifica l’uscita e che la porta locale sia libera, poi riattivala." };
    text("preview-status", connected ? labels[status] || labels.idle : "Preview non disponibile: servizio non connesso");
    if (status === "live") {
      const source = `/api/channels/${encodeURIComponent(selectedChannel)}/preview-frame?frame=${preview.frame_id}`;
      if (previewImageUrl !== source) {
        previewImageUrl = source;
        const image = new Image();
        image.alt = "Anteprima dell’uscita del canale selezionato";
        image.onload = () => { if (previewImageUrl === source && connected) slot.replaceChildren(image); };
        image.onerror = () => {
          if (previewImageUrl !== source) return;
          slot.replaceChildren(); text("preview-status", "Nessuna immagine aggiornata disponibile");
        };
        image.src = source;
      }
    } else { previewImageUrl = null; slot.replaceChildren(); }
  }
  byId("preview-toggle").addEventListener("click", () => void runAction(state?.preview?.pid ? "preview-stop" : "preview", {}));
  window.addEventListener("hashchange", setView);
  window.addEventListener("beforeunload", (event) => {
    byId("srt-passphrase").value = "";
    if (dirty) { event.preventDefault(); event.returnValue = ""; }
  });
  window.addEventListener("pagehide", () => { byId("srt-passphrase").value = ""; });
  setView();
  updateControls();
  void poll();
})();
