/* The preset browser drawer, shared with presetbrowser.js (see the notes
 * there).  Loaded on first open.
 *
 * Search narrows the scope's list while the drawer is open; the chips set the
 * scope itself, which outlives the drawer and drives the header's ‹ ›.  A
 * click or ↑↓ auditions, a double click or Enter keeps, Done keeps, Esc and
 * "Back to" put back the sound the drawer opened with.  A module may add tabs
 * of its own (`host.views`): { label, placeholder, rows(query) -> [{ num,
 * name, note }], pick(row) -> footer text }, an element of controls for the
 * foot (`host.tools`), and a delete button on the presets it owns
 * (`host.removable(i)`, `host.remove(i)`).
 */
import { el } from "./presetbrowser.js";

export function presetDrawer(model, host) {
  const noun = host.noun || "preset";
  const nouns = host.nouns || noun + "s";
  const views = host.views || [];
  const back = el("div", "pb-back");
  const panel = el("aside", "pb-drawer");
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-modal", "true");
  panel.setAttribute("aria-label", noun[0].toUpperCase() + noun.slice(1) + " browser");
  const head = el("div", "pb-head");
  const tabs = el("div", "pb-tabs");
  const tabButtons = [nouns[0].toUpperCase() + nouns.slice(1), ...views.map((v) => v.label)].map((label, k) => {
    const b = el("button", "pb-tab", label);
    b.type = "button";
    b.onclick = () => { tab = k; refresh(); };
    return b;
  });
  if (views.length) tabs.append(...tabButtons);
  else tabs.append(el("h2", "pb-heading", tabButtons[0].textContent));
  const done = el("button", "pb-done", "Done");
  done.type = "button";
  head.append(tabs, done);
  const search = el("input", "pb-search");
  search.type = "search";
  search.setAttribute("aria-label", "Search");
  const chips = el("div", "pb-chips");
  const banks = el("div", "pb-chips pb-banks");
  const list = el("div", "pb-list");
  list.setAttribute("role", "listbox");
  const foot = el("div", "pb-foot");
  const info = el("span", "pb-info");
  const revertBtn = el("button", "pb-revert");
  revertBtn.type = "button";
  foot.append(info, revertBtn);
  if (host.tools) {
    host.tools.classList.add("pb-tools");
    foot.prepend(host.tools);
  }
  panel.append(head, search, chips, banks, list, foot);
  document.body.append(back, panel);

  let tab = 0;          // 0: the presets; k: views[k - 1]
  let rows = [];        // [{ i or item, row }] as listed
  let cursor = -1;      // index into rows
  let auditioned = false;
  let openedWith = "";
  let opener = null;

  const chip = (label, on, onclick, title) => {
    const b = el("button", "pb-chip" + (on ? " on" : ""), label);
    b.type = "button";
    b.setAttribute("aria-pressed", String(on));
    if (title) b.title = title;
    b.onclick = onclick;
    return b;
  };
  const drawChips = () => {
    chips.textContent = "";
    banks.textContent = "";
    if (tab) return;
    const s = model.scope;
    chips.append(
      chip("★ Favourites", s.fav, () => model.setScope({ fav: !s.fav })),
      chip("All " + model.count(""), !s.cat, () => model.setScope({ cat: "" })),
      ...model.categories.map((c) => chip(c.label + " " + model.count(c.id), s.cat === c.id,
        () => model.setScope({ cat: s.cat === c.id ? "" : c.id }), c.id)),
    );
    if (model.banks.length > 1) {
      banks.append(chip("All banks", !s.bank, () => model.setScope({ bank: "" })),
        ...model.banks.map((b) => chip(b.label + " " + model.bankCount(b.id), s.bank === b.id,
          () => model.setScope({ bank: s.bank === b.id ? "" : b.id }))));
    }
  };

  const markCurrent = () => {
    const cur = host.current();
    rows.forEach((r, k) => {
      const on = r.i === cur;
      r.row.classList.toggle("on", on);
      r.row.setAttribute("aria-selected", String(on));
      if (on) cursor = k;
    });
    rows.forEach((r, k) => r.row.classList.toggle("cursor", k === cursor));
    revertBtn.hidden = !auditioned;
    revertBtn.textContent = "↺ Back to " + (openedWith || "the sound you had");
  };

  const drawPresets = () => {
    const cur = host.current();
    const several = model.banks.length > 1 && !model.scope.bank;
    rows = model.list(search.value).map((i) => {
      const p = model.presets[i];
      const row = el("div", "pb-row");
      row.setAttribute("role", "option");
      const fav = model.isFav(i);
      const star = el("button", "pb-star" + (fav ? " on" : ""), "★");
      star.type = "button";
      star.setAttribute("aria-label", (fav ? "Remove " : "Add ") + p.name + (fav ? " from" : " to") + " favourites");
      star.onclick = (e) => { e.stopPropagation(); model.toggleFav(i); };
      const note = [p.cat && model.catLabel(p.cat), several && model.bankLabel(model.bankOf(i))].filter(Boolean).join(" · ");
      row.append(el("span", "pb-num", String(model.number(i)).padStart(3, "0")), star,
        el("span", "pb-row-name", p.name), el("span", "pb-row-note", note));
      if (host.removable?.(i)) {
        const del = el("button", "pb-del", "×");
        del.type = "button";
        del.setAttribute("aria-label", "Delete " + p.name);
        del.onclick = (e) => { e.stopPropagation(); host.remove(i); };
        row.append(del);
      }
      // A click on the current, edited preset auditions it as stored; the double click that click
      // began keeps the sound the drawer opened with, edits and all, rather than the stored one.
      let asOpened = false;
      row.onclick = (e) => {
        if (e.detail <= 1) asOpened = !auditioned && i === host.current() && host.edited();
        audition(i);
      };
      row.ondblclick = () => close(!asOpened);
      list.appendChild(row);
      return { i, row };
    });
    cursor = rows.findIndex((r) => r.i === cur);
    info.textContent = rows.length + " of " + model.presets.length + " " + nouns +
      (model.scoped ? " in " + model.scopeLabel() : "") + " · ↑↓ audition · Enter keep · Esc revert";
    markCurrent();
  };

  const drawView = (v) => {
    rows = v.rows(search.value.trim().toLowerCase()).map((item) => {
      const row = el("div", "pb-row");
      row.setAttribute("role", "option");
      row.append(el("span", "pb-num", item.num || ""), el("span"),
        el("span", "pb-row-name", item.name), el("span", "pb-row-note", item.note || ""));
      row.onclick = () => { info.textContent = v.pick(item) || ""; };
      list.appendChild(row);
      return { item, row };
    });
    cursor = -1;
    info.textContent = rows.length + " " + (v.unit || "items");
    revertBtn.hidden = true;
  };

  const refresh = () => {
    list.textContent = "";
    tabButtons.forEach((b, k) => b.classList.toggle("on", k === tab));
    const v = views[tab - 1];
    search.placeholder = v ? v.placeholder || "Search" : "Search " + model.list().length + " " + nouns;
    drawChips();
    if (v) drawView(v); else drawPresets();
    rows[cursor]?.row.scrollIntoView({ block: "nearest" });
  };

  const audition = (i) => {
    if (i === host.current() && !host.edited()) return markCurrent();
    host.load(i, { audition: true });
    auditioned = true;
    markCurrent();
  };
  const revert = () => {
    if (auditioned) host.revert();
    auditioned = false;
    markCurrent();
    rows[cursor]?.row.scrollIntoView({ block: "nearest" });
  };

  const isOpen = () => panel.classList.contains("open");
  const close = (keep) => {
    if (!isOpen()) return;
    if (keep) { if (auditioned) host.keep?.(); } else revert();
    auditioned = false;
    panel.classList.remove("open");
    back.classList.remove("open");
    opener?.focus();
  };
  done.onclick = () => close(true);
  back.onclick = () => close(true);
  revertBtn.onclick = revert;
  search.oninput = refresh;

  panel.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.preventDefault(); close(false); return; }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (!rows.length) return;
      e.preventDefault();
      const d = e.key === "ArrowDown" ? 1 : -1;
      cursor = cursor < 0 ? (d > 0 ? 0 : rows.length - 1) : Math.max(0, Math.min(rows.length - 1, cursor + d));
      rows.forEach((r, k) => r.row.classList.toggle("cursor", k === cursor));
      rows[cursor].row.scrollIntoView({ block: "nearest" });
      if (!tab) audition(rows[cursor].i);
      return;
    }
    if (e.key === "Enter" && rows[cursor]) {
      e.preventDefault();
      if (tab) rows[cursor].row.click();
      else { // keeps what is playing: the current row, edited or not, isn't loaded again
        if (rows[cursor].i !== host.current()) audition(rows[cursor].i);
        close(true);
      }
    }
  });
  // The scope can change on Move (pb_cat) or from a chip; favourites from a star.
  model.onChange(() => { if (isOpen()) refresh(); });
  host.onChange(() => { if (isOpen() && !tab) markCurrent(); });

  return {
    open(from) {
      opener = from || null;
      host.begin();
      openedWith = host.name();
      auditioned = false;
      search.value = "";
      tab = 0;
      refresh();
      panel.classList.add("open");
      back.classList.add("open");
      search.focus();
    },
  };
}
