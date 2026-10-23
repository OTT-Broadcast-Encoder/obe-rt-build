"use strict";

(() => {
  const byId = (id) => document.getElementById(id);
  const form = byId("config-form");
  const inputUrl = globalThis.obeInputUrl;
  const inputUrlControlIds = ["config-input-protocol", "config-input-host", "config-input-port", "config-input-path", "config-input-local-port", "config-input-srt-mode", "config-input-srt-latency", "config-input-srt-rcvbuf", "config-input-srt-ffs", "config-input-options"];
  const fields = ["name", "input_url", "output_url", "video_bitrate_kbps", "mux_bitrate_kbps", "vbv_buffer_kbits", "preset", "gop_frames", "bframes", "lookahead_frames", "threads", "audio_bitrate_kbps"];
  const broadcastDefaults = {"video_profile": "auto", "video_level": "auto", "video_tune": "none", "vbv_maxrate_kbps": 0, "reference_frames": 0, "b_pyramid": "auto", "b_adapt": "auto", "scenecut_threshold": null, "aq_mode": "auto", "aq_strength": null, "field_order": "source", "aspect_ratio": "source", "audio_codec": "mp2", "aac_profile": "aac-lc", "aac_encapsulation": "adts", "mp2_mode": "auto", "audio_gain_db": 0, "audio_offset_ms": 0, "audio_language": "", "audio_type": "undefined", "ts_id": 0, "program_number": 0, "pmt_pid": 0, "video_pid": 0, "audio_pid": 0, "pcr_pid": 0, "pcr_period_ms": 0, "pat_period_ms": 0, "service_name": "", "provider_name": "", "input_type": "url", "input_card_index": 0, "input_video_format": "pal", "input_video_connection": "sdi", "input_audio_connection": "embedded", "ndi_source": ""};
  Object.assign(broadcastDefaults, { auto_restart: false, resume_on_restart: false, restart_attempts: 3, restart_delay_seconds: 2 });
  const audioProcessingDefaults = { audio_normalize: false, audio_loudness_lufs: -23, audio_true_peak_dbtp: -1, audio_loudness_range_lu: 7 };
  Object.assign(broadcastDefaults, audioProcessingDefaults);
  const x264Defaults = {
    x264_speedcontrol: "adaptive", qcomp: null, ipratio: null, pbratio: null,
    qp_min: null, qp_max: null, qp_step: null, chroma_qp_offset: null,
    min_gop_frames: null, b_bias: null, open_gop: "auto", mbtree: "auto",
    deblock: "auto", deblock_alpha: null, deblock_beta: null,
    psy: "auto", psy_rd: null, psy_trellis: null, direct_pred: "preset",
    weighted_bipred: "auto", weighted_pred: "auto", fast_pskip: "auto",
    transform_8x8: "auto", entropy_coding: "auto", motion_estimation: "auto",
    motion_range: null, subme: null, trellis: "auto", mixed_refs: "auto", partitions: "auto",
  };
  const x264PresetOrder = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"];
  Object.assign(broadcastDefaults, x264Defaults);
  Object.assign(broadcastDefaults, { vapoursynth_enabled: false, vapoursynth_script: "import vapoursynth as vs\n\nclip.set_output()\n" });
  const booleanFields = new Set(["auto_restart", "resume_on_restart", "vapoursynth_enabled", "audio_normalize"]);
  fields.push(...Object.keys(broadcastDefaults));
  const numericFields = new Set(["video_bitrate_kbps", "mux_bitrate_kbps", "vbv_buffer_kbits", "gop_frames", "bframes", "lookahead_frames", "threads", "audio_bitrate_kbps"]);
  const nullableNumericFields = new Set(["scenecut_threshold", "aq_strength", ...Object.keys(x264Defaults).filter(name => x264Defaults[name] === null)]);
  const qualityFields = ["b_adapt", "scenecut_threshold", "aq_mode", "aq_strength"];
  for (const [name, value] of Object.entries(broadcastDefaults)) if (typeof value === "number") numericFields.add(name);
  const credentialFields = { passphrase: "srt-passphrase", username: "source-username", password: "source-password", query: "source-query" };
  const phaseLabels = { idle: "Fermo", probing: "Verifica ingresso", starting: "Avvio in corso", running: "In codifica", reconnecting: "Riconnessione", restart_wait: "Attesa riavvio", stopping: "Arresto in corso", error: "Errore" };
  const phaseTitles = { idle: "Encoder fermo", probing: "Verifica della sorgente", starting: "Avvio dell’encoder", running: "Codifica in corso", reconnecting: "Sorgente in riconnessione", restart_wait: "Recupero del processo", stopping: "Arresto dell’encoder", error: "Intervento richiesto" };
  const viewTitles = { overview: "Controllo", configuration: "Impostazioni", monitoring: "Diagnostica" };
  const configTabNames = ["connections", "video", "filters", "audio", "ts", "outputs", "management"];
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
  let inputCheck = null;
  let scriptCheck = null;
  let scriptTestPending = false;
  let mutationEpoch = 0;
  let eventSignature = "";
  let exitSignature = "";
  let cpuSamples = [];
  let chartPid = null;
  let lastSampleAt = 0;
  let historyLoading = false;
  let historyLoadedAt = 0;
  let historyEpoch = 0;
  let alarmSignature = "";
  let ruleSignature = "";
  let selectedChannel = new URLSearchParams(location.search).get("channel") || "main";
  let channelSignature = "";
  let deleteSnapshot = null;
  let renameSnapshot = null;
  let applyingRevision = null;
  let authRequired = false;
  let authBlocked = false;
  let sessionKnown = false;
  let draftGeneration = 0;
  let restoreSnapshot = null;
  let passphraseGeneration = 0;
  let passphraseRequestKey = null;
  let passphraseRequestOrigin = null;
  let savedPassphrase = null;
  let passphraseDisplayOrigin = null;
  let passphraseLoadError = "";
  let passphraseTouched = false;
  const savedOutputKeys = new WeakMap();
  const outputKeyLoads = new WeakSet();
  const outputKeyFailures = new WeakSet();
  const outputKeyTouched = new WeakSet();
  let outputKeyGeneration = 0;
  let loggingOut = false;
  let selectedConfigTab = "connections";
  let configTabsReady = false;

  const text = (id, value) => { byId(id).textContent = value; };
  const isFiniteNumber = (value) => typeof value === "number" && Number.isFinite(value);
  const activeProcess = () => state && (state.applying || state.cleanup_pending || state.process_alive || state.recovery?.active || ["probing", "starting", "running", "reconnecting", "restart_wait", "stopping"].includes(state.phase));
  const canConfigure = () => state && !activeProcess() && ["idle", "error"].includes(state.phase);
  const canApply = () => state?.recovery?.active && !state.applying && ["probing", "starting", "running", "reconnecting", "restart_wait"].includes(state.phase);
  const conflict = () => Boolean(state && draftRevision !== null && state.revision !== draftRevision);
  const canDeleteChannel = () => selectedChannel !== "main" && connected && !pending && !dirty && !conflict() && canConfigure() && typeof state.instance_id === "string" && Boolean(state.instance_id);
  const deleteSnapshotMatches = () => deleteSnapshot && deleteSnapshot.id === selectedChannel && deleteSnapshot.revision === state?.revision && deleteSnapshot.instance_id === state?.instance_id;
  const canRenameChannel = () => connected && !pending && !dirty && !conflict() && state && !state.applying && typeof state.instance_id === "string" && Boolean(state.instance_id);
  const renameSnapshotMatches = () => renameSnapshot && renameSnapshot.id === selectedChannel && renameSnapshot.revision === state?.revision && renameSnapshot.instance_id === state?.instance_id;

  function setNotice(message, kind = "info") {
    const notice = byId("action-notice");
    notice.textContent = message;
    notice.className = `notice${kind === "error" ? " notice-error" : kind === "warning" ? " notice-warning" : ""}`;
    notice.hidden = !message;
    notice.setAttribute("role", kind === "error" ? "alert" : "status");
  }

  function activateConfigTab(name, replaceHash = false) {
    if (!configTabsReady) return;
    if (!configTabNames.includes(name)) name = "connections";
    selectedConfigTab = name;
    for (const tab of byId("config-tabs").querySelectorAll("[data-config-tab]")) {
      const active = tab.dataset.configTab === name;
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
      byId(`config-panel-${tab.dataset.configTab}`).hidden = !active;
    }
    if (replaceHash && location.hash.startsWith("#configuration")) {
      history.replaceState(null, "", `#configuration/${name}`);
    }
  }

  function setupConfigTabs() {
    const tabbar = byId("config-tabs");
    if (!tabbar) return;
    const groups = {
      connections: [byId("profile-heading")?.closest(".form-section")],
      video: [byId("video-heading")?.closest(".form-section")],
      filters: [byId("vapoursynth-editor")],
      audio: [byId("audio-advanced")?.closest(".form-section")],
      ts: [byId("mux-advanced")?.closest(".form-section")],
      outputs: [byId("outputs-heading")?.closest(".form-section"), byId("delivery-settings")],
      management: [byId("profile-tools"), byId("recovery-options")]
    };
    if (Object.values(groups).flat().some(element => !element)) {
      tabbar.hidden = true;
      return;
    }
    const host = document.querySelector(".config-fields");
    const audioBasics = document.createElement("div");
    audioBasics.className = "field-grid audio-basics";
    audioBasics.append(byId("config-audio-bitrate").closest(".field"));
    groups.audio[0].prepend(audioBasics);
    const muxBasics = document.createElement("div");
    muxBasics.className = "field-grid mux-basics";
    muxBasics.append(byId("config-mux-bitrate").closest(".field"));
    groups.ts[0].prepend(muxBasics);
    const primaryOutput = byId("config-output-url").closest(".field");
    const primaryGrid = document.createElement("div");
    primaryGrid.className = "field-grid output-primary";
    primaryGrid.append(primaryOutput);
    groups.outputs[0].querySelector(".form-section-heading").after(primaryGrid);
    for (const name of configTabNames) {
      const panel = document.createElement("div");
      panel.id = `config-panel-${name}`;
      panel.className = "config-tab-panel";
      panel.dataset.configPanel = name;
      panel.setAttribute("role", "tabpanel");
      panel.setAttribute("aria-labelledby", `config-tab-${name}`);
      panel.tabIndex = 0;
      host.append(panel);
      panel.append(...groups[name]);
    }
    const tabs = [...tabbar.querySelectorAll("[data-config-tab]")];
    tabs.forEach((tab, index) => {
      tab.addEventListener("click", () => {
        const hash = `#configuration/${tab.dataset.configTab}`;
        if (location.hash === hash) activateConfigTab(tab.dataset.configTab);
        else location.hash = hash;
      });
      tab.addEventListener("keydown", (event) => {
        const next = event.key === "ArrowRight" ? (index + 1) % tabs.length
          : event.key === "ArrowLeft" ? (index + tabs.length - 1) % tabs.length
          : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : null;
        if (next === null) return;
        event.preventDefault();
        tabs[next].focus();
        tabs[next].click();
      });
    });
    configTabsReady = true;
    activateConfigTab(selectedConfigTab);
  }

  setupConfigTabs();

  document.addEventListener("click", event => {
    if (event.target.closest(".encoding-help")) return;
    for (const help of document.querySelectorAll(".encoding-help[open]")) help.open = false;
  });
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    for (const help of document.querySelectorAll(".encoding-help[open]")) help.open = false;
  });

  function setView() {
    const requested = window.location.hash.slice(1);
    if (requested === "main-content") {
      byId("main-content").focus();
      return;
    }
    const [route, configTab] = requested.split("/", 2);
    const view = route === "events" ? "monitoring" : Object.hasOwn(viewTitles, route) ? route : "overview";
    for (const name of Object.keys(viewTitles)) byId(`view-${name}`).hidden = name !== view;
    if (view === "configuration") activateConfigTab(configTabNames.includes(configTab) ? configTab : selectedConfigTab);
    document.querySelectorAll("[data-view]").forEach((link) => {
      if (link.dataset.view === view) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
    text("breadcrumb-current", viewTitles[view]);
    document.title = `OBE · ${viewTitles[view]}`;
    const pane = route === "events" ? "events" : configTab === "details" ? "details" : "live";
    for (const name of ["live", "events", "details"]) byId(`diagnosis-${name}`).hidden = name !== pane;
    document.querySelectorAll("[data-diagnosis-pane]").forEach(link => {
      if (link.dataset.diagnosisPane === pane) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
    if (["overview", "monitoring"].includes(view)) loadHistory(true);
  }

  function revealField(element, selectTab = true) {
    const panel = element.closest("[data-config-panel]");
    if (panel && selectTab) activateConfigTab(panel.dataset.configPanel, true);
  }

  function readDraft() {
    const config = {};
    for (const name of fields) {
      const field = form.elements.namedItem(name);
      const value = field.value;
      if (booleanFields.has(name)) config[name] = field.type === "checkbox" ? field.checked : value === "true";
      else if (name === "vapoursynth_script") config[name] = value;
      else if (nullableNumericFields.has(name)) config[name] = value === "" ? null : Number(value);
      else if (numericFields.has(name)) config[name] = Number(value);
      else config[name] = value.trim();
    }
    config.outputs = readOutputs();
    if (state?.capabilities?.audio_tracks) {
      const tracks = readAudioTracks();
      config.audio_tracks = baseConfig?.audio_tracks == null && JSON.stringify(tracks) === JSON.stringify(audioTracksFromConfig(config)) ? null : tracks;
    }
    if (!state?.capabilities?.audio_processing) for (const name of Object.keys(audioProcessingDefaults)) delete config[name];
    return config;
  }

  function renderInputUrlFields(value) {
    const parsed = inputUrl.parse(value);
    if (!parsed) return;
    for (const [id, field] of Object.entries({
      "config-input-protocol": "protocol", "config-input-host": "host", "config-input-port": "port",
      "config-input-path": "path", "config-input-local-port": "localPort",
      "config-input-srt-mode": "mode", "config-input-srt-latency": "latencyMs", "config-input-options": "options",
      "config-input-srt-rcvbuf": "rcvbuf", "config-input-srt-ffs": "ffs"
    })) byId(id).value = parsed[field];
  }

  function syncInputUrl() {
    const values = {};
    for (const [field, id] of Object.entries({ protocol: "config-input-protocol", host: "config-input-host",
      port: "config-input-port", path: "config-input-path", options: "config-input-options",
      mode: "config-input-srt-mode", latencyMs: "config-input-srt-latency", localPort: "config-input-local-port",
      rcvbuf: "config-input-srt-rcvbuf", ffs: "config-input-srt-ffs" })) values[field] = byId(id).value;
    if (inputUrl.reservedOptions(values.protocol, values.options).length) return;
    byId("config-input-url").value = inputUrl.build(values);
  }

  function updateInputOptionsValidity() {
    const field = byId("config-input-options");
    const reserved = form.elements.namedItem("input_type").value === "url"
      ? inputUrl.reservedOptions(byId("config-input-protocol").value, field.value) : [];
    const message = reserved.length ? `Sposta ${reserved.join(", ")} nei campi dedicati.` : "";
    field.setCustomValidity(message);
    text("input-options-help", message || "Parametri aggiuntivi senza «?». Usa i campi dedicati per password e chiavi.");
  }

  function updateInputUrlControls(kind, blocked) {
    const protocol = byId("config-input-protocol").value;
    const url = kind === "url";
    const srt = protocol === "srt", local = protocol === "udp" || protocol === "rtp";
    const path = ["http", "https", "rtsp", "rtmp"].includes(protocol);
    byId("input-path-field").hidden = !path;
    byId("input-local-port-field").hidden = !local;
    byId("input-srt-mode-field").hidden = !srt;
    byId("input-srt-latency-field").hidden = !srt;
    byId("input-srt-rcvbuf-field").hidden = !srt;
    byId("input-srt-ffs-field").hidden = !srt;
    byId("config-input-host").required = url;
    byId("config-input-port").required = url && ["udp", "rtp", "srt", "tcp"].includes(protocol);
    byId("config-input-path").required = url && protocol === "rtmp";
    byId("config-input-url").required = url;
    for (const id of inputUrlControlIds) byId(id).disabled = blocked || !url;
    for (const key of ["rcvbuf", "ffs"]) byId(`config-input-srt-${key}`).disabled = blocked || !url || !srt;
    byId("config-input-url").disabled = blocked || !url;
    updateInputOptionsValidity();
    text("input-port-help", ["udp", "rtp", "srt", "tcp"].includes(protocol) ? "Obbligatoria per questo protocollo." : "Facoltativa; vuoto usa la porta predefinita.");
    text("input-path-help", protocol === "rtmp" ? "App e nome del flusso, ad esempio /live/stream." : "Percorso sul server, se richiesto.");
    const hints = { rtp: "Verifica anche la porta RTCP adiacente.",
      http: "Credenziali in «Accesso alla sorgente».", https: "Credenziali in «Accesso alla sorgente».",
      rtsp: "Credenziali in «Accesso alla sorgente»." };
    text("input-protocol-help", hints[protocol] || "");
    byId("input-protocol-help").hidden = !hints[protocol];
  }

  const outputCards = () => [...byId("outputs-list").querySelectorAll("[data-output-id]")];
  const outputField = (card, name) => card.querySelector(`[data-output-field="${name}"]`);

  function savedOutputEndpoint(card) {
    const previous = state?.config.outputs?.find(output => output.id === card.dataset.outputId);
    const current = credentialOrigin(outputField(card, "url").value.trim());
    if (previous?.protocol !== "srt" || outputField(card, "protocol").value !== "srt"
        || previous.srt_mode !== outputField(card, "srt_mode").value
        || !current || current !== credentialOrigin(previous.url)) return null;
    return current;
  }

  function clearMovedOutputKey(card) {
    const saved = savedOutputKeys.get(card);
    if (!saved || (saved.endpoint === savedOutputEndpoint(card) && !conflict()
        && state?.output_credentials?.[card.dataset.outputId])) return;
    const field = outputField(card, "passphrase");
    if (field.value === saved.value) field.value = "";
    savedOutputKeys.delete(card);
  }

  function clearOutputKeys() {
    outputKeyGeneration += 1;
    for (const card of outputCards()) {
      outputField(card, "passphrase").value = "";
      savedOutputKeys.delete(card);
      outputKeyTouched.delete(card);
      outputKeyFailures.delete(card);
    }
  }

  const audioCards = () => [...byId("audio-tracks-list").querySelectorAll("[data-audio-track]")];
  let audioControlSequence = 0;
  const audioField = (card, name) => card.querySelector(`[data-audio-field="${name}"]`);
  const audioTrackDefaults = { source: 1, enabled: true, pid: 0, language: "", audio_type: "undefined" };
  const audioTypes = [["undefined", "Non specificato"], ["clean-effects", "Musica ed effetti"], ["hearing-impaired", "Per non udenti"], ["visual-impaired", "Audiodescrizione"]];
  const audioTracksFromConfig = config => config.audio_tracks ?? [{ ...audioTrackDefaults,
    pid: config.audio_pid || 0, language: config.audio_language || "", audio_type: config.audio_type || "undefined" }];
  const enabledAudioCount = config => audioTracksFromConfig(config).filter(track => track.enabled).length;

  function readAudioTracks() {
    return audioCards().map(card => ({ source: Number(audioField(card, "source").value),
      enabled: audioField(card, "enabled").checked, pid: Number(audioField(card, "pid").value),
      language: audioField(card, "language").value.trim().toLowerCase(), audio_type: audioField(card, "audio_type").value }));
  }

  function renderAudioTracks(config) {
    byId("audio-tracks-list").replaceChildren();
    audioTracksFromConfig(config).forEach(addAudioTrack);
  }

  function addAudioTrack(values) {
    const track = { ...audioTrackDefaults, ...values };
    const card = document.createElement("section");
    card.className = "audio-track-card";
    card.dataset.audioTrack = "";
    const heading = document.createElement("div");
    heading.className = "audio-track-heading";
    const title = document.createElement("h3");
    const enabledLabel = document.createElement("label");
    enabledLabel.className = "audio-track-enabled";
    const enabled = document.createElement("input");
    enabled.type = "checkbox"; enabled.dataset.audioField = "enabled"; enabled.checked = track.enabled;
    enabledLabel.append(enabled, "Abilitata");
    const remove = document.createElement("button");
    remove.type = "button"; remove.className = "button button-quiet"; remove.textContent = "Rimuovi";
    remove.addEventListener("click", () => {
      card.remove(); clearFieldErrors(); updateDraft(); byId("add-audio-track").focus();
    });
    heading.append(title, enabledLabel, remove);
    const grid = document.createElement("div");
    grid.className = "field-grid";
    for (const [name, label, options, help] of [
      ["source", "Sorgente audio", Array.from({ length: 8 }, (_, i) => [i + 1, `Audio ${i + 1}`]), "Ordine rilevato dal probe; su SDI corrisponde alla coppia audio."],
      ["pid", "PID audio", null, "0 automatico; PID manuale da 32 a 8190."],
      ["language", "Lingua", null, "Codice ISO 639: ita, eng, fra… Vuoto: non segnalata."],
      ["audio_type", "Tipo audio", audioTypes, ""]
    ]) {
      const wrapper = document.createElement("div"); wrapper.className = "field";
      const caption = document.createElement("label"); caption.textContent = label;
      const input = document.createElement(options ? "select" : "input");
      input.dataset.audioField = name;
      input.id = `audio-track-${++audioControlSequence}-${name}`;
      caption.htmlFor = input.id;
      if (options) for (const [value, text] of options) input.add(new Option(text, value));
      else if (name === "pid") { input.type = "number"; input.min = "0"; input.max = "8190"; input.step = "1"; input.required = true; }
      else { input.type = "text"; input.maxLength = 3; input.pattern = "[a-zA-Z]{3}"; input.autocomplete = "off"; input.placeholder = "ita"; }
      input.value = track[name];
      wrapper.append(caption, input);
      if (help) { const note = document.createElement("small"); note.id = `${input.id}-help`; note.textContent = help; input.setAttribute("aria-describedby", note.id); wrapper.append(note); }
      grid.append(wrapper);
    }
    card.append(heading, grid); byId("audio-tracks-list").append(card);
  }

  function updateAudioTracks(blocked) {
    const processing = Boolean(state?.capabilities?.audio_processing);
    byId("audio-processing").hidden = !processing;
    for (const name of Object.keys(audioProcessingDefaults)) {
      form.elements.namedItem(name).disabled = blocked || !processing || (name !== "audio_normalize" && form.elements.namedItem("audio_normalize").value !== "true");
    }
    const supported = Boolean(state?.capabilities?.audio_tracks);
    byId("audio-tracks-section").hidden = !supported;
    for (const name of ["audio_pid", "audio_language", "audio_type"]) form.elements.namedItem(name).closest(".field").hidden = supported;
    byId("add-audio-track").disabled = blocked || audioCards().length >= 8;
    const sources = state?.signal?.audio_tracks || [];
    const currentSource = state && ["input_url", "input_type", "input_card_index", "ndi_source"].every(name => String(state.config[name] ?? "") === form.elements.namedItem(name).value);
    audioCards().forEach((card, i) => {
      const enabled = audioField(card, "enabled").checked;
      card.querySelector("h3").textContent = `Traccia ${i + 1}`;
      card.querySelector("button").setAttribute("aria-label", `Rimuovi traccia audio ${i + 1}`);
      audioField(card, "enabled").setAttribute("aria-label", `Abilita traccia audio ${i + 1}`);
      card.querySelectorAll("input,select,button").forEach(input => { input.disabled = blocked || (!enabled && input.dataset.audioField && input.dataset.audioField !== "enabled"); });
      for (const option of audioField(card, "source").options) {
        const source = currentSource ? sources[Number(option.value) - 1] : null;
        option.textContent = `Audio ${option.value}${source ? ` · ${source.description}` : ""}`;
      }
    });
    const count = readAudioTracks().filter(track => track.enabled).length;
    text("audio-tracks-summary", count ? `${count} ${count === 1 ? "abilitata" : "abilitate"} · ${count * Number(form.elements.namedItem("audio_bitrate_kbps").value)} kb/s totali` : "Audio disattivato");
  }

  byId("add-audio-track").addEventListener("click", () => {
    if (!state?.capabilities?.audio_tracks || pending || audioCards().length >= 8) return;
    const used = new Set(readAudioTracks().map(track => track.source));
    const source = Array.from({ length: 8 }, (_, i) => i + 1).find(value => !used.has(value)) || 1;
    addAudioTrack({ source }); updateDraft(); audioField(audioCards().at(-1), "source").focus();
  });

  function outputSrtParameters() {
    // Frozen compatibility contract for services predating parameter metadata.
    return state?.capabilities?.outputs?.srt_parameters || {
      srt_mode: { type: "string", default: "caller", choices: ["caller", "listener"] },
      latency_ms: { type: "integer", default: 200, min: 20, max: 8000 },
      srt_key_length: { type: "integer", default: 32, choices: [16, 24, 32] },
      srt_max_bandwidth_kbps: { type: "integer", default: 0, min: 320, max: 1000000, allow_zero: true },
      srt_connect_timeout_ms: { type: "integer", default: 1000, min: 100, max: 60000 },
      srt_send_timeout_ms: { type: "integer", default: 1000, min: 100, max: 60000 },
      srt_drop_late_packets: { type: "boolean", default: true }
    };
  }

  function outputSrtDefaults() {
    return Object.fromEntries(Object.entries(outputSrtParameters()).map(([key, spec]) => [key, spec.default]));
  }

  function readOutputs() {
    const supported = state?.capabilities?.outputs?.srt_options === true;
    return outputCards().map(card => {
      const output = {
        id: card.dataset.outputId, name: outputField(card, "name").value.trim(),
        enabled: outputField(card, "enabled").checked, protocol: outputField(card, "protocol").value,
        url: outputField(card, "url").value.trim()
      };
      const previous = state?.config.outputs?.find(item => item.id === output.id);
      for (const [key, spec] of Object.entries(outputSrtParameters())) {
        if (!supported && !["srt_mode", "latency_ms"].includes(key)) continue;
        const element = outputField(card, key);
        output[key] = output.protocol !== "srt" ? previous?.[key] ?? spec.default
          : spec.type === "boolean" ? element.checked : spec.type === "integer" ? Number(element.value) : element.value;
      }
      return output;
    });
  }

  function readOutputCredentials() {
    const result = {};
    for (const card of outputCards()) {
      if (outputField(card, "protocol").value !== "srt") continue;
      if (outputField(card, "remove-key").checked) result[card.dataset.outputId] = "";
      else {
        const value = outputField(card, "passphrase").value;
        const saved = savedOutputKeys.get(card);
        if (saved && saved.endpoint === savedOutputEndpoint(card)) {
          if (value !== saved.value) result[card.dataset.outputId] = value;
        } else if (value && value !== saved?.value) result[card.dataset.outputId] = value;
      }
    }
    return result;
  }

  async function loadSavedOutputKey(card) {
    const endpoint = savedOutputEndpoint(card);
    if (!endpoint || state?.capabilities?.output_passphrase_reveal !== true || !authRequired
        || authBlocked || loggingOut || !connected || pending || conflict()
        || !state.output_credentials?.[card.dataset.outputId] || outputKeyLoads.has(card)
        || outputKeyFailures.has(card) || outputKeyTouched.has(card)
        || outputField(card, "remove-key").checked
        || outputField(card, "passphrase").value) return;
    const context = { channel: selectedChannel, revision: state.revision, instanceId: state.instance_id,
      outputId: card.dataset.outputId, endpoint, generation: outputKeyGeneration };
    outputKeyLoads.add(card);
    card.querySelector("[data-output-key-status]").textContent = "Caricamento…";
    try {
      const response = await api("reveal-output-passphrase", "POST", { revision: context.revision,
        instance_id: context.instanceId, output_id: context.outputId }, context.channel);
      if (outputKeyGeneration !== context.generation || !card.isConnected || outputKeyTouched.has(card) || loggingOut || authBlocked || !authRequired
          || !connected || pending || conflict() || selectedChannel !== context.channel
          || state?.revision !== context.revision || state?.instance_id !== context.instanceId
          || card.dataset.outputId !== context.outputId || savedOutputEndpoint(card) !== context.endpoint
          || outputField(card, "passphrase").value) return;
      if (typeof response.passphrase !== "string" || !response.passphrase) throw new Error("Passphrase non disponibile.");
      savedOutputKeys.set(card, { value: response.passphrase, endpoint: context.endpoint });
      outputField(card, "passphrase").value = response.passphrase;
      response.passphrase = "";
    } catch {
      if (card.isConnected && outputKeyGeneration === context.generation
          && selectedChannel === context.channel) outputKeyFailures.add(card);
    } finally {
      outputKeyLoads.delete(card);
      if (card.isConnected) updateControls();
    }
  }

  function outputErrorField(key) {
    const audio = /^audio_tracks\.(\d+)(?:\.(\w+))?$/.exec(key);
    if (audio) return audioField(audioCards()[Number(audio[1])] || byId("audio-tracks-list"), audio[2] || "source");
    if (key === "audio_tracks") return byId("add-audio-track");
    const match = /^outputs\.(\d+)(?:\.(\w+))?$/.exec(key);
    if (match) return outputField(outputCards()[Number(match[1])] || byId("outputs-list"), match[2] || "url");
    if (key.startsWith("output_credentials.")) {
      const card = outputCards().find(entry => entry.dataset.outputId === key.slice(19));
      return card ? outputField(card, "passphrase") : null;
    }
    return key === "outputs" ? byId("add-output") : null;
  }

  function renderOutputForm(outputs) {
    byId("outputs-list").replaceChildren();
    outputs.forEach(output => addOutputCard(output));
  }

  function addOutputCard(output) {
    output = { enabled: true, ...outputSrtDefaults(), ...output };
    const parameters = outputSrtParameters();
    const range = name => `Da ${numberFormat.format(parameters[name].min)} a ${numberFormat.format(parameters[name].max)}`;
    const card = document.createElement("section");
    card.className = "output-card";
    card.dataset.outputId = output.id;
    const legend = document.createElement("h3");
    legend.className = "output-title";
    legend.textContent = output.name;
    card.append(legend);
    const grid = document.createElement("div");
    grid.className = "field-grid";
    const control = (name, label, options = {}) => {
      const spec = parameters[name];
      if (spec) options = { ...options, required: spec.type !== "boolean",
        min: spec.allow_zero ? 0 : spec.min, max: spec.max,
        ...(spec.type === "integer" ? { type: "number", step: 1 } : {}) };
      const wrapper = document.createElement("div");
      wrapper.className = "field";
      const element = document.createElement(options.choices ? "select" : "input");
      const id = `output-${output.id}-${name}`;
      element.id = id;
      element.dataset.outputField = name;
      const caption = document.createElement("label");
      caption.htmlFor = id;
      caption.textContent = label;
      if (options.choices) for (const [value, text] of options.choices) element.add(new Option(text, value));
      else element.type = options.type || "text";
      if (element.type === "checkbox") element.checked = Boolean(output[name]);
      else element.value = output[name] ?? options.value ?? "";
      if (options.required) element.required = true;
      if (options.min !== undefined) element.min = options.min;
      if (options.max !== undefined) element.max = options.max;
      if (options.step !== undefined) element.step = options.step;
      if (options.maxlength) element.maxLength = options.maxlength;
      element.autocomplete = options.type === "password" ? "new-password" : "off";
      wrapper.append(caption, element);
      if (options.help) {
        const help = document.createElement("small");
        help.id = `${id}-help`;
        help.textContent = options.help;
        element.setAttribute("aria-describedby", help.id);
        wrapper.append(help);
      }
      if (options.srt) wrapper.dataset.srtOnly = "true";
      if (options.srtOptions) wrapper.dataset.srtOptionsOnly = "true";
      grid.append(wrapper);
      return { wrapper, element };
    };
    control("name", "Nome destinazione", { required: true, maxlength: 64 });
    control("enabled", "Abilitata", { type: "checkbox" });
    control("protocol", "Protocollo", { choices: [["udp", "UDP"], ["rtp", "RTP"], ["srt", "SRT"]] });
    control("url", "Destinazione · protocollo://host:porta", { required: true, maxlength: 800 });
    const srtHeading = document.createElement("h4");
    srtHeading.className = "output-srt-heading";
    srtHeading.dataset.srtOnly = "true";
    srtHeading.textContent = "Connessione SRT";
    grid.append(srtHeading);
    control("srt_mode", "Modalità SRT", { choices: parameters.srt_mode.choices.map(value => [value, value[0].toUpperCase() + value.slice(1)]), srt: true });
    control("latency_ms", "Latenza SRT · ms", {
      help: `${range("latency_ms")}; per un collegamento WAN parti da 200–400 ms e misura gli scarti.`, srt: true });
    const secret = control("passphrase", "Passphrase SRT", { type: "text", maxlength: 79, srt: true });
    secret.element.spellcheck = false;
    const status = document.createElement("small");
    status.id = `output-${output.id}-passphrase-status`;
    status.dataset.outputKeyStatus = "true";
    secret.element.setAttribute("aria-describedby", status.id);
    secret.wrapper.append(status);
    control("remove-key", "Rimuovi passphrase salvata", { type: "checkbox", srt: true });
    control("srt_key_length", "Lunghezza chiave AES", { choices: parameters.srt_key_length.choices.map(value => [String(value), `${value} byte · AES-${value * 8}`]),
      help: "Usata quando è impostata una passphrase.", srt: true, srtOptions: true });
    control("srt_max_bandwidth_kbps", "Banda massima SRT · kb/s", {
      help: `0 = automatica. ${range("srt_max_bandwidth_kbps")} kb/s, incluse le ritrasmissioni. Con TS a 9.500, prova almeno 12.000.`, srt: true, srtOptions: true });
    control("srt_connect_timeout_ms", "Timeout connessione · ms", {
      help: `${range("srt_connect_timeout_ms")}; ${numberFormat.format(parameters.srt_connect_timeout_ms.default)} predefinito, prova 3.000 se la connessione scade.`, srt: true, srtOptions: true });
    control("srt_send_timeout_ms", "Timeout invio · ms", {
      help: `${range("srt_send_timeout_ms")}; ${numberFormat.format(parameters.srt_send_timeout_ms.default)} predefinito per un invio bloccato.`, srt: true, srtOptions: true });
    control("srt_drop_late_packets", "Scarta pacchetti tardivi", { type: "checkbox",
      help: "Evita che un collegamento in ritardo accumuli dati vecchi.", srt: true, srtOptions: true });
    card.append(grid);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "button button-quiet";
    remove.textContent = "Rimuovi dalla configurazione";
    remove.addEventListener("click", () => {
      card.remove();
      updateDraft();
      setNotice("Destinazione rimossa dalla bozza. Salva o applica per confermare la modifica.");
      byId("add-output").focus();
    });
    card.append(remove);
    byId("outputs-list").append(card);
  }

  function updateOutputProtocolFields(card, blocked, supported) {
    const srt = outputField(card, "protocol").value === "srt";
    card.querySelectorAll("[data-srt-only]").forEach(wrapper => {
      wrapper.hidden = !srt || (wrapper.dataset.srtOptionsOnly === "true" && !supported);
      wrapper.querySelectorAll("input,select").forEach(element => {
        element.disabled = blocked || wrapper.hidden;
      });
    });
  }

  function updateBroadcastControls() {
    const blocked = !state || Boolean(pending);
    const srtOptions = state?.capabilities?.outputs?.srt_options === true;
    updateAudioTracks(blocked);
    const kind = form.elements.namedItem("input_type").value;
    byId("url-input-fields").hidden = kind !== "url";
    byId("decklink-input-fields").hidden = kind !== "decklink";
    byId("ndi-input-fields").hidden = kind !== "ndi";
    updateInputUrlControls(kind, blocked);
    form.elements.namedItem("ndi_source").required = kind === "ndi";
    const capability = state?.capabilities?.inputs?.[kind];
    const inputCapability = capability?.build_supported === false ? "Ingresso non disponibile in questa build." : kind !== "url" ? capability?.message || "" : "";
    text("input-capability", inputCapability);
    byId("input-capability").hidden = !inputCapability;
    const codec = form.elements.namedItem("audio_codec").value;
    for (const name of ["aac_profile", "aac_encapsulation", "mp2_mode"]) {
      form.elements.namedItem(name).closest(".field").hidden = name === "mp2_mode" ? codec !== "mp2" : codec !== "aac";
    }
    byId("add-output").disabled = blocked || outputCards().length >= 8;
    for (const card of outputCards()) {
      clearMovedOutputKey(card);
      card.querySelectorAll("input,select,button").forEach(element => { element.disabled = blocked; });
      const protocol = outputField(card, "protocol").value;
      card.querySelector(".output-title").textContent = `${outputField(card, "name").value || "Destinazione"} · ${protocol.toUpperCase()}${outputField(card, "enabled").checked ? "" : " · disabilitata"}`;
      const srt = protocol === "srt";
      updateOutputProtocolFields(card, blocked, srtOptions);
      outputField(card, "latency_ms").required = srt;
      const sameEndpoint = savedOutputEndpoint(card);
      const saved = Boolean(sameEndpoint && state?.output_credentials?.[card.dataset.outputId]);
      const loaded = saved && savedOutputKeys.get(card)?.endpoint === sameEndpoint;
      const remove = outputField(card, "remove-key");
      remove.closest(".field").hidden = !srt || (!saved && !remove.checked) || (loaded && !remove.checked);
      const passphrase = outputField(card, "passphrase");
      passphrase.disabled = blocked || !srt || remove.checked;
      card.querySelector("[data-output-key-status]").textContent = remove.checked ? "Rimozione al salvataggio"
        : loaded ? passphrase.value === savedOutputKeys.get(card).value ? "Salvata · svuota per rimuoverla"
          : passphrase.value ? "Modifiche da salvare" : "Rimozione al salvataggio"
        : passphrase.value ? "Nuova passphrase da salvare"
        : saved ? outputKeyFailures.has(card) ? "Salvata · non caricata" : outputKeyLoads.has(card) ? "Caricamento…"
          : "Salvata · lascia vuoto per conservarla" : "Facoltativa · da 10 a 79 byte";
      if (saved) void loadSavedOutputKey(card);
    }
    text("output-capability", state?.capabilities?.outputs?.srt === false ? "Le uscite SRT richiedono il gateway SRT compilato sul server." : "Una destinazione indisponibile viene riconnessa senza interrompere le altre.");
    for (const id of ["copy-channel", "copy-profile", "export-profile", "import-profile"]) byId(id).disabled = blocked;
    const restorable = !blocked && connected && state.profile_backup && !dirty && !conflict() && (canConfigure() || canApply());
    byId("restore-profile").disabled = !restorable;
    const sameRestore = restoreSnapshot && restoreSnapshot.channel === selectedChannel && restoreSnapshot.revision === state?.revision && restoreSnapshot.instance_id === state?.instance_id;
    byId("confirm-restore").disabled = !restorable || !sameRestore;
    text("restore-status", restoreSnapshot && (!restorable || !sameRestore) ? "Il canale o la configurazione sono cambiati. Annulla e verifica prima di confermare." : "");
  }

  function renderTransport() {
    const container = byId("transport-status");
    const outputs = connected ? state?.transport?.outputs || [] : [];
    const labels = { idle: "Ferma", disabled: "Disabilitata", connecting: "Connessione", live: "In trasmissione", reconnecting: "Riconnessione", error: "Errore" };
    const items = outputs.map(output => {
      const item = document.createElement("li");
      const title = document.createElement("strong");
      const waiting = state?.transport?.independent_process && state.transport.input_status !== "live" && output.status === "live";
      title.textContent = `${output.name} · ${output.protocol.toUpperCase()} · ${waiting ? "In attesa di segnale" : labels[output.status] || output.status}`;
      const detail = document.createElement("span");
      detail.textContent = `${formatRate(output.bitrate_kbps)} · scartati ${output.dropped_packets ?? 0} · errori ${output.errors ?? 0}${output.protocol === "srt" ? ` · RTT ${isFiniteNumber(output.rtt_ms) ? numberFormat.format(output.rtt_ms) : "—"} ms · persi ${output.lost_packets ?? 0} · ritrasmessi ${output.retransmitted_packets ?? 0}${output.encrypted ? " · cifrata" : ""}` : ""}`;
      item.append(title, detail);
      return item;
    });
    if (!items.length) {
      const item = document.createElement("li");
      item.textContent = !connected ? "Misure del trasporto non aggiornate: servizio non connesso." : state?.transport?.last_error || (state?.config.outputs?.length ? "Nessuna misura delle destinazioni." : "Solo uscita UDP primaria configurata.");
      items.push(item);
    }
    container.replaceChildren(...items);
  }

  function renderSystem() {
    const system = state?.system;
    const live = connected && system?.status === "live";
    text("system-status", live ? "Misure viste dal contenitore" : "Misure non disponibili");
    const percentage = value => isFiniteNumber(value) ? `${numberFormat.format(value)}%` : "—";
    const metrics = [
      ["CPU sistema", live ? percentage(system.cpu?.usage_percent) : "—"],
      ["RAM sistema", live ? percentage(system.memory?.used_percent) : "—"],
      ["Disco utilizzato", live ? percentage(system.disk?.used_percent) : "—"],
      ["Limite CPU", isFiniteNumber(system?.limits?.cpu_quota_cores) ? `${numberFormat.format(system.limits.cpu_quota_cores)} core` : "Non rilevato"],
      ["Limite RAM", isFiniteNumber(system?.limits?.memory_limit_bytes) ? `${numberFormat.format(system.limits.memory_limit_bytes / 1073741824)} GiB` : "Non rilevato"]
    ];
    byId("system-metrics").replaceChildren(...metrics.map(([label, value]) => {
      const wrapper = document.createElement("div");
      const title = document.createElement("dt");
      const reading = document.createElement("dd");
      title.textContent = label;
      reading.textContent = value;
      wrapper.append(title, reading);
      return wrapper;
    }));
    const signal = state?.signal_details;
    text("signal-diagnostics", signal?.width ? `Sorgente: ${signal.width} × ${signal.height}${signal.scan || ""} · ${isFiniteNumber(signal.frame_rate) ? numberFormat.format(signal.frame_rate) : "—"} frame/s${signal.field_rate ? ` · ${numberFormat.format(signal.field_rate)} semiquadri/s nominali` : ""}. Ordine dei campi e colore da verificare sul segnale.` : "Formato sorgente non rilevato");
    const network = live ? system.network || [] : [];
    const nodes = network.map(item => {
      const li = document.createElement("li");
      li.textContent = `${item.interface} · ${item.state} · RX ${formatRate(item.rx_bitrate_kbps)} · TX ${formatRate(item.tx_bitrate_kbps)} · errori RX/TX ${item.rx_errors ?? "—"}/${item.tx_errors ?? "—"} · scarti RX/TX ${item.rx_dropped ?? "—"}/${item.tx_dropped ?? "—"}`;
      return li;
    });
    if (!nodes.length) { const li = document.createElement("li"); li.textContent = "Nessuna misura di rete aggiornata."; nodes.push(li); }
    byId("network-status").replaceChildren(...nodes);
  }

  function configsEqual(left, right) {
    if (!left || !right) return false;
    const outputs = left.outputs || [], previous = right.outputs || [];
    return fields.every((key) => left[key] === right[key]) && outputs.length === previous.length
      && outputs.every((output, index) => Object.keys(output).length === Object.keys(previous[index]).length
        && Object.keys(output).every(key => output[key] === previous[index][key]))
      && JSON.stringify(left.audio_tracks ?? null) === JSON.stringify(right.audio_tracks ?? null);
  }

  function onlyOutputChanges() {
    if (!state?.capabilities?.outputs?.live_update || !baseConfig || Object.keys(readCredentialDraft()).length) return false;
    const draft = readDraft();
    return fields.every(key => key === "output_url" || draft[key] === baseConfig[key]);
  }

  function populateForm(config, revision) {
    scriptCheck = null;
    config = { ...broadcastDefaults, ...config, outputs: config.outputs || [] };
    renderOutputForm(config.outputs);
    renderAudioTracks(config);
    for (const name of fields) {
      const field = form.elements.namedItem(name);
      if (booleanFields.has(name) && field.type === "checkbox") field.checked = Boolean(config[name]);
      else field.value = config[name] ?? "";
    }
    renderInputUrlFields(config.input_url);
    baseConfig = { ...config };
    if (!state?.capabilities?.audio_processing) for (const name of Object.keys(audioProcessingDefaults)) delete baseConfig[name];
    draftRevision = revision;
    dirty = false;
    clearCredentialInputs();
    clearFieldErrors();
    updateCredentials();
  }

  function credentialOrigin(value) {
    try {
      const url = new URL(value);
      const defaultPorts = { "http:": "80", "https:": "443", "rtsp:": "554" };
      return `${url.protocol}//${url.hostname.toLowerCase()}:${url.port || defaultPorts[url.protocol] || ""}`;
    } catch { return null; }
  }

  function availableCredentialKeys() {
    const input = form.elements.namedItem("input_url").value.trim();
    if (form.elements.namedItem("input_type").value !== "url") return [];
    return /^srt:\/\//i.test(input) ? ["passphrase"] : /^https?:\/\/|^rtsp:\/\//i.test(input) ? ["username", "password", "query"] : [];
  }

  function canLoadSavedSrtPassphrase() {
    const input = form.elements.namedItem("input_url").value.trim();
    return state?.capabilities?.passphrase_reveal === true && authRequired && !authBlocked && !loggingOut && connected && !pending && !conflict()
      && state?.config.input_type === "url" && state.credentials?.passphrase
      && availableCredentialKeys().includes("passphrase")
      && Boolean(credentialOrigin(input) && credentialOrigin(input) === credentialOrigin(state.config.input_url));
  }

  function clearSavedSrtPassphrase() {
    passphraseGeneration += 1;
    passphraseRequestKey = null;
    passphraseRequestOrigin = null;
    savedPassphrase = null;
    passphraseDisplayOrigin = null;
    passphraseLoadError = "";
    passphraseTouched = false;
    byId("srt-passphrase").value = "";
  }

  function clearCredentialInputs() {
    clearSavedSrtPassphrase();
    for (const id of Object.values(credentialFields)) {
      byId(id).value = "";
      if (id !== "srt-passphrase") byId(`${id}-remove`).checked = false;
    }
  }

  function readCredentialDraft() {
    const credentials = {};
    for (const key of availableCredentialKeys()) {
      const id = credentialFields[key];
      if (key === "passphrase") {
        const value = byId(id).value;
        if (savedPassphrase !== null && passphraseDisplayOrigin === credentialOrigin(form.elements.namedItem("input_url").value)) {
          if (value !== savedPassphrase) credentials[key] = value;
        } else if (value || (passphraseTouched && state?.credentials?.passphrase
            && credentialOrigin(form.elements.namedItem("input_url").value) === credentialOrigin(state.config.input_url))) credentials[key] = value;
      } else if (byId(`${id}-remove`).checked) credentials[key] = "";
      else if (byId(id).value) credentials[key] = byId(id).value;
    }
    return credentials;
  }

  async function loadSavedSrtPassphrase() {
    if (!canLoadSavedSrtPassphrase() || passphraseRequestKey || passphraseTouched || byId("srt-passphrase").value) return;
    const context = { channel: selectedChannel, revision: state.revision, instanceId: state.instance_id,
      origin: credentialOrigin(state.config.input_url) };
    const key = JSON.stringify(context);
    const generation = ++passphraseGeneration;
    passphraseRequestKey = key;
    passphraseRequestOrigin = context.origin;
    passphraseLoadError = "";
    text("srt-passphrase-status", "Caricamento…");
    try {
      const response = await api("reveal-passphrase", "POST", { revision: context.revision, instance_id: context.instanceId }, context.channel);
      if (generation !== passphraseGeneration || passphraseRequestKey !== key || passphraseTouched || !canLoadSavedSrtPassphrase()
          || selectedChannel !== context.channel || state?.revision !== context.revision || state?.instance_id !== context.instanceId
          || credentialOrigin(form.elements.namedItem("input_url").value) !== context.origin || byId("srt-passphrase").value) return;
      if (typeof response.passphrase !== "string" || !response.passphrase) throw new Error("Passphrase non disponibile.");
      savedPassphrase = response.passphrase;
      passphraseDisplayOrigin = context.origin;
      byId("srt-passphrase").value = response.passphrase;
      response.passphrase = "";
      updateCredentials();
    } catch (error) {
      if (generation === passphraseGeneration) { passphraseLoadError = "Non caricata · ricarica la pagina"; updateCredentials(); }
    } finally {
      if (generation === passphraseGeneration && savedPassphrase === null && !passphraseLoadError) {
        passphraseRequestKey = null;
        passphraseRequestOrigin = null;
      }
    }
  }

  function updateCredentials() {
    const keys = availableCredentialKeys();
    const origin = credentialOrigin(form.elements.namedItem("input_url").value.trim());
    if ((passphraseDisplayOrigin && origin !== passphraseDisplayOrigin)
        || (passphraseRequestOrigin && (origin !== passphraseRequestOrigin || !keys.includes("passphrase")))
        || (conflict() && savedPassphrase !== null && byId("srt-passphrase").value === savedPassphrase)) clearSavedSrtPassphrase();
    const sameOrigin = Boolean(origin && origin === credentialOrigin(state?.config.input_url));
    const sourceCredentials = byId("source-credentials");
    if (sourceCredentials) {
      sourceCredentials.hidden = !keys.includes("username");
      const changed = Object.keys(readCredentialDraft()).length > 0;
      const saved = sameOrigin && keys.some(key => state?.credentials?.[key]);
      text("source-credentials-status", changed ? "Modifiche da salvare" : saved ? "Salvate" : "Facoltative");
    }
    byId("srt-section").hidden = !keys.includes("passphrase");
    text("passphrase-help", sameOrigin && state?.credentials?.passphrase && savedPassphrase === null && !passphraseTouched
      ? "Passphrase salvata non visibile: il campo vuoto la conserva." : "Da 10 a 79 byte. Svuota e salva per rimuoverla.");
    byId("http-credentials-section").hidden = !keys.includes("username");
    const originChanged = Boolean(state && !sameOrigin && Object.values(state.credentials || {}).some(Boolean));
    byId("credentials-origin-warning").hidden = !originChanged;
    for (const [key, id] of Object.entries(credentialFields)) {
      const input = byId(id);
      const unavailable = !state || Boolean(pending) || !keys.includes(key);
      if (key === "passphrase") {
        input.disabled = unavailable;
        const unchanged = savedPassphrase !== null && sameOrigin && input.value === savedPassphrase;
        text(`${id}-status`, unchanged ? "Salvata" : input.value ? "Modifiche da salvare" : passphraseTouched && sameOrigin && state?.credentials?.passphrase ? "Sarà rimossa al salvataggio" : sameOrigin && state?.credentials?.passphrase ? passphraseLoadError || (authRequired ? "Caricamento…" : "Salvata · login richiesto per visualizzarla") : "Non salvata");
        continue;
      }
      const remove = byId(`${id}-remove`);
      input.disabled = unavailable || remove.checked;
      remove.disabled = unavailable;
      remove.closest("label").hidden = !remove.checked && !(sameOrigin && state?.credentials?.[key]);
      text(`${id}-status`, remove.checked ? "Rimozione al prossimo salvataggio" : input.value ? "Nuovo valore da salvare" : sameOrigin && state?.credentials?.[key] ? "Salvato · lascia vuoto per conservarlo" : "Non salvato");
    }
    if (canLoadSavedSrtPassphrase() && savedPassphrase === null && !passphraseRequestKey && !passphraseTouched && !byId("srt-passphrase").value) void loadSavedSrtPassphrase();
  }

  function clearFieldErrors() {
    form.querySelectorAll("[aria-invalid]").forEach((element) => element.removeAttribute("aria-invalid"));
    form.querySelectorAll(".field-error").forEach((element) => element.remove());
    for (const element of form.querySelectorAll("input, select, textarea")) element.setCustomValidity("");
    for (const id of Object.values(credentialFields)) byId(id).setCustomValidity("");
  }

  function markFieldErrors(errors) {
    if (!errors || typeof errors !== "object") return;
    let firstError = null;
    for (const [key, message] of Object.entries(errors)) {
      const credentialKey = key.replace(/^credentials\./, "");
      const element = fields.includes(key) ? form.elements.namedItem(key) : Object.hasOwn(credentialFields, credentialKey) ? byId(credentialFields[credentialKey]) : outputErrorField(key);
      if (!element) continue;
      revealField(element, false);
      firstError ||= element;
      element.setAttribute("aria-invalid", "true");
      const hint = document.createElement("small");
      hint.className = "field-error";
      hint.textContent = String(message);
      (element.closest(".field") || element.parentElement).append(hint);
    }
    if (firstError) {
      revealField(firstError);
      if (!firstError.disabled && firstError.getClientRects().length) firstError.focus();
      else firstError.closest("[data-config-panel]")?.focus();
    }
  }

  function updateDraft() {
    draftGeneration += 1;
    const options = byId("config-input-options").value.trim().replace(/^\?/, "");
    const parsed = inputUrl.parse(form.elements.namedItem("input_url").value);
    const optionsPending = form.elements.namedItem("input_type").value === "url" && options !== (parsed?.options ?? "");
    dirty = !configsEqual(readDraft(), baseConfig) || optionsPending || Object.keys(readCredentialDraft()).length > 0 || Object.keys(readOutputCredentials()).length > 0;
    updateControls();
  }

  function updatePresetLabels() {
    const value = name => form.elements.namedItem(name).value;
    const preset = Math.max(0, x264PresetOrder.indexOf(value("preset")));
    const tune = value("video_tune");
    const adaptive = value("x264_speedcontrol") === "adaptive";
    const gop = Number(value("gop_frames"));
    const kind = value("input_type");
    const sameSource = state?.config?.input_type === kind && (kind !== "url" || state.config.input_url === value("input_url"))
      && (kind !== "decklink" || state.config.input_video_format === value("input_video_format"))
      && (kind !== "ndi" || state.config.ndi_source === value("ndi_source"));
    const signal = sameSource ? state.signal_details : null;
    const format = kind === "decklink" ? value("input_video_format") : "";
    const sd = signal?.width && signal?.height ? signal.width < 1280 || signal.height < 720
      : format ? /^(pal|ntsc)$/.test(format) : null;
    const interlaced = value("field_order") !== "source" || signal?.scan === "i" || /^(pal|ntsc|1080i)/.test(format);
    const fps = signal?.frame_rate || (format === "pal" || format === "1080i50" ? 25 : format === "ntsc" || format === "1080i59.94" ? 29.97 : format === "1080i60" ? 30 : Number(format.match(/p([\d.]+)$/)?.[1]));
    const minGop = Math.max(1, Math.floor(gop / 10));
    const baseAdapt = preset === 0 ? 0 : preset >= 7 ? 2 : 1;
    const bAdapt = Number(value("bframes")) === 0 ? "0" : sd === null && baseAdapt !== 2
      ? `${baseAdapt} HD / 2 SD` : String(sd ? 2 : baseAdapt);
    const mode = preset <= 1 ? "DIA" : preset >= 7 ? "UMH" : "HEX";
    const subme = [0, 1, 2, 4, 6, 7, 8, 9, 10][preset];
    const trellis = preset <= 2 ? 0 : preset >= 6 ? 2 : 1;
    const partitions = preset === 0 ? "nessuna" : preset === 1 ? "I4×4, I8×8"
      : `I4×4, I8×8, P8×8, B8×8${preset >= 7 ? ", P4×4" : ""}`;
    const psyEnabled = value("psy") === "1" || (value("psy") === "auto" && !["psnr", "ssim"].includes(tune));
    const weightp = interlaced || tune === "fastdecode" || preset === 0 ? 0 : preset <= 4 ? 1 : 2;
    const labels = {
      b_adapt: bAdapt,
      scenecut_threshold: preset === 0 ? 0 : 40,
      aq_mode: tune === "psnr" ? 0 : tune === "ssim" ? 2 : preset === 0 ? 0 : 1,
      aq_strength: tune === "animation" ? "0,6" : tune === "grain" ? "0,5" : "1,0",
      qcomp: tune === "grain" ? "0,8" : "0,6",
      ipratio: tune === "grain" ? "1,1" : "1,4",
      pbratio: tune === "grain" ? "1,1" : "1,3",
      qp_min: 0, qp_max: 51, qp_step: 4,
      chroma_qp_offset: "0 (prima della compensazione psy)",
      min_gop_frames: fps > 0 ? Math.min(minGop, Math.floor(fps), Math.floor(gop / 2) + 1) : `≤${minGop} (secondo fps)`,
      b_bias: 0,
      direct_pred: Number(value("bframes")) === 0 ? "nessuna" : preset >= 6 ? "automatica" : "spaziale",
      weighted_pred: weightp === 0 || signal?.scan || format || value("field_order") !== "source" ? weightp : `${weightp} (0 se interlacciato)`,
      deblock_alpha: tune === "animation" ? 1 : tune === "grain" ? -2 : 0,
      deblock_beta: tune === "animation" ? 1 : tune === "grain" ? -2 : 0,
      motion_estimation: adaptive ? "DIA / HEX / UMH (adattivo)" : mode,
      motion_range: preset === 8 ? 24 : 16,
      subme: adaptive ? "1–8 (adattivo)" : subme,
      trellis: adaptive ? "0–1 (adattivo)" : trellis,
      partitions: adaptive ? "0–4 gruppi (adattivo)" : partitions,
      psy_rd: adaptive ? "0–1 (adattivo)" : !psyEnabled ? 0 : tune === "animation" ? "0,4" : "1,0",
      psy_trellis: adaptive || !psyEnabled ? 0 : tune === "grain" ? "0,25" : 0,
    };
    for (const [name, presetValue] of Object.entries(labels)) {
      const field = form.elements.namedItem(name);
      const label = `Da preset: ${presetValue}`;
      if (field.tagName === "SELECT") field.querySelector('option[value="auto"], option[value="preset"]').textContent = label;
      else field.placeholder = label;
    }
  }

  function updateControls() {
    const qualitySupported = Boolean(state?.config && Object.hasOwn(state.config, "aq_mode"));
    byId("quality-scenes-controls").hidden = !qualitySupported;
    const x264Supported = Boolean(state?.config && Object.hasOwn(state.config, "x264_speedcontrol"));
    byId("x264-controls").hidden = !x264Supported;
    text("x264-mode-hint", form.elements.namedItem("x264_speedcontrol").value === "fixed"
      ? "Qualità fissa: se la CPU non basta, la coda e il ritardo aumentano."
      : "Adattiva: riferimenti, partizioni, ricerca del movimento, Trellis e intensità psy variano con il carico CPU.");
    updatePresetLabels();
    renderInputCheck();
    const stopped = canConfigure();
    const active = activeProcess();
    const restartable = canApply();
    const liveOutputs = restartable && dirty && onlyOutputChanges();
    const ready = connected && !pending && stopped && !dirty && !conflict();
    byId("channel-select").disabled = Boolean(pending);
    byId("new-channel").disabled = !connected || Boolean(pending) || dirty || byId("channel-select").options.length >= 4;
    byId("rename-channel").disabled = !canRenameChannel();
    byId("save-rename-channel").disabled = !canRenameChannel() || !renameSnapshotMatches();
    byId("cancel-rename-channel").disabled = pending === "rename-channel";
    byId("delete-channel").hidden = selectedChannel === "main";
    byId("delete-channel").disabled = !canDeleteChannel();
    byId("confirm-delete-channel").disabled = !canDeleteChannel() || !deleteSnapshotMatches();
    byId("cancel-delete-channel").disabled = pending === "delete-channel";
    text("delete-channel-status", pending === "delete-channel" ? "Eliminazione in corso…" : deleteSnapshot && !deleteSnapshotMatches() ? "Il canale o la configurazione sono cambiati. Annulla e verifica prima di confermare nuovamente." : deleteSnapshot && !canDeleteChannel() ? "Eliminazione non disponibile: verifica connessione, attività del canale e modifiche non salvate." : "");
    byId("preview-toggle").disabled = !connected || Boolean(pending) || (!state?.preview?.pid && state?.phase !== "running");
    byId("preview-toggle").textContent = state?.preview?.pid ? "Spegni anteprima" : "Attiva anteprima";
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
      button.disabled = !connected || Boolean(pending) || (state?.phase === "stopping" && !state.applying);
    });
    byId("save-button").disabled = !connected || Boolean(pending) || (!stopped && !restartable) || !dirty || conflict();
    byId("save-button").textContent = pending === "apply" || state?.applying ? "Applicazione…" : pending === "config" ? "Salvataggio…" : liveOutputs ? "Applica uscite" : restartable ? "Applica e riavvia" : "Salva";
    byId("discard-button").disabled = !state || Boolean(pending) || (!dirty && !conflict());
    for (const name of fields) form.elements.namedItem(name).disabled = !state || Boolean(pending);
    if (!x264Supported) for (const name of Object.keys(x264Defaults)) form.elements.namedItem(name).disabled = true;
    updateCredentials();
    updateBroadcastControls();
    updateScriptControls();
    byId("overview-draft").hidden = !dirty;
    const tag = byId("draft-status");
    tag.textContent = !state ? "Caricamento" : conflict() ? "Revisione aggiornata" : dirty ? "Modifiche non salvate" : "Profilo salvato";
    tag.className = `status-tag${dirty || conflict() ? " warning" : ""}`;
    text("config-revision", state ? `rev. ${state.revision}` : "—");
    let status = "Configurazione salvata";
    let hint = "";
    if (!state) { status = "Caricamento del profilo…"; }
    else if (!connected) { status = "Servizio non raggiungibile"; hint = dirty ? "Bozza non salvata." : ""; }
    else if (conflict()) { status = "Il profilo salvato è cambiato"; hint = "Ripristina per caricare la revisione aggiornata."; }
    else if (state.applying || applyingRevision === state.revision) { status = dirty ? "Nuova bozza conservata" : "Configurazione salvata · riavvio in corso"; hint = "Arresta encoder annulla il riavvio."; }
    else if (restartable) { status = dirty ? "Modifiche pronte da applicare" : "Profilo in uso"; hint = dirty ? liveOutputs ? "La codifica continua. Si aggiornano solo le uscite modificate." : "Applica e riavvia interrompe brevemente il flusso." : ""; }
    else if (!stopped) { status = dirty ? "Bozza conservata" : "Operazione in corso"; hint = "Salvataggio disponibile al termine dell’operazione."; }
    else if (state.phase === "error") { status = dirty ? "Modifiche da salvare · encoder fermo" : "Encoder fermo · verifica richiesta"; hint = state.message || "Controlla la diagnostica prima di riavviare."; }
    else if (dirty) { status = "Ci sono modifiche da salvare"; hint = "Salva il profilo prima di verificare o avviare."; }
    text("save-status", status);
    text("save-hint", hint);
    byId("save-hint").hidden = !hint;
  }

  function scriptSignalValues(metadata) {
    if (!metadata) return Array(8).fill("—");
    const props = metadata.props || {};
    const colour = (name, labels) => props[name] == null ? "—" : labels[props[name]] || `codice ${props[name]}`;
    const matrix = colour("_Matrix", { 1: "BT.709", 2: "non specificata", 5: "BT.601", 6: "BT.601", 9: "BT.2020 NCL", 10: "BT.2020 CL" });
    const primaries = colour("_Primaries", { 1: "BT.709", 2: "non specificate", 5: "BT.470 BG", 6: "SMPTE 170M", 9: "BT.2020" });
    const transfer = colour("_Transfer", { 1: "BT.709", 2: "non specificata", 4: "BT.470 M", 5: "BT.470 BG", 6: "BT.601", 13: "sRGB", 14: "BT.2020 10", 15: "BT.2020 12", 16: "PQ", 18: "HLG" });
    return [
      `${metadata.width} × ${metadata.height}`,
      `${metadata.fps_num}/${metadata.fps_den} fps`,
      { p: "Progressivo", tff: "Interlacciato TFF", bff: "Interlacciato BFF" }[metadata.scan] || "—",
      `${metadata.pixel_format} · ${metadata.bit_depth} bit`,
      `SAR ${metadata.sar || "—"} · DAR ${metadata.dar || "—"}`,
      `M: ${matrix} · P: ${primaries} · T: ${transfer}`,
      props._Range != null ? colour("_Range", { 0: "Limitato", 1: "Completo" }) : colour("_ColorRange", { 0: "Completo", 1: "Limitato" }),
      colour("_ChromaLocation", { 0: "Sinistra", 1: "Centro", 2: "Alto sinistra", 3: "Alto", 4: "Basso sinistra", 5: "Basso" }),
    ];
  }

  function renderScriptSignal() {
    const section = byId("vapoursynth-test-signal");
    section.hidden = !scriptCheck?.input;
    if (section.hidden) return;
    const input = scriptSignalValues(scriptCheck.input);
    const output = scriptSignalValues(scriptCheck.output);
    const labels = ["Dimensioni", "Cadenza", "Scansione", "Formato pixel", "Proporzioni", "Matrice · primarie · transfer", "Range", "Crominanza"];
    const body = byId("vapoursynth-test-values");
    const signature = JSON.stringify([input, output]);
    if (body.dataset.signal !== signature) {
      body.replaceChildren(...labels.map((label, index) => {
        const row = document.createElement("tr");
        [label, input[index], output[index]].forEach((value, column) => {
          const cell = document.createElement(column ? "td" : "th");
          if (!column) cell.scope = "row";
          cell.textContent = value;
          row.append(cell);
        });
        return row;
      }));
      body.dataset.signal = signature;
    }
    const origin = scriptCheck.input.origin || {};
    const detected = origin.geometry === "detected" && origin.props === "detected";
    const assumptions = [];
    if (origin.field_order !== "detected") assumptions.push("scansione/campi simulati");
    if (origin.pixel_format !== "detected") assumptions.push("formato pixel simulato");
    text("vapoursynth-test-origin", `Frame sintetici · ${detected ? "metadati ingresso rilevati" : origin.geometry === "detected" ? "geometria rilevata" : "formato simulato"}${assumptions.length ? ` · ${assumptions.join(" · ")}` : ""}.`);
    text("vapoursynth-test-props", JSON.stringify({ ingresso: scriptCheck.input.props, uscita: scriptCheck.output?.props || null }, null, 2));
  }

  function updateScriptControls() {
    const supported = Boolean(state?.capabilities?.vapoursynth_editor);
    const editor = byId("config-vapoursynth-script");
    const enabled = byId("config-vapoursynth-enabled");
    enabled.disabled ||= !supported;
    editor.disabled ||= !supported;
    const testSupported = Boolean(state?.capabilities?.vapoursynth_test);
    if (scriptCheck && (scriptCheck.source !== editor.value || scriptCheck.instance !== state?.instance_id || scriptCheck.revision !== state?.revision)) scriptCheck = null;
    byId("validate-vapoursynth").disabled = !connected || Boolean(pending) || !testSupported || !editor.value.trim() || scriptTestPending;
    text("validate-vapoursynth", scriptTestPending ? "Test in corso…" : "Esegui test");
    const result = byId("vapoursynth-validation");
    result.textContent = scriptCheck?.message || (supported && !testSupported ? "Riavvia il servizio del pannello per abilitare il test." : "");
    result.className = `notice${scriptCheck?.error ? " notice-error" : ""}`;
    result.hidden = !result.textContent;
    result.setAttribute("role", scriptCheck?.error ? "alert" : "status");
    renderScriptSignal();
    const log = byId("vapoursynth-test-log");
    if (log.textContent !== (scriptCheck?.log || "")) log.textContent = scriptCheck?.log || "";
    log.hidden = !log.textContent;
    const statuses = { active: "In uso", waiting: "In attesa del filtro", error: "Errore filtro · controlla il registro eventi", disabled: "Disabilitato", stopped: "Encoder fermo" };
    text("vapoursynth-status", supported ? statuses[state?.vapoursynth?.status] || "" : state ? "Riavvio del servizio necessario" : "");
  }

  byId("validate-vapoursynth").addEventListener("click", async () => {
    if (byId("validate-vapoursynth").disabled) return;
    const check = scriptCheck = { source: byId("config-vapoursynth-script").value, instance: state?.instance_id, revision: state?.revision, message: "Test in esecuzione…" };
    scriptTestPending = true;
    updateScriptControls();
    try {
      const result = await api("vapoursynth-test", "POST", { script: check.source });
      check.message = result.message;
      check.log = result.log;
      check.input = result.input;
      check.output = result.output;
      check.error = !result.valid;
    } catch (error) {
      check.message = "Test non riuscito";
      check.log = error.fields?.vapoursynth_script || error.message;
      check.error = true;
    } finally {
      scriptTestPending = false;
      updateScriptControls();
    }
  });

  function renderInputCheck() {
    const box = byId("input-check");
    if (inputCheck && (!state || inputCheck.revision !== state.revision || inputCheck.instanceId !== state.instance_id)) inputCheck = null;
    box.hidden = !inputCheck;
    if (!inputCheck) return;
    let title = "Verifica dell’ingresso in corso…";
    let detail = "";
    let tone = "";
    if (!connected) {
      title = "Verifica non aggiornata";
      detail = "Connessione al servizio interrotta.";
      tone = "warning";
    } else if (inputCheck.error || (pending !== "probe" && state.phase === "error")) {
      title = "Verifica dell’ingresso non riuscita";
      detail = inputCheck.error || state.message;
      tone = "error";
    } else if (pending === "probe" || state.phase === "probing") {
      // Keep the progress visible until the asynchronous probe finishes.
    } else if (state.phase === "stopping") {
      title = "Conclusione della verifica…";
    } else if (state.phase === "idle") {
      const exit = state.recovery?.last_exit;
      if (exit?.action === "probe" && exit.reason === "probe_complete" && state.signal) {
        const signal = state.signal;
        const fps = state.signal_details?.frame_rate;
        title = "Ingresso verificato";
        detail = `${signal.width} × ${signal.height}${signal.scan || ""}`;
        if (isFiniteNumber(fps)) detail += ` · ${numberFormat.format(fps)} fps`;
        if (state.signal_details?.field_rate) detail += ` · ${numberFormat.format(state.signal_details.field_rate)} semiquadri/s`;
        detail += ` · ${signal.audio || "Nessun audio rilevato"}`;
        tone = "success";
      } else {
        title = "Verifica annullata";
        tone = "warning";
      }
    } else {
      inputCheck = null;
      box.hidden = true;
      return;
    }
    if (dirty) {
      detail += " La bozza contiene modifiche non verificate.";
      if (tone === "success") tone = "warning";
    }
    box.className = `notice input-check${tone ? ` notice-${tone}` : ""}`;
    if (byId("input-check-title").textContent !== title) text("input-check-title", title);
    if (byId("input-check-detail").textContent !== detail) text("input-check-detail", detail);
    byId("input-check-detail").hidden = !detail;
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

  function renderDiagnosisOverview() {
    const health = window.OBEOverview.diagnose(state, connected);
    byId("diag-health").dataset.tone = health.tone;
    text("diag-title", health.title);
    text("diag-detail", health.detail);
    text("diag-evidence", health.evidence);
    byId("diag-evidence").hidden = !health.evidence;
    const action = byId("diag-action");
    action.hidden = !health.action;
    if (health.action) {
      action.textContent = health.action.label;
      action.setAttribute("href", health.action.href);
    }
    ["source", "fps", "output"].forEach((name, index) => {
      const stage = health.stages[index];
      text(`diag-${name}`, stage.label);
      text(`diag-${name}-note`, stage.note);
      byId(`diag-${name}-note`).hidden = !stage.note;
      byId(`diag-${name}-card`).dataset.tone = stage.tone;
    });
    text("diag-fps-current", isFiniteNumber(health.fps) ? `${numberFormat.format(health.fps)} fps` : "—");
    text("diag-updated", health.freshness);
    const error = health.lastError;
    byId("diag-last-error").hidden = !error;
    if (error) {
      const time = Date.parse(error.time);
      text("diag-last-error-time", `Ultimo errore registrato${Number.isFinite(time) ? ` · ${new Date(time).toLocaleString("it-IT")}` : ""}`);
      text("diag-last-error-text", error.message || "");
    }
  }

  function renderSignal(config) {
    const signal = state.signal;
    const audioCount = enabledAudioCount(config);
    const signalLabels = { unknown: "Da verificare", receiving: "Sorgente in ricezione", reconnecting: "Sorgente in riconnessione", stopped: "Sorgente ferma" };
    text("signal-state", signalLabels[state.signal_state] || "Stato sconosciuto");
    text("input-url", config.input_type === "ndi" ? config.ndi_source : config.input_type === "decklink" ? `Scheda ${config.input_card_index} · ${config.input_video_connection} · ${config.input_video_format}` : config.input_url || "—");
    text("output-url", config.output_url || "—");
    text("input-protocol", config.input_type && config.input_type !== "url" ? config.input_type.toUpperCase() : (config.input_url || "").split(":")[0].toUpperCase() || "—");
    text("video-settings", `${formatRate(config.video_bitrate_kbps)} configurati · ${config.preset || "—"}`);
    text("mux-settings", `Bitrate configurato: ${formatRate(config.mux_bitrate_kbps)}`);
    byId("signal-dot").className = `status-dot ${state.signal_state === "receiving" ? "good" : state.signal_state === "reconnecting" ? "" : "neutral"}`;
    if (signal && signal.width && signal.height) {
      text("input-format", `${signal.width} × ${signal.height}${signal.scan || ""}`);
      const rate = String(signal.fps || "").split("/").map(Number);
      const fps = rate.length === 2 && rate[1] ? rate[0] / rate[1] : rate[0];
      text("input-detail", `${Number.isFinite(fps) && fps > 0 ? `${numberFormat.format(fps)} fps` : "Cadenza non disponibile"}${state.encrypted ? " · SRT protetto" : ""}`);
      text("audio-format", !audioCount ? "Audio disattivato" : signal.audio ? `${audioCount} tracce ${(config.audio_codec || "mp2").toUpperCase()} stereo · ${audioCount * config.audio_bitrate_kbps} kb/s totali` : "Sorgente senza audio rilevato");
    } else {
      text("input-format", "In attesa della sorgente");
      text("input-detail", "Formato non ancora rilevato");
      text("audio-format", `Audio ${(config.audio_codec || "mp2").toUpperCase()} se presente`);
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

  function renderAudioMeters() {
    const tracks = audioTracksFromConfig(state?.config || broadcastDefaults).map((track, i) => ({ ...track, index: i })).filter(track => track.enabled);
    const sources = state?.signal?.audio_tracks || [];
    const readings = state?.telemetry?.audio || [];
    const db = value => value <= -119 ? "−∞" : `${numberFormat.format(value)} dBFS`;
    const percent = value => Math.max(0, Math.min(100, (value + 60) / 60 * 100));
    document.querySelectorAll("[data-audio-meters]").forEach(panel => {
      panel.hidden = !state?.capabilities?.audio_processing || !tracks.length;
      const list = panel.querySelector(".audio-meter-list");
      const signature = tracks.map(t => `${t.index}:${t.source}:${t.language}:${t.pid}`).join("|");
      if (list.dataset.signature !== signature) {
        list.replaceChildren(); list.dataset.signature = signature;
        tracks.forEach(track => {
          const card = document.createElement("article"); card.className = "audio-meter-card";
          card.innerHTML = '<div class="audio-meter-title"><h3></h3><span class="audio-meter-status"></span></div><div class="audio-meter-channels"></div><div class="audio-meter-scale" aria-hidden="true"><div class="audio-meter-ticks"><span>−60</span><span>−30</span><span>−12</span><span>0</span></div></div>';
          card.querySelector("h3").textContent = `Traccia ${track.index + 1}${track.language ? ` · ${track.language}` : ""}${track.pid ? ` · PID ${track.pid}` : ""}`;
          for (const name of ["L", "R"]) {
            const row = document.createElement("div"); row.className = "audio-meter-channel";
            row.innerHTML = '<span class="audio-meter-side"></span><div class="audio-meter-bar" role="meter" aria-valuemin="-60" aria-valuemax="0"><i class="audio-meter-fill"></i><i class="audio-meter-peak"></i></div><span class="audio-meter-reading"></span><small class="audio-meter-rms"></small>';
            row.querySelector(".audio-meter-side").textContent = name;
            row.querySelector(".audio-meter-bar").setAttribute("aria-label", `Picco traccia ${track.index + 1}, ${name === "L" ? "sinistra" : "destra"}`);
            card.querySelector(".audio-meter-channels").append(row);
          }
          list.append(card);
        });
      }
      tracks.forEach((track, i) => {
        const input = sources[track.source - 1];
        const meter = readings.find(item => input && item.input_id === input.id);
        const live = connected && state?.process_alive && state?.telemetry?.status === "live" && meter?.status === "live";
        const card = list.children[i];
        const clipped = live && meter.clipped.some(Boolean);
        card.classList.toggle("clipped", Boolean(clipped));
        card.querySelector(".audio-meter-status").textContent = clipped ? "CLIP" : !connected ? "Offline" : !state?.process_alive ? "Fermo" : !live ? "In attesa" : meter.normalizing ? "Normalizzato" : "";
        card.querySelectorAll(".audio-meter-channel").forEach((row, c) => {
          const peak = live ? meter.peak_dbfs[c] : -120, rms = live ? meter.rms_dbfs[c] : -120;
          const bar = row.querySelector(".audio-meter-bar");
          bar.style.setProperty("--rms", `${percent(rms)}%`);
          bar.style.setProperty("--peak", `${percent(peak)}%`);
          bar.classList.toggle("unavailable", !live);
          bar.setAttribute("aria-valuenow", String(Math.max(-60, peak)));
          bar.setAttribute("aria-valuetext", live ? `Picco ${db(peak)}, RMS ${db(rms)}` : "Misura non disponibile");
          row.querySelector(".audio-meter-reading").textContent = live ? db(peak) : "—";
          row.querySelector(".audio-meter-rms").textContent = live ? `RMS ${db(rms)}` : "RMS —";
        });
      });
    });
  }

  function renderTelemetry() {
    renderAudioMeters();
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
      "measured-output-errors": telemetry.output_errors,
      "measured-video-qp": telemetry.video_qp_aq,
      "measured-video-pvmaf": telemetry.video_pvmaf
    };
    for (const [id, value] of Object.entries(readings)) text(id, live && isFiniteNumber(value) ? numberFormat.format(value) : "—");
    byId("measured-qp-row").hidden = !live || !isFiniteNumber(telemetry.video_qp_aq);
    byId("measured-pvmaf-row").hidden = !live || !isFiniteNumber(telemetry.video_pvmaf);
    byId("measured-output-errors").classList.toggle("has-errors", live && telemetry.output_errors > 0);
    let detail = "Nessuna misura disponibile.";
    if (status === "live") {
      const pendingRates = [telemetry.video_bitrate_kbps, telemetry.output_bitrate_kbps, telemetry.video_fps].some((value) => !isFiniteNumber(value));
      detail = pendingRates ? "Calcolo di bitrate e fps in corso." : `Misure sull’ultimo intervallo${isFiniteNumber(telemetry.interval_seconds) ? ` di ${numberFormat.format(telemetry.interval_seconds)} s` : ""}.`;
    } else if (status === "waiting") detail = "In attesa di campioni.";
    else if (status === "stale") detail = `Nessun campione recente${isFiniteNumber(telemetry.age_seconds) ? `: l’ultimo risale a ${numberFormat.format(telemetry.age_seconds)} s fa` : ""}. Misure sospese.`;
    else if (status === "unavailable" || !Object.hasOwn(labels, status)) detail = "Telemetria non disponibile.";
    else if (status === "offline") detail = "Misure sospese: servizio non connesso.";
    text("stream-telemetry-detail", detail);
  }

  function renderSrt() {
    const srt = state?.srt || {};
    byId("srt-telemetry").hidden = !state?.config.input_url.startsWith("srt://");
    const status = !connected ? "offline" : srt.status || "unavailable";
    const live = status === "live";
    const labels = { live: "Misure aggiornate", waiting: "In attesa", stale: "Campione scaduto", unavailable: "Non disponibile", idle: "Encoder fermo", offline: "Servizio non connesso" };
    text("srt-status", labels[status] || "Non applicabile");
    byId("srt-status").className = `status-tag${live ? " good" : ["stale", "unavailable", "offline"].includes(status) ? " warning" : ""}`;
    const readings = { "srt-rtt": srt.rtt_ms, "srt-bitrate": srt.receive_bitrate_kbps,
      "srt-loss": srt.lost_packets, "srt-retransmitted": srt.retransmitted_packets,
      "srt-dropped": srt.dropped_packets, "srt-buffer-bytes": srt.buffer_bytes === null ? null : srt.buffer_bytes / 1024,
      "srt-buffer-ms": srt.buffer_ms, "srt-available": srt.buffer_available_bytes === null ? null : srt.buffer_available_bytes / 1024,
      "srt-latency": srt.latency_ms, "srt-received": srt.received_packets };
    for (const [id, value] of Object.entries(readings)) text(id, live && isFiniteNumber(value) ? numberFormat.format(value) : "—");
    text("srt-detail", live ? `Connessione ${srt.connection} · campione ${numberFormat.format(srt.age_seconds)} s fa. Le perdite recuperate non sono scarti.` :
      status === "stale" ? "Misure scadute" : status === "offline" ? "Servizio non connesso" : "Statistiche SRT non disponibili");
  }

  function monitorItem(title, detail, tone = "") {
    const item = document.createElement("li");
    item.className = tone;
    const label = document.createElement("strong");
    label.textContent = title;
    const description = document.createElement("p");
    description.textContent = detail;
    item.append(label, description);
    return item;
  }

  function renderMonitoring() {
    const monitoring = state?.monitoring;
    const available = connected && window.OBEOverview.fresh(monitoring);
    const active = available ? monitoring.active_alarms || [] : [];
    const summary = !available ? "Stato degli allarmi non aggiornato." : active.length ? `${active.length} allarm${active.length === 1 ? "e attivo" : "i attivi"}.` : "Nessun allarme attivo";
    text("alarms-status", summary);
    byId("diagnosis-alarms").hidden = available && active.length === 0;
    byId("alarms-status").className = `status-tag ${!available || active.length ? "warning" : "good"}`;
    byId("alarm-summary").hidden = available && active.length === 0;
    text("alarm-summary-text", summary);
    const signature = JSON.stringify([available, active]);
    if (signature !== alarmSignature) {
      alarmSignature = signature;
      const items = active.map((alarm) => monitorItem(alarm.label, `${new Date(alarm.time).toLocaleString("it-IT")} · ${alarm.guide}`, alarm.level));
      if (!items.length && !available) items.push(monitorItem("Allarmi non disponibili", "Nuovo tentativo automatico."));
      byId("active-alarms").hidden = items.length === 0;
      byId("active-alarms").replaceChildren(...items);
    }
    const rules = monitoring?.rules || {};
    if (JSON.stringify(rules) !== ruleSignature) {
      ruleSignature = JSON.stringify(rules);
      byId("alarm-rules").replaceChildren(...Object.values(rules).map((rule) => monitorItem(rule.label, `${rule.guide} Attivazione: ${rule.trigger_seconds ? `${rule.trigger_seconds} s` : "immediata"}; rientro dopo ${rule.clear_seconds} s di condizione regolare.`)));
    }
    if (monitoring && !monitoring.history_available) text("history-status", "Storico su disco non disponibile. Nuovo tentativo automatico.");
  }

  function renderHistory(data) {
    window.OBEOverview?.history(data);
    const windowMs = Number(byId("diag-window").value) * 1000;
    const generatedAt = Date.parse(data.generated_at);
    const windowEnd = Number.isFinite(generatedAt) ? generatedAt : Date.now();
    const cutoff = windowEnd - windowMs;
    const samples = (data.samples || []).filter((sample) => {
      const time = Date.parse(sample.time);
      return time >= cutoff && time <= windowEnd;
    });
    text("history-status", `${data.total_samples} campioni conservati · letto alle ${timeFormat.format(new Date(windowEnd))}`);
    const rows = samples.slice(-20).reverse().map((sample) => {
      const row = document.createElement("tr");
      const values = [new Date(sample.time).toLocaleString("it-IT"), sample.telemetry.video_bitrate_kbps,
        sample.telemetry.output_bitrate_kbps, sample.telemetry.video_fps, sample.srt.rtt_ms, sample.srt.dropped_packets];
      for (const value of values) {
        const cell = document.createElement("td");
        cell.textContent = typeof value === "string" ? value : isFiniteNumber(value) ? numberFormat.format(value) : "—";
        row.append(cell);
      }
      return row;
    });
    if (!rows.length) {
      const row = document.createElement("tr"), cell = document.createElement("td");
      cell.colSpan = 6;
      cell.textContent = "Nessun campione nell’intervallo selezionato.";
      row.append(cell);
      rows.push(row);
    }
    byId("history-rows").replaceChildren(...rows);
    const windowLabel = byId("diag-window").selectedOptions[0].textContent.toLowerCase().replace(/^ultimi /, "");
    const values = (read, valid) => samples.filter(valid).map(read).filter(isFiniteNumber);
    const trace = (id, read, valid, scale, continuity = sample => sample.run_id) => {
      byId(id).setAttribute("d", window.OBEOverview.trace(samples,
        sample => valid(sample) ? read(sample) : null, continuity, windowEnd, windowMs / 1000, scale));
    };
    const target = (id, value, scale, measured) => {
      const y = 110 - 100 * value / scale;
      byId(id).setAttribute("d", measured && isFiniteNumber(value) && value > 0 ? `M0 ${y}H600` : "");
    };
    text("diag-history-time", `${timeFormat.format(new Date(cutoff))} – ${timeFormat.format(new Date(windowEnd))}`);
    const liveVideo = sample => sample.telemetry?.status === "live";
    const bitrateValues = values(sample => sample.telemetry.video_bitrate_kbps, liveVideo)
      .concat(values(sample => sample.telemetry.output_bitrate_kbps, liveVideo));
    const muxTarget = state?.config?.mux_bitrate_kbps;
    const bitrateScale = Math.max(1000, Math.ceil(Math.max(muxTarget || 0, ...bitrateValues) / 1000) * 1000);
    target("diag-bitrate-target", muxTarget, bitrateScale, bitrateValues.length);
    trace("history-video-line", sample => sample.telemetry.video_bitrate_kbps, liveVideo, bitrateScale);
    trace("history-output-line", sample => sample.telemetry.output_bitrate_kbps, liveVideo, bitrateScale);
    text("history-chart-title", bitrateValues.length ? `Bitrate video e uscita negli ultimi ${windowLabel}; scala 0–${numberFormat.format(bitrateScale / 1000)} Mbit/s` : "Nessun bitrate misurato");
    text("history-chart-detail", bitrateValues.length ? `0–${numberFormat.format(bitrateScale / 1000)} Mbit/s${isFiniteNumber(muxTarget) ? ` · tratteggio: target mux attuale ${numberFormat.format(muxTarget)} kb/s` : ""}` : "Nessun bitrate misurato nell’intervallo");
    const fpsValues = values(sample => sample.telemetry.video_fps, liveVideo);
    const expectedFps = state?.signal_details?.frame_rate;
    const fpsScale = Math.max(30, Math.ceil(Math.max(expectedFps || 0, ...fpsValues) / 10) * 10);
    target("diag-fps-target", expectedFps, fpsScale, fpsValues.length);
    trace("diag-fps-line", sample => sample.telemetry.video_fps, liveVideo, fpsScale);
    text("diag-fps-title", fpsValues.length ? `Frame al secondo negli ultimi ${windowLabel}; scala 0–${fpsScale}` : "Nessun frame al secondo misurato");
    text("diag-fps-detail", fpsValues.length ? `0–${fpsScale} fps${isFiniteNumber(expectedFps) ? ` · tratteggio: attesi ${numberFormat.format(expectedFps)} fps` : ""}` : "Nessuna cadenza misurata nell’intervallo");
    const liveSrt = sample => sample.srt?.status === "live";
    const rttValues = values(sample => sample.srt.rtt_ms, liveSrt);
    const rttScale = Math.max(50, Math.ceil(Math.max(0, ...rttValues) / 50) * 50);
    trace("diag-rtt-line", sample => sample.srt?.rtt_ms, liveSrt, rttScale,
      sample => `${sample.run_id}/${sample.srt?.connection}`);
    text("diag-rtt-title", rttValues.length ? `RTT SRT negli ultimi ${windowLabel}; scala 0–${rttScale} ms` : "Nessun RTT SRT misurato");
    text("diag-rtt-detail", rttValues.length ? `Ultimi ${windowLabel} · 0–${rttScale} ms · scarti non recuperati` : "Nessun dato RTT · scarti non recuperati");
    const reasons = { recovered: "Rientrato", encoder_stopped: "Chiuso: encoder arrestato", process_changed: "Chiuso: nuovo processo", monitor_restarted: "Chiuso: pannello riavviato" };
    const events = (data.alarms || []).slice().reverse().map((alarm) => monitorItem(
      `${alarm.state === "active" ? "Attivato" : reasons[alarm.reason] || "Chiuso"} · ${alarm.label}`,
      `${new Date(alarm.time).toLocaleString("it-IT")} · ${alarm.guide}`, alarm.state === "active" ? alarm.level : ""));
    if (!events.length) events.push(monitorItem("Nessuna transizione registrata", ""));
    byId("alarm-history").replaceChildren(...events);
  }

  async function loadHistory(force = false) {
    if (authBlocked || historyLoading || (byId("view-monitoring").hidden && byId("view-overview").hidden) || (!force && Date.now() - historyLoadedAt < 5000)) return;
    const epoch = historyEpoch;
    historyLoading = true;
    try {
      const data = await api(`history?limit=${byId("diag-window").value}`);
      if (epoch === historyEpoch) renderHistory(data);
    } catch (error) {
      if (epoch === historyEpoch) {
        text("history-status", `Storico non aggiornato. ${error.message} Nuovo tentativo automatico.`);
        text("diag-history-time", "Storico non aggiornato · nuovo tentativo automatico");
        window.OBEOverview?.historyError("Storico non aggiornato: " + error.message);
      }
    } finally {
      if (epoch === historyEpoch) {
        historyLoadedAt = Date.now();
        historyLoading = false;
      }
    }
  }

  function updateHistoryLinks() {
    for (const link of document.querySelectorAll("[data-history-format]")) {
      link.href = `/api/channels/${encodeURIComponent(selectedChannel)}/history?limit=1800&format=${link.dataset.historyFormat}`;
    }
  }

  function changeHistoryWindow(value) {
    byId("diag-window").value = value;
    byId("overview-window").value = value;
    historyEpoch += 1;
    historyLoading = false;
    historyLoadedAt = 0;
    void loadHistory(true);
  }
  byId("diag-window").addEventListener("change", event => changeHistoryWindow(event.target.value));
  byId("overview-window").addEventListener("change", event => changeHistoryWindow(event.target.value));
  byId("overview-refresh").addEventListener("click", () => { void loadHistory(true); });
  byId("overview-interface").addEventListener("change", () => window.OBEOverview?.selectInterface());

  function renderRecovery() {
    const recovery = state?.recovery;
    let summary = "Recupero manuale dopo un arresto imprevisto.";
    if (!connected) summary = "Stato del recupero non disponibile: servizio non connesso.";
    else if (!recovery) summary = "Diagnostica del recupero non disponibile.";
    else if (recovery.exhausted) summary = `Budget esaurito: ${recovery.attempts} tentativi. Controlla la diagnostica prima di avviare nuovamente.`;
    else if (isFiniteNumber(recovery.next_retry_seconds)) summary = `Riavvio ${recovery.attempts + 1}/${recovery.max_attempts} tra ${numberFormat.format(recovery.next_retry_seconds)} s. Arresta encoder per annullarlo.`;
    else if (recovery.active && recovery.enabled) summary = `Recupero automatico attivo · ${recovery.attempts}/${recovery.max_attempts} tentativi utilizzati.`;
    else if (recovery.enabled) summary = `Recupero terminato · ${recovery.attempts}/${recovery.max_attempts} tentativi utilizzati.`;
    text("recovery-status", summary);
    byId("recovery-status").parentElement.hidden = !connected || !(recovery?.exhausted || (recovery?.enabled && recovery.active));
    const history = recovery?.history || [];
    const signature = JSON.stringify(history);
    if (signature === exitSignature) return;
    exitSignature = signature;
    const reasons = { process_signal: "Segnale di sistema", process_exit: "Uscita imprevista", operator_stop: "Arresto manuale", panel_shutdown: "Spegnimento pannello", probe_complete: "Verifica completata", probe_timeout: "Timeout verifica", start_timeout: "Avvio non confermato", unsupported_input: "Ingresso non supportato", operation_failed: "Operazione fallita", spawn_failed: "Avvio processo fallito", encoder_unavailable: "Eseguibile non disponibile" };
    const items = [...history].reverse().map((entry) => {
      const item = document.createElement("li");
      const date = new Date(entry.time);
      const stamp = Number.isNaN(date.getTime()) ? "—" : timeFormat.format(date);
      const outcome = entry.signal || (entry.returncode === null ? "nessun processo avviato" : `codice ${entry.returncode}`);
      item.textContent = `${stamp} · PID ${entry.pid ?? "—"} · ${reasons[entry.reason] || "Processo terminato"} · ${outcome} · durata ${entry.uptime_seconds} s · tentativo ${entry.restart_attempt}`;
      return item;
    });
    if (!items.length) {
      const item = document.createElement("li");
      item.textContent = "Nessuna uscita registrata dall’avvio del pannello.";
      items.push(item);
    }
    byId("exit-history").replaceChildren(...items);
  }

  function renderChart() {
    const count = cpuSamples.length;
    byId("chart-empty").hidden = count > 1;
    text("chart-empty", count === 1 ? "Primo campione CPU" : "Nessun dato CPU");
    if (count < 2) {
      byId("cpu-line").setAttribute("d", "");
      byId("cpu-area").setAttribute("d", "");
      text("chart-title", "CPU encoder: campioni insufficienti per tracciare il grafico");
      text("chart-range", count ? "1 campione" : "Nessun campione");
      text("chart-scale", "—");
      return;
    }
    const max = Math.max(100, Math.ceil(Math.max(...cpuSamples.map((point) => point.value)) / 100) * 100);
    const elapsed = cpuSamples[count - 1].time - cpuSamples[0].time;
    const points = cpuSamples.map((point) => ({ x: 600 * (point.time - cpuSamples[0].time) / Math.max(elapsed, 1), y: 80 - 68 * Math.max(0, point.value) / max }));
    const line = points.map((point, index) => `${index ? "L" : "M"}${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(" ");
    byId("cpu-line").setAttribute("d", line);
    byId("cpu-area").setAttribute("d", `${line} L600,80 L0,80 Z`);
    text("chart-range", `Ultimi ${Math.round(elapsed / 1000)} s`);
    text("chart-scale", `0–${max}%`);
    text("chart-title", `CPU encoder: ${count} campioni, ultimo ${numberFormat.format(cpuSamples[count - 1].value)}%, scala da 0 a ${max}%`);
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
    if (["probing", "starting"].includes(state?.phase) && ["idle", "running", "reconnecting", "restart_wait", "error"].includes(next.phase)) setNotice("");
    if (applyingRevision !== null) {
      if (applyingRevision !== next.revision) applyingRevision = null;
      else if (!next.applying && ["running", "reconnecting", "restart_wait", "error", "idle"].includes(next.phase)) {
        const outcomes = {
          running: "Configurazione applicata. Codifica riavviata.",
          reconnecting: "Configurazione applicata. La sorgente è in riconnessione.",
          restart_wait: "Configurazione salvata. Il processo è terminato; recupero automatico in corso.",
          error: `Configurazione salvata, ma il riavvio non è riuscito. ${next.message || "Controlla la diagnostica."}`,
          idle: "Configurazione salvata. L’encoder è fermo."
        };
        setNotice(outcomes[next.phase], next.phase === "error" ? "error" : next.phase === "running" ? "info" : "warning");
        applyingRevision = null;
      }
    }
    state = next;
    connected = true;
    byId("api-error").hidden = true;
    text("api-status", "Servizio connesso");
    byId("api-dot").className = "status-dot good";
    text("panel-version", next.version?.panel ? `v${next.version.panel}` : "—");
    if (options.saved || !baseConfig || (!dirty && draftRevision !== next.revision && !form.contains(document.activeElement))) populateForm(next.config, next.revision);
    renderTransport();
    renderSystem();
    text("session-name", next.config.name || "Profilo encoder");
    text("phase-tag", phaseLabels[next.phase] || "Stato sconosciuto");
    const warning = ["probing", "starting", "reconnecting", "restart_wait", "stopping"].includes(next.phase);
    const tone = next.phase === "running" ? "good" : next.phase === "error" ? "bad" : warning ? "warning" : "";
    byId("phase-tag").className = `status-tag ${tone}`;
    document.querySelector(".session-panel").dataset.phase = next.phase;
    byId("encoder-indicator").className = `encoder-indicator ${tone === "good" ? "active" : tone}`;
    text("session-heading", phaseTitles[next.phase] || "Stato dell’encoder");
    text("session-message", next.message || "Nessun dettaglio disponibile");
    text("session-pid", `PID ${next.pid ?? "—"}`);
    text("session-detail", next.encoding_started ? "Codifica confermata dal processo" : next.process_alive ? "Processo avviato · codifica non ancora confermata" : next.phase === "restart_wait" ? "Nessun processo attivo · riavvio programmato" : "Nessun processo di codifica attivo");
    renderRecovery();
    renderSignal(next.config);
    renderMetrics(options.sample !== false);
    renderTelemetry();
    renderSrt();
    renderMonitoring();
    renderDiagnosisOverview();
    window.OBEOverview?.render(state, connected);
    window.OBEDelivery?.render(state.delivery, state.instance_id, connected);
    renderPreview();
    renderEvents();
    text("last-update", `Aggiornato alle ${timeFormat.format(new Date())}`);
    updateControls();
    if (!activeProcess() && byId("stop-dialog").open) byId("stop-dialog").close();
  }

  function markDisconnected(error) {
    connected = false;
    window.OBEDelivery?.render(state?.delivery, state?.instance_id, false);
    renderTransport();
    renderSystem();
    renderRecovery();
    renderPreview();
    const banner = byId("api-error");
    banner.textContent = `Connessione al servizio interrotta. ${error.message} Nuovo tentativo automatico tra 2 secondi.`;
    banner.hidden = false;
    text("api-status", "Servizio non connesso");
    byId("api-dot").className = "status-dot bad";
    text("phase-tag", "Stato non disponibile");
    byId("phase-tag").className = "status-tag warning";
    text("session-heading", "Connessione interrotta");
    text("session-message", "Stato non disponibile.");
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
    renderSrt();
    renderMonitoring();
    renderDiagnosisOverview();
    window.OBEOverview?.render(state, connected);
    cpuSamples = [];
    renderChart();
    text("chart-empty", "Grafico sospeso: servizio non connesso");
    updateControls();
  }

  async function api(path, method = "GET", body, channelId = selectedChannel) {
    const controller = new AbortController();
    const timeoutMs = path === "vapoursynth-test" ? 75000 : 15000;
    const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      const endpoint = ["session", "login", "logout"].includes(path) ? `/api/${path}` : path === "channels" ? "/api/channels" : `/api/channels/${encodeURIComponent(channelId)}${path === "channel" ? "" : `/${path}`}`;
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
        error.code = data?.error?.code;
        error.fields = data?.error?.fields;
        error.status = response.status;
        if (response.status === 401 && path !== "login") requireLogin();
        throw error;
      }
      if (!data) throw new Error("Risposta del servizio non leggibile.");
      return data;
    } catch (error) {
      if (error.name === "AbortError") throw new Error(`Il servizio non ha risposto entro ${timeoutMs / 1000} secondi.`);
      if (error instanceof TypeError) throw new Error("Verifica che il servizio del pannello sia attivo.");
      throw error;
    } finally { window.clearTimeout(timeout); }
  }

  window.OBEDelivery?.initialize(api);

  async function poll() {
    const epoch = mutationEpoch;
    try {
      if (!pending && !authBlocked) {
        if (!sessionKnown) {
          try { await loadSession(); } catch (error) { if (error.status !== 404) throw error; }
        }
        if (!authBlocked) {
          const [next, channels] = await Promise.all([api("state"), api("channels")]);
          if (epoch === mutationEpoch && !pending) { renderChannels(channels); applyState(next); }
        }
      }
    } catch (error) {
      if (epoch === mutationEpoch && !pending) {
        if (error.status === 404 && selectedChannel !== "main") {
          if (dirty) {
            byId("delete-channel-dialog").close();
            markDisconnected(error);
            setNotice("Il canale non è più disponibile. La bozza è conservata: copiala prima di ripristinare o lasciare la pagina.", "warning");
          } else {
            await selectChannel("main");
            setNotice("Il canale richiesto non è disponibile. Aperto il canale principale.", "warning");
          }
        } else markDisconnected(error);
      }
    } finally { loadHistory(); window.setTimeout(poll, 2000); }
  }

  function requireLogin() {
    livePlayer.destroy();
    clearSavedSrtPassphrase();
    clearOutputKeys();
    authBlocked = true;
    connected = false;
    document.querySelector(".app-shell").inert = true;
    document.querySelector(".app-shell").hidden = true;
    byId("preview-image").replaceChildren();
    if (!byId("login-dialog").open) byId("login-dialog").showModal();
    updateControls();
  }

  async function loadSession() {
    const session = await api("session");
    authRequired = Boolean(session.auth_required);
    sessionKnown = true;
    byId("logout-button").hidden = !authRequired;
    if (authRequired && !session.authenticated) requireLogin();
  }

  async function initializeSession() {
    try { await loadSession(); } catch (error) {
      // A connection failure remains visible and is retried by regular polling.
      if (error.status !== 404) markDisconnected(error);
    }
    void poll();
  }

  byId("login-dialog").addEventListener("cancel", event => event.preventDefault());
  byId("login-form").addEventListener("submit", async event => {
    event.preventDefault();
    byId("login-submit").disabled = true;
    text("login-error", "");
    try {
      await api("login", "POST", { username: byId("login-username").value, password: byId("login-password").value });
      byId("login-password").value = "";
      authRequired = true;
      sessionKnown = true;
      authBlocked = false;
      byId("login-dialog").close();
      document.querySelector(".app-shell").inert = false;
      document.querySelector(".app-shell").hidden = false;
      applyState(await api("state"));
      renderChannels(await api("channels"));
    } catch (error) { text("login-error", error.message); }
    finally { byId("login-submit").disabled = false; }
  });
  byId("logout-button").addEventListener("click", async () => {
    loggingOut = true;
    clearSavedSrtPassphrase();
    updateCredentials();
    try {
      await api("logout", "POST", {});
      clearCredentialInputs();
      clearOutputKeys();
      requireLogin();
    } catch (error) { setNotice(error.message, "error"); }
    finally { loggingOut = false; updateCredentials(); }
  });

  byId("add-output").addEventListener("click", () => {
    if (!state || pending || outputCards().length >= 8) return;
    const count = outputCards().length;
    const id = `out-${crypto.getRandomValues(new Uint32Array(1))[0].toString(16)}`;
    addOutputCard({ id, name: count === 0 ? "MAIN" : count === 1 ? "BACKUP" : `Uscita ${count + 1}`, enabled: true,
      protocol: "udp", url: `udp://127.0.0.1:${14000 + count * 10}`, srt_mode: "caller", latency_ms: 200 });
    updateDraft();
    outputField(outputCards().at(-1), "name").focus();
  });

  function loadProfileDraft(config, description) {
    if (!config || typeof config !== "object" || Array.isArray(config)) throw new Error("Il file non contiene un profilo valido.");
    if (Object.keys(config).some(name => !fields.includes(name) && !["outputs", "audio_tracks"].includes(name))) throw new Error("Il profilo contiene campi non supportati da questa versione.");
    const merged = { ...broadcastDefaults, ...config, outputs: config.outputs || [] };
    if (merged.audio_normalize && !state?.capabilities?.audio_processing) throw new Error("Aggiorna il servizio prima di importare la normalizzazione loudness.");
    if (merged.audio_tracks != null) {
      if (!state?.capabilities?.audio_tracks) throw new Error("Il servizio deve essere aggiornato per importare più tracce audio.");
      if (!Array.isArray(merged.audio_tracks) || merged.audio_tracks.length > 8) throw new Error("L’elenco delle tracce audio non è valido.");
      merged.audio_tracks = merged.audio_tracks.map(value => {
        if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).some(key => !Object.hasOwn(audioTrackDefaults, key))) throw new Error("Traccia audio non valida.");
        const track = { ...audioTrackDefaults, ...value };
        if (!Number.isInteger(track.source) || track.source < 1 || track.source > 8 || !Number.isInteger(track.pid) || track.pid < 0 || track.pid > 8190
            || typeof track.enabled !== "boolean" || typeof track.language !== "string" || !/^(?:[a-zA-Z]{3})?$/.test(track.language)
            || !audioTypes.some(([type]) => type === track.audio_type)) throw new Error("Parametri della traccia audio non validi.");
        return track;
      });
    }
    if (!Array.isArray(merged.outputs) || merged.outputs.length > 8) throw new Error("L’elenco delle destinazioni non è valido.");
    const outputIds = new Set();
    for (const output of merged.outputs) {
      if (!output || typeof output !== "object" || !/^[a-z0-9][a-z0-9-]{0,31}$/.test(output.id || "") || outputIds.has(output.id)
          || Object.keys(output).some(key => !["id", "name", "enabled", "protocol", "url", ...Object.keys(outputSrtParameters())].includes(key))) throw new Error("Una destinazione del profilo non è valida.");
      if (state?.capabilities?.outputs?.srt_options !== true && Object.keys(output).some(key =>
          Object.hasOwn(outputSrtParameters(), key) && !["srt_mode", "latency_ms"].includes(key)))
        throw new Error("Aggiorna il servizio del pannello prima di importare queste opzioni SRT.");
      outputIds.add(output.id);
    }
    for (const name of fields) {
      const valid = booleanFields.has(name) ? typeof merged[name] === "boolean"
        : nullableNumericFields.has(name) ? merged[name] === null || typeof merged[name] === "number"
        : ["string", "number"].includes(typeof merged[name]);
      if (!valid) throw new Error(`Valore mancante o non valido: ${name}.`);
    }
    for (const name of fields) {
      const field = form.elements.namedItem(name);
      if (booleanFields.has(name) && field.type === "checkbox") field.checked = merged[name];
      else field.value = merged[name];
    }
    renderInputUrlFields(merged.input_url);
    renderOutputForm(merged.outputs);
    renderAudioTracks(merged);
    clearCredentialInputs();
    clearFieldErrors();
    updateDraft();
    setNotice(`${description} nella bozza.`);
  }

  byId("copy-profile").addEventListener("click", async () => {
    const source = byId("copy-channel").value;
    if (!source || !state || pending) return;
    const channel = selectedChannel;
    const revision = draftRevision;
    const before = JSON.stringify(readDraft());
    const generation = draftGeneration;
    try {
      const other = await api("state", "GET", undefined, source);
      if (channel !== selectedChannel || revision !== draftRevision || generation !== draftGeneration || before !== JSON.stringify(readDraft())) throw new Error("La bozza è cambiata durante la lettura; ripeti la copia.");
      loadProfileDraft({ ...other.config, name: readDraft().name, output_url: readDraft().output_url }, "Profilo copiato");
    } catch (error) { setNotice(error.message, "error"); }
  });
  byId("export-profile").addEventListener("click", async () => {
    if (!state || pending) return;
    try {
      const profile = await api("profile");
      const blob = new Blob([JSON.stringify(profile, null, 2) + "\n"], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `obe-${selectedChannel}-profile.json`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setNotice("Profilo salvato esportato senza credenziali.");
    } catch (error) { setNotice(error.message, "error"); }
  });
  byId("import-profile").addEventListener("click", () => byId("import-profile-file").click());
  byId("import-profile-file").addEventListener("change", async event => {
    const file = event.target.files[0];
    event.target.value = "";
    if (!file || !state || pending) return;
    const channel = selectedChannel;
    const revision = draftRevision;
    const before = JSON.stringify(readDraft());
    const generation = draftGeneration;
    try {
      if (file.size > 65536) throw new Error("Il profilo supera il limite di 64 KiB.");
      const profile = JSON.parse(await file.text());
      if (channel !== selectedChannel || revision !== draftRevision || generation !== draftGeneration || before !== JSON.stringify(readDraft())) throw new Error("La bozza è cambiata durante la lettura; ripeti l’importazione.");
      if (!profile || profile.format !== "obe-webpanel-profile" || profile.version !== 1 || Object.keys(profile).sort().join() !== "config,format,version") throw new Error("Formato o versione del backup non supportati.");
      const validated = await api("profile-validate", "POST", profile);
      if (channel !== selectedChannel || revision !== draftRevision || generation !== draftGeneration) throw new Error("La bozza è cambiata durante la verifica; ripeti l’importazione.");
      loadProfileDraft(validated.config, "Profilo importato");
    } catch (error) { setNotice(error.message, "error"); }
  });
  byId("restore-profile").addEventListener("click", () => {
    if (byId("restore-profile").disabled) return;
    restoreSnapshot = { channel: selectedChannel, revision: state.revision, instance_id: state.instance_id };
    updateControls();
    byId("restore-dialog").showModal();
  });
  byId("cancel-restore").addEventListener("click", () => { restoreSnapshot = null; byId("restore-dialog").close(); });
  byId("confirm-restore").addEventListener("click", () => {
    if (byId("confirm-restore").disabled || !restoreSnapshot) return;
    const { revision, instance_id } = restoreSnapshot;
    restoreSnapshot = null;
    byId("restore-dialog").close();
    void runAction("restore", { revision, instance_id });
  });

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
    const copy = byId("copy-channel");
    const previous = copy.value;
    copy.replaceChildren(new Option("Seleziona un canale…", ""), ...data.channels.filter(channel => channel.id !== selectedChannel).map(channel => new Option(channel.name, channel.id)));
    copy.value = previous;
    text("channel-summary", `${data.channels.filter(channel => channel.process_alive).length} attivi · ${data.channels.length}/${data.limit} canali`);
  }

  async function selectChannel(id) {
    livePlayer.destroy();
    if (dirty || pending) { byId("channel-select").value = selectedChannel; setNotice("Salva o ripristina la bozza prima di cambiare canale.", "warning"); return; }
    byId("delete-channel-dialog").close();
    byId("rename-channel-dialog").close();
    deleteSnapshot = null;
    renameSnapshot = null;
    selectedChannel = id;
    mutationEpoch += 1;
    pending = "channel";
    state = null; baseConfig = null; draftRevision = null; applyingRevision = null; cpuSamples = []; chartPid = null;
    window.OBEOverview?.render(null, false);
    historyEpoch += 1; historyLoading = false; historyLoadedAt = 0;
    exitSignature = ""; alarmSignature = ""; ruleSignature = "";
    renderRecovery(); renderMonitoring();
    renderHistory({ samples: [], alarms: [], total_samples: 0, sample_limit: 1800 });
    text("history-status", "Caricamento dello storico del canale…");
    updateHistoryLinks();
    renderPreview();
    clearCredentialInputs();
    setNotice(""); updateControls();
    const url = new URL(location.href);
    url.searchParams.set("channel", id);
    history.replaceState(null, "", url);
    try { const [next, channels] = await Promise.all([api("state"), api("channels")]); channelSignature = ""; renderChannels(channels); applyState(next); }
    catch (error) { markDisconnected(error); }
    finally { pending = null; updateControls(); }
  }

  byId("channel-select").addEventListener("change", event => void selectChannel(event.target.value));
  updateHistoryLinks();
  byId("new-channel").addEventListener("click", () => {
    byId("channel-dialog").showModal();
  });
  byId("rename-channel").addEventListener("click", () => {
    if (!canRenameChannel()) return;
    renameSnapshot = { id: selectedChannel, revision: state.revision, instance_id: state.instance_id };
    byId("rename-channel-name").value = state.config.name;
    text("rename-channel-status", "");
    byId("rename-channel-dialog").showModal();
    updateControls();
  });
  byId("cancel-rename-channel").addEventListener("click", () => byId("rename-channel-dialog").close());
  byId("rename-channel-dialog").addEventListener("cancel", event => {
    if (pending === "rename-channel") event.preventDefault();
  });
  byId("rename-channel-dialog").addEventListener("close", () => { renameSnapshot = null; updateControls(); });
  byId("rename-channel-form").addEventListener("submit", async event => {
    event.preventDefault();
    if (!canRenameChannel() || !renameSnapshotMatches()) return;
    const snapshot = renameSnapshot;
    pending = "rename-channel";
    mutationEpoch += 1;
    text("rename-channel-status", "Salvataggio in corso…");
    updateControls();
    try {
      const next = await api("channel", "PATCH", {
        name: byId("rename-channel-name").value.trim(), revision: snapshot.revision, instance_id: snapshot.instance_id
      }, snapshot.id);
      byId("rename-channel-dialog").close();
      applyState(next, { saved: true, sample: false });
      try { renderChannels(await api("channels")); }
      catch { channelSignature = ""; }
      setNotice("Nome del canale aggiornato.");
    } catch (error) {
      if (error.status === 409) {
        byId("rename-channel-dialog").close();
        try {
          const [fresh, channels] = await Promise.all([api("state", "GET", undefined, snapshot.id), api("channels")]);
          applyState(fresh, { saved: true, sample: false });
          renderChannels(channels);
          setNotice(error.message, "warning");
        } catch (refreshError) { markDisconnected(refreshError); }
      } else if (!error.status) markDisconnected(error);
      else { text("rename-channel-status", error.message); byId("rename-channel-name").focus(); }
    } finally { pending = null; updateControls(); }
  });
  byId("delete-channel").addEventListener("click", () => {
    if (!canDeleteChannel()) return;
    deleteSnapshot = { id: selectedChannel, revision: state.revision, instance_id: state.instance_id };
    text("delete-channel-name", state.config.name || selectedChannel);
    updateControls();
    byId("delete-channel-dialog").showModal();
  });
  byId("cancel-delete-channel").addEventListener("click", () => byId("delete-channel-dialog").close());
  byId("delete-channel-dialog").addEventListener("cancel", event => {
    if (pending === "delete-channel") event.preventDefault();
  });
  byId("delete-channel-dialog").addEventListener("close", () => { deleteSnapshot = null; updateControls(); });
  byId("confirm-delete-channel").addEventListener("click", async () => {
    if (!byId("delete-channel-dialog").open || !canDeleteChannel() || !deleteSnapshotMatches()) return;
    const snapshot = deleteSnapshot;
    pending = "delete-channel";
    mutationEpoch += 1;
    setNotice("");
    updateControls();
    let removed = false;
    let alreadyRemoved = false;
    let cleanupError = "";
    try {
      const channels = await api("channel", "DELETE", { revision: snapshot.revision, instance_id: snapshot.instance_id }, snapshot.id);
      removed = true;
      renderChannels(channels);
    } catch (error) {
      if (error.status === 404) { removed = true; alreadyRemoved = true; }
      else if (error.code === "channel_cleanup_failed") { removed = true; cleanupError = error.message; }
      else {
        setNotice(error.message, "error");
        if (error.status === 409) {
          try { applyState(await api("state", "GET", undefined, snapshot.id), { sample: false }); }
          catch (refreshError) {
            if (refreshError.status === 404) { removed = true; alreadyRemoved = true; }
            else markDisconnected(refreshError);
          }
        } else if (!error.status) markDisconnected(error);
      }
    } finally {
      byId("delete-channel-dialog").close();
      deleteSnapshot = null;
      pending = null;
      updateControls();
    }
    if (removed) {
      await selectChannel("main");
      if (cleanupError) setNotice(cleanupError, "error");
      else setNotice(alreadyRemoved ? "Il canale era già stato eliminato. Aperto il canale principale." : "Canale eliminato. Aperto il canale principale.", alreadyRemoved ? "warning" : "info");
    }
  });
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
    if (action === "probe") inputCheck = { revision: state.revision, instanceId: state.instance_id, error: null };
    else if (action === "start") inputCheck = null;
    mutationEpoch += 1;
    setNotice("");
    clearFieldErrors();
    updateControls();
    try {
      const next = await api(action, action === "config" ? "PUT" : "POST", payload);
      if (action === "apply" || action === "restore") applyingRevision = next.applying ? next.revision : null;
      applyState(next, { saved: ["config", "apply", "restore"].includes(action), sample: false });
      const confirmations = { config: "Configurazione salvata.", apply: next.apply_mode === "outputs" ? "Uscite aggiornate. La codifica continua." : next.applying ? "Configurazione salvata. Riavvio automatico in corso…" : "Configurazione salvata.", probe: "", start: "Avvio dell’encoder in corso…", stop: next.process_alive ? "Arresto dell’encoder in corso…" : "Encoder arrestato.", preview: "Anteprima in avvio…", "preview-stop": "Preview spenta." };
      setNotice(action === "restore" ? next.applying ? "Profilo precedente ripristinato. Riavvio automatico in corso…" : next.apply_mode === "outputs" ? "Uscite precedenti ripristinate. La codifica continua." : "Profilo precedente ripristinato; encoder fermo." : confirmations[action]);
    } catch (error) {
      if (action === "probe" && inputCheck) inputCheck.error = error.message;
      else setNotice(error.message, "error");
      markFieldErrors(error.fields);
      if (error.status === 409) {
        try { applyState(await api("state"), { sample: false }); } catch (refreshError) { markDisconnected(refreshError); }
      } else if (!error.status) markDisconnected(error);
    } finally {
      if (payload?.credentials) for (const key of Object.keys(payload.credentials)) payload.credentials[key] = "";
      if (payload?.output_credentials) for (const key of Object.keys(payload.output_credentials)) payload.output_credentials[key] = "";
      pending = null;
      updateControls();
    }
  }

  function prepareAction(action) {
    if (!state || !connected || pending) return;
    if (action === "stop") { byId("stop-dialog").showModal(); return; }
    if (dirty || !canConfigure() || conflict()) return;
    const payload = { revision: state.revision };
    if (action === "start" && state.recovery) payload.auto_restart = byId("auto-restart").value === "true";
    void runAction(action, payload);
  }

  form.addEventListener("input", (event) => {
    const outputCard = event.target.closest("[data-output-id]");
    if (outputCard && event.target.dataset.outputField === "passphrase") outputKeyTouched.add(outputCard);
    if (outputCard && ["protocol", "url", "srt_mode"].includes(event.target.dataset.outputField)) clearMovedOutputKey(outputCard);
    if (event.target.id === "config-input-protocol") return;
    if (inputUrlControlIds.includes(event.target.id)) syncInputUrl();
    if (event.target.id === "srt-passphrase") {
      passphraseTouched = true;
      passphraseGeneration += 1;
      passphraseRequestKey = null;
      passphraseRequestOrigin = null;
      passphraseDisplayOrigin = credentialOrigin(form.elements.namedItem("input_url").value);
    }
    if (!fields.includes(event.target.name) && !inputUrlControlIds.includes(event.target.id) && !event.target.hasAttribute("data-credential") && !event.target.closest("[data-output-id],[data-audio-track]")) return;
    clearFieldErrors();
    updateDraft();
  });
  form.addEventListener("change", (event) => {
    if (event.target.id === "config-input-protocol") {
      const previous = inputUrl.parse(byId("config-input-url").value)?.protocol;
      const protocol = event.target.value;
      if (previous !== protocol) {
        byId("config-input-options").value = "";
        byId("config-input-port").value = "";
        if (!(previous === "http" && protocol === "https") && !(previous === "https" && protocol === "http")) byId("config-input-path").value = "";
        byId("config-input-local-port").value = "";
        byId("config-input-srt-mode").value = protocol === "srt" ? "caller" : "";
        byId("config-input-srt-latency").value = protocol === "srt" ? "200" : "";
        byId("config-input-srt-rcvbuf").value = "";
        byId("config-input-srt-ffs").value = "";
      }
      syncInputUrl();
    } else if (event.target.id === "config-input-url") renderInputUrlFields(event.target.value);
    else if (inputUrlControlIds.includes(event.target.id)) {
      syncInputUrl();
      if (event.target.id === "config-input-options" && !inputUrl.reservedOptions(byId("config-input-protocol").value, event.target.value).length) {
        event.target.value = inputUrl.parse(byId("config-input-url").value)?.options ?? event.target.value;
      }
    }
    if (event.target.dataset.outputField === "protocol") {
      const url = outputField(event.target.closest("[data-output-id]"), "url");
      url.value = url.value.replace(/^(?:udp|rtp|srt):\/\//, `${event.target.value}://`);
    }
    const outputCard = event.target.closest("[data-output-id]");
    if (outputCard && ["protocol", "url", "srt_mode"].includes(event.target.dataset.outputField)) clearMovedOutputKey(outputCard);
    if (fields.includes(event.target.name) || inputUrlControlIds.includes(event.target.id) || event.target.hasAttribute("data-credential") || event.target.closest("[data-output-id],[data-audio-track]")) { clearFieldErrors(); updateDraft(); }
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!connected || pending || (!canConfigure() && !canApply()) || !dirty || conflict()) return;
    clearFieldErrors();
    const config = readDraft();
    if (!Object.hasOwn(state.config, "auto_restart")) {
      if (config.auto_restart !== baseConfig.auto_restart) {
        setNotice("Riavvia il servizio del pannello per salvare la modalità di recupero.", "error");
        return;
      }
      delete config.auto_restart;
    }
    if (!state.capabilities?.vapoursynth_editor) {
      delete config.vapoursynth_enabled;
      delete config.vapoursynth_script;
    }
    if (!Object.hasOwn(state.config, "aq_mode")) for (const name of qualityFields) delete config[name];
    if (!Object.hasOwn(state.config, "x264_speedcontrol")) for (const name of Object.keys(x264Defaults)) delete config[name];
    const credentials = readCredentialDraft();
    const outputCredentials = readOutputCredentials();
    const parsedInput = inputUrl.parse(config.input_url);
    if (config.input_type === "url" && !parsedInput) byId("config-input-url").setCustomValidity("Inserisci un URL di rete valido per il protocollo selezionato.");
    if (config.input_type === "url" && parsedInput?.protocol === "rtmp" && parsedInput.path.split("/").filter(Boolean).length < 2) {
      byId("config-input-path").setCustomValidity("Indica app e flusso, per esempio /live/stream.");
    }
    updateInputOptionsValidity();
    if (credentials.passphrase) {
      const byteLength = new TextEncoder().encode(credentials.passphrase).length;
      if (byteLength < 10 || byteLength > 79) byId("srt-passphrase").setCustomValidity("La passphrase SRT deve contenere da 10 a 79 byte.");
    }
    for (const card of outputCards()) {
      if (outputField(card, "protocol").value !== "srt") continue;
      const passphrase = outputField(card, "passphrase");
      if (passphrase.value) {
        const bytes = new TextEncoder().encode(passphrase.value).length;
        if (bytes < 10 || bytes > 79) passphrase.setCustomValidity("La passphrase SRT deve contenere da 10 a 79 byte.");
      }
      if (state?.capabilities?.outputs?.srt_options) {
        const bandwidth = outputField(card, "srt_max_bandwidth_kbps");
        const minimum = outputSrtParameters().srt_max_bandwidth_kbps.min;
        if (Number(bandwidth.value) > 0 && Number(bandwidth.value) < minimum)
          bandwidth.setCustomValidity(`Usa 0 (automatico) oppure almeno ${minimum} kb/s.`);
      }
    }
    const mux = form.elements.namedItem("mux_bitrate_kbps");
    if (config.mux_bitrate_kbps < (config.vbv_maxrate_kbps || config.video_bitrate_kbps) + config.audio_bitrate_kbps * enabledAudioCount(config) + 128) mux.setCustomValidity("Il multiplex deve coprire il massimo VBV + tutte le tracce audio abilitate + 128 kb/s.");
    if (config.bframes === 0 && config.b_adapt !== "auto") byId("config-b-adapt").setCustomValidity("Imposta almeno un B-frame per usare questa scelta.");
    if (config.aq_mode === "0" && config.aq_strength !== null) byId("config-aq-strength").setCustomValidity("Svuota il campo quando AQ è disattivata.");
    let firstInvalid = null;
    for (const element of form.elements) {
      if (!element.willValidate || element.validity.valid) continue;
      revealField(element, false);
      firstInvalid ||= element;
    }
    if (firstInvalid) revealField(firstInvalid);
    if (!form.reportValidity()) return;
    const restart = canApply();
    void runAction(restart ? "apply" : "config", { revision: draftRevision, ...(restart ? { instance_id: state.instance_id } : {}), config, ...(Object.keys(credentials).length ? { credentials } : {}), ...(Object.keys(outputCredentials).length ? { output_credentials: outputCredentials } : {}) });
  });
  byId("discard-button").addEventListener("click", () => {
    if (!state || pending) return;
    populateForm(state.config, state.revision);
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
  const livePlayer = new window.OBELivePreview();
  function renderPreview() {
    const slot = byId("preview-image");
    const preview = connected ? state?.preview : null;
    const status = preview?.status || "idle";
    const labels = { idle: state?.phase === "running" ? "Anteprima spenta" : "Disponibile durante la codifica", waiting: "In attesa dell’immagine…", stale: "Immagine non aggiornata", live: "Anteprima aggiornata", error: "Anteprima interrotta: verifica l’uscita e che la porta locale sia libera, poi riattivala." };
    text("preview-status", connected ? labels[status] || labels.idle : "Preview non disponibile: servizio non connesso");
    if (status === "live" && preview.mode === "video") {
      previewImageUrl = null;
      if (!window.OBELivePreview.supported()) {
        livePlayer.destroy(); slot.replaceChildren();
        text("preview-status", "Questo browser non supporta il monitor video: disattiva e riattiva per usare le immagini JPEG.");
        return;
      }
      text("preview-status", "Video live · anteprima ricodificata");
      livePlayer.show(slot, selectedChannel, preview, () => text("preview-status", "Riconnessione del monitor video…"));
      return;
    }
    livePlayer.destroy();
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
  byId("preview-toggle").addEventListener("click", () => {
    const stopping = Boolean(state?.preview?.pid);
    void runAction(stopping ? "preview-stop" : "preview", stopping ? {} : {mode: window.OBELivePreview.supported() ? "video" : "snapshot"});
  });
  window.addEventListener("pagehide", () => livePlayer.destroy());
  window.addEventListener("hashchange", setView);
  window.addEventListener("beforeunload", (event) => {
    if (dirty) { event.preventDefault(); event.returnValue = ""; }
  });
  window.addEventListener("pagehide", () => {
    clearCredentialInputs();
    clearOutputKeys();
  });
  setView();
  updateControls();
  void initializeSession();
})();
