"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");
const source = fs.readFileSync(path.join(__dirname, "../frontend/card-popover.js"), "utf8");

function fixture({ width = 800, height = 600, popupWidth = 320, popupHeight = 180 } = {}) {
  let focused = null;
  class EventTarget {
    constructor() { this.listeners = new Map(); }
    addEventListener(name, fn, capture) { const rows = this.listeners.get(name) || []; rows.push({ fn, capture }); this.listeners.set(name, rows); }
    removeEventListener(name, fn, capture) { this.listeners.set(name, (this.listeners.get(name) || []).filter(row => row.fn !== fn || row.capture !== capture)); }
    dispatch(name, target = this, values = {}) { const event = { target, prevented: false, stopped: false, preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; }, ...values }; (this.listeners.get(name) || []).slice().forEach(row => row.fn(event)); return event; }
    get listenerCount() { return [...this.listeners.values()].reduce((count, rows) => count + rows.length, 0); }
  }
  class Element extends EventTarget {
    constructor(tag) { super(); this.tag = tag; this.children = []; this.attrs = {}; this.style = {}; this._text = ""; this.bounds = null; }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
    set textContent(value) { this._text = String(value); this.children = []; }
    get isConnected() { return this === document.body || !!this.parent && this.parent.isConnected; }
    get parentElement() { return this.parent; }
    appendChild(node) { this.children.push(node); node.parent = this; return node; }
    remove() { if (this.parent) { this.parent.children = this.parent.children.filter(child => child !== this); this.parent = null; } }
    contains(node) { return this === node || this.children.some(child => child.contains(node)); }
    setAttribute(key, value) { this.attrs[key] = String(value); }
    getAttribute(key) { return this.attrs[key] === undefined ? null : this.attrs[key]; }
    removeAttribute(key) { delete this.attrs[key]; }
    focus(options) { focused = this; this.focusOptions = options; }
    getBoundingClientRect() { return this.bounds || { left: 0, top: 0, bottom: popupHeight, width: popupWidth, height: popupHeight }; }
  }
  const document = new EventTarget(); document.createElement = tag => new Element(tag); document.body = new Element("body"); document.documentElement = { clientWidth: width, clientHeight: height }; document.contains = node => document.body.contains(node);
  const observers = [];
  class MutationObserver { constructor(callback) { this.callback = callback; this.disconnected = false; observers.push(this); } observe(target, options) { this.target = target; this.options = options; } disconnect() { this.disconnected = true; } }
  const window = new EventTarget(); Object.assign(window, { innerWidth: width, innerHeight: height, MutationObserver });
  vm.runInNewContext(source, { window, document });
  const card = document.body.appendChild(new Element("article")), anchor = card.appendChild(new Element("button")); anchor.bounds = { left: 50, top: 40, bottom: 70, width: 90, height: 30 }; anchor.focus();
  return { window, document, card, anchor, api: window.NimdaLibraryCardPopover, observers, Element,
    get focused() { return focused; }, get popup() { return document.body.children.find(child => child.className === "library-card-popover"); },
    get tooltip() { return document.body.children.find(child => child.className === "library-card-tooltip"); },
    mutations() { observers.filter(observer => !observer.disconnected).forEach(observer => observer.callback()); } };
}

test("popover displays only safe text outside the card and exposes a labelled nonmodal dialog", () => {
  const f = fixture(), before = f.card.children.slice();
  const popup = f.api.open(f.anchor, { title: "<img src=x>", lines: ["<script>alert(1)</script>", "BDRip · VCB", { dangerous: true }] });
  assert.equal(popup.parent, f.document.body);
  assert.deepEqual(f.card.children, before);
  assert.equal(popup.getAttribute("role"), "dialog"); assert.equal(popup.getAttribute("aria-modal"), "false");
  assert.equal(popup.getAttribute("aria-labelledby"), popup.children[0].children[0].id);
  assert.equal(popup.children[0].children[0].textContent, "<img src=x>");
  assert.deepEqual(popup.children[1].children.map(line => line.textContent), ["<script>alert(1)</script>", "BDRip · VCB"]);
  assert.equal(f.anchor.getAttribute("aria-expanded"), "true"); assert.equal(f.anchor.getAttribute("aria-controls"), popup.id);
  assert.equal(f.focused, popup.children[0].children[1]);
  assert.equal(popup.style.position, "fixed"); assert.equal(popup.style.left, "50px"); assert.equal(popup.style.top, "76px");
});

test("same anchor toggles closed, restores focus and removes every listener and observer", () => {
  const f = fixture(); f.anchor.setAttribute("aria-haspopup", "menu");
  f.api.open(f.anchor, { title: "资源", lines: ["一行"] }); const dismiss = f.focused;
  assert.equal(f.document.listenerCount, 4); assert.equal(f.window.listenerCount, 1);
  assert.equal(f.api.open(f.anchor, { title: "资源", lines: ["一行"] }), null);
  assert.equal(f.popup, undefined); assert.equal(f.focused, f.anchor);
  assert.equal(f.anchor.getAttribute("aria-haspopup"), "menu"); assert.equal(f.anchor.getAttribute("aria-controls"), null);
  assert.equal(f.document.listenerCount, 0); assert.equal(f.window.listenerCount, 0);
  assert.equal(f.observers[0].disconnected, true);
  assert.equal(dismiss.listenerCount, 0);
});

test("Escape and close restore focus without scrolling; outside click does not steal focus", () => {
  const f = fixture(); f.api.open(f.anchor, { title: "资源", lines: [] });
  const event = f.document.dispatch("keydown", f.focused, { key: "Escape" });
  assert.equal(event.prevented, true); assert.equal(event.stopped, true); assert.equal(f.focused, f.anchor);
  assert.equal(f.anchor.focusOptions.preventScroll, true);
  f.api.open(f.anchor, {}); const close = f.focused; close.dispatch("click"); assert.equal(f.focused, f.anchor); assert.equal(f.popup, undefined);
  f.api.open(f.anchor, {}); const outside = f.document.body.appendChild(new f.Element("input")); outside.focus();
  f.document.dispatch("pointerdown", outside); assert.equal(f.popup, undefined); assert.equal(f.focused, outside);
  f.api.open(f.anchor, {}); outside.focus(); f.document.dispatch("click", outside); assert.equal(f.popup, undefined); assert.equal(f.focused, outside);
});

test("inside interaction and internal scrolling stay open; page scrolling and resize clean up", () => {
  const f = fixture(); f.api.open(f.anchor, { lines: ["测试"] }); const popup = f.popup;
  f.document.dispatch("pointerdown", popup.children[1]); f.document.dispatch("click", f.anchor); f.document.dispatch("scroll", popup.children[1]);
  assert.equal(f.popup, popup);
  f.document.dispatch("scroll", f.document); assert.equal(f.popup, undefined); assert.equal(f.document.listenerCount, 0);
  f.api.open(f.anchor, {}); f.window.dispatch("resize"); assert.equal(f.popup, undefined); assert.equal(f.window.listenerCount, 0);
});

test("right and bottom edge placement flips above and clamps to viewport", () => {
  const f = fixture({ width: 500, height: 300, popupWidth: 320, popupHeight: 180 });
  f.anchor.bounds = { left: 450, top: 250, bottom: 280, width: 40, height: 30 };
  const popup = f.api.open(f.anchor, {});
  assert.equal(popup.style.left, "168px"); assert.equal(popup.style.top, "64px");
  assert.equal(popup.style.maxWidth, "476px"); assert.equal(popup.style.maxHeight, "276px");
});

test("oversized popovers are constrained and anchors outside viewport cannot position them offscreen", () => {
  const f = fixture({ width: 240, height: 160, popupWidth: 900, popupHeight: 600 });
  f.anchor.bounds = { left: -100, top: -100, bottom: -50, width: 20, height: 50 };
  const popup = f.api.open(f.anchor, {});
  assert.equal(popup.style.left, "12px"); assert.equal(popup.style.top, "12px");
  assert.equal(popup.style.maxWidth, "216px"); assert.equal(popup.style.maxHeight, "136px");
});

test("changing anchors replaces the instance without restoring focus to the retired anchor", () => {
  const f = fixture(), other = f.card.appendChild(new f.Element("button")); other.bounds = f.anchor.bounds;
  f.api.open(f.anchor, {}); const old = f.popup;
  f.api.open(other, { title: "新详情" }); assert.equal(old.isConnected, false); assert.notEqual(f.popup, old);
  assert.equal(f.document.listenerCount, 4); assert.equal(f.window.listenerCount, 1);
  assert.equal(f.anchor.getAttribute("aria-expanded"), null); assert.equal(other.getAttribute("aria-expanded"), "true");
  f.api.close(); assert.equal(f.focused, other);
});

test("removed anchors or popup nodes release observers and listeners without focusing detached nodes", () => {
  const f = fixture(); f.api.open(f.anchor, {}); const focused = f.focused; f.anchor.remove(); f.mutations();
  assert.equal(f.popup, undefined); assert.equal(f.document.listenerCount, 0); assert.equal(f.observers[0].disconnected, true);
  assert.equal(f.focused, focused); assert.equal(f.api.open(f.anchor, {}), null);
  f.card.appendChild(f.anchor); f.api.open(f.anchor, {}); f.popup.remove(); f.mutations();
  assert.equal(f.document.listenerCount, 0); assert.equal(f.window.listenerCount, 0);
});

test("dispose and close(false) are idempotent and never restore the anchor focus", () => {
  const f = fixture(); f.api.open(f.anchor, {}); const focused = f.focused; f.api.close(false); f.api.dispose(); f.api.close();
  assert.equal(f.focused, focused); assert.equal(f.document.listenerCount, 0); assert.equal(f.window.listenerCount, 0);
  f.api.open(f.anchor, {}); f.api.dispose(); assert.equal(f.popup, undefined); assert.notEqual(f.focused, f.anchor);
});

test("structured resource sections keep full details as safe text alongside legacy lines", () => {
  const f = fixture(), popup = f.api.open(f.anchor, {
    title: "资源", lines: ["原有内容"], sections: [
      { title: "BDRip/VCB", fields: [{ label: "压制组", value: "VCB-Studio" }, { label: "目录", value: "<img src=x>" }] },
      { title: "BDRip/----", fields: [{ label: "字幕组", value: "未填写" }, null, { label: "忽略", value: {} }] },
      null, "忽略", {}
    ]
  });
  const body = popup.children[1];
  assert.equal(body.children.length, 3);
  assert.equal(body.children[0].textContent, "原有内容");
  const first = body.children[1], fields = first.children[1];
  assert.equal(first.className, "library-card-popover__section");
  assert.equal(first.children[0].className, "library-card-popover__section-title");
  assert.equal(first.children[0].textContent, "BDRip/VCB");
  assert.equal(fields.className, "library-card-popover__fields");
  assert.deepEqual(fields.children.map(node => node.tag), ["dt", "dd", "dt", "dd"]);
  assert.deepEqual(fields.children.map(node => node.textContent), ["压制组", "VCB-Studio", "目录", "<img src=x>"]);
  assert.equal(fields.children[1].className, "library-card-popover__field-value");
  assert.equal(body.children[2].children[1].children.length, 2);
});

test("delegated tooltip preserves multiline group-only text, existing description and focus", () => {
  const f = fixture(), text = "VCB·VCB-Studio\n----\nCK·澄空学园", child = f.anchor.appendChild(new f.Element("span"));
  f.anchor.setAttribute("data-card-tooltip", text); f.anchor.setAttribute("aria-describedby", "existing-help");
  const manager = f.api.bindTooltip(f.card), focused = f.focused;
  f.card.dispatch("pointerover", child, { pointerType: "mouse" });
  const tooltip = f.tooltip;
  assert.equal(tooltip.textContent, text); assert.equal(tooltip.getAttribute("role"), "tooltip");
  assert.equal(tooltip.parent, f.document.body); assert.equal(tooltip.children.length, 0);
  assert.equal(tooltip.getAttribute("tabindex"), null); assert.equal(f.anchor.getAttribute("title"), null);
  assert.equal(f.anchor.getAttribute("aria-describedby"), "existing-help " + tooltip.id);
  assert.equal(f.focused, focused); assert.equal(f.observers.length, 1);
  f.card.dispatch("pointerout", child, { relatedTarget: f.anchor }); assert.equal(f.tooltip, tooltip);
  f.card.dispatch("pointerover", f.anchor); assert.equal(f.tooltip, tooltip);
  f.card.dispatch("pointerout", f.anchor, { relatedTarget: f.card });
  assert.equal(f.tooltip, undefined); assert.equal(f.anchor.getAttribute("aria-describedby"), "existing-help");
  manager.dispose();
});

test("keyboard focus gets a tooltip without focus movement and blur or Escape dismisses it", () => {
  const f = fixture(); f.anchor.setAttribute("data-card-tooltip", "----");
  const manager = f.api.bindTooltip(f.card);
  f.card.dispatch("focusin", f.anchor); assert.equal(f.tooltip.textContent, "----"); assert.equal(f.focused, f.anchor);
  f.card.dispatch("focusout", f.anchor, { relatedTarget: f.card }); assert.equal(f.tooltip, undefined);
  assert.equal(f.anchor.getAttribute("aria-describedby"), null);
  f.card.dispatch("focusin", f.anchor);
  const event = f.document.dispatch("keydown", f.anchor, { key: "Escape" });
  assert.equal(f.tooltip, undefined); assert.equal(event.prevented, false); assert.equal(event.stopped, false);
  manager.dispose();
});

test("tooltip position prefers above and clamps small viewports with below fallback", () => {
  const f = fixture({ width: 500, height: 500, popupWidth: 200, popupHeight: 100 });
  f.anchor.setAttribute("data-card-tooltip", "VCB·VCB-Studio"); f.anchor.bounds = { left: 460, top: 300, bottom: 330, width: 40, height: 30 };
  const manager = f.api.bindTooltip(f.card); f.card.dispatch("pointerover", f.anchor);
  assert.equal(f.tooltip.style.top, "194px"); assert.equal(f.tooltip.style.left, "288px");
  manager.hide(); f.anchor.bounds.top = 20; f.anchor.bounds.bottom = 50; f.card.dispatch("pointerover", f.anchor);
  assert.equal(f.tooltip.style.top, "56px"); manager.dispose();
  const small = fixture({ width: 200, height: 150, popupWidth: 500, popupHeight: 400 });
  small.anchor.setAttribute("data-card-tooltip", "----"); small.api.bindTooltip(small.card); small.card.dispatch("pointerover", small.anchor);
  assert.equal(small.tooltip.style.left, "12px"); assert.equal(small.tooltip.style.top, "12px");
  assert.equal(small.tooltip.style.maxWidth, "176px"); assert.equal(small.tooltip.style.maxHeight, "126px"); small.api.dispose();
});

test("click, pointerdown, scrolling and resize dismiss hover before full click details open", () => {
  const f = fixture(); f.anchor.setAttribute("data-card-tooltip", "VCB·VCB-Studio\n----");
  const manager = f.api.bindTooltip(f.card);
  ["click", "pointerdown", "scroll"].forEach(name => {
    f.card.dispatch("pointerover", f.anchor); assert.ok(f.tooltip);
    f.document.dispatch(name, f.anchor); assert.equal(f.tooltip, undefined);
  });
  f.card.dispatch("pointerover", f.anchor); f.window.dispatch("resize"); assert.equal(f.tooltip, undefined);
  f.card.dispatch("pointerover", f.anchor); f.api.open(f.anchor, { sections: [{ title: "BDRip/VCB", fields: [{ label: "目录", value: "作品_BDRip(VCB)" }] }] });
  assert.equal(f.tooltip, undefined); assert.ok(f.popup); assert.equal(f.anchor.getAttribute("aria-describedby"), null);
  f.card.dispatch("pointerover", f.anchor); assert.equal(f.tooltip, undefined);
  assert.equal(f.popup.children[1].textContent, "BDRip/VCB目录作品_BDRip(VCB)");
  f.api.close(false); manager.dispose();
});

test("tooltip root delegates new cards using one observer and removes detached anchors", () => {
  const f = fixture(), manager = f.api.bindTooltip(f.card), other = f.card.appendChild(new f.Element("button"));
  other.setAttribute("data-card-tooltip", "CK·澄空学园"); f.card.dispatch("pointerover", other);
  assert.equal(f.tooltip.textContent, "CK·澄空学园"); assert.equal(f.observers.length, 1);
  other.remove(); f.mutations(); assert.equal(f.tooltip, undefined); assert.equal(other.getAttribute("aria-describedby"), null);
  assert.equal(f.observers[0].disconnected, false);
  f.anchor.setAttribute("data-card-tooltip", "----"); f.card.dispatch("pointerover", f.anchor); const focused = f.focused;
  f.card.remove(); f.mutations();
  assert.equal(f.tooltip, undefined); assert.equal(f.focused, focused); assert.equal(f.observers[0].disconnected, true);
  assert.equal(f.card.listenerCount, 0); assert.equal(f.document.listenerCount, 0); assert.equal(f.window.listenerCount, 0);
  manager.dispose();
});

test("tooltip disposal cleans every listener and observer and does not reanimate stale managers", () => {
  const f = fixture(); f.anchor.setAttribute("data-card-tooltip", "----");
  const manager = f.api.bindTooltip(f.card); f.card.dispatch("pointerover", f.anchor);
  assert.equal(f.card.listenerCount, 4); assert.equal(f.document.listenerCount, 4); assert.equal(f.window.listenerCount, 1);
  manager.dispose(); manager.dispose(); f.card.dispatch("pointerover", f.anchor);
  assert.equal(f.tooltip, undefined); assert.equal(f.card.listenerCount, 0); assert.equal(f.document.listenerCount, 0); assert.equal(f.window.listenerCount, 0);
  assert.equal(f.observers[0].disconnected, true);
  f.api.bindTooltip(f.card); f.card.dispatch("focusin", f.anchor); f.api.dispose();
  assert.equal(f.tooltip, undefined); assert.equal(f.card.listenerCount, 0); assert.equal(f.document.listenerCount, 0);
});

test("only one tooltip is visible across roots, and touch or blank text never opens one", () => {
  const f = fixture(), otherRoot = f.document.body.appendChild(new f.Element("section")), other = otherRoot.appendChild(new f.Element("button"));
  f.anchor.setAttribute("data-card-tooltip", "VCB·VCB-Studio"); other.setAttribute("data-card-tooltip", "----");
  f.api.bindTooltip(f.card); f.api.bindTooltip(otherRoot);
  f.card.dispatch("pointerover", f.anchor, { pointerType: "touch" }); assert.equal(f.tooltip, undefined);
  f.card.dispatch("pointerover", f.anchor, { pointerType: "mouse" }); const previous = f.tooltip;
  otherRoot.dispatch("pointerover", other); assert.equal(previous.isConnected, false); assert.equal(f.tooltip.textContent, "----");
  assert.equal(f.anchor.getAttribute("aria-describedby"), null);
  other.setAttribute("data-card-tooltip", "  "); otherRoot.dispatch("pointerover", other); assert.equal(f.tooltip, undefined);
  f.api.dispose(); assert.equal(f.card.listenerCount, 0); assert.equal(otherRoot.listenerCount, 0); assert.equal(f.document.listenerCount, 0);
  const detached = new f.Element("section"); f.api.bindTooltip(detached).dispose(); f.api.bindTooltip(null).dispose(); assert.equal(f.observers.length, 2);
});
