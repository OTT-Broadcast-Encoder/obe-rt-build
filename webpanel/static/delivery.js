/* Receiver supervision is independent of encoder configuration and lifecycle. */
(() => {
  "use strict";
  let api, current, instance, dirty = false, modeDirty = false, busy = false, connected = false;
  const byId = id => document.getElementById(id);
  const text = (id, value) => { byId(id).textContent = value; };
  const labels = { live: "Ricezione verificata", degraded: "Ricezione degradata", unavailable: "Non disponibile", disabled: "Non configurato" };
  const number = value => Number.isFinite(value) ? new Intl.NumberFormat("it-IT", { maximumFractionDigits: 1 }).format(value) : "—";

  function initialize(request) {
    api = request;
    const settings = byId("delivery-settings");
    for (const event of ["input", "change"]) settings.addEventListener(event, e => {
      e.stopPropagation();
      if (e.target.id === "delivery-mode") { modeDirty = true; return; }
      dirty = true;
      text("delivery-message", "Modifiche non salvate");
    });
    byId("delivery-save").addEventListener("click", async () => {
      if (busy || !current || !connected) return;
      const config = { enabled: byId("delivery-enabled").checked,
        source_url: byId("delivery-source").value.trim(), receiver_url: byId("delivery-receiver").value.trim() };
      const tokens = {};
      for (const role of ["source", "receiver"]) {
        const field = byId(`delivery-${role}-token`);
        if (field.value) tokens[role] = field.value;
      }
      await perform("delivery", "PUT", { revision: current.revision, config, tokens });
    });
    byId("delivery-mode-apply").addEventListener("click", async () => {
      if (busy || !connected || !current?.selector || dirty) return;
      await perform("delivery-control", "POST", { revision: current.revision, mode: byId("delivery-mode").value });
    });
  }

  async function perform(path, method, payload) {
    const context = instance;
    busy = true;
    updateControls();
    text("delivery-message", "Applicazione…");
    try {
      const data = await api(path, method, { ...payload, instance_id: context });
      if (context !== instance) return;
      dirty = modeDirty = false;
      for (const role of ["source", "receiver"]) byId(`delivery-${role}-token`).value = "";
      render(data, context, true, true);
      text("delivery-message", path === "delivery" ? "Monitoraggio salvato" : "Modalità applicata");
    } catch (error) {
      if (context === instance) text("delivery-message", error.message);
    } finally {
      busy = false;
      updateControls();
    }
  }

  function updateControls() {
    byId("delivery-save").disabled = !connected || busy;
    byId("delivery-mode-apply").disabled = !connected || busy || dirty || !current?.selector || current.status !== "live";
  }

  function render(data, id, online, saved = false) {
    if (!byId("delivery-settings")) return;
    const changed = id !== instance;
    if (changed) {
      dirty = modeDirty = busy = false;
      for (const role of ["source", "receiver"]) byId(`delivery-${role}-token`).value = "";
      text("delivery-message", "");
    }
    instance = id;
    const revisionChanged = current?.revision !== data?.revision;
    current = data;
    connected = online && Boolean(data);
    if (data && !dirty && (changed || saved || revisionChanged)) {
      byId("delivery-enabled").checked = data.config.enabled;
      for (const role of ["source", "receiver"]) byId(`delivery-${role}`).value = data.config[`${role}_url`];
    }
    for (const role of ["source", "receiver"]) {
      byId(`delivery-${role}-token`).placeholder = data?.tokens?.[role] ? "Token salvato" : "Token osservatore";
    }
    const live = connected && data.status === "live";
    const receiver = live ? data.receiver : null;
    const latency = live ? data.latency : null;
    const selector = live ? data.selector : null;
    text("delivery-status", connected && !data.config.enabled ? "Non configurato" : labels[receiver?.status] || "Non disponibile");
    byId("delivery-status").className = `status-tag ${receiver?.status === "live" ? "good" : data?.config?.enabled ? "warning" : ""}`;
    text("delivery-rate", number(receiver?.bitrate_kbps));
    text("delivery-errors", number(receiver?.continuity_errors));
    text("delivery-latency", latency?.status === "measured" ? `${number(latency.latency_ms)} ± ${number(latency.uncertainty_ms)} ms` : "—");
    text("delivery-latency-detail", latency?.reason || "Misura non disponibile");
    text("delivery-percentiles", latency?.status === "measured" ? `P50 ${number(latency.p50_ms)} ms · P95 ${number(latency.p95_ms)} ms · ${latency.samples} misure` : "");
    text("delivery-active", selector?.active?.toUpperCase() || "—");
    text("delivery-switches", selector ? `${selector.switches} commutazioni · ${selector.mode === "auto" ? "Automatico" : selector.mode.toUpperCase()}` : "Selettore non configurato");
    byId("delivery-selector").hidden = !selector;
    if (selector && !modeDirty && document.activeElement !== byId("delivery-mode")) byId("delivery-mode").value = selector.mode;
    byId("delivery-branches").replaceChildren(...Object.entries(live ? data.streams || {} : {}).map(([name, stream]) => {
      const row = document.createElement("tr");
      for (const value of [name === "return" ? "Ritorno" : name.toUpperCase(), labels[stream.status], number(stream.bitrate_kbps), number(stream.continuity_errors), number(stream.frame_age_seconds)]) {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.append(cell);
      }
      return row;
    }));
    if (!dirty && data?.error) text("delivery-message", data.error);
    updateControls();
  }

  window.OBEDelivery = { initialize, render };
})();
