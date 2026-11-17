/* Preset browsing shared by the Move modules' Remote UIs (Amino, Bizar, Crest).
 *
 * The canonical copy is Amino's src/web/presetbrowser.js (with presetdrawer.js
 * and presetbrowser.css); the other modules carry byte-identical copies, the
 * way Crest FX carries Crest's fx.c.  Behaviour and markup live here.  Each
 * module styles them through the --pb-* custom properties and talks to its
 * engine through a host adapter (see presetHeader).
 *
 * One model for every surface:
 * - A library { v: 1, categories: [{id, label}], banks: [{id, label}],
 *   presets: [{name, cat, bank?}] }.  A preset's id is its index; a preset
 *   without a bank is in the first one.  A module may add banks of its own
 *   at run time (saved patches, imported banks) with setLibrary.
 * - A scope: category, bank and favourites.  The header's ‹ ›, the browser's
 *   ↑↓ and Move's jog all step through the scope and wrap, so the next preset
 *   is always one the list shows.
 * - Browsing auditions: each step loads.  Enter or Done keeps the preset; Esc
 *   puts back the sound you had when the browser opened, edits included.
 * - Favourites are keyed "<bank>/<name>", so they survive a reordered bank.
 */

export const LIBRARY_VERSION = 1;

/** Favourites kept in this browser's storage under `key`; in memory if storage is blocked. */
export function storedFavourites(key) {
  let set = new Set();
  try { set = new Set(JSON.parse(localStorage.getItem(key) || "[]")); } catch (e) { /* no storage */ }
  return {
    has: (k) => set.has(k),
    toggle(k) {
      if (set.has(k)) set.delete(k); else set.add(k);
      try { localStorage.setItem(key, JSON.stringify([...set])); } catch (e) { /* private mode */ }
    },
    get size() { return set.size; },
  };
}

export class BrowseModel {
  constructor(lib, favourites = storedFavourites("")) {
    this.favourites = favourites;
    this.scope = { cat: "", bank: "", fav: false };
    this.listeners = new Set();
    this.setLibrary(lib);
  }

  /** Swap in a changed library (a bank added or removed, a preset saved). */
  setLibrary(lib) {
    this.presets = lib.presets || [];
    this.categories = lib.categories || [];
    this.banks = lib.banks && lib.banks.length ? lib.banks : [{ id: "", label: "" }];
    this.firsts = new Map();
    this.presets.forEach((p, i) => { if (!this.firsts.has(this.bankOf(i))) this.firsts.set(this.bankOf(i), i); });
    if (this.scope.bank && !this.firsts.has(this.scope.bank)) this.scope.bank = "";
    this.emit("library");
  }

  bankOf(i) { return this.presets[i].bank || this.banks[0].id; }
  /** 1-based number of preset i within its bank, as the bank's own list counts. */
  number(i) { return i - this.firsts.get(this.bankOf(i)) + 1; }
  catLabel(id) {
    const c = this.categories.find((x) => x.id === id);
    return c ? c.label : id;
  }
  bankLabel(id) {
    const b = this.banks.find((x) => x.id === id);
    return b ? b.label : id;
  }
  favKey(i) { return this.bankOf(i) + "/" + this.presets[i].name; }
  isFav(i) { return this.favourites.has(this.favKey(i)); }
  toggleFav(i) {
    this.favourites.toggle(this.favKey(i));
    this.emit("fav");
  }

  inScope(i) {
    const s = this.scope;
    if (s.cat && this.presets[i].cat !== s.cat) return false;
    if (s.bank && this.bankOf(i) !== s.bank) return false;
    return !s.fav || this.isFav(i);
  }
  /** The scope's presets matching `query` (name or category, any case), in bank order. */
  list(query = "") {
    const q = query.trim().toLowerCase();
    const out = [];
    for (let i = 0; i < this.presets.length; i++) {
      if (!this.inScope(i)) continue;
      if (q && !(this.presets[i].name + " " + this.catLabel(this.presets[i].cat)).toLowerCase().includes(q)) continue;
      out.push(i);
    }
    return out;
  }
  count(cat) {
    let n = 0;
    for (const p of this.presets) if (!cat || p.cat === cat) n++;
    return n;
  }
  /** Presets in bank `id`. */
  bankCount(id) {
    let n = 0;
    for (let i = 0; i < this.presets.length; i++) if (this.bankOf(i) === id) n++;
    return n;
  }

  /**
   * The preset `delta` steps from `from` within the scope, wrapping.  From a
   * preset outside it, the nearest one in that direction.  -1 when it is empty.
   */
  step(from, delta) {
    const set = this.list();
    if (!set.length) return -1;
    const at = set.indexOf(from);
    if (at >= 0) return set[((at + delta) % set.length + set.length) % set.length];
    if (delta > 0) return set.find((i) => i > from) ?? set[0];
    for (let k = set.length - 1; k >= 0; k--) if (set[k] < from) return set[k];
    return set[set.length - 1];
  }
  /** { pos, count }: 1-based position of `i` in the scope (0 when outside it) and the scope's size. */
  position(i) {
    const set = this.list();
    return { pos: set.indexOf(i) + 1, count: set.length };
  }
  /** "Bass", "★ Bass", "★ Factory", or "All" for no scope. */
  scopeLabel() {
    const s = this.scope;
    const parts = [];
    if (s.bank && this.banks.length > 1) parts.push(this.bankLabel(s.bank));
    if (s.cat) parts.push(this.catLabel(s.cat));
    const text = parts.join(" · ");
    return s.fav ? "★" + (text ? " " + text : "") : text || "All";
  }
  get scoped() { return !!(this.scope.cat || this.scope.bank || this.scope.fav); }

  setScope(patch) {
    let changed = false;
    for (const k in patch) if (this.scope[k] !== patch[k]) { this.scope[k] = patch[k]; changed = true; }
    if (changed) this.emit("scope");
  }

  onChange(fn) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
  emit(what) { for (const fn of this.listeners) fn(what); }
}

/* ------------------------------------------------------------ DOM */

export function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

let styled = false;
export function presetStyles() {
  if (styled) return;
  styled = true;
  // A one-file page (Bizar's UI, Amino's single-file build) inlines the sheet,
  // and its modules may have no URL to resolve one against.
  if (getComputedStyle(document.documentElement).getPropertyValue("--pb-sheet").trim()) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = new URL("presetbrowser.css", import.meta.url).href;
  document.head.appendChild(link);
}

/**
 * The header control: ‹ name ›, with the scope and position above the name
 * and a chip that clears the scope.  `host` is the module's adapter:
 *
 *   noun, nouns         "preset" or "patch", and its plural (default noun + "s")
 *   current()           index of the loaded preset, or the last one loaded
 *                       once the sound is edited; -1 for none
 *   edited()            true once the sound differs from that preset
 *   name()              the name to show (the device's preset_name)
 *   load(i, opts)       load preset i; opts.audition marks a browser step
 *   begin() / revert()  the browser opened / Esc: put back the sound from begin()
 *   keep()              the browser closed with the auditioned preset kept
 *   onChange(fn)        call fn when the preset or name changes elsewhere
 *
 * and optionally:
 *
 *   status()            the word after the position ("edited", "comparing");
 *                       by default "edited" while edited()
 *   display, paint(s)   an element of the module's own to show in the name
 *                       button (a patch screen, say) in place of the two
 *                       lines; paint gets { scope, pos, count, status, name }
 *   views, tools, removable(i), remove(i)   for the drawer (presetdrawer.js)
 *
 * Returns the element; `.open()` opens the browser.
 */
export function presetHeader(model, host) {
  presetStyles();
  const noun = host.noun || "preset";
  const nouns = host.nouns || noun + "s";
  const wrap = el("div", "pb-nav");
  const prev = el("button", "pb-step", "‹");
  const next = el("button", "pb-step", "›");
  prev.type = next.type = "button";
  prev.setAttribute("aria-label", "Previous " + noun);
  next.setAttribute("aria-label", "Next " + noun);
  const name = el("button", "pb-name");
  name.type = "button";
  name.setAttribute("aria-haspopup", "dialog");
  name.title = "Browse " + nouns;
  const meta = el("span", "pb-meta");
  const title = el("span", "pb-title");
  if (host.display) {
    name.classList.add("pb-custom");
    name.append(host.display);
  } else name.append(meta, title);
  const scope = el("button", "pb-scope");
  scope.type = "button";
  wrap.append(prev, name, scope, next);

  const paint = () => {
    const i = host.current();
    const { pos, count } = model.position(i);
    const status = host.status ? host.status() : i >= 0 && host.edited() ? "edited" : "";
    const label = host.name() || (i >= 0 ? model.presets[i].name : "Init");
    if (host.paint) host.paint({ scope: model.scopeLabel(), pos, count, status, name: label });
    meta.textContent = model.scopeLabel() + " · " + (pos ? pos : "–") + "/" + count + (status ? " · " + status : "");
    title.textContent = label;
    prev.disabled = next.disabled = count < 1 || (count === 1 && pos === 1);
    prev.title = "Previous in " + model.scopeLabel() + " ([)";
    next.title = "Next in " + model.scopeLabel() + " (])";
    scope.hidden = !model.scoped;
    scope.textContent = model.scopeLabel() + " ×";
    scope.title = "Step through all " + nouns + " again";
    scope.setAttribute("aria-label", "Clear the " + model.scopeLabel() + " filter");
  };
  const step = (d) => {
    const i = model.step(host.current(), d);
    if (i >= 0) host.load(i, { audition: false });
  };
  prev.onclick = () => step(-1);
  next.onclick = () => step(1);
  // [ and ] step as well, in every module's Remote UI.
  document.addEventListener("keydown", (e) => {
    if ((e.key !== "[" && e.key !== "]") || e.metaKey || e.ctrlKey || e.altKey) return;
    if (/^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) return;
    e.preventDefault();
    step(e.key === "]" ? 1 : -1);
  });
  scope.onclick = () => model.setScope({ cat: "", bank: "", fav: false });
  model.onChange(paint);
  host.onChange(paint);

  let drawer = null;
  wrap.open = () => import("./presetdrawer.js").then((m) => {
    drawer = drawer || m.presetDrawer(model, host);
    drawer.open(name);
  });
  name.onclick = wrap.open;
  paint();
  return wrap;
}
