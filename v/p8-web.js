// PRO-8000 V in a browser (scripts/build_web.sh): what the app's native side does, here in the page. Loaded first, before
// ui/p8-shim.js and the firmware's editor page:
//   - a start screen (a browser starts audio only from a click), then the engine (p8v.wasm in worklet.js);
//   - window.p8_send / window.p8_call as the app's bridge has them (ui/editor_view.cpp), so the shim, ui/p8-app.js and
//     ui/p8-fx.js work unchanged: the editor's SysEx to the engine and back, banks, the effects, the mode;
//   - the library in this browser's storage (IndexedDB): banks of 128 slots in the synth's format, the effects per
//     slot beside them, the firmware's system area; the presets' bank on a first visit;
//   - the MIDI keyboards on this computer (WebMIDI) play it;
//   - With PRO 800: the editor talks to the synth itself over WebMIDI (as the firmware's own editor does) and the
//     engine is silent. Nothing is mirrored or copied: in the browser that's the synth's own editor.
(() => {
  "use strict";
  window.__p8_web = true;
  const realMIDI = navigator.requestMIDIAccess ? navigator.requestMIDIAccess.bind(navigator) : null; // before the shim
  const HDR = [0xF0, 0x00, 0x20, 0x32, 0x00, 0x01, 0x24, 0x00];
  const isOurs = m => m.length > HDR.length + 1 && HDR.every((b, i) => m[i] === b) && m[m.length - 1] === 0xF7;
  const PATCH_AREA = 98304, EEPROM = 131072, RECORD = 768;
  const PRESETS_NAME = "PRO-8000 presets";

  // --- storage: IndexedDB (banks are 96 kB each), settings in localStorage ---
  const db = new Promise((resolve, reject) => {
    const r = indexedDB.open("pro8000v", 1);
    r.onupgradeneeded = () => { r.result.createObjectStore("banks"); r.result.createObjectStore("misc"); };
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
  const idb = (store, mode, fn) => db.then(d => new Promise((resolve, reject) => {
    const tx = d.transaction(store, mode), st = tx.objectStore(store), req = fn(st);
    tx.oncomplete = () => resolve(req && req.result);
    tx.onerror = () => reject(tx.error);
  }));
  const getBank = n => idb("banks", "readonly", s => s.get(n));
  const putBank = (n, b) => idb("banks", "readwrite", s => s.put(b, n));
  const bankKeys = () => idb("banks", "readonly", s => s.getAllKeys());
  const pref = (k, v) => {
    try { if (v === undefined) return JSON.parse(localStorage.getItem("p8v." + k)); localStorage.setItem("p8v." + k, JSON.stringify(v)); } catch (e) { return null; }
  };
  const recordIsPatch = r => r[0] === 0x50 && r[1] === 0x38 && r[2] === 0x50 && r[3] === 0x32;
  const recordName = r => {
    if (!recordIsPatch(r)) return "";
    let s = "";
    for (let i = 4; i < 20 && r[i]; i++) s += String.fromCharCode(r[i] >= 0x20 && r[i] < 0x7F ? r[i] : 32);
    return s;
  };

  // --- state ---
  const st = {
    node: null, ctx: null, gain: null, version: "", started: false, error: "",
    bank: 0, slot: 0, banks: new Map(), // n -> { name, area: Uint8Array, fx: {slot: values[]}, cats?: {slot: group} }
    mode: pref("mode") === "device" ? "device" : "standalone",
    timing: pref("timing") || 0,
    midiAccess: null, midiOff: new Set(pref("midiOff") || []),
    fxInfo: null, peak: 0,
  };
  const waiting = new Map();
  let nextId = 1;
  const ask = (msg) => new Promise(resolve => { const id = nextId++; waiting.set(id, resolve); st.node.port.postMessage({ ...msg, id }); });
  const post = msg => st.node && st.node.port.postMessage(msg);
  const listeners = new Set();
  const changed = what => listeners.forEach(fn => { try { fn(what); } catch (e) { console.error(e); } });

  // --- the effects, per slot, as the plugin keeps them (Engine::applySlotFx / saveSlotFx) ---
  const applySlotFx = (slot) => {
    const b = st.banks.get(st.bank), v = b && b.fx && b.fx[slot];
    if (v) post({ t: "fxSet", values: v }); else post({ t: "fxDefaults" });
  };
  const saveSlotFx = async (slot) => {
    const r = await ask({ t: "fxValues" });
    const b = st.banks.get(st.bank);
    if (!b) return;
    b.fx = b.fx || {};
    const defaults = st.fxDefaults;
    if (defaults && r.values.every((v, i) => Math.abs(v - defaults[i]) < 1e-6)) delete b.fx[slot]; else b.fx[slot] = r.values;
    putBank(st.bank, b);
  };

  // --- the library ---
  async function loadLibrary() {
    for (const n of await bankKeys()) st.banks.set(n, await getBank(n));
    if (!st.banks.size) { // a first visit: the presets in bank 0
      const [area, meta] = await Promise.all([
        fetch("presets.p8bank").then(r => r.ok ? r.arrayBuffer() : null),
        fetch("presets-meta.json").then(r => r.ok ? r.json() : null).catch(() => null),
      ]);
      const cats = {};
      if (meta) for (const s in meta.slots) cats[s - 1] = meta.slots[s].group;
      const b = { name: PRESETS_NAME, area: new Uint8Array(area || PATCH_AREA), fx: {}, cats };
      st.banks.set(0, b);
      await putBank(0, b);
    }
    st.bank = Math.max(0, pref("bank") || 0);
    if (!st.banks.has(st.bank)) st.bank = Math.min(...st.banks.keys());
  }
  const bankArea = n => (st.banks.get(n) || {}).area || new Uint8Array(PATCH_AREA);
  async function selectBank(n, slot = -1) {
    n = Math.max(0, Math.min(16383, n | 0));
    if (!st.banks.has(n)) st.banks.set(n, { name: "", area: new Uint8Array(PATCH_AREA), fx: {} });
    st.bank = n;
    pref("bank", n);
    st.slot = slot < 0 ? st.slot : slot;
    post({ t: "bank", area: bankArea(n).slice().buffer, slot: st.slot });
    applySlotFx(st.slot);
    changed("bank");
    return true;
  }

  // --- the engine ---
  async function start() {
    await loadLibrary();
    const sys = await idb("misc", "readonly", s => s.get("system"));
    const image = new Uint8Array(EEPROM);
    image.set(bankArea(st.bank));
    if (sys) image.set(new Uint8Array(sys), PATCH_AREA);
    const ctx = new AudioContext({ latencyHint: "interactive" });
    const [wasm] = await Promise.all([
      fetch("p8v.wasm").then(r => { if (!r.ok) throw new Error("p8v.wasm: HTTP " + r.status); return r.arrayBuffer(); }),
      ctx.audioWorklet.addModule("worklet.js"),
    ]);
    const node = new AudioWorkletNode(ctx, "pro8000v", { numberOfInputs: 0, outputChannelCount: [2],
      processorOptions: { wasm, image: image.buffer, formatted: !!sys, ideal: st.timing === 1 } });
    const ready = await new Promise(resolve => { node.port.onmessage = e => { if (e.data.t === "ready") resolve(e.data); }; });
    if (!ready.ok) throw new Error(ready.error || "the engine didn't start");
    st.version = ready.version;
    st.ctx = ctx;
    st.node = node;
    st.gain = ctx.createGain();
    node.connect(st.gain).connect(ctx.destination);
    node.port.onmessage = e => onEngine(e.data);
    if (ctx.state !== "running") await ctx.resume();
    st.fxInfo = await ask({ t: "fxInfo" }).then(r => r.info);
    st.fxDefaults = (await ask({ t: "fxValues" })).values;
    if (st.slot) post({ t: "bank", area: bankArea(st.bank).slice().buffer, slot: st.slot });
    applySlotFx(0);
    st.started = true;
    openKeyboards();
    setInterval(saveSystem, 10000);
    document.addEventListener("visibilitychange", () => { if (document.hidden) saveSystem(); });
    changed("started");
  }
  async function saveSystem() {
    if (!st.node) return;
    const r = await ask({ t: "eeprom" });
    await idb("misc", "readwrite", s => s.put(r.image.slice(PATCH_AREA).buffer, "system"));
  }
  function onEngine(d) {
    if (d.t === "midi") { if (st.mode !== "device" && window.__p8_receive) window.__p8_receive(d.list); }
    else if (d.t === "saved") { // the firmware wrote slots: into the bank
      const b = st.banks.get(st.bank);
      if (!b) return;
      d.slots.forEach((s, k) => b.area.set(d.records[k], s * RECORD));
      putBank(st.bank, b);
      changed("library");
    } else if (d.t === "slot") { if (d.slot !== st.slot) { st.slot = d.slot; applySlotFx(d.slot); changed("slot"); } }
    else if (d.t === "peak") st.peak = d.v;
    else if (d.t === "stopped") { st.error = d.error; changed("stopped"); }
    else if (waiting.has(d.id)) { const fn = waiting.get(d.id); waiting.delete(d.id); fn(d); }
  }

  // --- MIDI keyboards (WebMIDI, no SysEx needed) ---
  function openKeyboards() {
    if (!realMIDI) return;
    realMIDI({ sysex: false }).then(access => {
      st.midiAccess = access;
      const wire = () => {
        for (const input of access.inputs.values()) {
          const off = st.midiOff.has(input.name) || /PRO 800/.test(input.name || "");
          input.onmidimessage = off ? null : e => {
            const m = e.data;
            if (st.mode === "device" || m[0] >= 0xF0) return; // clock and SysEx aren't for the engine
            post({ t: "send", m: Array.from(m) });
          };
        }
        changed("midi");
      };
      access.onstatechange = wire;
      wire();
    }).catch(() => {});
  }

  // --- With PRO 800: the editor on the synth's own port; the engine silent ---
  window.__p8_realAccess = () => (st.mode === "device" && realMIDI ? realMIDI({ sysex: true }) : null);
  function setMode(m) {
    st.mode = m === "device" ? "device" : "standalone";
    pref("mode", st.mode);
    if (st.mode === "device") st.deviceAsked = Date.now();
    if (window.__p8_reconnect) window.__p8_reconnect();
    setTimeout(() => { if (typeof connect === "function") connect(); }, 400);
  }
  // Linked: the page's port is a real one and the page's own check found PRO-8000 on it
  const linked = () => {
    try { return st.mode === "device" && !!output && output.id !== "p8-output" && /^Connected|^PRO 800 linked/.test(document.getElementById("status").textContent); } catch (e) { return false; }
  };

  // --- the bridge the shim, p8-app.js and p8-fx.js talk to ---
  window.p8_send = bytes => {
    if (!st.node) return;
    const m = Array.from(bytes);
    if (isOurs(m)) { // the effects go with the library's slot (Engine::uiSend)
      const c = m[HDR.length], n = m[HDR.length + 1] << 7 | m[HDR.length + 2];
      if (m.length === HDR.length + 4 && c === 0x5D) saveSlotFx(n);
      if (m.length === HDR.length + 4 && c === 0x4F) { const b = st.banks.get(st.bank); if (b && b.fx) { delete b.fx[n]; putBank(st.bank, b); } }
      if (m.length === HDR.length + 2 && c === 0x5B) { const b = st.banks.get(st.bank); if (b) { b.fx = {}; putBank(st.bank, b); } }
      if (m.length === HDR.length + 2 && c === 0x5F) post({ t: "fxDefaults" });
    }
    post({ t: "send", m });
  };
  const calls = {
    status() {
      const b = st.banks.get(st.bank) || {};
      if (st.gain) st.gain.gain.value = linked() ? 0 : 1; // silent only while the synth itself is there
      const deviceTrying = st.mode === "device" && !linked() && Date.now() - (st.deviceAsked || 0) < 4000;
      return { bank: st.bank, bankName: b.name || "", slot: st.slot, timing: st.timing, sound: "synth", mode: st.mode, hostClock: false,
        standalone: true, web: true, firmware: st.version, version: window.P8V_VERSION || "", library: "this browser's storage",
        link: { connected: linked(), busy: deviceTrying, progress: 0,
          message: st.mode !== "device" ? "" : !realMIDI ? "This browser has no WebMIDI (use Chrome or Edge)"
            : linked() ? "The editor is on the PRO 800; this browser is silent" : deviceTrying ? "Looking for the PRO 800..."
              : (document.getElementById("status").textContent || "No PRO 800 running PRO-8000 answered") } };
    },
    selectBank: (n) => selectBank(n),
    renameBank(n, name) { const b = st.banks.get(n); if (b) { b.name = String(name || "").slice(0, 64); putBank(n, b); changed("library"); } return true; },
    newBank() { return Math.min(16383, Math.max(-1, ...st.banks.keys()) + 1); },
    banks: () => [...st.banks.keys()].sort((a, b) => a - b).map(n => ({ bank: n, name: st.banks.get(n).name })),
    slotNames(n) { const a = bankArea(n == null ? st.bank : n); return Array.from({ length: 128 }, (_, s) => recordName(a.subarray(s * RECORD, (s + 1) * RECORD))); },
    setTiming(ideal) { st.timing = ideal ? 1 : 0; pref("timing", st.timing); post({ t: "timing", ideal: !!ideal }); },
    setHostClock() {}, setSound() {}, sendSound() {}, openLibraryFolder() {},
    setMode: (m) => setMode(m),
    fxInfo: () => st.fxInfo && { params: JSON.stringify(st.fxInfo.params), keys: st.fxInfo.keys, presets: st.fxInfo.presets },
    fxValues: () => ask({ t: "fxValues" }).then(r => ({ values: r.values, active: r.active })),
    fxSet(i, v) { post({ t: "fx", i, v }); },
    fxPreset(k) { post({ t: "fxPreset", k }); },
    openUrl(url) { if (/^https?:/.test(url)) window.open(url, "_blank", "noopener"); },
    audioInfo() {
      const inputs = st.midiAccess ? [...st.midiAccess.inputs.values()].filter(i => !/PRO 800/.test(i.name || "")) : [];
      return { web: true, devices: [], device: "", rate: st.ctx ? st.ctx.sampleRate : 0, buffer: 128, running: !!st.ctx && st.ctx.state === "running",
        actualDevice: "this browser's audio output", actualRate: st.ctx ? st.ctx.sampleRate : 0,
        actualBuffer: st.ctx ? Math.round((st.ctx.baseLatency || 128 / st.ctx.sampleRate) * st.ctx.sampleRate) : 128,
        error: st.ctx ? "" : "not started", midi: inputs.map(i => ({ name: i.name, on: !st.midiOff.has(i.name) })),
        midiNote: realMIDI ? "" : "This browser has no WebMIDI: play from the Play tab (Chrome or Edge for MIDI keyboards)" };
    },
    setMidiInput(name, on) { if (on) st.midiOff.delete(name); else st.midiOff.add(name); pref("midiOff", [...st.midiOff]); if (st.midiAccess) st.midiAccess.onstatechange(); },
    // for the patch browser (p8-browser.js)
    library: () => ({ bank: st.bank, slot: st.slot, banks: [...st.banks.keys()].sort((a, b) => a - b).map(n => {
      const b = st.banks.get(n);
      return { bank: n, name: b.name, cats: b.cats || null, names: calls.slotNames(n) };
    }) }),
    capture: (name) => ask({ t: "capture", name: name || "" }).then(r => r.record),
    loadRecord(record, slot) { post({ t: "load", record, slot }); },
  };
  window.p8_call = (name, ...args) => {
    if (!st.started) return name === "status" || !calls[name] ? Promise.resolve(null) : whenStarted.then(() => window.p8_call(name, ...args));
    try { return Promise.resolve(calls[name] ? calls[name](...args) : null); } catch (e) { return Promise.reject(e); }
  };
  window.p8_web = { onChange: fn => { listeners.add(fn); return () => listeners.delete(fn); }, state: st };
  let started;
  const whenStarted = new Promise(r => { started = r; });

  // --- the start screen: audio starts from a click ---
  function startScreen() {
    const card = document.createElement("div");
    card.id = "p8-start";
    card.innerHTML = `<div class="p8-start-card">
      <div class="p8-start-logo">PRO·8000 <span>V</span></div>
      <p>The PRO-8000 firmware for the Behringer PRO-800, running in this browser on a model of the synth's voice board.
        Play it from a MIDI keyboard on this computer or from the Play tab; it keeps your patches in this browser.</p>
      <button type="button" id="p8-start-button">Start</button>
      <p class="p8-start-small">Chrome or Edge for MIDI keyboards and for editing a PRO 800 (With PRO 800). Experimental and unofficial; not affiliated with Behringer or Music Tribe.</p>
      <p class="p8-start-error" id="p8-start-error"></p></div>`;
    document.documentElement.append(card);
    const go = async () => {
      const b = document.getElementById("p8-start-button");
      b.disabled = true; b.textContent = "Starting...";
      try {
        await start();
        card.remove();
        started();
        // the editor tried its port before there was an engine: once that attempt is over, it connects
        const tryConnect = () => {
          try {
            if (connecting) { setTimeout(tryConnect, 250); return; } // (it holds a port while it asks)
            if (output) return;
            connect();
          } catch (e) { setTimeout(tryConnect, 250); }
        };
        tryConnect();
      } catch (err) {
        document.getElementById("p8-start-error").textContent = "It didn't start: " + (err && err.message || err);
        b.disabled = false; b.textContent = "Try again";
      }
    };
    document.getElementById("p8-start-button").onclick = go;
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", startScreen); else startScreen();
})();
