"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), test = require("node:test"), vm = require("node:vm");
const SOURCE = fs.readFileSync(path.join(__dirname, "../frontend/display-settings.js"), "utf8");
const flush = () => new Promise(resolve => setImmediate(resolve));
const payload = () => ({ revision: "version1", config: { default_priority: "original" }, collection_types: [{ domain: "television", country: "korea", label: "电视剧 · 韩国", priority: "chinese" }] });
function fixture(load = async () => payload(), save = async () => ({ ok: true })) {
  class Element {
    constructor(tag) { this.tagName = tag; this.children = []; this.listeners = {}; this.attributes = {}; this.value = ""; this.textContent = ""; }
    appendChild(child) { child.parent = this; this.children.push(child); return child; }
    setAttribute(name, value) { this.attributes[name] = value; }
    addEventListener(name, handler) { this.listeners[name] = handler; }
    remove() { this.parent.children = this.parent.children.filter(row => row !== this); }
    focus() { document.activeElement = this; }
    showModal() { this.open = true; }
    close() { this.open = false; if (this.listeners.close) this.listeners.close(); }
  }
  const document = { createElement: tag => new Element(tag), body: new Element("body") }, window = {}, saves = [], notices = [];
  vm.runInNewContext(SOURCE, { window, document });
  const traverse = node => [node, ...node.children.flatMap(traverse)];
  return { api: window.NimdaLibraryDisplaySettings, nodes: () => traverse(document.body), saves, notices,
    open: () => window.NimdaLibraryDisplaySettings.open({ load, save: async body => { saves.push(JSON.parse(JSON.stringify(body))); return save(body); }, setStatus: message => notices.push(message) }) };
}
test("display dialog has editable per-type priorities and saves one versioned configuration", async () => {
  const f = fixture(); await f.open(); const selects = f.nodes().filter(node => node.tagName === "select");
  assert.equal(f.nodes().find(node => node.tagName === "h2").textContent, "章节标题、人物与角色名");
  assert.match(selects[1].attributes["aria-label"], /电视剧 · 韩国标题、人物与角色名语言/);
  assert.deepEqual(selects.map(node => node.value), ["original", "chinese"]); selects[1].value = "original";
  await f.nodes().find(node => node.tagName === "form").listeners.submit({ preventDefault() {} });
  assert.deepEqual(f.saves[0], { revision: "version1", config: { schema_version: 1, default_priority: "original", chapter_titles: [{ domain: "television", country: "korea", priority: "original" }] } });
  assert.equal(f.nodes().some(node => node.tagName === "dialog"), false);
});
test("a configuration conflict retains the draft instead of closing or retrying with a new revision", async () => {
  const f = fixture(undefined, async () => { throw Error("显示配置已变化"); }); await f.open();
  await f.nodes().find(node => node.tagName === "form").listeners.submit({ preventDefault() {} });
  assert.equal(f.saves.length, 1); assert.equal(f.nodes().find(node => node.tagName === "dialog").open, true); assert.match(f.notices[0], /已变化/);
});
test("closing during load retires the response and never saves", async () => {
  let resolve; const f = fixture(() => new Promise(done => { resolve = done; })), task = f.open();
  f.api.close(); resolve(payload()); await task; await flush();
  assert.equal(f.nodes().length, 1); assert.equal(f.saves.length, 0);
});

test("save locks every dropdown and duplicate submit until the request resolves", async () => {
  let reject; const f = fixture(undefined, () => new Promise((_resolve, fail) => { reject = fail; })); await f.open();
  const form = f.nodes().find(node => node.tagName === "form"), selectors = f.nodes().filter(node => node.tagName === "select");
  const task = form.listeners.submit({ preventDefault() {} });
  assert.ok(selectors.every(node => node.disabled)); assert.ok(f.nodes().filter(node => node.tagName === "button").every(node => node.disabled));
  await form.listeners.submit({ preventDefault() {} }); assert.equal(f.saves.length, 1);
  reject(Error("保存失败")); await task;
  assert.ok(selectors.every(node => !node.disabled)); assert.deepEqual(selectors.map(node => node.value), ["original", "chinese"]);
});

test("missing or unsuccessful save receipts preserve the dialog and report the failure", async () => {
  for (const receipt of [undefined, false, {}, { ok: false, error: "服务端拒绝保存" }]) {
    const f = fixture(undefined, async () => receipt); await f.open();
    await f.nodes().find(node => node.tagName === "form").listeners.submit({ preventDefault() {} });
    assert.equal(f.nodes().find(node => node.tagName === "dialog").open, true);
    assert.equal(f.notices.length, 1); assert.equal(f.saves.length, 1);
    assert.match(f.notices[0], /保存|未确认/);
  }
});

test("unmount while saving ignores late success or failure without recreating the dialog", async () => {
  for (const fails of [false, true]) {
    let resolve, reject;
    const f = fixture(undefined, () => new Promise((done, fail) => { resolve = done; reject = fail; })); await f.open();
    const task = f.nodes().find(node => node.tagName === "form").listeners.submit({ preventDefault() {} });
    f.api.close(); if (fails) reject(Error("late failure")); else resolve({ ok: true }); await task;
    assert.equal(f.nodes().length, 1); assert.equal(f.notices.length, 0);
  }
});
