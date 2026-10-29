// Theme toggle (remembered per browser when storage works), contents highlighting, and SVG helpers for the figures.
(function () {
  var root = document.documentElement;
  try { var saved = localStorage.getItem("pro800-theme"); if (saved) root.dataset.theme = saved; } catch (e) {}
  function dark() {
    return root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  }
  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.querySelector(".theme-btn");
    if (btn) btn.addEventListener("click", function () {
      root.dataset.theme = dark() ? "light" : "dark";
      try { localStorage.setItem("pro800-theme", root.dataset.theme); } catch (e) {}
    });
    var links = Array.prototype.slice.call(document.querySelectorAll(".toc a"));
    if (!links.length || !("IntersectionObserver" in window)) return;
    var byId = {};
    links.forEach(function (a) { byId[a.getAttribute("href").slice(1)] = a; });
    var obs = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        links.forEach(function (a) { a.classList.remove("active"); });
        var a = byId[en.target.id];
        if (a) a.classList.add("active");
      });
    }, { rootMargin: "-20% 0px -70% 0px" });
    Object.keys(byId).forEach(function (id) { var el = document.getElementById(id); if (el) obs.observe(el); });
  });
})();

var NS = "http://www.w3.org/2000/svg";
// S("rect", {x: 1, class: "box"}, parent): makes an SVG element; a "text" attribute sets its text
function S(tag, attrs, parent) {
  var el = document.createElementNS(NS, tag);
  for (var k in attrs) {
    if (k === "text") el.textContent = attrs[k];
    else el.setAttribute(k, attrs[k]);
  }
  if (parent) parent.appendChild(el);
  return el;
}
// A polyline path through n + 1 samples of f(t), t = 0..1, mapped by x(t) and y(value)
function plot(f, n, x, y) {
  var d = "";
  for (var i = 0; i <= n; i++) {
    var t = i / n;
    d += (i ? "L" : "M") + x(t).toFixed(1) + " " + y(f(t)).toFixed(1);
  }
  return d;
}
function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }
// A small seeded random generator, so the pictures are the same at every load
function rng(seed) {
  var s = seed >>> 0 || 1;
  return function () { s ^= s << 13; s >>>= 0; s ^= s >> 17; s ^= s << 5; s >>>= 0; return s / 4294967296; };
}
// Segmented buttons: calls on(value) with the clicked button's data-v, and marks it pressed
function segmented(el, on) {
  el.addEventListener("click", function (ev) {
    var b = ev.target.closest("button");
    if (!b || !el.contains(b)) return;
    Array.prototype.forEach.call(el.querySelectorAll("button"), function (x) { x.setAttribute("aria-pressed", x === b); });
    on(b.dataset.v);
  });
}
