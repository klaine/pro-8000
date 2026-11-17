// PRO-8000 V's own controls, put into the firmware's editor page where the page's own stood, so it is one app window
// and not a page with a toolbar over it:
//   - in the header, where the page's Connect button was: the mode, Standalone (the built-in PRO-8000 plays) or
//     With PRO 800 (linked to the synth: its memory is a bank of its own, the editor edits the synth, the synth plays),
//     what the link is doing, and the page's own status line, reworded to say what it's connected to;
//   - at the right of the tab row, where the website's links were: the library's banks (no limit: the firmware sees
//     one bank of 128 at a time), or, while linked, where the sound comes from and "Send this sound to P128" (two
//     presses: it overwrites P128);
//   - a PRO-8000 V section at the top of the System tab: the version, the library's folder, the CV timing, the clock.
// What is only about a synth on a USB port is hidden: the Connect button and the website's links (ui/p8-app.css),
// the panel mapping, MIDI routing between ports, and standalone also autotune and the factory bank. What the
// Patches tab exports lands in the library's exports folder (ui/p8-shim.js hands it over).
(() => {
  "use strict";
  const call = (name, ...args) => (window.p8_call ? window.p8_call(name, ...args) : Promise.resolve(null));
  const $ = id => document.getElementById(id);
  const el = (tag, attrs, ...kids) => {
    const e = document.createElement(tag);
    for (const k in attrs || {}) {
      const v = attrs[k];
      if (v === undefined || v === null || v === false) continue;
      if (k === "class") e.className = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids) if (kid !== null && kid !== undefined) e.append(kid);
    return e;
  };
  let status = null, linked = false, linkedAt = 0, busy = false, editingName = false, armed = false, armTimer = 0, asked = 0, bridged = false;

  // --- the header: the mode where the page's Connect button was, the page's status line under it ---
  const line = $("status"), conn = line.parentElement;
  conn.classList.add("p8-conn");
  const modeStandalone = el("button", { type: "button", id: "p8-mode-standalone",
    title: "The built-in PRO-8000 plays on its own: from MIDI keyboards on this computer (in a DAW, from the track) and from the Play tab. Nothing is sent to a synth." }, "Standalone");
  const modeDevice = el("button", { type: "button", id: "p8-mode-device",
    title: "Link to a PRO 800 running PRO-8000 over USB: its memory is copied into a bank of its own, the editor edits the synth, and the synth plays" }, "With PRO 800");
  const dot = el("span", { id: "p8-link-state", class: "p8-dot", hidden: true });
  const linkMsg = el("div", { id: "p8-link-msg", hidden: true });
  conn.prepend(el("div", { class: "p8-mode", role: "group", "aria-label": "Mode" }, modeStandalone, modeDevice, dot), linkMsg);

  // --- the tab row's right side: the banks, or the synth's controls while linked ---
  const bankNumber = el("input", { id: "p8-bank", type: "number", min: 0, max: 16383, value: 0,
    title: "The library's bank: 128 slots each, as many banks as wanted (bank select picks one too)" });
  const bankName = el("input", { id: "p8-bank-name", type: "text", placeholder: "bank name", maxlength: 64 });
  const bankPrev = el("button", { type: "button", id: "p8-bank-prev", title: "Previous bank" }, "◀");
  const bankNext = el("button", { type: "button", id: "p8-bank-next", title: "Next bank" }, "▶");
  const bankNew = el("button", { type: "button", id: "p8-bank-new", title: "A new bank: the next number nobody uses" }, "New");
  const banks = el("span", { class: "p8-banks" }, el("span", { class: "p8-label" }, "Bank"), bankPrev, bankNumber, bankNext, bankName, bankNew);
  const soundHere = el("option", { value: "plugin", id: "p8-sound-here" }, "this computer");
  const sound = el("select", { id: "p8-sound", title: "Where the sound comes from while linked" }, el("option", { value: "synth" }, "the PRO 800"), soundHere);
  const send = el("button", { type: "button", id: "p8-send",
    title: "Write the sound playing here into the synth's P128, which it then loads, and into this bank's P128. It overwrites what P128 holds." }, "Send this sound to P128");
  const synth = el("span", { class: "p8-synth", hidden: true }, el("label", {}, "Sound from ", sound), send);
  $("tabs").append(el("span", { class: "p8-tabs-right" }, banks, synth));

  // --- the System tab: PRO-8000 V's own settings, first ---
  const version = el("b", { id: "p8-version" }, "");
  const library = el("span", { class: "msg", id: "p8-library" });
  const folder = el("button", { type: "button", id: "p8-folder" }, "Open the library's folder");
  const libraryHint = el("p", { class: "hint" }, "The library: one file a bank, in the synth's own format (the patch area of its memory), with the plugin's effects in a file beside it. What the Patches tab exports lands in its exports folder.");
  let webDone = false;
  const timing = el("select", { id: "p8-timing" },
    el("option", { value: "0" }, "hardware: as the synth's S&H refresh writes them, 0.7-2.7 ms a pass"),
    el("option", { value: "1" }, "ideal: every 0.25 ms"));
  const clock = el("select", { id: "p8-clock" },
    el("option", { value: "1" }, "the host's tempo and transport, as MIDI clock"),
    el("option", { value: "0" }, "the firmware's own clock"));
  const clockRow = el("label", { class: "sel", id: "p8-clock-row" }, el("span", { class: "lbl" }, "Tempo and transport"), clock);
  $("tab-system").prepend(el("section", { id: "p8-settings" },
    el("h2", {}, "PRO-8000 V"),
    el("div", {}, el("div", { class: "stat" }, version, el("span", {}, "PRO-8000 V"))),
    libraryHint,
    el("div", { class: "row" }, folder, library),
    el("div", { class: "row" }, el("label", { class: "sel" }, el("span", { class: "lbl" }, "CV timing"), timing), clockRow),
    el("p", { class: "hint" }, "Hardware timing steps the CVs as the synth's refresh does, and costs what it costs there; ideal refreshes every 0.25 ms. The host's clock reaches the firmware as MIDI clock, so the sequencer, the arp and synced LFOs follow the song.")));

  // --- the app's audio and MIDI (in a plugin the host's, so no section): built on the first status that says it's the app ---
  let audioSection = null, audioKey = "";
  const audioDevice = el("select", { id: "p8-audio-device" });
  const audioRate = el("select", { id: "p8-audio-rate" }, ...[44100, 48000, 88200, 96000].map(r => el("option", { value: r }, `${r / 1000} kHz`)));
  const audioBuffer = el("select", { id: "p8-audio-buffer" }, ...[64, 128, 256, 512, 1024].map(b => el("option", { value: b }, `${b} samples`)));
  const audioState = el("span", { class: "msg", id: "p8-audio-state" });
  const midiInputs = el("div", { class: "row", id: "p8-midi-inputs" });
  const refreshAudio = () => call("audioInfo").then(a => {
    if (!a || !audioSection) return;
    const key = JSON.stringify([a.devices, a.midi]);
    if (key !== audioKey) { // the lists, rebuilt only when they change (a click mustn't be lost)
      audioKey = key;
      audioDevice.replaceChildren(el("option", { value: "" }, "the computer's default"), ...(a.devices || []).map(d => el("option", { value: d }, d)));
      midiInputs.replaceChildren(...(a.midi || []).map(m => el("label", {}, el("input", { type: "checkbox", checked: m.on,
        onchange: e => call("setMidiInput", m.name, e.target.checked).then(refreshAudio) }), " ", m.name)));
      if (!(a.midi || []).length) midiInputs.append(el("span", { class: "msg" }, a.midiNote || "No MIDI input on this computer (the Play tab plays it)"));
      // in a browser the output, its rate and its buffer are the browser's
      for (const sel of [audioDevice, audioRate, audioBuffer]) sel.closest("label").hidden = !!a.web;
      audioHint.textContent = a.web ? "The MIDI inputs that play it (WebMIDI). The sound goes to this browser's audio output, at its rate."
        : "The MIDI inputs that play it; the PRO 800's own port is the link's. On macOS and Linux other programs can play it through a port called PRO-8000 V.";
    }
    for (const [sel, v] of [[audioDevice, a.device], [audioRate, a.rate], [audioBuffer, a.buffer]])
      if (document.activeElement !== sel) sel.value = String(v);
    audioState.textContent = a.running ? `${a.actualDevice}: ${a.actualRate} Hz, ${a.actualBuffer} samples (${(a.actualBuffer / a.actualRate * 1000).toFixed(1)} ms${a.web ? " in the browser's own buffer" : ""})`
      : `No audio output: ${a.error || "pick an output"}`;
    audioState.className = "msg" + (a.running ? "" : " bad");
  }).catch(() => {});
  const audioHint = el("p", { class: "hint" });
  const buildAudio = () => {
    audioSection = el("section", { id: "p8-audio" },
      el("h2", {}, "Audio and MIDI"),
      el("div", { class: "row" },
        el("label", { class: "sel" }, el("span", { class: "lbl" }, "Output"), audioDevice),
        el("label", { class: "sel" }, el("span", { class: "lbl" }, "Sample rate"), audioRate),
        el("label", { class: "sel" }, el("span", { class: "lbl" }, "Buffer"), audioBuffer)),
      el("div", { class: "row" }, audioState),
      audioHint,
      midiInputs);
    $("p8-settings").after(audioSection);
    const apply = () => call("setAudio", audioDevice.value, +audioRate.value, +audioBuffer.value).then(refreshAudio);
    audioDevice.onchange = audioRate.onchange = audioBuffer.onchange = apply;
    refreshAudio();
    setInterval(() => { if (typeof tab === "undefined" || tab === "system") refreshAudio(); }, 2000);
  };

  // --- what is only about a synth on a USB port ---
  const hide = e => { if (e) e.hidden = true; };
  hide(document.querySelector('#tabs button[data-tab="mapping"]')); // the panel's wiring, a tool for the firmware's author
  hide($("route-from") && $("route-from").closest("section"));       // MIDI routing between the browser's ports
  if (typeof tab !== "undefined" && tab === "mapping" && typeof showTab === "function") showTab("sound");
  const tuning = $("autotune") && $("autotune").closest("section"), factory = $("bank-factory"); // standalone: no synth to tune or copy from

  // The page connects again, as when a synth is unplugged and plugged back: it now talks to the other side
  const pageReconnect = () => {
    if (window.__p8_reconnect) window.__p8_reconnect();
    setTimeout(() => { if (typeof connect === "function") connect(); }, 400);
  };

  const show = s => {
    if (!s) return;
    status = s;
    const l = s.link || {}, device = s.mode === "device";
    modeStandalone.setAttribute("aria-pressed", String(!device));
    modeDevice.setAttribute("aria-pressed", String(device));
    document.body.classList.toggle("p8-device", device);
    document.body.classList.toggle("p8-linked", !!l.connected);
    const trying = l.busy || (device && !l.connected && Date.now() - asked < 1500);
    dot.className = "p8-dot" + (l.connected ? " on" : trying ? " busy" : device ? " bad" : "");
    dot.hidden = !device;
    let msg = "";
    if (device && trying)
      msg = (l.busy && l.message ? l.message : "Looking for the PRO 800...") + (l.busy && l.progress ? ` ${Math.round(l.progress * 100)} %` : "");
    else if (device && !l.connected)
      msg = (l.message && l.message !== "Not linked" ? l.message.replace(/\.*\s*$/, "") + (/[?!]$/.test(l.message.trim()) ? " " : ". ") : "") +
        `Click With PRO 800 to try again; meanwhile ${s.web ? "this browser" : s.standalone ? "this computer" : "the plugin"} plays.`;
    else if (device && l.connected && Date.now() - linkedAt < 8000)
      msg = l.message || ""; // what linking did, for a while
    linkMsg.textContent = msg;
    linkMsg.hidden = !msg;
    banks.hidden = !!l.connected; // while linked the bank is the synth's memory
    synth.hidden = !l.connected || !!s.web; // in a browser the synth's own editor: nothing to choose or send
    soundHere.textContent = s.standalone ? "this computer" : "the plugin";
    sound.value = s.sound;
    send.disabled = !!l.busy;
    if (document.activeElement !== bankNumber) bankNumber.value = s.bank;
    if (!editingName) bankName.value = s.bankName || "";
    timing.value = String(s.timing);
    clock.value = s.hostClock ? "1" : "0";
    clockRow.hidden = !!s.standalone; // the app has no host
    if (s.standalone && !audioSection) buildAudio(); // and its own audio and MIDI
    if (tuning) tuning.hidden = !l.connected;
    if (factory) factory.hidden = !l.connected;
    library.textContent = s.library || "";
    if (s.web && !webDone) { // in a browser: the library is the browser's storage
      webDone = true;
      folder.hidden = true;
      library.hidden = true;
      libraryHint.textContent = "The library: banks of 128 slots in the synth's own format, with the effects per slot beside them, kept in this browser's storage. Back up a bank from the Patches tab (Back up everything); clearing the site's data clears them.";
    }
    version.textContent = s.version || "";
    line.title = device ? "" : s.standalone ? "Plays from MIDI keyboards on this computer and from the Play tab" : "Plays from the track's MIDI and from the Play tab";
    const wasBusy = busy;
    busy = !!l.busy;
    if (!!l.connected !== linked) {
      linked = !!l.connected;
      linkedAt = Date.now();
      pageReconnect();
    } else if (linked && wasBusy && !busy)
      pageReconnect(); // the synth has loaded what was sent: the page reads it again
    rewordStatus();
  };
  const refresh = () => call("status").then(s => {
    if (s && !bridged) { // the bridge is there (on Windows it comes after the page): the page connects if it hasn't
      bridged = true;
      setTimeout(() => { if (typeof output !== "undefined" && !output && typeof connect === "function") connect(); }, 600);
    }
    show(s);
  }).catch(() => {});

  // the mode
  modeStandalone.onclick = () => call("setMode", "standalone").then(refresh);
  modeDevice.onclick = () => {
    if (status && status.mode === "device" && status.link && (status.link.connected || status.link.busy)) return;
    asked = Date.now();
    call("setMode", "device").then(refresh);
  };
  sound.onchange = e => call("setSound", e.target.value).then(refresh);
  // a second press confirms (a WebView may not show confirm() dialogs)
  const disarm = () => {
    armed = false;
    send.textContent = "Send this sound to P128";
    send.classList.remove("armed");
  };
  send.onclick = () => {
    clearTimeout(armTimer);
    if (!armed) {
      armed = true;
      send.textContent = "Overwrite the synth's P128?";
      send.classList.add("armed");
      armTimer = setTimeout(disarm, 4000);
      return;
    }
    disarm();
    call("sendSound", 127).then(refresh);
  };

  // the banks
  const goBank = b => {
    b = Math.max(0, Math.min(16383, b | 0));
    call("selectBank", b).then(() => { refresh(); pageReconnect(); });
  };
  bankPrev.onclick = () => goBank((status ? status.bank : 0) - 1);
  bankNext.onclick = () => goBank((status ? status.bank : 0) + 1);
  bankNumber.onchange = e => goBank(+e.target.value);
  bankNew.onclick = () => call("newBank").then(b => goBank(b));
  bankName.onfocus = () => { editingName = true; };
  bankName.onblur = e => { editingName = false; call("renameBank", status ? status.bank : 0, e.target.value).then(refresh); };
  bankName.onkeydown = e => { if (e.key === "Enter") e.target.blur(); };

  // the settings
  folder.onclick = () => call("openLibraryFolder");
  timing.onchange = e => call("setTiming", +e.target.value).then(refresh);
  clock.onchange = e => call("setHostClock", e.target.value === "1").then(refresh);

  // The page's status line speaks of a synth on a USB port and a Connect button: say what it's connected to here.
  // An export saved by the shim is reported there too.
  function rewordStatus() {
    const t = line.textContent;
    let m = /^Connected: firmware (\S+)(.*)$/.exec(t), n = t;
    if (m) n = (linked ? `PRO 800 linked: PRO-8000 ${m[1]}` : `Built-in PRO-8000 ${m[1]}`) + m[2];
    else if (/^Not connected/.test(t) || t === "Disconnected") n = linked ? "Switching to the PRO 800..." : "Starting the built-in PRO-8000...";
    if (n !== t) line.textContent = n;
  }
  new MutationObserver(rewordStatus).observe(line, { childList: true, characterData: true, subtree: true });
  window.addEventListener("p8-saved", e => {
    line.textContent = e.detail.where ? "Saved in the library's exports folder" : `Couldn't save ${e.detail.name}`;
    line.className = e.detail.where ? "ok" : "bad";
    line.title = e.detail.where || "";
  });

  // The faceplate as on the synth down to 860 px (p8-app.css): below 1180 px it's scaled as a whole, laid out as at
  // 1180, so nothing wraps. A transform, not zoom, so pointers and the displays' coordinates stay consistent.
  const FIT = 1180, LEAST = 860;
  const fitFace = () => {
    const face = document.querySelector("#tab-sound .face");
    if (!face) return;
    const w = window.innerWidth, z = w >= LEAST && w < FIT ? w / FIT : 1;
    if (z === 1) {
      face.style.transform = face.style.width = face.style.marginBottom = face.style.transformOrigin = "";
      return;
    }
    face.style.transformOrigin = "0 0";
    face.style.width = (100 / z) + "%";
    face.style.transform = `scale(${z})`;
    face.style.marginBottom = -(face.offsetHeight * (1 - z)) + "px";
  };
  window.addEventListener("resize", fitFace);
  new ResizeObserver(fitFace).observe(document.body);
  fitFace();

  refresh();
  setInterval(refresh, 500);
  // the page waits for a click on Connect in a browser; here the engine is always there
  setTimeout(() => { if (typeof connect === "function") connect(); }, 50);
  document.body.classList.add("p8-ready");
})();
