"use strict";

// One bounded MSE player per visible channel. The encoder never waits for it.
window.OBELivePreview = class {
  static supported() {
    return Boolean(window.MediaSource?.isTypeSupported('video/mp4; codecs="avc1.42C01F, mp4a.40.2"'));
  }
  constructor() { this.session = null; this.controller = null; this.video = null; this.url = null; }
  destroy() {
    this.controller?.abort();
    this.controller = null;
    this.session = null;
    if (this.video) { this.video.pause(); this.video.removeAttribute("src"); this.video.load(); }
    this.video = null;
    if (this.url) URL.revokeObjectURL(this.url);
    this.url = null;
  }
  show(slot, channel, state, failed) {
    const session = `${channel}:${state.session}`;
    if (session === this.session) return;
    this.destroy();
    this.session = session;
    this.controller = new AbortController();
    const signal = this.controller.signal;
    const media = new MediaSource();
    const video = this.video = document.createElement("video");
    video.controls = true; video.muted = true; video.autoplay = true; video.playsInline = true;
    video.setAttribute("aria-label", "Anteprima video e audio dell’uscita; audio inizialmente disattivato");
    this.url = URL.createObjectURL(media);
    video.src = this.url;
    slot.replaceChildren(video);
    media.addEventListener("sourceopen", () => {
      void this.play(media, video, channel, state, signal).catch(() => {
        if (!signal.aborted && this.session === session) {
          this.destroy(); slot.replaceChildren(); failed();
        }
      });
    }, { once: true });
  }
  async play(media, video, channel, state, signal) {
    if (signal.aborted) return;
    const buffer = media.addSourceBuffer(state.mime);
    const base = `/api/channels/${encodeURIComponent(channel)}/preview-`;
    const query = `?session=${encodeURIComponent(state.session)}`;
    const fetchMedia = async path => {
      const response = await fetch(path, { signal, cache: "no-store" });
      if (response.status === 204) return null;
      if (!response.ok) throw new Error("Preview unavailable");
      return response.arrayBuffer();
    };
    const change = operation => new Promise((resolve, reject) => {
      if (signal.aborted || media.readyState !== "open") { reject(new Error("Preview closed")); return; }
      const cleanup = () => {
        buffer.removeEventListener("updateend", done); buffer.removeEventListener("error", error);
        signal.removeEventListener("abort", error);
      };
      const done = () => { cleanup(); resolve(); };
      const error = () => { cleanup(); reject(new Error("Preview closed")); };
      buffer.addEventListener("updateend", done, { once: true });
      buffer.addEventListener("error", error, { once: true }); signal.addEventListener("abort", error, { once: true });
      try { operation(); } catch (failure) { cleanup(); reject(failure); }
    });
    const init = await fetchMedia(`${base}init${query}`);
    if (!init) throw new Error("Preview initialization missing");
    await change(() => buffer.appendBuffer(init));
    let sequence = state.first_sequence;
    let started = false;
    while (!signal.aborted) {
      const chunk = await fetchMedia(`${base}segment${query}&sequence=${sequence}`);
      if (chunk) {
        await change(() => buffer.appendBuffer(chunk));
        sequence += 1;
        if (buffer.buffered.length) {
          const start = buffer.buffered.start(0), end = buffer.buffered.end(buffer.buffered.length - 1);
          if (video.currentTime < start || end - video.currentTime > 2) video.currentTime = Math.max(start, end - 0.8);
          if (end - start > 8) await change(() => buffer.remove(start, end - 6));
        }
        if (!started) { started = true; void video.play().catch(() => {}); }
      } else {
        await new Promise(resolve => {
          const finish = () => { clearTimeout(timer); signal.removeEventListener("abort", finish); resolve(); };
          const timer = setTimeout(finish, 150);
          signal.addEventListener("abort", finish, { once: true });
        });
      }
    }
  }
};
