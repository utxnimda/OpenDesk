"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../frontend/card-layout.js"), "utf8");
const plain = value => JSON.parse(JSON.stringify(value));
function load(window = {}) { vm.runInNewContext(source, { window }); return window.NimdaLibraryCardLayout; }

test("card size has a strict allowlist and medium fallback", () => {
  const api = load();
  for (const size of ["small", "medium", "large", "auto"]) assert.equal(api.normalizeSize(size), size);
  for (const size of [null, undefined, "", "SMALL", " medium ", "cards", {}, 0]) assert.equal(api.normalizeSize(size), "medium");
});

test("prefix packing keeps all chips without reserving an unnecessary overflow button", () => {
  const api = load();
  assert.equal(api.fitCount([30, 40, 20], 100, 5, () => { throw Error("unnecessary measurement"); }), 3);
  assert.equal(api.fitCount([], 0, 5, 20), 0);
  assert.equal(api.fitCount([40], 40, 5, 20), 1);
});

test("prefix packing reserves the exact hidden-count button and never skips a long leading chip", () => {
  const api = load(), calls = [];
  assert.equal(api.fitCount([30, 40, 20], 70, 5, hidden => { calls.push(hidden); return 16; }), 1);
  assert.deepEqual(calls, [1, 2]);
  assert.equal(api.fitCount([100, 20, 20], 60, 5, 20), 0);
  assert.equal(api.fitCount([20, 20, 20], 69, 5, [0, 19, 22, 23]), 2);
  assert.equal(api.fitCount([20, 20, 20], 68, 5, [0, 19, 22, 23]), 1);
});

test("prefix packing handles zero space, fractional widths and multidigit overflow counts", () => {
  const api = load();
  assert.equal(api.fitCount([10, 10], 0, 5, 20), 0);
  assert.equal(api.fitCount([10, 10], NaN, 5, 20), 0);
  assert.equal(api.fitCount([10.5, 10.5], 22.25, 1.25, 8), 2);
  assert.equal(api.fitCount(Array(12).fill(25), 54, 5, hidden => hidden >= 10 ? 30 : 20), 0);
  assert.equal(api.fitCount(Array(12).fill(25), 60, 5, hidden => hidden >= 10 ? 30 : 20), 1);
});

test("prefix packing matches an exhaustive whole-prefix oracle", () => {
  const api = load();
  for (let length = 1; length <= 16; length++) {
    const widths = Array.from({ length }, (_, i) => 13.5 + i % 4 * 7);
    for (let available = 0; available < 270; available += 3) {
      const sum = count => widths.slice(0, count).reduce((a, b) => a + b, 0) + Math.max(0, count - 1) * 5;
      const more = hidden => 13 + String(hidden).length * 6;
      let expected = 0;
      for (let count = 0; count <= length; count++) {
        const needed = sum(count) + (count < length ? (count ? 5 : 0) + more(length - count) : 0);
        if (needed <= available) expected = count;
      }
      assert.equal(api.fitCount(widths, available, 5, more), expected, `${length}/${available}`);
    }
  }
});

test("facts prioritize score and year but preserve original display order", () => {
  const api = load();
  assert.deepEqual(plain(api.fitFacts([30, 32, 40, 50, 60], [10, 90, 20, 30, 100], 110, 4, 10)), [1, 4]);
  assert.deepEqual(plain(api.fitFacts([30, 32, 40], [10, 90, 20], 110, 4, 10)), [0, 1, 2]);
  assert.deepEqual(plain(api.fitFacts([30, 30, 30], [1, 1, 1], 44, 4, 10)), [0]);
});

test("facts skip an oversized fact, reserve ellipsis and tolerate missing priorities", () => {
  const api = load();
  assert.deepEqual(plain(api.fitFacts([10, 32, 60], [10, 90, 100], 20, 4, 5)), [0]);
  assert.deepEqual(plain(api.fitFacts([10, 10], [], 19, 4, 6)), []);
  assert.deepEqual(plain(api.fitFacts([10, 10], [], 20, 4, 6)), [0]);
  assert.deepEqual(plain(api.fitFacts([], [], 0, 4, 6)), []);
});

test("forced facts overflow reserves exactly one button and one intervening gap", () => {
  const api = load(), widths = [30, 32, 40], priorities = [10, 90, 20];
  assert.deepEqual(plain(api.fitFacts(widths, priorities, 124, 4, 10, true)), [0, 1, 2]);
  assert.deepEqual(plain(api.fitFacts(widths, priorities, 123, 4, 10, true)), [1, 2]);
  assert.deepEqual(plain(api.fitFacts([30], [10], 44, 4, 10, true)), [0]);
  assert.deepEqual(plain(api.fitFacts([30], [10], 43, 4, 10, true)), []);
  assert.deepEqual(plain(api.fitFacts([], [], 10, 4, 10, true)), []);
});

class EventTarget {
  constructor() { this.listeners = {}; }
  addEventListener(type, listener) { (this.listeners[type] ||= new Set()).add(listener); }
  removeEventListener(type, listener) { this.listeners[type]?.delete(listener); }
  emit(type) { for (const listener of this.listeners[type] || []) listener(); }
  count(type) { return this.listeners[type]?.size || 0; }
}
function fixture({ raf = true, timers = true, observers = true, fonts = true } = {}) {
  const window = new EventTarget(), frames = new Map(), cancelled = [], resizeObservers = [], mutationObservers = [];
  let next = 1, fontReady;
  const document = { fonts: fonts ? new EventTarget() : null };
  class Element extends EventTarget {
    constructor(className, width = 0) { super(); this.className = className; this.width = width; this.children = []; this.style = {}; this.attributes = {}; this.hidden = false; this.textContent = ""; this.ownerDocument = document; this.css = {}; }
    appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
    setAttribute(key, value) { this.attributes[key] = String(value); }
    getAttribute(key) { return this.attributes[key] ?? null; }
    removeAttribute(key) { delete this.attributes[key]; }
    querySelectorAll(selector) { const cls = selector.slice(1); return this.children.flatMap(child => [...(child.className.split(" ").includes(cls) ? [child] : []), ...child.querySelectorAll(selector)]); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    getBoundingClientRect() {
      let node = this; while (node) { if (node.hidden) return { width: 0 }; node = node.parentNode; }
      return { width: typeof this.width === "function" ? this.width(this) : this.width };
    }
  }
  document.documentElement = new Element("html"); document.body = document.documentElement.appendChild(new Element("body"));
  window.document = document; window.getComputedStyle = node => node.css;
  const schedule = callback => { const id = next++; frames.set(id, callback); return id; };
  const cancel = id => { frames.delete(id); cancelled.push(id); };
  if (raf) { window.requestAnimationFrame = schedule; window.cancelAnimationFrame = cancel; }
  if (timers) { window.setTimeout = schedule; window.clearTimeout = cancel; }
  class Observer {
    constructor(callback, group) { this.callback = callback; this.observed = []; this.disconnected = false; group.push(this); }
    observe(node, options) { this.observed.push({ node, options }); }
    disconnect() { this.disconnected = true; }
  }
  if (observers) {
    window.ResizeObserver = class extends Observer { constructor(callback) { super(callback, resizeObservers); } };
    window.MutationObserver = class extends Observer { constructor(callback) { super(callback, mutationObservers); } };
  }
  if (fonts) document.fonts.ready = new Promise(resolve => { fontReady = resolve; });
  const api = load(window), grid = document.body.appendChild(new Element("library-grid", 400));
  function flush() { const current = [...frames.values()]; frames.clear(); current.forEach(callback => callback()); }
  function resources(width = 100, chipWidths = [30, 35, 40]) {
    const row = grid.appendChild(new Element("library-card-resources", width));
    const chips = chipWidths.map(width => row.appendChild(new Element("library-press-chip", width)));
    const more = row.appendChild(new Element("library-press-more", node => 12 + node.textContent.length * 4)); more.hidden = true; more.textContent = "+0";
    return { row, chips, more };
  }
  function facts(width = 110) {
    const row = grid.appendChild(new Element("library-card-facts", width));
    const facts = [30, 32, 40, 50, 60].map((width, i) => { const node = row.appendChild(new Element("library-card-fact", width)); node.setAttribute("data-fact-priority", [10, 90, 20, 30, 100][i]); return node; });
    const more = row.appendChild(new Element("library-facts-more", 10)); more.hidden = true;
    return { row, facts, more };
  }
  function addDots(row, items) {
    const dots = [], children = [];
    items.forEach((item, i) => { if (i) { const dot = new Element("library-card-dot", 3); dot.parentNode = row; dots.push(dot); children.push(dot); } children.push(item); });
    row.children = children.concat(row.children.filter(child => !items.includes(child)));
    return dots;
  }
  return { api, window, document, grid, frames, cancelled, resizeObservers, mutationObservers, flush, resources, facts, fontReady, addDots };
}

test("dot-separated resources measure separators without reserving a dot before overflow", () => {
  const f = fixture(), r = f.resources(98, [30, 30, 30]), dots = f.addDots(r.row, r.chips);
  const manager = f.api.attach(f.grid, {}); f.flush();
  assert.deepEqual(r.chips.map(node => node.hidden), [false, false, true]);
  assert.deepEqual(dots.map(node => node.hidden), [false, true]); assert.equal(r.more.textContent, "+1");
  r.row.width = 116; manager.refresh(); f.flush();
  assert.ok(r.chips.every(node => !node.hidden)); assert.ok(dots.every(node => !node.hidden)); assert.equal(r.more.hidden, true);
  r.row.width = 115; manager.refresh(); f.flush(); assert.equal(r.more.textContent, "+1");
  r.row.width = 20; manager.refresh(); f.flush();
  assert.ok(r.chips.every(node => node.hidden)); assert.ok(dots.every(node => node.hidden)); assert.equal(r.more.textContent, "+3");
});

test("prioritized facts show dots only between visible facts and never before ellipsis", () => {
  const f = fixture(), r = f.facts(117), dots = f.addDots(r.row, r.facts);
  const manager = f.api.attach(f.grid, {}); f.flush();
  assert.deepEqual(r.facts.map(node => node.hidden), [true, false, true, true, false]);
  assert.deepEqual(dots.map(node => node.hidden), [true, true, true, false]);
  r.row.width = 300; manager.refresh(); f.flush(); assert.ok(dots.every(node => !node.hidden));
  r.row.width = 70; manager.refresh(); f.flush();
  assert.equal(r.facts.filter(node => !node.hidden).length, 1); assert.ok(dots.every(node => node.hidden));
});

test("packing uses distinct dot gaps and plain overflow gaps at exact boundaries", () => {
  const api = load();
  assert.equal(api.fitCount([30, 30, 30], 98, 13, 20, 5), 2);
  assert.equal(api.fitCount([30, 30, 30], 97, 13, 20, 5), 1);
  assert.deepEqual(plain(api.fitFacts([32, 60], [90, 100], 117, 11, 10, true, 4)), [0, 1]);
  assert.deepEqual(plain(api.fitFacts([32, 60], [90, 100], 116, 11, 10, true, 4)), [1]);
});

test("manager measures hidden chips, packs complete items and restores all items when widened", () => {
  const f = fixture(), r = f.resources(); r.chips[1].hidden = true;
  const manager = f.api.attach(f.grid, { size: "small" });
  assert.equal(f.grid.getAttribute("data-card-size"), "small"); assert.equal(f.frames.size, 1); f.flush();
  assert.deepEqual(r.chips.map(chip => chip.hidden), [false, false, true]);
  assert.equal(r.more.hidden, false); assert.equal(r.more.textContent, "+1");
  assert.equal(r.more.getAttribute("aria-label"), "另有 1 种压制格式，查看全部资源");
  r.row.width = 150; manager.refresh(); f.flush();
  assert.deepEqual(r.chips.map(chip => chip.hidden), [false, false, false]); assert.equal(r.more.hidden, true);
});

test("manager excludes row padding and borders and honors computed gaps", () => {
  const f = fixture(), r = f.resources(117);
  r.row.css = { paddingLeft: "10px", paddingRight: "10px", borderLeftWidth: "1px", borderRightWidth: "1px", columnGap: "8px" };
  f.api.attach(f.grid, {}); f.flush();
  assert.deepEqual(r.chips.map(chip => chip.hidden), [false, true, true]); assert.equal(r.more.textContent, "+2");
});

test("very narrow resources retain overflow without an unnecessary resource count", () => {
  const f = fixture(), r = f.resources(30);
  f.api.attach(f.grid, {}); f.flush();
  assert.deepEqual(r.chips.map(chip => chip.hidden), [true, true, true]);
  assert.equal(r.more.textContent, "+3"); assert.equal(r.more.hidden, false);
});

test("manager facts retain score and year in source order with a single ellipsis", () => {
  const f = fixture(), r = f.facts(); const manager = f.api.attach(f.grid, {}); f.flush();
  assert.deepEqual(r.facts.map(fact => fact.hidden), [true, false, true, true, false]);
  assert.equal(r.more.hidden, false); assert.equal(r.more.textContent, "…");
  r.row.width = 250; manager.refresh(); f.flush();
  assert.equal(r.facts.every(fact => !fact.hidden), true); assert.equal(r.more.hidden, true);
});

test("extra facts remain accessible even when all base facts fit", () => {
  const f = fixture(), r = f.facts(250); r.row.setAttribute("data-extra-facts", "true");
  const manager = f.api.attach(f.grid, {}); f.flush();
  assert.equal(r.facts.every(fact => !fact.hidden), true); assert.equal(r.more.hidden, false);
  // 212px of facts + four 4px gaps + one 4px gap + 10px control = 242px.
  r.row.width = 242; manager.refresh(); f.flush();
  assert.equal(r.facts.every(fact => !fact.hidden), true); assert.equal(r.more.hidden, false);
  r.row.width = 241; manager.refresh(); f.flush();
  assert.deepEqual(r.facts.map(fact => fact.hidden), [true, false, false, false, false]); assert.equal(r.more.hidden, false);
  r.row.removeAttribute("data-extra-facts"); manager.refresh(); f.flush();
  assert.equal(r.facts.every(fact => !fact.hidden), true); assert.equal(r.more.hidden, true);
});

test("extra-only facts show their control without creating an unnecessary fact gap", () => {
  const f = fixture(), r = f.facts(10); r.row.children = [r.more]; r.row.setAttribute("data-extra-facts", "true");
  const manager = f.api.attach(f.grid, {}); f.flush(); assert.equal(r.more.hidden, false);
  r.row.setAttribute("data-extra-facts", "false"); manager.refresh(); f.flush(); assert.equal(r.more.hidden, true);
});

test("zero-width hidden grids and rows retain their previous visibility until measurable", () => {
  const f = fixture(), r = f.resources(); r.chips[2].hidden = true; r.more.hidden = false; r.more.textContent = "+1";
  f.grid.hidden = true; const manager = f.api.attach(f.grid, {}); f.flush();
  assert.deepEqual(r.chips.map(chip => chip.hidden), [false, false, true]); assert.equal(r.more.textContent, "+1");
  f.grid.hidden = false; r.row.width = 0; manager.refresh(); f.flush();
  assert.deepEqual(r.chips.map(chip => chip.hidden), [false, false, true]);
  r.row.width = 150; manager.refresh(); f.flush(); assert.equal(r.chips.every(chip => !chip.hidden), true);
});

test("unmeasurable individual chips or facts restore prior visibility rather than making a false layout", () => {
  const f = fixture(), r = f.resources(), facts = f.facts();
  r.chips[1].hidden = true; r.chips[2].width = 0; r.more.textContent = "+2"; r.more.hidden = false;
  facts.facts[1].hidden = true; facts.facts[0].width = 0;
  f.api.attach(f.grid, {}); f.flush();
  assert.deepEqual(r.chips.map(chip => chip.hidden), [false, true, false]); assert.equal(r.more.textContent, "+2"); assert.equal(r.more.hidden, false);
  assert.deepEqual(facts.facts.map(fact => fact.hidden), [false, true, false, false, false]); assert.equal(facts.more.hidden, true);
});

test("empty resource and fact rows hide overflow controls", () => {
  const f = fixture(), r = f.resources(100, []), facts = f.facts();
  facts.row.children = [facts.more]; r.more.hidden = false; facts.more.hidden = false;
  f.api.attach(f.grid, {}); f.flush(); assert.equal(r.more.hidden, true); assert.equal(facts.more.hidden, true);
});

test("auto targets 160px columns, never imposes a horizontal minimum on tiny windows, and restores inline style", () => {
  const f = fixture(); f.grid.style.gridTemplateColumns = "repeat(3, 1fr)"; f.grid.setAttribute("data-card-size", "large");
  f.grid.width = 900; const manager = f.api.attach(f.grid, { size: "auto" }); f.flush();
  assert.equal(f.grid.style.gridTemplateColumns, "repeat(5, minmax(0, 1fr))");
  f.grid.width = 300; manager.refresh(); f.flush(); assert.equal(f.grid.style.gridTemplateColumns, "repeat(2, minmax(0, 1fr))");
  f.grid.width = 90; manager.refresh(); f.flush(); assert.equal(f.grid.style.gridTemplateColumns, "repeat(1, minmax(0, 1fr))");
  manager.dispose(); assert.equal(f.grid.style.gridTemplateColumns, "repeat(3, 1fr)"); assert.equal(f.grid.getAttribute("data-card-size"), "large");
});

test("resize, font and theme changes coalesce into one frame and use scoped observers", async () => {
  const f = fixture(), manager = f.api.attach(f.grid, {}); f.flush();
  assert.deepEqual(f.resizeObservers[0].observed.map(item => item.node), [f.grid]);
  assert.deepEqual(f.mutationObservers[0].observed.map(item => item.node), [f.document.documentElement, f.document.body]);
  assert.deepEqual(plain(f.mutationObservers[0].observed[0].options), { attributes: true, attributeFilter: ["class", "style", "data-theme", "data-font", "data-appearance"] });
  f.window.emit("resize"); f.resizeObservers[0].callback(); f.mutationObservers[0].callback(); f.document.fonts.emit("loadingdone"); manager.refresh();
  assert.equal(f.frames.size, 1); f.flush();
  f.fontReady(); await Promise.resolve(); assert.equal(f.frames.size, 1);
});

test("list mode packs its rows without setting automatic grid columns", () => {
  const f = fixture(), r = f.resources(); f.grid.className += " is-list";
  const manager = f.api.attach(f.grid, { size: "auto" }); f.flush();
  assert.equal(f.grid.style.gridTemplateColumns || "", ""); assert.equal(r.more.textContent, "+1");
  f.grid.className = "library-grid"; manager.refresh(); f.flush(); assert.match(f.grid.style.gridTemplateColumns, /^repeat/);
  f.grid.className += " is-list"; manager.refresh(); f.flush(); assert.equal(f.grid.style.gridTemplateColumns, "");
});

test("dispose removes observers and listeners, cancels work and ignores late font readiness", async () => {
  const f = fixture(), r = f.resources(), manager = f.api.attach(f.grid, { size: "auto" });
  assert.equal(f.window.count("resize"), 1); assert.equal(f.document.fonts.count("loadingdone"), 1);
  manager.dispose(); manager.dispose();
  assert.equal(f.frames.size, 0); assert.equal(f.cancelled.length, 1);
  assert.equal(f.resizeObservers[0].disconnected, true); assert.equal(f.mutationObservers[0].disconnected, true);
  assert.equal(f.window.count("resize"), 0); assert.equal(f.document.fonts.count("loadingdone"), 0);
  assert.equal(f.grid.getAttribute("data-card-size"), null);
  f.fontReady(); await Promise.resolve(); manager.refresh(); f.resizeObservers[0].callback(); f.mutationObservers[0].callback();
  assert.equal(f.frames.size, 0); assert.equal(r.chips.every(chip => !chip.hidden), true);
});

test("reattaching a grid disposes old ownership without duplicate listeners or stale column state", () => {
  const f = fixture(), first = f.api.attach(f.grid, { size: "auto" }); f.flush();
  const second = f.api.attach(f.grid, { size: "large" }); f.flush();
  assert.equal(f.grid.getAttribute("data-card-size"), "large"); assert.equal(f.grid.style.gridTemplateColumns, "");
  assert.equal(f.resizeObservers[0].disconnected, true); assert.equal(f.window.count("resize"), 1);
  first.dispose(); assert.equal(f.grid.getAttribute("data-card-size"), "large");
  second.dispose(); assert.equal(f.window.count("resize"), 0); assert.equal(f.grid.getAttribute("data-card-size"), null);
});

test("timer fallback is coalesced and cancellable without ResizeObserver or font APIs", () => {
  const f = fixture({ raf: false, observers: false, fonts: false }), r = f.resources();
  const manager = f.api.attach(f.grid, {}); manager.refresh(); assert.equal(f.frames.size, 1); f.flush();
  assert.equal(r.more.textContent, "+1"); manager.refresh(); manager.dispose(); assert.equal(f.frames.size, 0);
});

test("synchronous fallback works in minimal hosts without scheduling or computed-style APIs", () => {
  const f = fixture({ raf: false, timers: false, observers: false, fonts: false }), r = f.resources();
  delete f.window.getComputedStyle;
  const manager = f.api.attach(f.grid, {}); assert.equal(r.more.textContent, "+1");
  r.row.width = 150; manager.refresh(); assert.equal(r.more.hidden, true); manager.dispose();
});
