"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");
const record = () => ({ id: "work:test", metadata: { summary: "manual" }, source_refs: [{ provider: "tmdb", external_id: "1" }], attributes: [
  { type: "name", data: "作品" }, { type: "country", data: "japan" },
  { type: "collection-type", data: { domain: "animation", release_type: "tv", path: "U:\\作品", collectioned: [{ press_format: "BDRip", press_group: "VCB", press_path: "old", custom: "keep" }], continuations: [{ title: "SP", collectioned: [{ press_format: "DVD", press_path: "special" }] }] } }
] });
function setup(overrides = {}) {
  let document;
  class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.attributes = {}; this.handlers = {}; this.value = ""; this.hidden = false; this.disabled = false; this.textContent = ""; }
    appendChild(child) { this.children.push(child); child.parent = this; return child; }
    removeChild(child) { this.children = this.children.filter(value => value !== child); child.parent = null; }
    remove() { if (this.parent) this.parent.removeChild(this); }
    get firstChild() { return this.children[0]; }
    get isConnected() { return this === document.body || !!(this.parent && this.parent.isConnected); }
    setAttribute(name, value) { this.attributes[name] = value; }
    querySelectorAll(selector) { const tags = selector.split(",").map(value => value.trim()); return nodes(this).slice(1).filter(node => tags.includes(node.tag)); }
    addEventListener(name, callback) { (this.handlers[name] ||= []).push(callback); }
    async emit(name) { for (const callback of this.handlers[name] || []) callback({ target: this, preventDefault() {} }); for (let i = 0; i < 15; i++) await Promise.resolve(); }
    focus() { document.activeElement = this; }
    showModal() { this.open = true; }
    close() { this.open = false; this.emit("close"); }
  }
  document = { createElement: tag => new Element(tag) }; document.body = new Element("body"); const opener = document.body.appendChild(new Element("button")); opener.focus();
  const window = { document }, context = vm.createContext({ window, document, console }), calls = [], saves = [], errors = [];
  for (const filename of ["../../../framework/frontend/src/common/enum-fields.js", "../../collection-detail/frontend/work-record-form.js", "../frontend/batch-resource-dialog.js"]) {
    vm.runInContext(fs.readFileSync(path.resolve(__dirname, filename), "utf8"), context);
  }
  const options = { record: record(), enumOptions: { press_format: ["BDRip", "WEB"], press_group: ["VCB", "----"] }, enumLabels: { press_format: { WEB: "网络资源" } },
    async request(url) { calls.push(url); return { ok: true, node: { relpath: "", name: "资源库", children: [] } }; },
    async onSubmit(next, patch) { saves.push({ next: JSON.parse(JSON.stringify(next)), patch: JSON.parse(JSON.stringify(patch)) }); return { ok: true, db_committed: true }; }, setStatus(message, error) { errors.push({ message, error }); }, ...overrides };
  const handle = window.NimdaBatchResourceDialog.open(options);
  function nodes(node = document.body) { return [node, ...node.children.flatMap(child => nodes(child))]; }
  return { options, api: window.NimdaBatchResourceDialog, document, opener, handle, calls, saves, errors, nodes,
    field: name => nodes().find(node => node.name === name), button: label => nodes().find(node => node.tag === "button" && node.textContent === label),
    async paste(value) { this.field("resource-paths").value = value; await this.button("加入待关联列表").emit("click"); },
    async set(name, value) { const field = this.field(name); field.value = value; await field.emit("input"); },
    error() { return nodes().find(node => node.attributes.role === "alert"); } };
}
test("batch resource dialog uses enum choices and preserves all existing record data through the shared patch", async () => {
  const f = setup(), original = JSON.parse(JSON.stringify(f.options.record));
  assert.equal(f.calls.length, 0); assert.equal(f.button("一次保存全部资源").disabled, true);
  assert.ok(f.nodes().some(node => node.tag === "option" && node.value === "WEB" && node.label === "网络资源"));
  await f.set("batch-format", "BDRip"); await f.set("batch-group", "VCB");
  await f.paste('"U:\\作品\\A_BDRip"\nB_WEB\na_bdrip');
  assert.match(f.error().textContent, /1 个/); assert.equal(f.saves.length, 0);
  await f.set("resource-format-1", "WEB"); await f.set("resource-group-1", "");
  await f.button("一次保存全部资源").emit("click");
  assert.equal(f.saves.length, 1); assert.equal(f.nodes().some(node => node.tag === "dialog"), false); assert.equal(f.document.activeElement, f.opener);
  assert.deepEqual(f.options.record, original);
  const saved = f.saves[0].next, collection = saved.attributes[2].data;
  assert.deepEqual(collection.collectioned, [original.attributes[2].data.collectioned[0], { press_format: "BDRip", press_group: "VCB", press_path: "A_BDRip" }, { press_format: "WEB", press_group: "", press_path: "B_WEB" }]);
  assert.deepEqual(collection.continuations, original.attributes[2].data.continuations); assert.deepEqual(saved.metadata, original.metadata); assert.deepEqual(saved.source_refs, original.source_refs);
});
test("path conversion handles pasted markers, case insensitive roots and UNC while rejecting siblings and traversal", () => {
  const f = setup(), convert = f.api.relativePath;
  assert.equal(convert('\u202a"u:\\作品\\资源"\u202c', "U:\\作品"), "资源");
  assert.equal(convert("\\\\NAS\\media\\作品\\Folder", "\\\\NAS\\media\\作品"), "Folder");
  for (const value of ["U:\\作品2\\Folder", "V:\\作品\\Folder", "U:\\作品", "../Sibling", "Folder/../Sibling", "U:relative", "/absolute", "Folder//item"]) assert.throws(() => convert(value, "U:\\作品"));
  assert.throws(() => convert("资源", "作品"), /先在编辑作品/);
});
test("invalid paste keeps the complete draft unchanged and unknown formats remain blank until explicit input", async () => {
  const f = setup(); await f.paste("one\n../unsafe"); assert.equal(f.field("resource-format-0"), undefined); assert.match(f.error().textContent, /上级跳转/);
  await f.paste("one\ntwo"); assert.equal(f.field("resource-format-0").value, "");
  await f.button("一次保存全部资源").emit("click"); assert.equal(f.saves.length, 0); assert.match(f.error().textContent, /压制格式/);
  await f.set("batch-format", "WEB"); await f.set("batch-group", "----"); await f.button("应用到待关联项").emit("click");
  assert.equal(f.field("resource-format-0").value, "WEB"); assert.equal(f.field("resource-format-1").value, "WEB");
});
test("missing or unresolved root is explicit and cannot silently expand or change the root binding", async () => {
  const data = record(); data.attributes[2].data.path = "作品";
  const f = setup({ record: data }); assert.equal(f.button("从资源库多选目录").disabled, true); assert.equal(f.button("加入待关联列表").disabled, true); assert.match(f.error().textContent, /绝对作品根目录/);
  await f.button("一次保存全部资源").emit("click"); assert.equal(f.saves.length, 0); assert.equal(f.options.record.attributes[2].data.path, "作品");
});
test("a resolved root supports legacy records without rewriting their stored root", async () => {
  const data = record(); data.attributes[2].data.path = "作品";
  const f = setup({ record: data, workRoot: "U:\\作品" }); await f.set("batch-format", "BDRip"); await f.paste("U:\\作品\\new"); await f.button("一次保存全部资源").emit("click");
  assert.equal(f.saves[0].next.attributes[2].data.path, "作品");
});
test("directory selections accumulate across navigation and already bound paths remain selectable for different resources", async () => {
  const seen = [], folder = (name, path, relpath) => ({ type: "folder", name, path, relpath, exists: true });
  const trees = { "": { name: "资源库", relpath: "", children: [folder("作品", "U:\\作品", "root:0/作品"), folder("别的作品", "U:\\别的作品", "root:0/其他")] },
    "root:0/作品": { name: "作品", path: "U:\\作品", relpath: "root:0/作品", children: [folder("A", "U:\\作品\\A", "root:0/作品/A"), folder("old", "U:\\作品\\old", "root:0/作品/old")] },
    "root:0/作品/A": { name: "A", path: "U:\\作品\\A", relpath: "root:0/作品/A", children: [folder("B", "U:\\作品\\A\\B", "root:0/作品/A/B")] } };
  const f = setup({ async request(url) { const relpath = decodeURIComponent(url.split("relpath=")[1]); seen.push(relpath); return { ok: true, node: trees[relpath] }; } });
  await f.button("从资源库多选目录").emit("click"); const checkbox = name => f.nodes().find(node => node.attributes["aria-label"] === "选择目录 " + name);
  assert.equal(checkbox("别的作品").disabled, true); assert.equal(checkbox("作品").disabled, true);
  await f.button("作品").emit("click"); assert.equal(checkbox("old").disabled, false); assert.equal(checkbox("old").checked, false); assert.match(checkbox("old").title, /其他格式或组/);
  checkbox("A").checked = true; await checkbox("A").emit("change"); await f.button("A").emit("click"); checkbox("B").checked = true; await checkbox("B").emit("change");
  assert.equal(f.nodes().filter(node => node.name && node.name.startsWith("resource-format-")).length, 2);
  await f.button("上级目录").emit("click"); assert.equal(checkbox("A").checked, true); assert.deepEqual(seen, ["", "root:0/作品", "root:0/作品/A", "root:0/作品"]);
});
test("save is guarded against double clicks, blocks dismissal, and retains all drafts on failure", async () => {
  let reject, calls = 0; const pending = new Promise((_, no) => { reject = no; });
  const f = setup({ onSubmit() { calls++; return pending; } }); await f.set("batch-format", "BDRip"); await f.paste("one\ntwo");
  await f.button("一次保存全部资源").emit("click"); assert.equal(f.button("一次保存全部资源").disabled, true);
  await f.button("一次保存全部资源").emit("click"); await f.button("取消").emit("click"); assert.equal(calls, 1); assert.ok(f.nodes().some(node => node.tag === "dialog"));
  reject(new Error("数据库版本已更新")); for (let i = 0; i < 15; i++) await Promise.resolve();
  assert.match(f.error().textContent, /数据库版本已更新/); assert.equal(f.field("resource-format-1").value, "BDRip"); assert.equal(f.button("一次保存全部资源").disabled, false);
});
test("cancelling unsaved edits is explicit and restores focus", async () => {
  const f = setup(); await f.paste("one"); await f.button("取消").emit("click"); assert.ok(f.button("放弃修改")); assert.ok(f.nodes().some(node => node.tag === "dialog"));
  await f.button("继续编辑").emit("click"); await f.button("取消").emit("click"); await f.button("放弃修改").emit("click"); assert.equal(f.saves.length, 0); assert.equal(f.document.activeElement, f.opener);
});
test("a stale directory response after close cannot resurrect the dialog or emit errors", async () => {
  let resolve; const f = setup({ request: () => new Promise(yes => { resolve = yes; }) }); await f.button("从资源库多选目录").emit("click");
  await f.button("关闭").emit("click"); resolve({ ok: false, error: "late" }); for (let i = 0; i < 15; i++) await Promise.resolve();
  assert.equal(f.nodes().some(node => node.tag === "dialog"), false); assert.equal(f.errors.length, 0);
});
test("over-limit paste is atomic and directory lists page without losing pending selections", async () => {
  const children = Array.from({ length: 24 }, (_, index) => ({ type: "folder", name: "Dir" + index, path: "U:\\作品\\Dir" + index, relpath: "root:0/作品/Dir" + index, exists: true }));
  const f = setup({ request: async () => ({ ok: true, node: { relpath: "root:0/作品", children } }) });
  await f.paste(Array.from({ length: 200 }, (_, index) => "Folder" + index).join("\n"));
  assert.equal(f.field("resource-format-0"), undefined); assert.match(f.error().textContent, /最多支持 200/);
  await f.button("从资源库多选目录").emit("click");
  assert.equal(f.nodes().filter(node => node.type === "checkbox").length, 20);
  const first = f.nodes().find(node => node.attributes["aria-label"] === "选择目录 Dir0"); first.checked = true; await first.emit("change");
  await f.button("下一页").emit("click"); assert.equal(f.nodes().filter(node => node.type === "checkbox").length, 4);
  await f.button("上一页").emit("click"); assert.equal(f.nodes().find(node => node.attributes["aria-label"] === "选择目录 Dir0").checked, true);
});
test("same directory may hold different format and group resources but exact main-row duplicates are rejected before writing", async () => {
  const f = setup();
  await f.paste("OLD"); assert.ok(f.field("resource-format-0"), "the path is not lost before the format is entered");
  await f.set("resource-format-0", "BDRip"); await f.set("resource-group-0", "VCB");
  await f.button("一次保存全部资源").emit("click"); assert.equal(f.saves.length, 0); assert.match(f.error().textContent, /资源重复/);
  await f.set("resource-group-0", "Jsum");
  await f.paste("old"); await f.set("resource-format-1", "WEB"); await f.set("resource-group-1", "VCB");
  await f.button("一次保存全部资源").emit("click"); assert.equal(f.saves.length, 1);
  assert.deepEqual(f.saves[0].next.attributes[2].data.collectioned.slice(1).map(row => [row.press_path, row.press_format, row.press_group]), [["OLD", "BDRip", "Jsum"], ["old", "WEB", "VCB"]]);
});
test("continuation resources and main resources have distinct identities, while duplicate pending rows cannot be saved", async () => {
  const f = setup(); await f.set("batch-format", "DVD"); await f.paste("special");
  await f.set("batch-format", "WEB"); await f.paste("special"); assert.ok(f.field("resource-format-1"));
  await f.set("resource-format-1", "DVD"); await f.button("一次保存全部资源").emit("click"); assert.equal(f.saves.length, 0); assert.match(f.error().textContent, /资源重复/);
  await f.set("resource-format-1", "WEB"); await f.button("一次保存全部资源").emit("click"); assert.equal(f.saves.length, 1);
  assert.equal(f.saves[0].next.attributes[2].data.collectioned.length, 3);
});
test("unverified save outcomes always retain the dialog and draft", async () => {
  for (const receipt of [undefined, false, {}, { ok: false, db_committed: true }, { ok: true, db_committed: false }, { ok: true }]) {
    const f = setup({ async onSubmit() { return receipt; } }); await f.set("batch-format", "BDRip"); await f.paste("new");
    await f.button("一次保存全部资源").emit("click");
    assert.ok(f.nodes().some(node => node.tag === "dialog")); assert.equal(f.field("resource-format-0").value, "BDRip"); assert.match(f.error().textContent, /未收到数据库保存成功/);
  }
});
test("redraws do not retain detached controls for save locking", async () => {
  let resolve; const f = setup({ onSubmit: () => new Promise(yes => { resolve = yes; }) });
  await f.set("batch-format", "BDRip"); await f.paste("new"); const detached = f.field("resource-format-0");
  await f.button("应用到待关联项").emit("click"); assert.equal(detached.isConnected, false);
  await f.button("一次保存全部资源").emit("click"); assert.equal(detached.disabled, false); assert.equal(f.field("resource-format-0").disabled, true);
  resolve({ db_committed: true }); for (let i = 0; i < 15; i++) await Promise.resolve(); assert.equal(f.nodes().some(node => node.tag === "dialog"), false);
});
