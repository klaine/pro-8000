// The plugin's effects (crest-fx) as a section of the firmware's editor, after the sequencer, built from the page's
// own pieces (sec, collapsible, modelKnob, settingSelect, row) so it looks and works as the rest of it. The effects
// are the plugin's alone: stored with the plugin's patches, never sent to the synth.
(() => {
  "use strict";
  if (typeof sec !== "function" || typeof modelKnob !== "function") return; // not the editor page this was built for
  const call = (name, ...args) => (window.p8_call ? window.p8_call(name, ...args) : Promise.resolve(null));
  const GROUPS = [
    ["fx_", "Mix"], ["dst_", "Distortion"], ["cho_", "Chorus"], ["dly_", "Delay"], ["rev_", "Reverb"],
  ];
  let params = null, presets = [], values = [], active = false, built = null, touchedAt = 0;

  const norm = (p, v) => (p.max > p.min ? (v - p.min) / (p.max - p.min) : 0);
  const denorm = (p, u) => p.min + Math.min(1, Math.max(0, u)) * (p.max - p.min);
  const show = (p, v) => (p.min < 0 ? `${v >= 0 ? "+" : "−"}${Math.round(Math.abs(v) * 100)}` : String(Math.round(v * 100)));
  const set = (i, v) => {
    values[i] = v;
    touchedAt = Date.now();
    call("fxSet", i, v);
  };

  function control(i) {
    const p = params[i], text = p.short_name || p.name;
    if (p.type === "enum")
      return settingSelect(text, p.options, () => values[i], v => set(i, v), p.name);
    return modelKnob(text, () => norm(p, values[i] ?? p.default), u => set(i, denorm(p, u)),
      u => show(p, denorm(p, u)), () => norm(p, p.default), p.name);
  }

  function build() {
    const groups = GROUPS.map(([prefix, title]) => {
      const ids = params.map((p, i) => (p.key.startsWith(prefix) ? i : -1)).filter(i => i >= 0);
      const enums = ids.filter(i => params[i].type === "enum"), knobs = ids.filter(i => params[i].type !== "enum");
      return h("div", { class: "p8fx-group" }, h("span", { class: "p8fx-title" }, title),
        row(...enums.map(control)), row(...knobs.map(control)));
    });
    const presetSelect = settingSelect("Preset", ["—", ...presets], () => 0, k => {
      if (k > 0) call("fxPreset", k - 1).then(poll);
    }, "load one of Crest FX's presets (the effects of a patch are saved with it: Save in the Patches tab)");
    const state = h("span", { class: "p8fx-state" });
    paints.push(() => { state.textContent = active ? "● on" : "○ off"; state.classList.toggle("on", active); });
    const section = sec("p8fx", "Effects (plugin only)",
      h("p", { class: "small" }, "Crest FX after the voices: distortion, chorus, delay, reverb. They stay in the plugin: saved with its patches and its project, never sent to the synth, so a patch syncs to the PRO 800 without them."),
      row(presetSelect, state),
      h("div", { class: "p8fx-groups" }, ...groups));
    return collapsible(section, "p8fx");
  }

  function place() {
    if (!params) return;
    const seq = document.querySelector(".extras .sec-seq");
    if (!seq) return;
    if (!built || !built.isConnected) {
      built = built || build();
      seq.parentNode.append(built); // the extras grid: a row of its own after the sequencer
      paintAll();
    }
  }

  async function poll() {
    const r = await call("fxValues").catch(() => null);
    if (!r) return;
    const fresh = r.values || [];
    const changed = !!r.active !== active || fresh.some((v, i) => Math.abs(v - (values[i] ?? NaN)) > 1e-6 || values[i] === undefined);
    active = !!r.active;
    if (Date.now() - touchedAt > 400) values = fresh; // a knob being turned keeps its own value
    if (changed && built) paintAll();
  }

  call("fxInfo").then(info => {
    if (!info) return;
    // crest-fx's descriptions, in the engine's order (the list also has a "preset" entry for the Move's menu)
    const byKey = Object.fromEntries(JSON.parse(info.params).map(p => [p.key, p]));
    params = info.keys.map(k => byKey[k]).map(p => ({ min: 0, max: p.type === "enum" ? p.options.length - 1 : 1, ...p }));
    presets = info.presets || [];
    return poll();
  }).then(() => {
    place();
    setInterval(place, 1000);
    setInterval(poll, 300);
  }).catch(() => {});
})();
