// Loaded before the firmware's editor page (firmware/tools/web/index.html): gives it a WebMIDI with one port called
// "PRO 800", which is the plugin's engine. Its SysEx reaches the plugin's own firmware, or the synth while linked, and
// the replies come back here (window.__p8_receive). The page itself is the firmware repository's, unchanged.
// It also makes the page behave as an app's window rather than a browser tab: no context menu, zoom, reload or
// find keys, no text selection outside fields (ui/p8-app.css), a dropped file doesn't replace the page, links open
// in the computer's browser, and a download goes to the library's exports folder.
(() => {
  "use strict";
  const inputListeners = new Set(), stateListeners = new Set();
  let onMessage = null;
  const makePort = (type, extra) => Object.assign({
    id: "p8-" + type, name: "PRO 800", manufacturer: "PRO-8000 V", version: "1", type,
    state: "connected", connection: "open",
    open() { return Promise.resolve(this); },
    close() { return Promise.resolve(this); },
    addEventListener(kind, fn) { if (kind === "midimessage") inputListeners.add(fn); if (kind === "statechange") stateListeners.add(fn); },
    removeEventListener(kind, fn) { inputListeners.delete(fn); stateListeners.delete(fn); },
  }, extra);
  const input = makePort("input", {});
  Object.defineProperty(input, "onmidimessage", { get: () => onMessage, set: f => { onMessage = f; } });
  const output = makePort("output", {
    send(data) { if (window.p8_send) window.p8_send(Array.from(data)); },
    clear() {},
  });
  const access = {
    inputs: new Map([[input.id, input]]),
    outputs: new Map([[output.id, output]]),
    sysexEnabled: true,
    onstatechange: null,
    addEventListener(kind, fn) { if (kind === "statechange") stateListeners.add(fn); },
    removeEventListener(kind, fn) { stateListeners.delete(fn); },
  };
  // The browser build (web/p8-web.js) gives the real access in its With PRO 800 mode: the editor on the synth itself
  Object.defineProperty(navigator, "requestMIDIAccess", { configurable: true,
    value: () => (window.__p8_realAccess && window.__p8_realAccess()) || Promise.resolve(access) });

  window.__p8_receive = messages => {
    for (const bytes of messages) {
      const event = { data: new Uint8Array(bytes), timeStamp: performance.now(), target: input };
      if (onMessage) onMessage(event);
      for (const fn of inputListeners) fn(event);
    }
  };
  // The page reconnects as it does when the synth is unplugged and plugged back (after linking or unlinking)
  window.__p8_reconnect = () => {
    const fire = state => {
      for (const p of [input, output]) {
        p.state = state;
        const event = { port: p };
        if (access.onstatechange) access.onstatechange(event);
        for (const fn of stateListeners) fn(event);
      }
    };
    fire("disconnected");
    setTimeout(() => fire("connected"), 300);
  };

  // A custom scheme may have no localStorage: the page keeps its panel map and choices in it
  try {
    localStorage.setItem("__p8", "1");
    localStorage.removeItem("__p8");
  } catch (e) {
    const data = new Map();
    const store = {
      getItem: k => (data.has(String(k)) ? data.get(String(k)) : null),
      setItem: (k, v) => data.set(String(k), String(v)),
      removeItem: k => data.delete(String(k)),
      clear: () => data.clear(),
      key: i => Array.from(data.keys())[i] ?? null,
      get length() { return data.size; },
    };
    Object.defineProperty(window, "localStorage", { configurable: true, value: store });
  }

  // --- an app's window, not a browser tab (in the browser build a web page keeps its browser's ways) ---
  if (window.__p8_web) return;
  const inField = t => !!t && t.nodeType === 1 && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable);
  document.addEventListener("contextmenu", e => { if (!inField(e.target)) e.preventDefault(); });
  document.addEventListener("wheel", e => { if (e.ctrlKey || e.metaKey) e.preventDefault(); }, { passive: false }); // zoom
  document.addEventListener("keydown", e => {
    const mod = e.ctrlKey || e.metaKey, k = e.key;
    if (["F3", "F5", "F6", "F7", "F11", "F12"].includes(k) // find, reload, the address bar, caret browsing, full screen, the tools
        || (mod && k.length === 1 && "rRfFpPsSoOuUjJhHdDgGeEtTnNlLwW+-=0".includes(k)) // reload, find, print, save, open, source, downloads, history, bookmarks, zoom...
        || (mod && e.shiftKey && k.length === 1 && "iIjJcC".includes(k)) // the developer tools
        || (e.altKey && (k === "ArrowLeft" || k === "ArrowRight") && !inField(e.target)) // back, forward
        || (k === "Backspace" && !inField(e.target)))
      e.preventDefault();
  }, true);
  for (const t of ["dragover", "drop"]) document.addEventListener(t, e => { if (!inField(e.target)) e.preventDefault(); }); // a dropped file would replace the page
  // A download (the Patches tab's exports and backups, the panel map) goes to the library's exports folder; the
  // app says where (a "p8-saved" event, ui/p8-app.js shows it). The page downloads through a made <a download>.
  const save = (name, href) => fetch(href).then(r => r.arrayBuffer()).then(buf => {
    const bytes = new Uint8Array(buf);
    let s = "";
    for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return window.p8_call("saveExport", name, btoa(s));
  }).then(where => window.dispatchEvent(new CustomEvent("p8-saved", { detail: { name, where: where || "" } })))
    .catch(() => window.dispatchEvent(new CustomEvent("p8-saved", { detail: { name, where: "" } })));
  const anchorClick = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function () {
    if (this.hasAttribute("download") && window.p8_call) return void save(this.getAttribute("download") || "export", this.href);
    return anchorClick.call(this);
  };
  document.addEventListener("click", e => {
    const a = e.target && e.target.closest ? e.target.closest("a[href]") : null;
    if (!a) return;
    if (a.hasAttribute("download") && window.p8_call) { e.preventDefault(); save(a.getAttribute("download") || "export", a.href); }
    else if (/^https?:/i.test(a.href)) { e.preventDefault(); if (window.p8_call) window.p8_call("openUrl", a.href); }
  }, true);
})();
