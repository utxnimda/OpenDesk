"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../frontend/people-retry.js"), "utf8");
const plain = value => JSON.parse(JSON.stringify(value));
function fixture() {
  class Element {
    constructor(tag) { this.tagName = tag; this.children = []; this.attributes = {}; this.listeners = {}; this.textContent = ""; }
    appendChild(child) { this.children.push(child); child.parentNode = this; return child; }
    setAttribute(key, value) { this.attributes[key] = value; }
    addEventListener(key, fn) { this.listeners[key] = fn; }
    async click() { if (this.listeners.click) await this.listeners.click(); }
  }
  const window = {}, document = { createElement: tag => new Element(tag) };
  vm.runInNewContext(source, { window, document });
  return { api: window.NimdaPeopleRetry, parent: new Element("main") };
}
function preview(overrides = {}) {
  return { mode: "people-import", provider: "tmdb", snapshot_id: "old", preview_token: "parent-old", retry_failures: [{ id: "person-detail:42", object_id: "42", label: "演员", path: "/person/42", language: "ko-KR", field: "人物详情", reason: "SSLEOFError", retryable: true }], entries: [
    { external_id: "tmdb:42", entity_type: "person", action: "reuse", identity_owner_id: "local", target_id: "local", selected_fields: ["name_cn", "summary"], selected_relations: ["cast"], overrides: {},
      targets: [{ id: "local", selected_fields: ["summary"], diff: [
        { field: "name_cn", before: "旧名", after: "新名", selectable: true, editable: true },
        { field: "summary", before: "", after: "新介绍", selectable: true, editable: true },
        { field: "aliases", before: ["保留"], after: ["候选"], selectable: true, editable: true }
      ] }], relations: [{ id: "cast", role: "主演", required_entity_keys: [] }] }
  ], ...overrides };
}
function replace(next) { next.snapshot_id = "new"; next.preview_token = "parent-new"; return { preview_patch: next, snapshot_replacements: { old: "new" } }; }
test("retry preserves unchanged confirmed fields, explicit deselection and relation selections", () => {
  const { api } = fixture(), root = preview(), next = plain(root);
  root.entries[0].selected_fields = ["name_cn"];
  api.applyPatch(root, replace(next));
  assert.equal(root.preview_token, "parent-new");
  assert.deepEqual(plain(root.entries[0].selected_fields), ["name_cn"]);
  assert.deepEqual(plain(root.entries[0].selected_relations), ["cast"]);
});

test("retry preserves per-person and safe-field fold choices without changing consent", () => {
  const { api } = fixture(), root = preview(), next = plain(root);
  root.entries[0]._detailsOpen = false; root.entries[0]._autoFieldsOpen = true;
  next.entries[0]._detailsOpen = true; next.entries[0]._autoFieldsOpen = false;
  const fields = root.entries[0].selected_fields.slice();
  api.applyPatch(root, replace(next));
  assert.equal(root.entries[0]._detailsOpen, false); assert.equal(root.entries[0]._autoFieldsOpen, true);
  assert.deepEqual(plain(root.entries[0].selected_fields).sort(), fields.sort());
});
test("retry keeps language-specific manual aliases and clears while adding only new safe language fills", () => {
  const { api } = fixture(), root = preview(), entry = root.entries[0];
  entry.targets[0].diff = [
    { field: "localized.summary.zh-Hans", before: "旧简介", after: "新简介", editable: true, selectable: true },
    { field: "localized.aliases.ko", before: [], after: ["별명"], editable: true, selectable: true }
  ];
  entry.selected_fields = entry.targets[0].selected_fields = ["localized.summary.zh-Hans", "localized.aliases.ko"];
  entry.overrides = { "localized.summary.zh-Hans": "", "localized.aliases.ko": ["수정 별명"] };
  const next = plain(root); next.entries[0].targets[0].diff.push({ field: "localized.name.en", before: "", after: "Official Name", selectable: true });
  next.entries[0].targets[0].selected_fields.push("localized.name.en");
  api.applyPatch(root, replace(next));
  assert.deepEqual(plain(root.entries[0].overrides), entry.overrides);
  assert.deepEqual(plain(root.entries[0].selected_fields).sort(), ["localized.aliases.ko", "localized.name.en", "localized.summary.zh-Hans"]);
});

test("changed source candidates lose previous consent while new safe fills use new defaults", () => {
  const { api } = fixture(), root = preview(), next = plain(root);
  next.entries[0].targets[0].diff[0].after = "完全不同的新名";
  next.entries[0].targets[0].diff.push({ field: "career", before: [], after: ["演员"], selectable: true });
  next.entries[0].targets[0].selected_fields.push("career");
  api.applyPatch(root, replace(next));
  assert.deepEqual(plain(root.entries[0].selected_fields).sort(), ["career", "summary"]);
});
test("manual overrides survive changed source values only while their local before stays unchanged", () => {
  const { api } = fixture(), root = preview(), next = plain(root);
  root.entries[0].overrides = { name_cn: "我填写的名字", summary: "我填写的介绍" };
  next.entries[0].targets[0].diff[0].after = "新的来源名";
  next.entries[0].targets[0].diff[1].before = "前序来源补齐的介绍";
  next.entries[0].targets[0].selected_fields = [];
  api.applyPatch(root, replace(next));
  assert.deepEqual(plain(root.entries[0].overrides), { name_cn: "我填写的名字" });
  assert.deepEqual(plain(root.entries[0].selected_fields), ["name_cn"]);
});
test("changed role details do not inherit old relationship confirmation", () => {
  const { api } = fixture(), root = preview(), next = plain(root);
  next.entries[0].relations[0].role = "导演";
  api.applyPatch(root, replace(next)); assert.deepEqual(plain(root.entries[0].selected_relations), []);
});

test("retry never reselects a cancelled missing-work-link recommendation", () => {
  const { api } = fixture(), root = preview();
  Object.assign(root.entries[0].relations[0], { kind: "work", work_relation_status: "missing", restore_missing: true, selected: true });
  const next = plain(root);
  root.entries[0].selected_relations = [];
  next.entries[0].selected_relations = ["cast"];
  api.applyPatch(root, replace(next));
  assert.deepEqual(plain(root.entries[0].selected_relations), []);
  assert.equal(root.entries[0].relations[0].restore_missing, true);
});
test("lost manually selected identity is made pending rather than attaching to a new default", () => {
  const { api } = fixture(), root = preview(), next = plain(root);
  next.entries[0].targets = [];
  api.applyPatch(root, replace(next));
  assert.equal(root.entries[0].action, "skip"); assert.match(root.entries[0]._targetError, /重新选择/);
});
test("changed source ownership requires confirmation instead of accepting new default identity", () => {
  const { api } = fixture(), root = preview(), next = plain(root);
  next.entries[0].identity_owner_id = "another-person";
  api.applyPatch(root, replace(next)); assert.equal(root.entries[0].action, "skip"); assert.match(root.entries[0]._targetError, /身份/);
});
test("root patch preserves work differences and unrelated source selection, and replaces only matching warnings", () => {
  const { api } = fixture(), child = preview(), other = preview({ provider: "wikipedia", snapshot_id: "wiki" });
  const root = { preview_token: "work-old", diff: [{ field: "name", after: "作品" }], people_preview: { sources: [child, other] }, warnings: ["封面失败", "人物旧失败"] };
  const next = plain(child); next.snapshot_id = "new";
  api.applyPatch(root, { preview_patch: { preview_token: "work-new", people_preview: { sources: [next, other] } }, snapshot_replacements: { old: "new" }, warning_replacements: { removed: ["人物旧失败"], added: ["人物新失败"] } });
  assert.deepEqual(plain(root.diff), [{ field: "name", after: "作品" }]); assert.equal(root.people_preview.sources[1], other);
  assert.deepEqual(plain(root.warnings), ["封面失败", "人物新失败"]);
});
test("failed response never destroys the current preview", () => {
  const { api } = fixture(), root = preview(), before = plain(root);
  assert.throws(() => api.applyPatch(root, { preview_patch: {} }), /原预览/); assert.deepEqual(plain(root), before);
});
test("failure panel shows exact object and reason and dispatches only selected failures", async () => {
  const { api, parent } = fixture(), root = preview(), calls = [];
  const panel = api.render(parent, root, { onRetry: (value, ids) => calls.push([value, ids]) });
  const flatten = node => [node, ...node.children.flatMap(flatten)], nodes = flatten(panel);
  assert.ok(nodes.some(node => node.textContent.includes("ID 42"))); assert.ok(nodes.some(node => node.textContent === "SSLEOFError"));
  await nodes.find(node => node.textContent === "重试此项").click(); assert.equal(calls[0][0], root); assert.deepEqual(plain(calls[0][1]), ["person-detail:42"]);
  await nodes.find(node => node.textContent === "重试全部失败项").click(); assert.equal(calls[1][1], undefined);
});
test("busy disables retry actions but leaves explicit cancellation available", async () => {
  const { api, parent } = fixture(), root = preview(); let cancelled = false;
  const panel = api.render(parent, root, { busy: true, retryingSnapshotId: "old", onRetry: () => { throw Error("disabled"); }, onCancelRetry: () => { cancelled = true; } });
  const buttons = [panel.children[0].children[1], panel.children[1].children[1]];
  assert.ok(buttons.every(button => button.disabled)); await panel.children[0].children[2].click(); assert.equal(cancelled, true);
});
