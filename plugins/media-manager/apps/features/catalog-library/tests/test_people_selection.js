"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../frontend/people-selection.js"), "utf8");
const retrySource = fs.readFileSync(path.join(__dirname, "../frontend/people-retry.js"), "utf8");
const panelSource = fs.readFileSync(path.join(__dirname, "../frontend/people-panel.js"), "utf8");
const taxonomy = fs.readFileSync(path.join(__dirname, "../../people-catalog/frontend/taxonomy.js"), "utf8");
const titles = fs.readFileSync(path.join(__dirname, "../frontend/chapter-title-format.js"), "utf8");
const plain = value => JSON.parse(JSON.stringify(value));
const flush = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };

test("remounting a pending target leaves a retryable state rather than an endless loading flag", async () => {
  const pending = deferred(), f = fixture(proposal(), { requestTarget: () => pending.promise });
  const selection = f.select("global-one");
  assert.equal(f.entry._loading, true);
  f.render({ requestTarget: async () => ({ ok: true, target: { id: "global-one", name: "Global Actor", diff: [] } }) });
  assert.equal(f.entry._loading, false); assert.match(f.entry._targetError, /重新选择/);
  pending.resolve({ ok: true, target: { id: "global-one", name: "Late", diff: [] } }); await selection;
  assert.match(f.entry._targetError, /重新选择/);
  await f.select("global-one");
  assert.equal(f.entry._targetError, ""); assert.equal(f.api.state(f.preview).invalid, false);
});

function proposal(overrides = {}) {
  return { mode: "people-import", provider: "tmdb", snapshot_id: "snapshot-one", selection_token: "selection-token", preview_token: "parent-token",
    targets: [{ id: "global-one", type: "person", kind: "individual", name: "Global Actor", name_cn: "全库演员", aliases: ["演员别名"] },
      { id: "other-type", type: "character", name: "Global Role" }],
    entries: [{ external_id: "source:123", entity_type: "person", candidate: { type: "person", kind: "individual", name: "Original", name_cn: "中文名", roles: ["主演"], character_names: ["角色甲"] },
      identity_owner_id: "", target_id: "local-one", selected_fields: [], selected_relations: [],
      targets: [{ id: "local-one", type: "person", kind: "individual", name: "Original", name_cn: "中文名", matched: true, diff: [
        { field: "name", before: "Local", after: "Original", changed: true },
        { field: "name_cn", before: "旧中文名", after: "中文名", changed: true },
        { field: "summary", before: "保留简介", after: "新简介", changed: true },
        { field: "aliases", before: ["旧别名"], after: ["别名一", "别名二"], changed: true },
        { field: "infobox", before: [], after: [{ key: "生日", value: "1月1日" }], changed: true },
        { field: "birth_date", before: {}, after: { year: 2000 }, changed: true },
        { field: "locked", before: "A", after: "B", selectable: false, changed: true },
      ] }],
      creation: { allowed: true, id: "new-one", required_fields: ["name"], diff: [
        { field: "name", before: "", after: "Original", required: true, changed: true },
        { field: "name_cn", before: "", after: "中文名", changed: true },
        { field: "summary", before: "", after: "新简介", changed: true },
      ] },
      relations: [{ id: "cast-one", label: "主演 · 角色甲", kind: "cast", required_entity_keys: [] },
        { id: "staff-one", label: "导演", kind: "staff" }],
    }], ...overrides };
}

function fixture(preview = proposal(), options = {}) {
  let document;
  class Element {
    constructor(tag) { this.tagName = tag; this.className = ""; this.children = []; this.attributes = {}; this.listeners = {}; this.value = ""; this.textContent = ""; this.hidden = false; this.disabled = false; this.checked = false; }
    appendChild(node) { this.children.push(node); node.parentNode = this; return node; }
    removeChild(node) { this.children = this.children.filter(child => child !== node); node.parentNode = null; return node; }
    remove() { if (this.parentNode) this.parentNode.removeChild(this); }
    get firstChild() { return this.children[0] || null; }
    get isConnected() { return this === document.body || !!(this.parentNode && this.parentNode.isConnected); }
    setAttribute(key, value) { this.attributes[key] = String(value); }
    getAttribute(key) { return this.attributes[key] ?? null; }
    removeAttribute(key) { delete this.attributes[key]; }
    addEventListener(event, callback) { (this.listeners[event] ||= []).push(callback); }
    emit(event) { return Promise.all((this.listeners[event] || []).map(callback => callback({ target: this, preventDefault() {}, stopPropagation() {} }))); }
  }
  document = { createElement: tag => new Element(tag), body: null };
  document.body = new Element("body");
  const window = { NimdaCommon: { async fetchJson() { throw Error("unexpected network request"); } } }, globals = { window, document };
  vm.runInNewContext(titles, globals); vm.runInNewContext(taxonomy, globals); vm.runInNewContext(retrySource, globals); vm.runInNewContext(panelSource, globals); vm.runInNewContext(source, globals);
  const parent = document.body.appendChild(new Element("main")), api = window.NimdaPeopleSelection, changes = [];
  const flatten = node => [node, ...node.children.flatMap(flatten)];
  const nodes = () => flatten(parent);
  let panel;
  function render(nextOptions = options) { while (parent.firstChild) parent.removeChild(parent.firstChild); panel = api.render(parent, preview, "chinese", { onChange: state => changes.push(plain(state)), ...nextOptions }); return panel; }
  render();
  const control = (prefix, tag) => nodes().find(node => (!tag || node.tagName === tag) && (node.attributes["aria-label"] || "").startsWith(prefix));
  return { window, document, parent, nodes, changes, preview, api, render, control,
    entry: preview.entries[0], get panel() { return panel; },
    async select(value) { const select = control("资料卡处理方式", "select"); select.value = value; return select.emit("change"); },
    async toggle(prefix, checked) { const input = control(prefix, "input"); assert.ok(input, prefix); input.checked = checked; await input.emit("change"); return input; },
    async edit(prefix, value) { const input = control(prefix); assert.ok(input, prefix); input.value = value; await input.emit("input"); return input; },
  };
}

test("age-stage conflicts show the precise relationship reason and remain unselected", () => {
  const preview = proposal(), entry = preview.entries[0]; entry.action = "reuse";
  entry.relations = [{ id: "age-relation", role: "演员", character_name_cn: "角色甲", kind: "work", work_relation_status: "conflict",
    ref: { qualifier: { kind: "age", value: "teen", explicit: true } },
    reason: "当前记录为成年，来源记录为少年；请确认是否补充该阶段关系。" }];
  const f = fixture(preview);
  const relation = f.control("关联演员 · 角色甲 · 少年", "input");
  assert.ok(relation); assert.equal(relation.checked, false);
  assert.ok(f.nodes().some(row => row.textContent === entry.relations[0].reason));
  assert.equal(f.api.requiresConfirmation(preview), true);
});

test("localized creation uses dynamic required names and text textarea list overrides without saving", async () => {
  const preview = proposal(), entry = preview.entries[0];
  entry.action = "create"; entry.candidate.localized = { name: { ko: "유인식", "zh-Hans": "刘仁植" } };
  entry.creation.required_fields = ["localized.name.ko"];
  entry.creation.diff = [
    { field: "localized.name.ko", label: "韩文名称", before: "", after: "유인식", edit_type: "text", required: true },
    { field: "localized.summary.zh-Hans", label: "中文简介", before: "", after: "导演", edit_type: "textarea" },
    { field: "localized.aliases.ko", label: "韩文别名", before: [], after: ["유인식"], edit_type: "list" }
  ];
  entry.creation.selected_fields = entry.creation.diff.map(row => row.field); delete entry.selected_fields;
  const f = fixture(preview); assert.equal(f.api.requiresConfirmation(preview), false);
  assert.equal(f.control("手填韩文名称").tagName, "input"); assert.equal(f.control("手填中文简介").tagName, "textarea");
  assert.equal(f.nodes().find(row => row.className === "library-person-selection-head").children[0].children[0].title, "유인식");
  await f.edit("手填韩文名称", ""); assert.equal(f.api.state(preview).invalid, true);
  await f.edit("手填韩文名称", "류인식"); await f.edit("手填中文简介", ""); await f.edit("手填韩文别名", "이름 하나\n이름 둘");
  const selected = plain(f.api.collect(preview))[0];
  assert.deepEqual(selected.fields, entry.creation.diff.map(row => row.field));
  assert.deepEqual(selected.overrides, { "localized.name.ko": "류인식", "localized.summary.zh-Hans": "", "localized.aliases.ko": ["이름 하나", "이름 둘"] });
  assert.equal(selected.action, "create"); assert.equal(f.api.state(preview).invalid, false);
});

test("different language fills remain automatic but a same-language conflict expands", () => {
  const preview = proposal(), entry = preview.entries[0]; entry.action = "reuse";
  entry.targets[0].diff = [{ field: "localized.name.ko", label: "韩文名称", before: "", after: "유인식", edit_type: "text", merge_action: "fill", default_selected: true, requires_confirmation: false }];
  entry.selected_fields = ["localized.name.ko"];
  const f = fixture(preview); assert.equal(f.api.requiresConfirmation(preview), false);
  assert.equal(f.nodes().find(row => row.className === "library-people-auto-group").open, false);
  entry.targets[0].diff.push({ field: "localized.name.zh-Hans", label: "中文名称", before: "柳仁植", after: "刘仁植", edit_type: "text", merge_action: "conflict", requires_confirmation: true });
  f.render(); assert.equal(f.api.requiresConfirmation(preview), true);
  const manual = f.nodes().find(row => row.className === "library-people-manual-group"); assert.equal(manual.hidden, false);
  assert.equal(manual.children[1].children[0].open, true);
  assert.deepEqual(plain(f.api.collect(preview))[0].fields, ["localized.name.ko"]);
});

test("all-library search includes localized names and language-specific aliases", async () => {
  const preview = proposal(); preview.targets[0].localized = { name: { ko: "유인식" }, aliases: { ko: ["류 감독"] } };
  const f = fixture(preview); await f.edit("查找已有资料卡", "류 감독");
  assert.ok(f.control("资料卡处理方式").children.some(row => row.value === "global-one"));
});

test("alias unions and added language slots stay folded and show the full retained union", async () => {
  const preview = proposal(), entry = preview.entries[0]; entry.action = "reuse";
  entry.targets[0].diff = [
    { field: "localized.aliases.ko", label: "韩文别名", before: ["기존"], after: ["새별명"], merged_value: ["기존", "새별명"], edit_type: "list", merge_action: "merge", default_selected: true, requires_confirmation: false },
    { field: "localized.name.zh-Hans", label: "中文名称", before: "", after: "金部长", edit_type: "text", merge_action: "fill", default_selected: true, requires_confirmation: false },
    { field: "localized.name.ko", label: "韩文名称", before: "", after: "김부장", edit_type: "text", merge_action: "fill", default_selected: true, requires_confirmation: false },
  ];
  entry.targets[0].selected_fields = entry.targets[0].diff.map(row => row.field); delete entry.selected_fields;
  const f = fixture(preview);
  assert.equal(f.api.requiresConfirmation(preview), false);
  assert.equal(f.nodes().find(row => row.className === "library-people-auto-group").open, false);
  assert.equal(f.nodes().find(row => row.className === "library-person-auto-fields").open, false);
  assert.ok(f.nodes().some(row => row.textContent === "合并去重"));
  assert.ok(f.nodes().some(row => row.className === "library-diff-value" && row.textContent === JSON.stringify(["기존", "새별명"], null, 2)));
  assert.equal(f.control("手填韩文别名").value, "기존\n새별명");
  assert.deepEqual(plain(f.api.collect(preview))[0].fields, entry.targets[0].selected_fields);
  assert.deepEqual(plain(f.api.collect(preview))[0].overrides, {});
  assert.deepEqual(plain(f.api.proposedValue({ field: "aliases", before: [], after: ["A", "A"], merged_value: ["A"], merge_action: "fill" })), ["A"]);
  await f.edit("手填韩文别名", "仅手工保留");
  assert.deepEqual(plain(f.api.collect(preview))[0].overrides, { "localized.aliases.ko": ["仅手工保留"] });
});

test("legacy unspecified choices and same-name candidates are not guessed by the browser", () => {
  for (const bound of [false, true]) {
    const preview = proposal(); if (bound) preview.entries[0].identity_owner_id = "local-one";
    const f = fixture(preview);
    assert.equal(f.entry.action, "skip"); assert.equal(f.control("资料卡处理方式").value, "");
    assert.deepEqual(plain(f.api.collect(preview)), []);
    assert.deepEqual(plain(f.api.state(preview)), { chosen: 0, pending: false, invalid: false });
    assert.ok(f.nodes().some(node => node.textContent.includes(bound ? "明确绑定" : "核对身份")));
  }
});

test("infobox keys fill independently while only a conflicting key needs confirmation", async () => {
  const preview = proposal(), entry = preview.entries[0]; entry.action = "reuse";
  const added = "infobox.place", filled = "infobox.height", conflict = "infobox.agency", kept = "infobox.blood";
  entry.targets[0].diff = [
    { field: added, label: "详细资料 · 出生地", before: "", after: "首尔", edit_type: "text", merge_action: "fill", default_selected: true, requires_confirmation: false },
    { field: filled, label: "详细资料 · 身高", before: "", after: "180 cm", edit_type: "text", merge_action: "fill", default_selected: true, requires_confirmation: false },
    { field: conflict, label: "详细资料 · 经纪公司", before: "旧公司", after: "新公司", edit_type: "text", merge_action: "conflict", requires_confirmation: true },
    { field: kept, label: "详细资料 · 血型", before: "A", after: "A", edit_type: "text", merge_action: "same", requires_confirmation: false },
  ];
  entry.targets[0].selected_fields = [added, filled]; delete entry.selected_fields;
  const f = fixture(preview), automatic = f.nodes().find(row => row.className === "library-person-auto-fields");
  assert.equal(automatic.open, false);
  assert.equal(f.api.requiresConfirmation(preview), true);
  assert.equal(f.control("采用详细资料 · 出生地").checked, true);
  assert.equal(f.control("采用详细资料 · 身高").checked, true);
  assert.equal(f.control("采用详细资料 · 经纪公司").checked, false);
  assert.equal(f.control("手填详细资料 · 身高").value, "180 cm");
  assert.equal(f.control("采用详细资料 · 血型").parentNode.parentNode.parentNode.parentNode, automatic);
  assert.notEqual(f.control("采用详细资料 · 经纪公司").parentNode.parentNode.parentNode.parentNode, automatic);
  assert.deepEqual(plain(f.api.collect(preview))[0].fields, [added, filled]);
  await f.edit("手填详细资料 · 经纪公司", "核对后公司");
  assert.deepEqual(plain(f.api.collect(preview))[0].overrides, { [conflict]: "核对后公司" });
  assert.deepEqual(plain(f.api.collect(preview))[0].fields, [added, filled, conflict]);
});

test("structured infobox values can be edited without treating key identifiers as object paths", async () => {
  const preview = proposal(), entry = preview.entries[0]; entry.action = "reuse";
  const key = "infobox.__proto__";
  entry.targets[0].diff = [{ field: key, label: "详细资料 · 别名资料", before: [], after: [{ k: "韩文", v: "이름" }],
    edit_type: "json", merge_action: "fill", default_selected: true, requires_confirmation: false }];
  entry.targets[0].selected_fields = [key]; delete entry.selected_fields;
  const f = fixture(preview);
  assert.equal(f.control("手填详细资料 · 别名资料").tagName, "textarea");
  await f.edit("手填详细资料 · 别名资料", "{");
  assert.equal(f.api.state(preview).invalid, true);
  await f.edit("手填详细资料 · 别名资料", '[{"k":"韩文","v":"다른 이름"}]');
  assert.equal(f.api.state(preview).invalid, false);
  assert.deepEqual(plain(f.api.collect(preview))[0].overrides, { [key]: [{ k: "韩文", v: "다른 이름" }] });
  assert.equal({}.polluted, undefined);
});

test("cross-source role identity suggestions explain their evidence but never auto-merge cards", () => {
  const preview = proposal(), entry = preview.entries[0]; entry.action = "skip"; entry.entity_type = "character";
  entry.match_status = "role_evidence"; entry.recommended_target_id = "local-one";
  entry.default_reason = "同一作品、已确认演员的其他来源存在对应角色；请确认是否复用，不会自动合并已有档案";
  entry.targets[0].matched = false;
  const f = fixture(preview);
  assert.equal(f.api.requiresConfirmation(preview), true);
  assert.deepEqual(plain(f.api.collect(preview)), []);
  assert.ok(f.nodes().some(row => row.textContent === entry.default_reason));
  assert.ok(f.control("资料卡处理方式").children.some(row => row.value === "local-one" && row.textContent.includes("同作品演员对应")));
});

test("legacy creation without server defaults confirms required name only", async () => {
  const f = fixture(); await f.select("__create__");
  const name = f.control("采用名称："); assert.equal(name.checked, true); assert.equal(name.disabled, true);
  assert.equal(f.control("采用中文名：").checked, false); assert.equal(f.control("关联主演").checked, false);
  assert.deepEqual(plain(f.api.collect(f.preview)), [{ external_id: "source:123", action: "create", fields: ["name"], overrides: {}, relations: [] }]);
  f.entry.selected_fields = []; assert.throws(() => f.api.collect(f.preview), /必填/);
});

test("server-default new cards are ready for one batch save without per-person confirmation", async () => {
  const preview = proposal(), entry = preview.entries[0];
  entry.targets = []; entry.action = "create"; entry.target_id = ""; entry.match_status = "new";
  entry.default_reason = "人物库未找到匹配资料，默认新增。";
  entry.creation.selected_fields = ["name", "name_cn", "summary"];
  entry.selected_fields = entry.creation.selected_fields.slice(); entry.selected_relations = ["cast-one", "staff-one"];
  entry.relations.forEach(row => { row.selected = true; });
  const f = fixture(preview);
  assert.equal(f.control("资料卡处理方式").value, "__create__");
  assert.deepEqual(plain(f.api.collect(preview))[0], { external_id: "source:123", action: "create", fields: ["name", "name_cn", "summary"], overrides: {}, relations: ["cast-one", "staff-one"] });
  assert.equal(f.nodes().find(node => node.tagName === "details").open, false);
  assert.ok(f.nodes().some(node => node.textContent.includes("将新增 1 项")));
  await f.toggle("采用简介：", false); await f.toggle("关联导演", false); f.render();
  assert.deepEqual(plain(f.api.collect(preview))[0].fields, ["name", "name_cn"]);
  assert.deepEqual(plain(f.api.collect(preview))[0].relations, ["cast-one"]);
  await f.select(""); f.render(); assert.deepEqual(plain(f.api.collect(preview)), []);
  await f.select("__create__");
  assert.deepEqual(plain(f.api.collect(preview))[0].fields, ["name", "name_cn", "summary"]);
  assert.deepEqual(plain(f.api.collect(preview))[0].relations, ["cast-one", "staff-one"]);
});

test("default reuse opens field differences but retains every local field unless selected", async () => {
  const preview = proposal(), entry = preview.entries[0];
  entry.action = "reuse"; entry.target_id = entry.identity_owner_id = "local-one"; entry.selected_relations = ["cast-one"];
  const f = fixture(preview);
  assert.deepEqual(plain(f.api.collect(preview))[0].fields, []);
  assert.equal(f.nodes().find(node => node.tagName === "details").open, true);
  assert.ok(f.nodes().some(node => node.textContent.includes("4 项双边差异待确认") && node.textContent.includes("中文名") && node.textContent.includes("未勾选项保持原值")));
  assert.ok(f.nodes().some(node => node.textContent === "旧中文名"));
  assert.ok(f.nodes().some(node => node.textContent === "中文名"));
  await f.toggle("采用中文名：", true);
  assert.deepEqual(plain(f.api.collect(preview))[0].fields, ["name_cn"]);
  assert.ok(f.nodes().some(node => node.textContent.includes("已选更新 1 个字段")));
});

test("creation recommendations never select unavailable fields or unresolved relationships", async () => {
  const preview = proposal(), entry = preview.entries[0];
  entry.creation.selected_fields = ["name", "name_cn", "summary"];
  entry.creation.diff.find(row => row.field === "summary").must_override = true;
  entry.relations[0].selected = true; entry.relations[0].required_entity_keys = ["unknown-role"];
  entry.relations[1].selected = true; entry.relations[1].selectable = false;
  const f = fixture(preview); await f.select("__create__");
  assert.deepEqual(plain(f.api.collect(preview))[0].fields, ["name", "name_cn"]);
  assert.deepEqual(plain(f.api.collect(preview))[0].relations, []);
  assert.equal(f.api.state(preview).invalid, false);
});

test("safe fills and unchanged cards share one collapsed group while conflicts stay expanded", async () => {
  const preview = proposal(), base = preview.entries[0];
  function entry(id, action, diff, selected = []) {
    const value = plain(base); value.external_id = id; value.candidate.name = value.candidate.name_cn = id;
    value.action = action; value.target_id = value.identity_owner_id = "local-" + id;
    value.targets = [{ id: value.target_id, name: id, selected_fields: selected.slice(), diff }];
    value.selected_fields = selected.slice(); return value;
  }
  const fill = entry("Fill", "reuse", [{ field: "summary", before: "", after: "补充简介", merge_action: "fill", default_selected: true, requires_confirmation: false }], ["summary"]);
  const keep = entry("Keep", "reuse", [{ field: "summary", before: "保留简介", after: "", merge_action: "keep", requires_confirmation: false, must_override: true }]);
  const conflict = entry("Conflict", "reuse", [{ field: "summary", before: "原简介", after: "新简介", merge_action: "conflict", requires_confirmation: true }]);
  const fresh = entry("New", "create", []); fresh.selected_fields = ["name"];
  preview.entries = [fill, keep, conflict, fresh];
  const f = fixture(preview);
  const auto = f.nodes().find(node => node.className === "library-people-auto-group");
  const manual = f.nodes().find(node => node.className === "library-people-manual-group");
  assert.equal(auto.open, false); assert.equal(auto.children[0].textContent, "自动补齐／保持原值（3）");
  assert.equal(manual.children[0].textContent, "需要确认（1）");
  assert.equal(manual.children[1].children.length, 1);
  assert.equal(manual.children[1].children[0].open, true);
  assert.equal(f.api.requiresConfirmation(preview), true);
  assert.equal(f.api.requiresConfirmation({ sources: [{ people_preview: preview }] }), true);
  assert.deepEqual(plain(f.api.collect(preview)).map(row => row.fields), [["summary"], [], [], ["name"]]);
  auto.open = true; await auto.emit("toggle");
  const checkbox = f.nodes().find(node => node.attributes["aria-label"] === "采用简介：Fill");
  checkbox.checked = false; await checkbox.emit("change"); f.render();
  assert.deepEqual(plain(f.api.collect(preview))[0].fields, []);
  assert.equal(f.nodes().find(node => node.className === "library-people-auto-group").open, true);
  preview.entries = [fill, keep, fresh]; assert.equal(f.api.requiresConfirmation(preview), false);
});

test("safe missing work links stay folded and cancellable while uncertain dependencies expand", async () => {
  const preview = proposal(), entry = preview.entries[0];
  entry.action = "reuse"; entry.target_id = entry.identity_owner_id = "local-one";
  entry.targets[0].diff = []; entry.selected_fields = [];
  entry.relations = [{ id: "restore-director", kind: "work", role: "导演", required_entity_keys: [],
    selected: true, restore_missing: true, work_relation_status: "missing" }];
  entry.selected_relations = ["restore-director"];
  const f = fixture(preview), manual = () => f.nodes().find(node => node.className === "library-people-manual-group");
  assert.equal(manual().hidden, true);
  const auto = f.nodes().find(node => node.className === "library-people-auto-group");
  assert.equal(auto.open, false);
  assert.equal(auto.children[1].children[0].open, false);
  assert.equal(f.api.requiresConfirmation(preview), false);
  assert.ok(f.nodes().some(node => node.textContent.includes("待补回作品关联")));
  assert.deepEqual(plain(f.api.collect(preview))[0].relations, ["restore-director"]);
  assert.deepEqual(plain(f.api.collect(preview))[0].fields, []);
  await f.toggle("关联导演：", false); f.render();
  assert.deepEqual(plain(f.api.collect(preview))[0].relations, []);
  assert.equal(f.control("关联导演：").checked, false);
  assert.equal(manual().hidden, true);
  await f.toggle("关联导演：", true);
  assert.deepEqual(plain(f.api.collect(preview))[0].relations, ["restore-director"]);
  entry.relations[0].restore_missing = false; entry.relations[0].work_relation_status = "existing";
  entry.selected_relations = []; f.render();
  assert.equal(f.api.requiresConfirmation(preview), false);
  assert.ok(f.nodes().some(node => node.textContent === "本作品已有关联"));
  entry.relations[0].work_relation_status = "missing"; entry.relations[0].required_entity_keys = ["unresolved"];
  f.render();
  assert.equal(f.api.requiresConfirmation(preview), true);
  assert.equal(f.control("关联导演：").checked, false);
});

test("only conflicts stay visible while same empty kept and autofill fields start folded", async () => {
  const preview = proposal(), entry = preview.entries[0];
  entry.action = "reuse"; entry.target_id = entry.identity_owner_id = "local-one";
  entry.targets[0].diff = [
    { field: "name", before: "Original", after: "Original", selectable: false, merge_action: "same" },
    { field: "name_cn", before: "", after: "中文名", merge_action: "fill", default_selected: true },
    { field: "summary", before: "旧简介", after: "新简介", merge_action: "conflict", requires_confirmation: true },
    { field: "aliases", before: [], after: [], selectable: false, merge_action: "empty" },
    { field: "career", before: ["演员"], after: [], selectable: false, merge_action: "keep" }
  ];
  entry.selected_fields = ["name_cn"];
  const f = fixture(preview), auto = f.nodes().find(node => node.className === "library-person-auto-fields");
  assert.equal(auto.open, false); assert.equal(auto.children[0].textContent, "自动补齐／保持原值字段（4）");
  const fields = auto.parentNode;
  assert.equal(fields.children.length, 2);
  assert.equal(fields.children[0].children[0].children[0].attributes["aria-label"], "采用简介：中文名");
  assert.equal(f.control("采用中文名：").checked, true);
  assert.deepEqual(plain(f.api.collect(preview))[0].fields, ["name_cn"]);
  auto.open = true; await auto.emit("toggle");
  await f.toggle("采用中文名：", false); f.render();
  assert.equal(f.nodes().find(node => node.className === "library-person-auto-fields").open, true);
  assert.deepEqual(plain(f.api.collect(preview))[0].fields, []);
});

test("each person folds independently without losing choices or reopening on rerender", async () => {
  const preview = proposal(), first = preview.entries[0];
  first.action = "reuse"; first.target_id = first.identity_owner_id = "local-one";
  const second = plain(first); second.external_id = "second"; second.candidate.name_cn = "第二个人";
  preview.entries.push(second);
  const f = fixture(preview), cards = () => f.nodes().filter(node => node.className === "library-person-selection");
  await f.edit("手填简介：中文名", "手工简介"); await f.toggle("关联导演：中文名", true);
  const before = plain(f.api.collect(preview));
  assert.deepEqual(cards().map(node => node.open), [true, true]);
  cards()[0].open = false; await cards()[0].emit("toggle");
  await f.toggle("采用简介：第二个人", true);
  assert.equal(cards()[0].open, false);
  f.render();
  assert.deepEqual(cards().map(node => node.open), [false, true]);
  assert.equal(cards()[0].children[0].tagName, "summary");
  assert.equal(cards()[0].children[0].children[1].textContent, "4 项差异待确认");
  assert.deepEqual(plain(f.api.collect(preview))[0], before[0]);
  cards()[0].open = true; await cards()[0].emit("toggle"); f.render();
  assert.equal(cards()[0].open, true); assert.equal(f.control("手填简介：中文名").value, "手工简介");
});

test("a manually collapsed person stays collapsed if it moves back to the conflict group", async () => {
  const preview = proposal(), entry = preview.entries[0];
  entry.action = "reuse"; entry.target_id = entry.identity_owner_id = "local-one";
  const f = fixture(preview), card = () => f.nodes().find(node => node.className === "library-person-selection");
  card().open = false; await card().emit("toggle");
  await f.select(""); assert.equal(card().open, false);
  await f.select("local-one");
  assert.equal(card().open, false); assert.equal(f.api.requiresConfirmation(preview), true);
  f.render(); assert.equal(card().open, false);
});

test("late target lookup does not expand a person the user manually collapsed", async () => {
  const pending = deferred(), f = fixture(proposal(), { requestTarget: () => pending.promise });
  const request = f.select("global-one"), card = f.nodes().find(node => node.className === "library-person-selection");
  card.open = false; await card.emit("toggle");
  pending.resolve({ target: { id: "global-one", name: "Global Actor", diff: [{ field: "summary", before: "旧简介", after: "新简介", merge_action: "conflict" }] } });
  await request; assert.equal(card.open, false); assert.equal(f.api.requiresConfirmation(f.preview), true);
  assert.match(card.children[0].children[1].textContent, /1 项差异/);
});

test("choosing existing targets adopts only safe fills and does not reselect them on remount", async () => {
  const preview = proposal(), target = preview.entries[0].targets[0];
  target.selected_fields = ["infobox", "birth_date", "locked"];
  const f = fixture(preview); await f.select("local-one");
  assert.deepEqual(plain(f.api.collect(preview))[0].fields, ["infobox", "birth_date"]);
  await f.toggle("采用出生日期：", false); f.render();
  assert.deepEqual(plain(f.api.collect(preview))[0].fields, ["infobox"]);
  await f.select("");
  assert.equal(f.api.requiresConfirmation(preview), false);
  assert.equal(f.nodes().find(node => node.className === "library-people-manual-group").hidden, true);
  await f.select("local-one"); assert.equal(f.api.requiresConfirmation(preview), true);
  assert.deepEqual(plain(f.api.collect(preview))[0].fields, ["infobox", "birth_date"]);
});

test("lazy targets use signed safe fill defaults while conflicting fields remain unchecked", async () => {
  const f = fixture(proposal(), { requestTarget: async () => ({ target: { id: "global-one", name: "Global Actor", selected_fields: ["name_cn"], diff: [
    { field: "name_cn", before: "", after: "中文名", merge_action: "fill", default_selected: true },
    { field: "summary", before: "原简介", after: "新简介", merge_action: "conflict", requires_confirmation: true },
  ] } }) });
  await f.select("global-one");
  assert.deepEqual(plain(f.api.collect(f.preview))[0].fields, ["name_cn"]);
  assert.equal(f.control("采用简介：").checked, false);
  const card = f.nodes().find(node => node.className === "library-person-selection");
  const originalParent = card.parentNode;
  await f.edit("手填简介：", "手工新简介");
  assert.equal(card.parentNode, originalParent);
  assert.deepEqual(plain(f.api.collect(f.preview))[0].fields, ["name_cn", "summary"]);
});

test("invalid manual values and missing required overrides cannot stay in the automatic group", async () => {
  const preview = proposal(), entry = preview.entries[0];
  entry.action = "reuse"; entry.target_id = "local-one";
  entry.targets[0].diff = [{ field: "infobox", before: [], after: [{ key: "生日", value: "1月1日" }], merge_action: "fill", default_selected: true },
    { field: "summary", before: "保留简介", after: "", merge_action: "keep", must_override: true }];
  entry.selected_fields = ["infobox"];
  const f = fixture(preview), group = name => f.nodes().find(node => node.className === name);
  assert.equal(group("library-people-manual-group").hidden, true);
  await f.edit("手填详细资料：", "{");
  assert.equal(group("library-people-manual-group").hidden, false);
  assert.equal(group("library-people-auto-group").hidden, true);
  assert.equal(f.api.state(preview).invalid, true);
  await f.edit("手填详细资料：", "[]");
  assert.equal(group("library-people-manual-group").hidden, true);
  await f.toggle("采用简介：", true);
  assert.equal(group("library-people-manual-group").hidden, false);
  assert.equal(f.api.requiresConfirmation(preview), true);
  await f.toggle("采用简介：", false);
  assert.equal(group("library-people-manual-group").hidden, true);
  assert.equal(f.api.state(preview).invalid, false);
});

test("profile fields and work relationships are independent explicit choices", async () => {
  const f = fixture(); await f.select("local-one");
  assert.deepEqual(plain(f.api.collect(f.preview))[0], { external_id: "source:123", action: "reuse", target_id: "local-one", fields: [], overrides: {}, relations: [] });
  await f.toggle("关联主演", true);
  assert.deepEqual(plain(f.api.collect(f.preview))[0].relations, ["cast-one"]);
  assert.deepEqual(plain(f.api.collect(f.preview))[0].fields, []);
  await f.toggle("采用简介：", true); await f.toggle("关联主演", false);
  assert.deepEqual(plain(f.api.collect(f.preview))[0].fields, ["summary"]);
  assert.deepEqual(plain(f.api.collect(f.preview))[0].relations, []);
  await f.select(""); assert.deepEqual(plain(f.api.collect(f.preview)), []);
});

test("manual text editing, explicit Chinese-name clearing and reset preserve field consent", async () => {
  const f = fixture(); await f.select("local-one");
  await f.edit("手填中文名：", ""); await f.edit("手填简介：", "手填说明");
  let selected = plain(f.api.collect(f.preview))[0];
  assert.deepEqual(selected.fields, ["name_cn", "summary"]); assert.deepEqual(selected.overrides, { name_cn: "", summary: "手填说明" });
  await f.toggle("采用中文名：", false); selected = plain(f.api.collect(f.preview))[0];
  assert.deepEqual(selected.overrides, { summary: "手填说明" });
  const summary = f.control("手填简介："), box = summary.parentNode;
  await box.children.find(node => node.tagName === "button" && node.textContent === "还原来源值").emit("click");
  assert.deepEqual(plain(f.api.collect(f.preview))[0].overrides, {});
  assert.ok(plain(f.api.collect(f.preview))[0].fields.includes("summary"));
});

test("unconfirmed relationship dependencies block confirmation but an existing source owner can be reused", async () => {
  const preview = proposal();
  preview.entries[0].relations[0].required_entity_keys = ["role-one"];
  preview.entries.push({ external_id: "role-one", entity_type: "character", candidate: { name: "角色甲", type: "character" }, action: "skip", targets: [], relations: [] });
  const f = fixture(preview); await f.select("local-one"); await f.toggle("关联主演", true);
  assert.throws(() => f.api.collect(preview), /请先确认.*角色甲/); assert.equal(f.api.state(preview).invalid, true);
  assert.ok(f.nodes().some(node => node.textContent.includes("请先确认关联所需的资料卡：角色甲")));
  preview.entries[1].identity_owner_id = "already-bound-role";
  assert.equal(f.api.state(preview).invalid, false);
  assert.deepEqual(plain(f.api.collect(preview))[0].relations, ["cast-one"]);
  assert.equal(plain(f.api.collect(preview)).length, 1);
});

test("manual override updates the proposed value and unchecking restores both editor and proposal to source", async () => {
  const f = fixture(); await f.select("local-one");
  await f.edit("手填简介：", "我确认的简介");
  let row = f.control("手填简介：").parentNode.parentNode;
  let proposed = row.children.find(node => node.className === "library-person-selection-values").children[1];
  assert.equal(proposed.children[1].textContent, "我确认的简介");
  await f.toggle("采用简介：", false);
  const input = f.control("手填简介："); assert.equal(input.value, "新简介");
  row = input.parentNode.parentNode; proposed = row.children.find(node => node.className === "library-person-selection-values").children[1];
  assert.equal(proposed.children[1].textContent, "新简介");
  assert.equal(f.entry.overrides.summary, undefined);
  await f.toggle("采用简介：", true);
  assert.deepEqual(plain(f.api.collect(f.preview))[0].overrides, {});
});

test("fields requiring manual values reject unchecked source defaults until the user supplies an override", async () => {
  const preview = proposal(); preview.entries[0].targets[0].diff.find(row => row.field === "summary").must_override = true;
  const f = fixture(preview); await f.select("local-one"); await f.toggle("采用简介：", true);
  assert.throws(() => f.api.collect(preview), /手动填写/); assert.equal(f.api.state(preview).invalid, true);
  await f.edit("手填简介：", "补充的本地简介");
  assert.equal(f.api.state(preview).invalid, false);
  assert.equal(plain(f.api.collect(preview))[0].overrides.summary, "补充的本地简介");
});

test("explicit classification adoption records editor values without a redundant dropdown change", async () => {
  const taxonomy = { category: "person", reality: "fictional", subtype: "character" };
  for (const changed of [false, true]) {
    const preview = proposal(), entry = preview.entries[0];
    entry.action = "reuse"; entry.identity_owner_id = "local-one";
    entry.candidate = { ...entry.candidate, type: "character", kind: "character", taxonomy };
    entry.targets[0].diff = [{ field: "taxonomy", label: "分类", before: changed ? { ...taxonomy, subtype: "unknown" } : taxonomy, after: taxonomy,
      changed, merge_action: changed ? "conflict" : "same", requires_confirmation: changed, must_override: true, selectable: true, editable: true }];
    const f = fixture(preview);
    assert.equal(f.api.state(preview).invalid, false);
    await f.toggle("采用分类：", true);
    assert.equal(f.api.state(preview).invalid, false);
    assert.deepEqual(plain(f.api.collect(preview))[0].overrides.taxonomy, taxonomy);
    assert.equal(f.nodes().some(node => node.className.includes("library-person-selection-state") && node.textContent === "待核对"), false);
    await f.toggle("采用分类：", false);
    assert.deepEqual(plain(f.api.collect(preview))[0].fields, []);
    assert.deepEqual(plain(f.api.collect(preview))[0].overrides, {});
    await f.toggle("采用分类：", true);
    assert.equal(f.api.state(preview).invalid, false);
    assert.deepEqual(plain(f.api.collect(preview))[0].overrides.taxonomy, taxonomy);
  }
});

test("classification adoption reads displayed values while absent text still requires a manual value", async () => {
  const preview = proposal(), entry = preview.entries[0], taxonomy = { category: "person", reality: "fictional", subtype: "character" };
  entry.action = "reuse";
  entry.targets[0].diff = [
    { field: "taxonomy", label: "分类", before: taxonomy, after: taxonomy, merge_action: "same", must_override: true, editable: true },
    { field: "summary", label: "简介", before: "已有简介", after: "", merge_action: "keep", must_override: true }
  ];
  const f = fixture(preview);
  f.control("细分类别", "select").value = "individual";
  await f.toggle("采用分类：", true);
  assert.deepEqual(plain(f.api.collect(preview))[0].overrides.taxonomy, { ...taxonomy, subtype: "individual" });
  await f.toggle("采用简介：", true);
  assert.equal(f.api.state(preview).invalid, true);
  assert.throws(() => f.api.collect(preview), /中文名：.*简介.*手动填写/);
  assert.ok(f.nodes().some(node => node.className.includes("library-person-selection-state") && /简介.*手动填写/.test(node.textContent)));
  await f.edit("手填简介：", "");
  assert.equal(f.api.state(preview).invalid, false);
  assert.equal(plain(f.api.collect(preview))[0].overrides.summary, "");
  await f.toggle("采用简介：", false);
  assert.equal(f.entry.overrides.summary, undefined);
  assert.equal(f.api.state(preview).invalid, false);
});

test("identity and invalid-field badges explain the remaining action instead of a generic pending label", async () => {
  const f = fixture();
  const badge = () => f.nodes().find(node => node.className.includes("library-person-selection-state")).textContent;
  assert.equal(badge(), "待选择已有资料卡或新增");
  await f.select("local-one");
  await f.edit("手填详细资料：", "not JSON");
  assert.match(badge(), /手填资料格式有误.*详细资料/);
  assert.throws(() => f.api.collect(f.preview), /中文名：.*详细资料/);
  await f.select("");
  assert.equal(badge(), "已跳过");
});

test("manual empty identity name is invalid until corrected and never erases the required name", async () => {
  const f = fixture(); await f.select("__create__");
  const input = await f.edit("手填名称：", "   ");
  assert.equal(input.attributes["aria-invalid"], "true"); assert.equal(f.api.state(f.preview).invalid, true);
  assert.throws(() => f.api.collect(f.preview), /格式有误/);
  await f.edit("手填名称：", "修正角色名");
  assert.equal(f.api.state(f.preview).invalid, false); assert.equal(input.attributes["aria-invalid"], undefined);
  assert.equal(plain(f.api.collect(f.preview))[0].overrides.name, "修正角色名");
});

test("invalid JSON blocks confirmation, can be repaired, or discarded by unchecking the field", async () => {
  const f = fixture(); await f.select("local-one");
  await f.edit("手填详细资料：", "not JSON"); assert.equal(f.api.state(f.preview).invalid, true);
  assert.throws(() => f.api.collect(f.preview), /格式有误/);
  await f.edit("手填详细资料：", '[{"key":"国籍","value":"韩国"}]');
  assert.deepEqual(plain(f.api.collect(f.preview))[0].overrides.infobox, [{ key: "国籍", value: "韩国" }]);
  await f.edit("手填详细资料：", "["); await f.toggle("采用详细资料：", false);
  assert.equal(f.api.state(f.preview).invalid, false); assert.equal(plain(f.api.collect(f.preview))[0].overrides.infobox, undefined);
  await f.edit("手填别名：", "别名 A\r\n\n 别名 B ");
  assert.deepEqual(plain(f.api.collect(f.preview))[0].overrides.aliases, ["别名 A", "别名 B"]);
});

test("all-library search is local and target differences are fetched lazily with signed snapshot payload", async () => {
  const calls = [], pending = deferred(), f = fixture(proposal(), { requestTarget(payload) { calls.push(plain(payload)); return pending.promise; } });
  assert.deepEqual(calls, []);
  await f.edit("查找已有资料卡：", "演员别名"); assert.deepEqual(calls, []);
  const select = f.control("资料卡处理方式"); assert.ok(select.children.some(option => option.value === "global-one"));
  assert.equal(select.children.some(option => option.value === "other-type"), false);
  const task = f.select("global-one"); await flush();
  assert.deepEqual(calls, [{ snapshot_id: "snapshot-one", preview_token: "selection-token", external_id: "source:123", target_id: "global-one" }]);
  assert.equal(f.api.state(f.preview).pending, true); assert.throws(() => f.api.collect(f.preview), /仍在读取/);
  pending.resolve({ ok: true, target: { id: "global-one", name: "Global Actor", diff: [{ field: "name_cn", before: "", after: "中文名" }] } }); await task;
  assert.equal(f.api.state(f.preview).pending, false);
  assert.equal(plain(f.api.collect(f.preview))[0].target_id, "global-one");
  assert.deepEqual(plain(f.api.collect(f.preview))[0].fields, []);
});

test("target endpoint uses fallback preview token and rejects failed HTTP responses", async () => {
  const preview = proposal(); delete preview.selection_token;
  const f = fixture(preview), calls = [];
  f.window.NimdaCommon.fetchJson = async (url, options) => { calls.push({ url, options: plain(options) }); return { res: { ok: false }, data: { error: "预览已经过期" } }; };
  await f.select("global-one");
  assert.equal(calls[0].url, "/api/catalog-library/people/selection-target");
  assert.deepEqual(JSON.parse(calls[0].options.body), { snapshot_id: "snapshot-one", preview_token: "parent-token", external_id: "source:123", target_id: "global-one" });
  assert.equal(calls[0].options.method, "POST"); assert.match(calls[0].options.operationTitle, /核对/);
  assert.equal(f.api.state(f.preview).invalid, true); assert.throws(() => f.api.collect(f.preview), /预览已经过期/);
});

test("late target results cannot overwrite a newer identity selection or skip", async () => {
  for (const next of ["local-one", ""]) {
    const pending = deferred(), f = fixture(proposal(), { requestTarget() { return pending.promise; } });
    const task = f.select("global-one"); await flush(); await f.select(next);
    pending.resolve({ target: { id: "global-one", name: "Late", diff: [] } }); await task;
    assert.equal(f.entry.target_id, next); assert.equal(f.entry._loading, false);
    assert.equal(f.entry.targets.some(target => target.id === "global-one"), false);
    assert.equal(f.api.state(f.preview).chosen, next ? 1 : 0);
  }
});

test("late target failures do not contaminate a replacement preview, detached panel, or retired owner", async () => {
  for (const retire of ["detach", "owner"]) {
    let current = true;
    const pending = deferred(), f = fixture(proposal(), { requestTarget() { return pending.promise; }, current: () => current });
    const task = f.select("global-one"); await flush(); const notifications = f.changes.length;
    if (retire === "detach") f.panel.remove(); else current = false;
    pending.reject(Error("late failure")); await task;
    assert.equal(f.entry._targetError, ""); assert.equal(f.changes.length, notifications);
  }
});

test("blocked and mismatched fetched identities cannot be collected or confirmed", async () => {
  for (const target of [{ id: "global-one", blocked: true, reason: "身份冲突", diff: [] }, { id: "other-id", diff: [] }]) {
    const f = fixture(proposal(), { requestTarget: async () => ({ target }) });
    await f.select("global-one");
    assert.equal(f.api.state(f.preview).invalid, true);
    assert.throws(() => f.api.collect(f.preview), /冲突|不一致/);
  }
  const preview = proposal(); preview.entries[0].creation.blocked = true;
  const f = fixture(preview); assert.equal(f.control("资料卡处理方式").children.find(node => node.value === "__create__").disabled, true);
  f.entry.action = "create"; f.entry.selected_fields = ["name"]; assert.throws(() => f.api.collect(preview), /有效/);
});

test("invalid identity targets and missing creation requirements disable confirmation before collection", async () => {
  const f = fixture(); await f.select("__create__");
  f.entry.selected_fields = []; assert.equal(f.api.state(f.preview).invalid, true);
  f.entry.selected_fields = ["name"]; f.entry.creation.blocked = true;
  assert.equal(f.api.state(f.preview).invalid, true);
  f.entry.action = "reuse"; f.entry.target_id = "missing-card";
  assert.equal(f.api.state(f.preview).invalid, true);
  f.entry.target_id = "local-one"; f.entry.targets[0].blocked = true;
  assert.equal(f.api.state(f.preview).invalid, true);
  f.entry.action = "skip"; assert.equal(f.api.state(f.preview).invalid, false);
});

test("non-selectable relationships never reach the confirmation payload even from stale selected state", async () => {
  const preview = proposal(); preview.entries[0].relations[1].selectable = false;
  const f = fixture(preview); await f.select("local-one");
  assert.equal(f.control("关联导演").disabled, true);
  f.entry.selected_relations = ["cast-one", "staff-one"];
  assert.deepEqual(plain(f.api.collect(preview))[0].relations, ["cast-one"]);
});

test("busy re-render preserves choices and disables every profile and relation control", async () => {
  const f = fixture(); await f.select("local-one"); await f.toggle("采用简介：", true); await f.toggle("关联主演", true);
  const selected = plain(f.api.collect(f.preview)); f.render({ busy: true });
  assert.deepEqual(plain(f.api.collect(f.preview)), selected);
  assert.equal(f.control("采用简介：").checked, true); assert.equal(f.control("关联主演").checked, true);
  assert.ok(f.nodes().filter(node => ["input", "select", "textarea"].includes(node.tagName)).every(node => node.disabled));
  assert.equal(f.nodes().some(node => node.tagName === "button" && node.textContent === "手动修改"), false);
  await f.select(""); assert.deepEqual(plain(f.api.collect(f.preview)), selected);
});

test("identity changes clear prior overrides, field failures and relationship decisions", async () => {
  const f = fixture(); await f.select("local-one"); await f.edit("手填简介：", "修改"); await f.edit("手填详细资料：", "{"); await f.toggle("关联主演", true);
  await f.select("__create__");
  assert.deepEqual(plain(f.api.collect(f.preview))[0], { external_id: "source:123", action: "create", fields: ["name"], overrides: {}, relations: [] });
});

test("selection payload filters unknown fields, deduplicates IDs and does not alias manual values", async () => {
  const f = fixture(); await f.select("local-one");
  f.entry.selected_fields = ["aliases", "aliases", "locked", "not-real"];
  f.entry.selected_relations = ["cast-one", "cast-one", "not-real"];
  f.entry.overrides = { aliases: ["Alias"], not_real: "ignored" };
  const result = f.api.collect(f.preview);
  assert.deepEqual(plain(result)[0], { external_id: "source:123", action: "reuse", target_id: "local-one", fields: ["aliases"], overrides: { aliases: ["Alias"] }, relations: ["cast-one"] });
  result[0].overrides.aliases.push("changed"); assert.deepEqual(f.entry.overrides.aliases, ["Alias"]);
});

test("nested single-source and combined previews collect explicit decisions by snapshot", async () => {
  const f = fixture(); await f.select("local-one"); await f.toggle("关联主演", true);
  const second = proposal({ snapshot_id: "snapshot-two" }); second.entries[0].action = "skip";
  const nested = { people_preview: { sources: [f.preview, { people_preview: second }] } };
  assert.deepEqual(plain(f.window.NimdaPeoplePanel.collectSelections(nested)), {
    "snapshot-one": [{ external_id: "source:123", action: "reuse", target_id: "local-one", fields: [], overrides: {}, relations: ["cast-one"] }], "snapshot-two": [],
  });
  second.entries[0].action = "reuse"; second.entries[0]._targetError = "失效";
  assert.throws(() => f.window.NimdaPeoplePanel.collectSelections(nested), /失效/);
});

test("supplement reuse identifies a primary planned card and rejects cancelled upstream creation", async () => {
  const primary = proposal({ snapshot_id: "primary-snapshot", source_role: "primary" });
  primary.entries[0].action = "create"; primary.entries[0].target_id = ""; primary.entries[0].selected_fields = ["name"];
  const supplemental = proposal({ snapshot_id: "supplement-snapshot", provider: "wikipedia", source_role: "supplement" });
  const target = supplemental.entries[0].targets[0];
  Object.assign(target, { id: "new-one", planned: true, planned_provider: "tmdb", planned_snapshot_id: "primary-snapshot", planned_external_id: "source:123" });
  supplemental.entries[0].action = "reuse"; supplemental.entries[0].target_id = "new-one"; supplemental.entries[0].selected_fields = ["name_cn"];
  const combined = { sources: [primary, supplemental] }, f = fixture(supplemental);
  const payload = plain(f.window.NimdaPeoplePanel.collectSelections(combined));
  assert.equal(payload["primary-snapshot"][0].action, "create");
  assert.equal(payload["supplement-snapshot"][0].target_id, "new-one");
  assert.ok(f.control("资料卡处理方式").children.some(option => option.textContent.includes("本次TMDB待新增")));
  assert.ok(f.nodes().some(node => node.textContent.includes("先建立再补充，不会重复建卡")));
  primary.entries[0].action = "skip";
  assert.throws(() => f.window.NimdaPeoplePanel.collectSelections(combined), /前序来源待新增资料卡已取消或改选/);
  primary.entries[0].action = "reuse"; primary.entries[0].target_id = "local-one";
  assert.throws(() => f.window.NimdaPeoplePanel.collectSelections(combined), /前序来源待新增资料卡已取消或改选/);
  primary.entries[0].action = "create"; primary.entries[0].target_id = "";
  assert.doesNotThrow(() => f.window.NimdaPeoplePanel.collectSelections(combined));
  supplemental.entries[0].action = "skip"; primary.entries[0].action = "skip";
  assert.deepEqual(plain(f.window.NimdaPeoplePanel.collectSelections(combined)), { "primary-snapshot": [], "supplement-snapshot": [] });
});

test("combined preview labels source order and surfaces dependency failures beside the batch", () => {
  const primary = proposal({ source_role: "primary" }), supplemental = proposal({ provider: "wikipedia", snapshot_id: "second", source_role: "supplement" });
  primary.entries[0].action = "skip";
  supplemental.entries[0].action = "reuse";
  Object.assign(supplemental.entries[0].targets[0], { planned: true, planned_provider: "tmdb", planned_snapshot_id: primary.snapshot_id, planned_external_id: "source:123" });
  const f = fixture(primary); f.panel.remove();
  f.window.NimdaPeoplePanel.renderPreview(f.parent, { sources: [primary, supplemental] }, "chinese");
  const headers = f.nodes().filter(node => node.tagName === "h3").map(node => node.textContent);
  assert.match(headers[0], /TMDB.*主来源/); assert.match(headers[1], /中文维基百科.*辅助来源/);
  assert.ok(f.nodes().some(node => node.attributes.role === "status" && !node.hidden && node.textContent.includes("前序来源待新增资料卡已取消或改选")));
});

test("source links, names and supplied taxonomy are rendered as safe data", () => {
  const preview = proposal(); preview.entries[0].candidate = { type: "person", kind: "corporation", name_cn: "<script>alert(1)</script>" };
  preview.entries[0].source_url = "https://www.themoviedb.org.evil/person/1";
  const f = fixture(preview);
  assert.ok(f.nodes().some(node => node.textContent === "<script>alert(1)</script>"));
  assert.ok(f.nodes().some(node => node.textContent === "组织 · 公司 · 现实"));
  assert.equal(f.nodes().some(node => ["script", "iframe", "img", "a"].includes(node.tagName)), false);
});
