// The on-screen keyboard, as Amino's and Crest's players have it (their keyboard.js): a bar at the foot of the
// window with pitch-bend and mod-wheel strips, as many octaves as the width holds, octave and velocity. It plays
// through the editor's own noteOn / noteOff (firmware/tools/web/index.html), so it shares the editor's octave,
// velocity, channel and held notes with the computer keyboard (A..' , Z / X, C / V) and the Play tab, records into
// a sequence as they do, and goes where the editor's port goes: the built-in PRO-8000, or the PRO 800 when linked.
// Folds away to a thin bar (remembered). In the app, the plugin and the browser build.
(() => {
  "use strict";
  const g = name => { try { return (0, eval)(name); } catch (e) { return undefined; } }; // the editor's globals
  if (typeof g("noteOn") !== "function") return;
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
  const KEYMAP = "awsedftgyhujkolp;'";
  const NAMES = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"];
  const octave = () => g("octave") || 4;
  const send = m => { const o = g("output"); if (o) o.send(m); };
  const channel = () => g("channel") || 0;

  const keys = el("div", { class: "p8k-keys", role: "group", "aria-label": "Keyboard" });
  const vel = el("input", { type: "range", min: 1, max: 127, value: String(g("velocity") || 100), "aria-label": "Velocity" });
  const octLabel = el("span", { class: "p8k-oct" });
  const keyEls = [];

  // as many octaves as the width holds (about 30 px a white key), from the editor's octave
  function build() {
    keys.textContent = "";
    keyEls.length = 0;
    const width = keys.clientWidth;
    if (!width) return;
    const whites = Math.max(7, Math.min(42, Math.floor(width / 30)));
    const ww = width / whites;
    const below = whites >= 21 && octave() > 0 ? 1 : 0; // wide: an octave below the one the computer keys play
    let n = 12 * (octave() + 1 - below), wi = 0;
    while (wi < whites && n <= 127) {
      const black = [1, 3, 6, 8, 10].includes(n % 12);
      const k = el("div", { class: "p8k-key" + (black ? " black" : ""), "data-note": n });
      if (black) { k.style.left = (wi * ww - ww * 0.32) + "px"; k.style.width = (ww * 0.64) + "px"; }
      else {
        k.style.left = (wi * ww) + "px"; k.style.width = ww + "px";
        if (n % 12 === 0) k.append(el("span", { class: "p8k-name" }, "C" + (n / 12 - 1)));
        wi++;
      }
      const i = n - 12 * (octave() + 1);
      if (i >= 0 && i < KEYMAP.length) k.append(el("span", { class: "p8k-hint" }, KEYMAP[i].toUpperCase()));
      keys.append(k);
      keyEls.push(k);
      n++;
    }
    octLabel.textContent = "C" + octave();
    painted = "";
  }
  new ResizeObserver(build).observe(keys);

  // lit: whatever the editor holds (its own keys and these), checked each frame
  let painted = "";
  const paint = () => {
    const down = g("down");
    const held = new Set(down ? down.values() : []);
    const sig = [...held].sort().join(",") + "|" + octave();
    if (sig !== painted) {
      if (!painted.endsWith("|" + octave()) && painted) build();
      painted = sig;
      for (const k of keyEls) k.classList.toggle("down", held.has(+k.dataset.note));
    }
    requestAnimationFrame(paint);
  };
  requestAnimationFrame(paint);

  // pointers: one per finger, and a drag across the keys moves from note to note, through the editor's own held notes
  const noteAt = (x, y) => {
    const k = document.elementFromPoint(x, y);
    const key = k && k.closest ? k.closest(".p8k-key") : null;
    return key && keys.contains(key) ? +key.dataset.note : null;
  };
  const press = (src, n) => { (0, eval)("velocity = " + (+vel.value | 0)); g("down").set(src, n); g("noteOn")(n); };
  const lift = src => { const d = g("down"), n = d.get(src); if (n !== undefined) { d.delete(src); g("noteOff")(n); } };
  keys.addEventListener("pointerdown", e => {
    const n = noteAt(e.clientX, e.clientY);
    if (n == null) return;
    e.preventDefault();
    try { keys.setPointerCapture(e.pointerId); } catch (err) { /* a synthetic event */ }
    press("k" + e.pointerId, n);
  });
  keys.addEventListener("pointermove", e => {
    const src = "k" + e.pointerId, d = g("down");
    if (!d || !d.has(src)) return;
    const n = noteAt(e.clientX, e.clientY);
    if (n == null || n === d.get(src)) return;
    lift(src);
    press(src, n);
  });
  for (const t of ["pointerup", "pointercancel"]) keys.addEventListener(t, e => lift("k" + e.pointerId));

  // bend and the mod wheel: two strips, the bend springs back
  const strip = (label, spring, onValue) => {
    const thumb = el("i");
    const s = el("div", { class: "p8k-strip", role: "slider", "aria-label": label, tabindex: "0" }, thumb);
    let v = spring ? 0.5 : 0;
    const show = () => { thumb.style.bottom = `calc(${(v * 100).toFixed(1)}% - 3px)`; };
    const at = e => { const r = s.getBoundingClientRect(); v = Math.min(1, Math.max(0, 1 - (e.clientY - r.top) / r.height)); show(); onValue(v); };
    let dragging = false;
    s.addEventListener("pointerdown", e => { dragging = true; try { s.setPointerCapture(e.pointerId); } catch (err) { /* synthetic */ } at(e); });
    s.addEventListener("pointermove", e => { if (dragging) at(e); });
    for (const t of ["pointerup", "pointercancel"]) s.addEventListener(t, () => { dragging = false; if (spring) { v = 0.5; show(); onValue(v); } });
    show();
    return el("div", { class: "p8k-strip-wrap" }, s, el("span", { class: "p8k-lbl" }, label));
  };
  const bend = strip("Bend", true, v => { const b = Math.round(v * 16383); send([0xE0 | channel(), b & 127, b >> 7]); });
  const wheel = strip("Mod", false, v => send([0xB0 | channel(), 1, Math.round(v * 127)]));

  // the octave and the velocity are the editor's (Z / X and C / V move them too)
  const shift = d => {
    (0, eval)("allNotesOff()");
    (0, eval)(`octave = Math.max(0, Math.min(8, octave + (${d})))`);
    if (typeof g("relabel") === "function") g("relabel")();
    build();
  };
  vel.addEventListener("input", () => {
    (0, eval)("velocity = " + (+vel.value | 0));
    if (typeof g("relabel") === "function") g("relabel")();
  });
  setInterval(() => { const v = g("velocity"); if (v && document.activeElement !== vel && +vel.value !== v) vel.value = String(v); }, 300);

  let folded = false;
  try { folded = localStorage.getItem("p8.keys.folded") === "1"; } catch (e) { /* no storage */ }
  const fold = el("button", { type: "button", class: "p8k-fold", "aria-expanded": String(!folded) });
  const bar = el("footer", { class: "p8k" + (folded ? " folded" : "") },
    el("div", { class: "p8k-ctl" },
      fold,
      el("span", { class: "p8k-group" },
        el("button", { type: "button", class: "p8k-btn", "aria-label": "Octave down", title: "Octave down (Z)", onclick: () => shift(-1) }, "−"),
        octLabel,
        el("button", { type: "button", class: "p8k-btn", "aria-label": "Octave up", title: "Octave up (X)", onclick: () => shift(1) }, "+")),
      el("label", { class: "p8k-group", title: "Velocity (C / V)" }, el("span", { class: "p8k-lbl" }, "Velocity"), vel),
      el("button", { type: "button", class: "p8k-btn", title: "Every note off", onclick: () => (0, eval)("allNotesOff()") }, "All notes off"),
      el("span", { class: "p8k-tip" }, "Keys A–' play, Z / X octave, C / V velocity")),
    el("div", { class: "p8k-strips" }, bend, wheel),
    keys);
  const showFold = () => {
    fold.textContent = folded ? "▴ Keyboard" : "▾ Keyboard";
    fold.title = folded ? "Show the keyboard" : "Fold the keyboard away";
    fold.setAttribute("aria-expanded", String(!folded));
    bar.classList.toggle("folded", folded);
  };
  fold.onclick = () => {
    folded = !folded;
    try { localStorage.setItem("p8.keys.folded", folded ? "1" : "0"); } catch (e) { /* no storage */ }
    showFold();
    if (!folded) requestAnimationFrame(build);
  };
  showFold();
  document.body.append(bar);
  // it sits over the page's foot: the page makes room for it
  new ResizeObserver(() => { document.body.style.paddingBottom = bar.offsetHeight + "px"; }).observe(bar);
  window.addEventListener("blur", () => { for (const src of [...(g("down") || new Map()).keys()]) if (String(src).startsWith("k")) lift(src); });
})();
