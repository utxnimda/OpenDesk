"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");
const read = file => fs.readFileSync(path.join(__dirname, file), "utf8");
const scripts = ["../../catalog-library/frontend/chapter-title-format.js", "../frontend/taxonomy.js", "../../catalog-library/frontend/people-selection.js", "../../catalog-library/frontend/people-panel.js", "../frontend/profile-images.js", "../frontend/profile-dialog.js", "../frontend/index.js"].map(read);
const flush = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const plain = value => JSON.parse(JSON.stringify(value));
const asset = "a".repeat(64) + ".jpg";
const avatarUrl = "/api/catalog-library/people/assets/" + asset;
const person = { id: "person_" + "1".repeat(32), type: "person", kind: "individual", name: "Original Name", name_cn: "中文姓名", summary: "原简介", gender: "男性", birth_date: {}, career: ["演员"], aliases: [], infobox: [], avatar_url: avatarUrl };
const detail = overrides => ({ person: { ...person, ...overrides }, version: "version-1", relations: [{ name: "关联作品", role: "演员", character_name: "角色名" }], sources: [{ provider: "tmdb", external_id: "42", source_url: "https://www.themoviedb.org/person/42" }], edit_fields: [
  { field: "name", label: "原名 / 名称", type: "text", editable: true, required: true },
  { field: "name_cn", label: "中文名", type: "text", editable: true },
  { field: "summary", label: "简介", type: "textarea", editable: true },
  { field: "birth_date", label: "出生日期", type: "date-parts", editable: true },
  { field: "avatar", label: "头像", type: "avatar", editable: false }
] });
function fixture(handler = route => route === "people-library/detail" ? detail() : { items: [], total: 0 }) {
  let activeElement, feature;
  const walk = root => [root, ...root.children.flatMap(walk)];
  const matches = (node, selector) => selector.split(",").some(part => { part = part.trim(); return part[0] === "." ? (node.className || "").split(" ").includes(part.slice(1)) : part[0] === "#" ? node.id === part.slice(1) : node.tag === part; });
  class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; this.dataset = {}; this.handlers = {}; this.value = ""; this._text = ""; this.disabled = false; }
    get firstChild() { return this.children[0] || null; }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
    set textContent(value) { this._text = String(value); this.children.forEach(child => { child.parent = null; }); this.children = []; }
    get isConnected() { return this === document.body || !!(this.parent && this.parent.isConnected); }
    appendChild(child) { if (child.parent) child.remove(); this.children.push(child); child.parent = this; return child; }
    removeChild(child) { this.children = this.children.filter(value => value !== child); child.parent = null; return child; }
    remove() { if (this.parent) this.parent.removeChild(this); }
    setAttribute(key, value) { this.attrs[key] = String(value); }
    addEventListener(key, callback) { (this.handlers[key] ||= []).push(callback); }
    removeEventListener(key, callback) { this.handlers[key] = (this.handlers[key] || []).filter(row => row !== callback); }
    dispatchEvent(event) { for (const callback of this.handlers[event.type] || []) callback(event); }
    querySelectorAll(selector) { return walk(this).slice(1).filter(node => matches(node, selector)); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    focus() { activeElement = this; }
    showModal() { this.open = true; }
    close() { this.open = false; this.dispatchEvent({ type: "close" }); }
  }
  const document = { createElement: tag => new Element(tag), body: null, handlers: {}, get activeElement() { return activeElement; } };
  document.body = new Element("body");
  document.querySelector = selector => document.body.querySelector(selector);
  for (const key of ["addEventListener", "removeEventListener", "dispatchEvent"]) document[key] = Element.prototype[key];
  const shell = new Element("div"), tabs = new Element("nav"); shell.className = "app-shell"; tabs.className = "app-tabs"; document.body.appendChild(tabs); document.body.appendChild(shell);
  const calls = [], notices = [], events = [], titles = [];
  document.addEventListener("nimda-people-changed", event => events.push(event.detail));
  const window = { NimdaCommon: { async fetchJson(url, options) { const route = url.replace("/api/catalog-library/", ""), body = JSON.parse(options.body); calls.push({ route, body }); titles.push(options.operationTitle); return { data: await handler(route, body) }; }, ensureFeatureRegistry() { return { register(value) { feature = value; } }; } }, NimdaOperationCenter: { notice(...args) { notices.push(args); } } };
  class CustomEvent { constructor(type, options) { this.type = type; this.detail = options && options.detail; } }
  const context = vm.createContext({ window, document, CustomEvent }); scripts.forEach(source => vm.runInContext(source, context));
  const fire = (element, type = "click") => { assert.ok(element, "event target exists"); element.dispatchEvent({ type, preventDefault() {} }); };
  return { window, document, calls, notices, events, titles, fire, get feature() { return feature; }, get nodes() { return walk(document.body); }, find: selector => document.querySelector(selector), button: label => walk(document.body).find(node => node.tag === "button" && node.textContent === label), async click(label) { fire(this.button(label)); await flush(); }, async open(overrides = {}) { window.NimdaPersonProfile.open({ personId: person.id, ...overrides }); await flush(); } };
}
const diff = rows => ({ preview_token: "preview-signed", diff: rows, warnings: [] });
const row = (field, after, extra = {}) => ({ field, label: field, before: person[field], after, selectable: true, selected: false, ...extra });
async function sourceForm(f, provider = "tmdb") { await f.click("获取来源资料"); const form = f.find(".person-library-source-form"); form.querySelector("select").value = provider; f.fire(form.querySelector("select"), "change"); return form; }
async function submit(f, form) { f.fire(form, "submit"); await flush(); }
function select(f, index, value = true) { const check = f.nodes.filter(node => node.type === "checkbox")[index]; check.checked = value; f.fire(check, "change"); return check; }

async function imageForm(f, provider = "wikipedia") {
  await f.click("图片来源"); const form = f.find(".person-library-image-form");
  form.querySelector("select").value = provider; f.fire(form.querySelector("select"), "change"); return form;
}
const imagePreview = () => ({ ...diff([row("avatar", { asset_id: asset, source_url: "https://upload.wikimedia.org/wikipedia/commons/a/a1/Portrait.jpg" }, { before: {}, selected: true })]), image_source: { provider: "commons", title: "角色图片", page_url: "https://commons.wikimedia.org/wiki/File:Portrait.jpg", author: "<b>Author</b>", license: "CC BY-SA", license_url: "https://creativecommons.org/licenses/by-sa/4.0/" } });

test("image-only import works on work-linked characters and requires separate confirmation", async () => {
  const f = fixture((route, body) => {
    if (route === "people-library/detail") return detail({ type: "character", role_origins: [{ provider: "tmdb" }] });
    if (route === "people-library/image-preview") { assert.deepEqual(body, { person_id: person.id, version: "version-1", provider: "commons", external_id: "File:Portrait.jpg" }); return imagePreview(); }
    if (route === "people-library/image-apply") { assert.deepEqual(body, { person_id: person.id, preview_token: "preview-signed", selected_fields: ["avatar"] }); return { ok: true }; }
    throw Error(route);
  });
  await f.open(); assert.equal(f.calls.length, 1); const form = await imageForm(f, "commons"); form.querySelector("input").value = "File:Portrait.jpg";
  await submit(f, form); assert.equal(f.calls.some(call => call.route.endsWith("apply")), false);
  assert.ok(f.find(".person-library-image-preview")); assert.equal(f.find(".library-person-auto-fields"), null);
  assert.ok(f.document.body.textContent.includes("<b>Author</b>")); assert.equal(f.find("b"), null);
  assert.equal(f.nodes.filter(row => row.tag === "img").every(row => row.src === avatarUrl), true);
  const link = f.nodes.find(row => row.tag === "a" && row.textContent === "查看来源"); assert.equal(link.href, "https://commons.wikimedia.org/wiki/File:Portrait.jpg");
  await f.click("确认采用此图片"); assert.equal(f.calls.filter(row => row.route.endsWith("image-apply")).length, 1);
  await f.click("关闭"); assert.equal(f.events.length, 1);
});

test("image source switching disables invisible required controls and cancellation never applies", async () => {
  const f = fixture(route => route === "people-library/detail" ? detail() : imagePreview()); await f.open(); const form = await imageForm(f, "local");
  const provider = form.querySelector("select"), remote = form.querySelector("input"), upload = form.querySelectorAll("input").find(row => row.type === "file");
  assert.equal(remote.disabled, true); assert.equal(remote.required, false); assert.equal(upload.disabled, false); assert.equal(upload.required, true);
  provider.value = "bangumi"; f.fire(provider, "change"); assert.equal(upload.disabled, true); assert.equal(upload.required, false); assert.equal(remote.disabled, false);
  remote.value = "42"; await submit(f, form); await f.click("取消预览"); assert.ok(f.button("图片来源")); assert.equal(f.calls.length, 2);
});

test("local images use bounded file bytes rather than paths and still await image confirmation", async () => {
  const f = fixture((route, body) => {
    if (route === "people-library/detail") return detail();
    assert.equal(route, "people-library/image-preview"); assert.deepEqual(body, { person_id: person.id, version: "version-1", provider: "local", file_name: "角色.png", mime_type: "image/png", image_base64: "aW1hZ2U=" }); return imagePreview();
  });
  f.window.FileReader = class { readAsDataURL() { this.result = "data:image/png;base64,aW1hZ2U="; this.onload(); } };
  await f.open(); const form = await imageForm(f, "local"), input = form.querySelectorAll("input").find(row => row.type === "file");
  input.files = [{ name: "角色.png", type: "image/png", size: 5 }]; await submit(f, form);
  assert.ok(f.button("确认采用此图片")); assert.equal(f.calls.length, 2);
});

test("invalid local image uploads fail before network requests and leave retry possible", async () => {
  const f = fixture(); await f.open(); const form = await imageForm(f, "local"), input = form.querySelectorAll("input").find(row => row.type === "file");
  for (const file of [undefined, { size: 2 * 1024 * 1024 + 1, type: "image/png" }, { size: 10, type: "image/svg+xml" }]) {
    input.files = file ? [file] : []; await submit(f, form); assert.equal(f.calls.length, 1); assert.equal(f.button("获取并预览图片").disabled, false);
  }
  assert.ok(f.document.body.textContent.includes("只支持 JPEG")); assert.equal(input.disabled, false);
});

test("TMDB still gallery uses local thumbnails and scoped candidate tokens with pagination", async () => {
  const f = fixture((route, body) => {
    if (route === "people-library/detail") return detail({ type: "character" });
    if (route === "people-library/image-candidates") {
      assert.equal(body.provider, "tmdb"); assert.equal(body.external_id, "259837"); assert.equal(body.season_number, 1); assert.equal(body.episode_number, 2);
      return { page: body.page, page_size: 8, total: 9, has_more: body.page === 1, candidates: [{ thumbnail_url: avatarUrl, width: 1280, height: 720, candidate_token: "signed-image-" + body.page }, { thumbnail_url: "https://image.tmdb.org/unsafe.jpg", candidate_token: "bad" }] };
    }
    if (route === "people-library/image-preview") { assert.deepEqual(body, { person_id: person.id, version: "version-1", provider: "tmdb-still", candidate_token: "signed-image-2" }); return imagePreview(); }
    throw Error(route);
  });
  await f.open(); const form = await imageForm(f, "tmdb-still"); form.querySelector("input").value = "259837"; const numbers = form.querySelectorAll("input").filter(row => row.type === "number"); numbers[0].value = "1"; numbers[1].value = "2";
  await submit(f, form); assert.equal(f.nodes.filter(row => row.tag === "img").length, 1); assert.equal(f.find("img").src, avatarUrl);
  assert.equal(f.button("上一页剧照").disabled, true); assert.equal(f.button("下一页剧照").disabled, false);
  await f.click("下一页剧照"); assert.equal(f.button("下一页剧照").disabled, true); assert.equal(f.button("上一页剧照").disabled, false);
  await f.click("选择此剧照"); assert.ok(f.button("确认采用此图片")); assert.equal(f.calls.some(row => row.route.endsWith("apply")), false);
});

test("closing pending image discovery ignores late candidate results", async () => {
  const pending = deferred(), f = fixture(route => route === "people-library/detail" ? detail() : pending.promise);
  await f.open(); const form = await imageForm(f, "tmdb-still"); form.querySelector("input").value = "259837"; await submit(f, form);
  await f.click("关闭"); pending.resolve({ page: 1, candidates: [], total: 0 }); await flush(); assert.equal(f.find("dialog"), null); assert.equal(f.notices.length, 0);
});

test("editing the TMDB target clears previously loaded gallery candidates", async () => {
  const f = fixture(route => route === "people-library/detail" ? detail() : ({ page: 1, total: 30, limited: true, has_more: true, candidates: [{ thumbnail_url: avatarUrl, candidate_token: "old-candidate" }] }));
  await f.open(); const form = await imageForm(f, "tmdb-still"), external = form.querySelector("input"); external.value = "259837"; await submit(f, form);
  assert.ok(f.button("选择此剧照")); assert.match(f.document.body.textContent, /仅列出前 30 张/);
  external.value = "259838"; f.fire(external, "input"); assert.equal(f.button("选择此剧照"), undefined); assert.equal(f.find(".person-library-image-gallery").textContent, "");
});

test("saved image evidence is text-only and rejects untrusted source links", async () => {
  const f = fixture(() => detail({ avatar_image_source: { provider: "commons", author: "<img src=x>", page_url: "https://commons.wikimedia.org.evil.test/File:X", license_url: "javascript:alert(1)" } }));
  await f.open(); assert.ok(f.find(".person-library-image-evidence").textContent.includes("<img src=x>")); assert.equal(f.find(".person-library-image-evidence").querySelectorAll("a").length, 0);
});

test("multilingual profile renders native hover and edits projected dynamic values with explicit empty slots", async () => {
  const local = detail({ name: "유인식", name_cn: "刘仁植", localized: { name: { ko: "유인식", "zh-Hans": "刘仁植" }, summary: { "zh-Hans": "导演" }, aliases: { ko: ["별명"] } } });
  local.edit_fields = [
    { field: "localized.name.ko", label: "韩文名称", type: "text", editable: true, value: "유인식" },
    { field: "localized.name.en", label: "英文名称", type: "text", editable: true, value: "" },
    { field: "localized.summary.zh-Hans", label: "中文简介", type: "textarea", editable: true, value: "投影简介" },
    { field: "localized.aliases.ko", label: "韩文别名", type: "list", editable: true, value: ["별명"] }
  ];
  const f = fixture((route, body) => {
    if (route === "people-library/detail") return local;
    if (route === "people-library/edit-preview") { assert.deepEqual(body.changes, { "localized.summary.zh-Hans": "", "localized.aliases.ko": [] }); return diff([row("localized.summary.zh-Hans", "", { selected: true }), row("localized.aliases.ko", [], { selected: true })]); }
    throw Error("unexpected save: " + route);
  });
  await f.open(); assert.equal(f.find("h2").textContent, "刘仁植"); assert.equal(f.find("h2").title, "유인식");
  assert.ok(f.document.body.textContent.includes("别名（韩语）"));
  await f.click("编辑资料"); const form = f.find(".person-library-edit"), textareas = form.querySelectorAll("textarea");
  assert.equal(form.querySelectorAll("input")[1].value, ""); assert.deepEqual(textareas.map(row => row.value).sort(), ["投影简介", "별명"].sort());
  textareas.forEach(row => { row.value = ""; }); await submit(f, form);
  assert.equal(f.calls.at(-1).route, "people-library/edit-preview"); assert.ok(f.button("确认采用所选资料"));
  assert.equal(f.calls.some(row => row.route.endsWith("apply")), false);
});

test("multilingual edit fallback reads dotted paths and untouched projected values do not submit", async () => {
  const local = detail({ localized: { name: { ko: "유인식" }, summary: { en: "Director" } } });
  local.edit_fields = [{ field: "localized.name.ko", label: "韩文名称", type: "text", editable: true }, { field: "localized.summary.en", label: "英文简介", type: "textarea", editable: true, value: "Projected biography" }];
  const f = fixture(() => local); await f.open(); await f.click("编辑资料"); const form = f.find(".person-library-edit");
  assert.equal(form.querySelector("input").value, "유인식"); assert.equal(form.querySelector("textarea").value, "Projected biography");
  await submit(f, form); assert.equal(f.calls.length, 1); assert.ok(f.document.body.textContent.includes("没有修改任何资料"));
});

test("language editor groups blank languages into collapsed sections without fabricating changes", async () => {
  const local = detail({ localized: { name: { ko: "유인식" } } });
  local.edit_fields = ["zh-Hans", "zh-Hant", "ko", "ko-Latn", "en", "ja", "und-Latn", "und"].flatMap(language => ["name", "summary", "aliases"].map(kind => ({ field: "localized." + kind + "." + language, label: kind + language, editable: true, type: kind === "name" ? "text" : kind === "summary" ? "textarea" : "list", value: kind === "aliases" ? [] : kind === "name" && language === "ko" ? "유인식" : "" })));
  const f = fixture(() => local); await f.open(); await f.click("编辑资料");
  const groups = f.nodes.filter(row => row.className === "person-library-language-fields");
  assert.equal(groups.length, 8); assert.deepEqual(groups.filter(row => row.open).map(row => row.children[0].textContent), ["韩语"]);
  await submit(f, f.find(".person-library-edit")); assert.equal(f.calls.length, 1);
});

test("shared profile renders Chinese names and only local portraits without an empty birthday", async () => {
  const f = fixture(); await f.open();
  assert.equal(f.find("h2").textContent, "中文姓名");
  assert.equal(f.find("img").src, avatarUrl);
  assert.equal(f.find("img").alt, "中文姓名");
  assert.ok(f.nodes.some(node => node.textContent === "男性"));
  assert.equal(f.document.body.textContent.includes("?-?-?"), false);
  assert.ok(f.document.body.textContent.includes("关联作品 · 演员 · 角色名"));
  assert.equal(f.calls.length, 1);
});

test("independent source adoption folds safe fields but keeps conflicts and default fills selectable", async () => {
  const f = fixture((route, body) => {
    if (route === "people-library/detail") return detail();
    if (route === "people-library/source-preview") return diff([
      row("name", person.name, { merge_action: "same", selectable: false }),
      row("name_cn", "补齐中文", { before: "", merge_action: "fill", selected: true }),
      row("summary", "新简介", { merge_action: "conflict", requires_confirmation: true }),
      row("aliases", [], { merge_action: "empty", selectable: false })
    ]);
    if (route === "people-library/source-apply") { assert.deepEqual(body.selected_fields, ["summary"]); return { ok: true }; }
    throw Error("unexpected request: " + route);
  });
  await f.open(); const form = await sourceForm(f); await submit(f, form);
  const box = f.find(".person-library-diff"), automatic = f.find(".library-person-auto-fields");
  assert.equal(automatic.open, false);
  assert.equal(automatic.children[0].textContent, "自动补齐／保持原值字段（3）");
  assert.equal(box.children.filter(node => node.className === "person-library-diff-row").length, 1);
  const conflict = box.children.find(node => node.className === "person-library-diff-row");
  assert.equal(conflict.querySelector("label").textContent, "summary");
  assert.equal(f.calls.some(call => call.route.endsWith("apply")), false);
  const fill = automatic.querySelectorAll("input").find(node => node.checked);
  assert.ok(fill); fill.checked = false; f.fire(fill, "change");
  const manual = conflict.querySelector("input"); manual.checked = true; f.fire(manual, "change");
  await f.click("确认采用所选资料");
  assert.equal(f.calls.filter(call => call.route === "people-library/source-apply").length, 1);
});

test("closing or replacing a shared profile ignores late detail success and failure", async () => {
  const pending = deferred(), newer = deferred(); let reads = 0;
  const f = fixture(() => (++reads === 1 ? pending : newer).promise);
  await f.open(); await f.click("关闭"); assert.equal(f.find("dialog"), null);
  await f.open({ personId: "person_second" }); newer.resolve(detail({ id: "person_second", name_cn: "第二个人" })); await flush();
  pending.resolve(detail({ name_cn: "迟到的人" })); await flush();
  assert.equal(f.find("h2").textContent, "第二个人"); assert.equal(f.nodes.filter(node => node.tag === "dialog").length, 1);
  const failed = deferred(), g = fixture(() => failed.promise); await g.open(); await g.click("关闭"); failed.reject(Error("迟到失败")); await flush();
  assert.equal(g.find("dialog"), null); assert.equal(g.notices.length, 0);
});

test("shared profile alias import defaults to the folded retained union without another confirmation", async () => {
  const f = fixture((route, body) => {
    if (route === "people-library/detail") return detail();
    if (route === "people-library/source-preview") return diff([
      row("localized.aliases.ko", ["새별명"], { before: ["기존"], merged_value: ["기존", "새별명"], merge_action: "merge", requires_confirmation: false, selected: true })
    ]);
    if (route === "people-library/source-apply") { assert.deepEqual(body.selected_fields, ["localized.aliases.ko"]); return { ok: true }; }
    throw Error("unexpected request: " + route);
  });
  await f.open(); await submit(f, await sourceForm(f));
  const automatic = f.find(".library-person-auto-fields");
  assert.equal(automatic.open, false);
  assert.equal(f.find(".person-library-diff").children.filter(node => node.className === "person-library-diff-row").length, 0);
  assert.ok(automatic.querySelectorAll("pre").some(node => node.textContent === JSON.stringify(["기존", "새별명"], null, 2)));
  assert.equal(f.button("确认采用所选资料").disabled, false);
  assert.equal(f.calls.some(call => call.route.endsWith("apply")), false);
  await f.click("确认采用所选资料");
  assert.equal(f.calls.filter(call => call.route === "people-library/source-apply").length, 1);
});

test("manual shared-person editing previews only changed fields before applying and notifies on close", async () => {
  let changed = 0, saved = false;
  const f = fixture((route, body) => {
    if (route === "people-library/detail") return detail(saved ? { summary: "<b>新简介</b>" } : {});
    if (route === "people-library/edit-preview") { assert.deepEqual(body.changes, { summary: "<b>新简介</b>" }); assert.equal(body.version, "version-1"); return diff([row("summary", body.changes.summary, { selected: true })]); }
    if (route === "people-library/edit-apply") { assert.deepEqual(body, { person_id: person.id, preview_token: "preview-signed", selected_fields: ["summary"] }); saved = true; return { ok: true }; }
    throw Error(route);
  });
  await f.open({ onChanged() { changed++; } }); await f.click("编辑资料");
  const form = f.find(".person-library-edit"); assert.equal(form.querySelectorAll("textarea").length, 1); form.querySelector("textarea").value = "<b>新简介</b>"; await submit(f, form);
  assert.equal(saved, false); await f.click("确认采用所选资料"); assert.equal(saved, true);
  assert.ok(f.nodes.some(node => node.textContent === "<b>新简介</b>")); assert.equal(f.find("b"), null); assert.equal(changed, 0);
  await f.click("关闭"); assert.equal(changed, 1); assert.deepEqual(plain(f.events), [{ person_id: person.id }]);
});

test("shared profile source folds automatic keyed details and submits them without extra clicks", async () => {
  const added = "infobox.place", filled = "infobox.height", conflict = "infobox.agency";
  const f = fixture((route, body) => {
    if (route === "people-library/detail") return detail();
    if (route === "people-library/source-preview") return diff([
      row(added, "首尔", { before: "", label: "详细资料 · 出生地", merge_action: "fill", requires_confirmation: false, selected: true }),
      row(filled, "180 cm", { before: "", label: "详细资料 · 身高", merge_action: "fill", requires_confirmation: false, selected: true }),
      row(conflict, "新公司", { before: "旧公司", label: "详细资料 · 经纪公司", merge_action: "conflict", requires_confirmation: true })
    ]);
    if (route === "people-library/source-apply") { assert.deepEqual(body.selected_fields, [added, filled]); return { ok: true }; }
    throw Error("unexpected request: " + route);
  });
  await f.open(); await submit(f, await sourceForm(f));
  const automatic = f.find(".library-person-auto-fields"), box = f.find(".person-library-diff");
  assert.equal(automatic.open, false);
  assert.equal(automatic.querySelectorAll("input").filter(node => node.checked).length, 2);
  assert.equal(box.children.filter(node => node.className === "person-library-diff-row").length, 1);
  assert.match(box.children.find(node => node.className === "person-library-diff-row").textContent, /经纪公司/);
  assert.equal(f.button("确认采用所选资料").disabled, false);
  await f.click("确认采用所选资料");
  assert.equal(f.calls.filter(call => call.route === "people-library/source-apply").length, 1);
});

test("clear preview starts entirely unchecked and only confirmed fields reach offline apply", async () => {
  const pending = deferred();
  const f = fixture(route => route === "people-library/detail" ? detail() : route === "people-library/clear-preview" ? diff([row("summary", "", { selected: true }), row("avatar", {}, { selected: true }), row("name", "", { selectable: false, selected: true })]) : pending.promise);
  await f.open(); await f.click("清空资料…");
  const checks = f.nodes.filter(node => node.type === "checkbox"); assert.deepEqual(checks.map(check => check.checked), [false, false, false]); assert.equal(checks[2].disabled, true);
  assert.equal(f.button("确认清空所选资料").disabled, true); await f.click("确认清空所选资料"); assert.equal(f.calls.length, 2);
  select(f, 0); f.fire(f.button("确认清空所选资料")); await flush();
  assert.deepEqual(f.calls.at(-1), { route: "people-library/clear-apply", body: { person_id: person.id, preview_token: "preview-signed", selected_fields: ["summary"] } });
  assert.equal(f.button("关闭").disabled, true); f.window.NimdaPersonProfile.close(); assert.ok(f.find("dialog"));
  pending.resolve({ ok: true }); await flush(); assert.equal(f.button("关闭").disabled, false);
});

test("source preview and apply use the selected global person and preserve explicit avatar choice", async () => {
  const f = fixture((route, body) => {
    if (route === "people-library/detail") return detail();
    if (route === "people-library/source-preview") { assert.deepEqual(body, { person_id: person.id, version: "version-1", provider: "wikipedia", external_id: "演员条目" }); return diff([row("summary", "来源简介"), row("avatar", { asset_id: asset, source_url: "https://untrusted.invalid/image" })]); }
    if (route === "people-library/source-apply") { assert.deepEqual(body.selected_fields, ["avatar"]); return { ok: true }; }
    throw Error(route);
  });
  await f.open(); const form = await sourceForm(f, "wikipedia"); form.querySelector("input").value = " 演员条目 "; await submit(f, form);
  assert.equal(f.button("确认采用所选资料").disabled, true); assert.equal(f.calls.filter(call => call.route.endsWith("apply")).length, 0);
  assert.deepEqual(f.nodes.filter(node => node.tag === "img").map(node => node.src), [avatarUrl]);
  select(f, 1); await f.click("确认采用所选资料"); assert.equal(f.calls.at(-2).route, "people-library/source-apply");
  assert.equal(Object.hasOwn(f.calls.at(-2).body, "ref"), false);
});

test("failed apply leaves checked preview available for retry and never duplicates an in-flight save", async () => {
  const pending = deferred(); let attempts = 0;
  const f = fixture(route => route === "people-library/detail" ? detail() : route.endsWith("preview") ? diff([row("summary", "新简介", { selected: true })]) : (++attempts === 1 ? pending.promise : { ok: true }));
  await f.open(); await submit(f, await sourceForm(f));
  f.fire(f.button("确认采用所选资料")); f.fire(f.button("确认采用所选资料")); await flush(); assert.equal(attempts, 1);
  pending.reject(Error("人物资料版本已变化")); await flush();
  assert.equal(f.nodes.find(node => node.type === "checkbox").checked, true); assert.equal(f.button("确认采用所选资料").disabled, false); assert.equal(f.button("关闭").disabled, false);
  await f.click("确认采用所选资料"); assert.equal(attempts, 2);
});




test("library filters reject stale browse responses and show Chinese names with safe local avatars", async () => {
  const older = deferred(); let first = true, opened;
  const f = fixture((route, body) => { assert.equal(route, "people-library/browse"); if (first) { first = false; return older.promise; } assert.deepEqual(body, { query: "中文", category: "", reality: "", subtype: "", provider: "", page: 1, page_size: 36 }); return { items: [person], total: 1 }; });
  f.window.NimdaPersonProfile.open = options => { opened = options; }; f.feature.activate(); await flush();
  f.find("input").value = " 中文 "; await submit(f, f.find(".person-library-filters")); older.resolve({ items: [{ ...person, name_cn: "迟到条目" }], total: 1 }); await flush();
  const card = f.find(".person-library-card"); assert.equal(card.querySelector("strong").textContent, "中文姓名"); assert.equal(card.querySelector("img").src, avatarUrl); assert.equal(f.document.body.textContent.includes("迟到条目"), false);
  assert.equal(card.attrs["data-ui-custom"], "");
  f.fire(card); assert.equal(opened.personId, person.id); assert.equal(opened.preference, "chinese"); assert.equal(opened.trigger, card);
  assert.equal(f.button("上一页").disabled, true); assert.equal(f.button("下一页").disabled, true);
});

test("library deactivation and redraw do not close the shared profile or accept late lists", async () => {
  const pending = deferred(), f = fixture(() => pending.promise); let closes = 0;
  f.window.NimdaPersonProfile.close = () => { closes++; }; f.feature.activate(); await flush(); f.feature.deactivate(); pending.resolve({ items: [person], total: 1 }); await flush();
  assert.equal(f.find(".person-library-card"), null); f.feature.dispose(); assert.equal(closes, 0); assert.equal(f.find(".person-library-view"), null);
  const count = f.calls.length; f.document.dispatchEvent({ type: "nimda-people-changed", detail: { person_id: person.id } }); await flush(); assert.equal(f.calls.length, count);
});

test("library filter UI remains consistent after dispose and remount and refreshes after shared edits", async () => {
  const f = fixture(); f.feature.activate(); await flush(); const form = f.find(".person-library-filters"), selects = form.querySelectorAll("select");
  form.querySelector("input").value = "保留搜索"; selects[0].value = "person"; f.fire(selects[0], "change"); await flush(); selects[3].value = "douban"; f.fire(selects[3], "change"); await flush();
  f.feature.dispose(); f.feature.activate(); await flush(); assert.equal(f.find("input").value, "保留搜索"); assert.deepEqual(f.find(".person-library-filters").querySelectorAll("select").map(node => node.value), ["person", "", "", "douban"]);
  assert.deepEqual(f.calls.at(-1).body, { query: "保留搜索", category: "person", reality: "", subtype: "", provider: "douban", page: 1, page_size: 36 });
  const count = f.calls.length; f.document.dispatchEvent({ type: "nimda-people-changed", detail: { person_id: person.id } }); await flush(); assert.equal(f.calls.length, count + 1);
});

test("initial profile read failure offers a working retry and removes the stale loading message", async () => {
  let attempts = 0; const f = fixture(() => { if (++attempts === 1) throw Error("读取失败，请重试"); return detail(); });
  await f.open(); assert.ok(f.button("重试读取人物资料")); assert.equal(f.document.body.textContent.includes("正在读取"), false);
  await f.click("重试读取人物资料"); assert.equal(attempts, 2); assert.equal(f.button("重试读取人物资料"), undefined); assert.ok(f.button("编辑资料"));
});

test("operation titles distinguish active source and local edit/clear actions", async () => {
  const f = fixture(() => ({ ok: true }));
  await f.window.NimdaPersonProfile.request("people-library/edit-apply", {}); assert.match(f.titles.at(-1), /保存.*修改/);
  await f.window.NimdaPersonProfile.request("people-library/clear-preview", {}); assert.match(f.titles.at(-1), /预览.*清空/);
  await f.window.NimdaPersonProfile.request("people-library/source-preview", { provider: "tmdb" }); assert.match(f.titles.at(-1), /TMDB.*来源资料预览/);
});


const role = { id: "character_" + "2".repeat(32), type: "character", kind: "character", name: "Role", name_cn: "当前角色" };
const targetRole = { ...role, id: "character_" + "3".repeat(32), name_cn: "目标角色" };
const roleDetail = overrides => ({ ...detail(), person: { ...role, ...overrides }, sources: [] });
test("same-name role hints never merge automatically and require identity confirmation", async () => {
  let merged = false;
  const f = fixture((route, body) => {
    if (route === "people-library/detail") return roleDetail(merged ? targetRole : {});
    if (route === "characters/merge/candidates") return { candidates: merged ? [] : [{ ...targetRole, matched_names: ["Role"] }] };
    if (route === "characters/merge/preview") return { person_id: role.id, target_id: targetRole.id, donor: role, target: targetRole, preview_token: "merge-signed", affected_work_count: 2, affected_works: [{ name: "作品一" }, { name: "作品二" }], diff: [row("summary", "来源简介", { selected: true })] };
    if (route === "characters/merge/apply") { assert.deepEqual(body, { person_id: role.id, target_id: targetRole.id, preview_token: "merge-signed", selected_fields: [] }); merged = true; return { ok: true }; }
    throw Error(route);
  });
  await f.open({ personId: role.id }); await flush();
  assert.ok(f.document.body.textContent.includes("发现 1 个同名／别名候选")); assert.equal(merged, false);
  assert.equal(f.calls.some(call => call.route.endsWith("apply")), false);
  await f.click("预览合并到此角色");
  assert.ok(f.document.body.textContent.includes("作品一")); assert.ok(f.document.body.textContent.includes("作品二"));
  assert.equal(f.button("确认合并这两个角色").disabled, true);
  assert.equal(f.nodes.filter(node => node.type === "checkbox").some(node => node.checked), false);
  await f.click("确认合并这两个角色"); assert.equal(merged, false);
  select(f, 1); await f.click("确认合并这两个角色");
  assert.equal(merged, true); assert.equal(f.find("h2").textContent, "目标角色");
  assert.deepEqual(f.calls.filter(call => call.route === "people-library/detail").at(-1).body, { person_id: targetRole.id });
  await f.click("关闭"); assert.deepEqual(plain(f.events), [{ person_id: targetRole.id }]);
});

test("manual merge target search ignores late suggested candidates and sends selected fields only", async () => {
  const pending = deferred(); let applied;
  const f = fixture((route, body) => {
    if (route === "people-library/detail") return roleDetail();
    if (route === "characters/merge/candidates") return pending.promise;
    if (route === "people-library/browse") { assert.deepEqual(body, { type: "character", category: "person", reality: "fictional", query: "目标", page: 1, page_size: 30 }); return { items: [role, targetRole] }; }
    if (route === "characters/merge/preview") return { person_id: role.id, target_id: targetRole.id, target: targetRole, preview_token: "merge", diff: [row("summary", "选择的简介"), row("avatar", {})] };
    if (route === "characters/merge/apply") { applied = body; throw Error("资料已经变化，请重新预览"); }
    throw Error(route);
  });
  await f.open({ personId: role.id }); const form = f.find(".person-library-source-form"); form.querySelector("input").value = " 目标 "; await submit(f, form);
  pending.resolve({ candidates: [{ ...targetRole, name_cn: "迟到候选" }] }); await flush();
  assert.equal(f.document.body.textContent.includes("迟到候选"), false);
  assert.equal(f.nodes.filter(node => node.textContent === "预览合并到此角色").length, 1);
  await f.click("预览合并到此角色"); select(f, 0); select(f, 2); await f.click("确认合并这两个角色");
  assert.deepEqual(applied.selected_fields, ["summary"]); assert.equal(f.events.length, 0);
  assert.equal(f.button("确认合并这两个角色").disabled, false); assert.ok(f.document.body.textContent.includes("资料已经变化"));
});

test("late role suggestions cannot reappear after editing or closing the card", async () => {
  const pending = deferred();
  const f = fixture(route => route === "people-library/detail" ? roleDetail() : pending.promise);
  await f.open({ personId: role.id }); await f.click("编辑资料");
  pending.resolve({ candidates: [targetRole] }); await flush();
  assert.equal(f.find(".person-library-merge-hints"), null); assert.ok(f.find(".person-library-edit"));
  await f.click("关闭"); assert.equal(f.calls.some(call => call.route.endsWith("apply")), false);
});

test("live-action role source UI opens its associated work instead of requesting an actor portrait", async () => {
  let opened;
  const ref = { file: "work.yaml", index: 0 };
  const f = fixture(route => route === "people-library/detail" ? { ...roleDetail({ role_origins: [{ work_key: "work:1" }] }), relations: [{ name: "作品甲", ref }, { name: "作品甲", ref }] } : { candidates: [] });
  f.window.NimdaCatalogLibrary = { openWork(value) { opened = value; } };
  await f.open({ personId: role.id }); await f.click("获取来源资料");
  assert.ok(f.document.body.textContent.includes("演员照片不会作为角色图片"));
  assert.equal(f.nodes.filter(node => node.tag === "button" && node.textContent === "打开作品：作品甲").length, 1);
  await f.click("打开作品：作品甲"); assert.deepEqual(plain(opened), ref); assert.equal(f.find("dialog"), null);
  assert.equal(f.calls.some(call => call.route === "people-library/source-preview"), false);
});

test("a manually merged role retains both work-cast and Bangumi source refresh options", async () => {
  const f = fixture(route => route === "people-library/detail" ? { ...roleDetail({ role_origins: [{ work_key: "work:1" }] }), sources: [{ provider: "tmdb", external_id: "role:hash" }, { provider: "bangumi", external_id: "42" }], relations: [{ name: "作品甲", ref: { file: "work.yaml", index: 0 } }] } : { candidates: [] });
  await f.open({ personId: role.id }); await f.click("获取来源资料");
  assert.ok(f.button("打开作品：作品甲"));
  const form = f.find(".person-library-source-form");
  assert.equal(form.querySelector("select").value, "bangumi"); assert.equal(form.querySelector("input").value, "42");
});

test("classification keeps company and fictional organization outside people and exposes counts", async () => {
  const company = { ...person, kind: "corporation", name_cn: "制作会社" };
  const f = fixture(() => ({ items: [company, { ...person, id: "other", type: "character", kind: "organization", name_cn: "虚构组织" }, { ...person, id: "unknown", kind: "", name_cn: "待确认条目" }], total: 3,
    facets: { categories: [{ value: "person", label: "人物", count: 0 }, { value: "organization", label: "组织", count: 2 }, { value: "unknown", label: "待分类", count: 1 }] } }));
  f.feature.activate(); await flush();
  assert.equal(f.feature.label, "人物与组织");
  const cards = f.nodes.filter(node => node.className === "person-library-card");
  assert.ok(cards[0].textContent.includes("组织 · 公司 · 现实"));
  assert.ok(cards[1].textContent.includes("组织 · 组织 · 虚构"));
  assert.ok(cards[2].textContent.includes("待分类"));
  assert.equal(cards.some(card => card.textContent.includes("现实人物")), false);
  const filters = f.find(".person-library-filters").querySelectorAll("select");
  assert.ok(filters[0].textContent.includes("组织（2）"));
  filters[0].value = "organization"; f.fire(filters[0], "change"); await flush();
  assert.equal(f.calls.at(-1).body.category, "organization");
  assert.ok(filters[2].textContent.includes("工作室"));
  assert.equal(filters[2].textContent.includes("机体"), false);
});

test("compound taxonomy editor resets invalid subtypes and submits only one confirmed field", async () => {
  const original = { category: "person", reality: "real", subtype: "individual" }, edited = { category: "organization", reality: "real", subtype: "studio" };
  const f = fixture((route, body) => {
    if (route === "people-library/detail") { const result = detail({ taxonomy: original }); result.edit_fields.unshift({ field: "taxonomy", type: "taxonomy", label: "实体分类", editable: true }); return result; }
    if (route === "people-library/edit-preview") { assert.deepEqual(body.changes, { taxonomy: edited }); return diff([row("taxonomy", edited, { selected: true })]); }
    return { ok: true };
  });
  await f.open(); await f.click("编辑资料");
  const form = f.find(".person-library-edit"), editor = f.find(".person-library-taxonomy-editor"), selects = editor.querySelectorAll("select");
  assert.deepEqual(selects.map(node => node.value), ["person", "real", "individual"]);
  selects[0].value = "organization"; f.fire(selects[0], "change");
  assert.equal(selects[2].value, "unknown"); selects[2].value = "studio"; f.fire(selects[2], "change");
  const birth = f.find(".person-library-date"); assert.equal(birth.parent.hidden, true);
  birth.querySelector("input").value = "2000";
  await submit(f, form);
  assert.equal(f.calls.at(-1).route, "people-library/edit-preview");
  assert.equal(f.calls.some(call => call.route.endsWith("edit-apply")), false);
  await f.click("确认采用所选资料");
  assert.deepEqual(f.calls.find(call => call.route.endsWith("edit-apply")).body.selected_fields, ["taxonomy"]);
});

test("organization profile does not display personal birthday or offer actor-only provider endpoints", async () => {
  const f = fixture(() => detail({ kind: "corporation", birth_date: { year: 1980 }, taxonomy: { category: "organization", reality: "real", subtype: "corporation" } }));
  await f.open();
  assert.equal(f.nodes.some(node => node.textContent === "男性 · 1980-?-?"), false);
  await f.click("获取来源资料");
  const providers = f.find(".person-library-source-form").querySelector("select").children.map(option => option.value);
  assert.deepEqual(providers, ["bangumi"]);
});

test("classification helper honors explicit taxonomy and never guesses unknown source entries as people", () => {
  const f = fixture(), helper = f.window.NimdaPeopleTaxonomy;
  assert.equal(helper.label({ type: "person" }), "待分类");
  assert.equal(helper.label({ type: "person", kind: "individual", taxonomy: { category: "organization", reality: "fictional", subtype: "association" } }), "组织 · 团体／协会 · 虚构");
  assert.deepEqual(plain(helper.project({ type: "character", kind: "mechanic" })), { category: "object", reality: "fictional", subtype: "mechanic" });
});

test("historical Douban person links and data remain readable without external provider actions", async () => {
  const local = detail(); local.sources = [{ provider: "douban", external_id: "personage:123", source_url: "https://www.douban.com/personage/123/" }];
  const f = fixture(route => route === "people-library/detail" ? local : { ok: true }); await f.open();
  assert.ok(f.nodes.some(node => node.tag === "a" && node.href === local.sources[0].source_url));
  assert.ok(f.document.body.textContent.includes("原简介")); assert.ok(f.find("img"));
  const form = await sourceForm(f, "tmdb"), choices = form.querySelector("select").children.map(node => node.value);
  assert.deepEqual(choices, ["bangumi", "tmdb", "wikipedia"]);
  const before = f.calls.length; form.querySelector("select").value = "douban"; await submit(f, form);
  assert.equal(f.calls.length, before); assert.equal(f.button("浏览器读取详情 / 图片"), undefined);
});

test("profile versions switch through the shared edit preview without saving or losing other preferences", async () => {
  const local = detail({
    localized: { name: { "zh-Hans": "维基姓名" }, summary: { "zh-Hans": "维基简介" } },
    profile_variants: { summary: { "zh-Hans": {
      wikipedia: { value: "维基简介", external_id: "9", source_url: "https://zh.wikipedia.org/wiki/Example" },
      tmdb: { value: "TMDB <script>plain text</script>", external_id: "42", source_url: "https://www.themoviedb.org/person/42" }
    } } },
    profile_preferences: { name: { "zh-Hans": { locked: true } } }
  });
  const f = fixture((route, body) => {
    if (route === "people-library/detail") return local;
    if (route === "people-library/edit-preview") {
      assert.deepEqual(body.changes, { profile_preferences: { name: { "zh-Hans": { locked: true } }, summary: { "zh-Hans": { source: "tmdb" } } } });
      return diff([row("profile_preferences", body.changes.profile_preferences, { selected: true,
        display_diff: [{ field: "localized.summary.zh-Hans", label: "简介（简体中文）", before: "维基简介", after: "TMDB <script>plain text</script>" }] })]);
    }
    throw Error("unexpected save: " + route);
  });
  await f.open(); await f.click("名称与简介版本");
  const form = f.find(".person-library-versions"), source = form.querySelector("select");
  assert.equal(source.value, "auto"); source.value = "source:tmdb"; f.fire(source, "change");
  assert.ok(form.textContent.includes("TMDB <script>plain text</script>"));
  assert.equal(form.querySelectorAll("script").length, 0);
  assert.equal(form.querySelector("a").href, "https://www.themoviedb.org/person/42");
  assert.equal(f.calls.length, 1);
  await submit(f, form); assert.equal(f.calls.at(-1).route, "people-library/edit-preview");
  assert.ok(f.find(".person-library-diff").textContent.includes("TMDB <script>plain text</script>"));
  assert.ok(f.find(".person-library-diff").textContent.includes("维基简介"));
  assert.equal(f.find(".person-library-diff").textContent.includes('"source":'), false);
  assert.equal(f.calls.some(call => call.route.endsWith("apply")), false);
  assert.deepEqual(local.person.profile_preferences, { name: { "zh-Hans": { locked: true } } });
});

test("profile versions can restore automatic selection and preserve an explicitly locked empty field", async () => {
  const local = detail({ localized: { summary: { "zh-Hans": "" } },
    profile_variants: { summary: { "zh-Hans": { wikipedia: { value: "可用简介", external_id: "9", source_url: "https://evil.example/" } } } },
    profile_preferences: { summary: { "zh-Hans": { locked: true } } }
  });
  const f = fixture((route, body) => {
    if (route === "people-library/detail") return local;
    assert.equal(route, "people-library/edit-preview"); assert.deepEqual(body.changes, { profile_preferences: {} });
    return diff([row("profile_preferences", {}, { selected: true })]);
  });
  await f.open(); await f.click("名称与简介版本"); const form = f.find(".person-library-versions"), source = form.querySelector("select");
  assert.equal(source.value, "locked"); assert.ok(form.textContent.includes("（当前为空）"));
  source.value = "source:wikipedia"; f.fire(source, "change"); assert.equal(form.querySelector("a").hidden, true);
  source.value = "auto"; f.fire(source, "change"); await submit(f, form);
  assert.equal(f.calls.length, 2);
});

test("profile versions do not submit unchanged selections and actor links include age stage", async () => {
  const local = detail({ localized: { name: { "zh-Hans": "中文姓名" } },
    profile_variants: { name: { "zh-Hans": { wikipedia: { value: "中文姓名", external_id: "9" } } } }
  });
  local.relations = [{ name: "作品", role: "饰演角色", actor: { name: "少年演员" }, relation: { qualifier: { kind: "age", value: "teen", label: "young", language: "en", explicit: true } } }];
  const f = fixture(() => local); await f.open(); assert.ok(f.document.body.textContent.includes("作品 · 饰演角色 · 少年演员 · 少年"));
  await f.click("名称与简介版本"); await submit(f, f.find(".person-library-versions"));
  assert.equal(f.calls.length, 1); assert.equal(f.notices.at(-1)[0], "没有修改显示设置");
  await f.click("返回资料"); assert.ok(f.button("编辑资料"));
});

test("multiple source identities retain separately selectable profile versions", async () => {
  const local = detail({ localized: { summary: { "zh-Hans": "角色版本一" } },
    profile_variants: { summary: { "zh-Hans": {
      bangumi: { value: "角色版本一", external_id: "101", provider: "bangumi" },
      "bangumi:202": { value: "角色版本二", external_id: "202", provider: "bangumi" }
    } } }
  });
  const f = fixture((route, body) => {
    if (route === "people-library/detail") return local;
    assert.equal(route, "people-library/edit-preview");
    assert.deepEqual(body.changes.profile_preferences, { summary: { "zh-Hans": { source: "bangumi:202" } } });
    return diff([row("profile_preferences", body.changes.profile_preferences, { selected: true })]);
  });
  await f.open(); await f.click("名称与简介版本");
  const form = f.find(".person-library-versions"), source = form.querySelector("select");
  assert.ok(source.children.some(option => option.textContent === "Bangumi · 101"));
  assert.ok(source.children.some(option => option.textContent === "Bangumi · 202"));
  source.value = "source:bangumi:202"; f.fire(source, "change");
  assert.ok(form.textContent.includes("角色版本二")); await submit(f, form);
});

test("locked profile values can explicitly save source versions without selecting display fields", async () => {
  const f = fixture((route, body) => {
    if (route === "people-library/detail") return detail();
    if (route === "people-library/source-preview") return { ...diff([row("summary", "来源简介", { selected: false, selectable: false, merge_action: "keep" })]), profile_versions_changed: true };
    assert.equal(route, "people-library/source-apply"); assert.deepEqual(body.selected_fields, []);
    return { ok: true };
  });
  await f.open(); const form = await sourceForm(f, "tmdb"); await submit(f, form);
  assert.equal(f.button("保存来源版本").disabled, false);
  assert.equal(f.calls.some(call => call.route.endsWith("apply")), false);
  await f.click("保存来源版本");
  assert.equal(f.calls.filter(call => call.route === "people-library/source-apply").length, 1);
});

test("an unchanged source preview still cannot save without any selected display fields", async () => {
  const f = fixture(route => route === "people-library/detail" ? detail() : { ...diff([]), profile_versions_changed: false });
  await f.open(); const form = await sourceForm(f, "tmdb"); await submit(f, form);
  assert.equal(f.button("确认采用所选资料").disabled, true);
  assert.equal(f.calls.some(call => call.route.endsWith("apply")), false);
});
