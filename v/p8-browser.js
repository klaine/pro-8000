/* PRO-8000 V's side of the shared patch browser (presetbrowser.js, presetdrawer.js: Amino's, byte for byte, as
 * Crest and Bizar carry them). In the editor's header in place of its ◀ ▶: ‹ the bank · position, the patch's name ›,
 * a click opens the drawer: search, categories, banks, favourites; a click or ↑↓ auditions, Enter or Done keeps, Esc
 * puts back the sound you had, edits included.
 *
 * The library is every bank in this browser's storage (p8-web.js), a patch for each slot that holds one; the presets'
 * bank brings its categories (the preset files' groups). With PRO 800 it is the synth's own 128 slots, read with the
 * editor's own scan, loaded with its own load. */
import { BrowseModel, presetHeader, storedFavourites } from "./presetbrowser.js";

const web = window.p8_web;
const CATS = { bass: "Bass", lead: "Lead", pad: "Pad", poly: "Poly", pluck: "Pluck", arp: "Arp", fx: "FX", other: "Other", user: "User" };
const g = (name) => { try { return (0, eval)(name); } catch (e) { return undefined; } }; // the editor page's own globals

let lib = null;           // p8_call("library"): { bank, slot, banks: [{ bank, name, cats, names }] }
let index = [];           // preset i -> { bank, slot }
const device = () => web.state.mode === "device";

function library() {
  const categories = [], banks = [], presets = [];
  index = [];
  const seen = new Set();
  if (device()) {
    const slots = g("slots") || [];
    banks.push({ id: "synth", label: "PRO 800" });
    slots.forEach((s, n) => {
      if (!s || s.kind !== 1) return;
      presets.push({ name: s.name.trim() || "P" + (n + 1), cat: "", bank: "synth" });
      index.push({ bank: -1, slot: n });
    });
    return { v: 1, categories, banks, presets };
  }
  for (const b of lib.banks) {
    const id = "b" + b.bank;
    let any = false;
    b.names.forEach((name, slot) => {
      if (!name) return;
      const cat = b.cats ? b.cats[slot] || "" : "";
      if (cat && !seen.has(cat)) { seen.add(cat); categories.push({ id: cat, label: CATS[cat] || cat }); }
      presets.push({ name, cat, bank: id });
      index.push({ bank: b.bank, slot });
      any = true;
    });
    if (any) banks.push({ id, label: (b.name || "Bank " + b.bank) });
  }
  categories.sort((a, b) => Object.keys(CATS).indexOf(a.id) - Object.keys(CATS).indexOf(b.id));
  return { v: 1, categories, banks, presets };
}

async function refreshLibrary() {
  lib = await window.p8_call("library");
  model.setLibrary(library());
}

// after the engine has a new sound behind the editor's back: the editor reads it again
async function pageReads() {
  try {
    (0, eval)("patchNameFor = -1; seqFor = null;");
    const rs = g("refreshSettings"), rp = g("refreshParams");
    if (rs) await rs();
    if (rp) await rp();
  } catch (e) { /* the page isn't connected yet */ }
}

// load preset i: a slot of the bank on show through the editor's own load; another bank is shown first
async function load(i) {
  const at = index[i];
  if (!at) return;
  cur = i;
  if (device() || at.bank === lib.bank) {
    (0, eval)(`steppedTo = ${at.slot}`);
    const ls = g("loadStepped");
    if (ls) await ls();
  } else {
    await window.p8_call("selectBank", at.bank);
    lib.bank = at.bank;
    (0, eval)(`slots = []; if (typeof paintSlots === "function") paintSlots();`);
    (0, eval)(`steppedTo = ${at.slot}`);
    const ls = g("loadStepped");
    if (ls) await ls();
  }
  lib.slot = at.slot;
  paint();
}

let cur = -1, visit = null;
const current = () => {
  if (device()) {
    const s = g("settings");
    const n = s ? s.patch : -1;
    return index.findIndex((x) => x.slot === n);
  }
  const s = web.state;
  const i = index.findIndex((x) => x.bank === s.bank && x.slot === s.slot);
  return i >= 0 ? i : cur;
};
const listeners = new Set();
const paint = () => listeners.forEach((fn) => fn());
const host = {
  noun: "patch",
  nouns: "patches",
  current,
  edited: () => false,
  name: () => { const i = current(); return i >= 0 ? model.presets[i].name : ""; },
  load(i) { if (i !== current()) load(i); },
  async begin() {
    if (device()) { visit = { i: current() }; return; }
    const s = web.state;
    visit = { bank: s.bank, slot: s.slot, record: await window.p8_call("capture", host.name()) };
  },
  keep() { visit = null; },
  async revert() {
    if (!visit) return;
    const v = visit;
    if (device()) { if (v.i >= 0) load(v.i); return; }
    if (v.bank !== web.state.bank) { await window.p8_call("selectBank", v.bank); lib.bank = v.bank; }
    if (v.record) window.p8_call("loadRecord", v.record, v.slot);
    setTimeout(pageReads, 60);
    paint();
  },
  onChange(fn) { listeners.add(fn); },
};

const model = new BrowseModel({ v: 1, categories: [], banks: [], presets: [] }, storedFavourites("pro8000v.favourites.v1"));

async function mount() {
  await refreshLibrary();
  const nav = presetHeader(model, host);
  nav.classList.add("p8-nav");
  const step = document.getElementById("patch-step");
  step.hidden = true;
  step.after(nav);
  let lastMode = web.state.mode;
  web.onChange((what) => {
    if (what === "library" || what === "bank") refreshLibrary();
    else paint();
  });
  setInterval(() => { // the mode, and the synth's slots once the editor has read them
    const n = (g("slots") || []).filter(Boolean).length;
    if (web.state.mode !== lastMode || (device() && n !== mount.seen)) {
      lastMode = web.state.mode;
      mount.seen = n;
      model.setLibrary(library());
    }
    paint();
  }, 700);
  // With PRO 800 the drawer needs the synth's slots: the editor's own scan, once
  const open = nav.open;
  nav.open = () => {
    if (device() && !(g("slots") || []).filter(Boolean).length && g("scanSlots")) g("scanSlots")().then(() => model.setLibrary(library()));
    return open();
  };
  nav.querySelector(".pb-name").onclick = nav.open;
}

if (window.p8_call) window.p8_call("library").then(mount); // waits for the start screen
