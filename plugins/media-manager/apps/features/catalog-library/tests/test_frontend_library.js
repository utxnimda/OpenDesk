"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const SOURCE = fs.readFileSync(path.join(__dirname, "../frontend/index.js"), "utf8");
const WORK_FORM = fs.readFileSync(path.join(__dirname, "../../collection-detail/frontend/work-record-form.js"), "utf8");
const CSS = fs.readFileSync(path.join(__dirname, "../frontend/style.css"), "utf8");
const PEOPLE_PANEL = fs.readFileSync(path.join(__dirname, "../frontend/people-panel.js"), "utf8");
const PEOPLE_SELECTION = fs.readFileSync(path.join(__dirname, "../frontend/people-selection.js"), "utf8");
const RATING_BROWSER = fs.readFileSync(path.join(__dirname, "../frontend/rating-browser.js"), "utf8");
const PEOPLE_RETRY = fs.readFileSync(path.join(__dirname, "../frontend/people-retry.js"), "utf8");
const CHAPTER_NUMBERS = fs.readFileSync(path.join(__dirname, "../frontend/chapter-number-format.js"), "utf8");
const CHAPTER_TITLES = fs.readFileSync(path.join(__dirname, "../frontend/chapter-title-format.js"), "utf8");
const PRESS_SUMMARY = fs.readFileSync(path.join(__dirname, "../../../framework/frontend/src/common/press-summary.js"), "utf8");
const CARD_LAYOUT = fs.readFileSync(path.join(__dirname, "../frontend/card-layout.js"), "utf8");
const CARD_POPOVER = fs.readFileSync(path.join(__dirname, "../frontend/card-popover.js"), "utf8");
const plain = value => JSON.parse(JSON.stringify(value));
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const flush = () => new Promise(resolve => setImmediate(resolve));
const ref = { yaml_source_rel: "2026.yaml", index_in_file: 0, source_sha256: "a".repeat(64), record_sha256: "b".repeat(64) };
const record = { attributes: [{ type: "name", data: "Test" }, { type: "country", data: "japan" }, { type: "date", data: { start: "20260000", end: "" } },
  { type: "collection-type", data: { domain: "animation", release_type: "tv", path: "T:/Test", collectioned: [], continuations: [{ title: "Extras", collectioned: [] }] } }],
  metadata: { summary: "Preserve", aliases: ["Alias"] }, extension: { custom: true } };
const item = { id: "legacy:2026.yaml#0", stable_id: false, ref, name: "Test", domain: "animation", domain_label: "动画", year: "2026", press_count: 0, classifications: [] };
const browse = () => ({ ok: true, items: [item], total: 1, page: 1, page_size: 24, legacy_count: 1, classification_revision: "revision-1",
  facets: { domains: [{ value: "animation", label: "动画", count: 1 }], years: [{ value: "2026", count: 1 }], classifications: [{ id: "series_1", name: "My series", type: "series", count: 0 }] } });
const detail = () => ({ ok: true, item, record: plain(record), ref, resources: [], chapters: [], classifications: [], sources: [], metadata: record.metadata });

test("work clear previews first and reloads using the committed fresh reference", async () => {
  const fresh = { ...ref, source_sha256: "c".repeat(64), record_sha256: "d".repeat(64) };
  const f = fixture(route => route === "work-details/clear-preview" ? { ref, preview_token: "clear-token", groups: [] } : route === "work-details/clear-apply" ? { records: [{ ref: fresh, record }] } : undefined);
  await f.controller.activate(); await f.controller.openDetail(item);
  const preview = await f.controller.previewClear();
  assert.equal(f.calls.some(call => call.route === "work-details/clear-apply"), false);
  await f.controller.applyClear(preview, ["description"]);
  assert.deepEqual(f.calls.find(call => call.route === "work-details/clear-apply").body, { ref, preview_token: "clear-token", groups: ["description"] });
  assert.deepEqual(f.calls.filter(call => call.route === "detail").at(-1).body.ref, fresh);
});

test("role materialization submits only explicit role choices and reloads the fresh work reference", async () => {
  const fresh = { ...ref, source_sha256: "e".repeat(64), record_sha256: "f".repeat(64) };
  const f = fixture(route => route === "characters/materialize/preview" ? { ref, preview_token: "role-token", entries: [{ id: "role-one" }, { id: "role-two" }] } : route === "characters/materialize/apply" ? { records: [{ ref: fresh, record }] } : undefined);
  await f.controller.activate(); await f.controller.openDetail(item);
  const preview = await f.controller.previewRoles();
  assert.equal(f.calls.some(call => call.route === "characters/materialize/apply"), false);
  await f.controller.applyRoles(preview, ["role-two"]);
  assert.deepEqual(f.calls.find(call => call.route === "characters/materialize/apply").body, { ref, preview_token: "role-token", character_ids: ["role-two"] });
  assert.deepEqual(f.calls.filter(call => call.route === "detail").at(-1).body.ref, fresh);
});

test("role materialization dialog never preselects new roles or applies before user confirmation", async () => {
  const cast = [{ person: { id: "person-one", name: "演员", type: "person" }, character_name: "角色" }];
  const f = domFixture(route => route === "detail" ? { ...detail(), people: { cast } } : route === "characters/materialize/preview" ? { ref, preview_token: "role-token", entries: [{ id: "role-one", name: "角色一", action: "create", selected: true }, { id: "role-two", name: "角色二", action: "create" }] } : route === "characters/materialize/apply" ? { records: [{ ref, record }] } : undefined);
  f.feature.init(f.context); f.feature.activate(f.context); await flush(); f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
  f.findButton("人物").emit("click"); await flush(); f.findButton("建立／更新角色档案").emit("click"); await flush();
  const checks = f.nodes().filter(node => node.type === "checkbox"); assert.equal(checks.length, 2); assert.deepEqual(checks.map(node => node.checked), [false, false]);
  assert.equal(f.findButton("确认保存所选角色档案").disabled, true); assert.equal(f.calls.some(call => call.route.endsWith("apply")), false);
  checks[1].checked = true; checks[1].emit("change"); f.findButton("确认保存所选角色档案").emit("click"); await flush(); await flush();
  assert.deepEqual(f.calls.find(call => call.route === "characters/materialize/apply").body.character_ids, ["role-two"]); f.feature.dispose();
});

test("people are read from detail without network until explicit preview and confirmation", async () => {
  const local = { ...detail(), sources: [{ provider: "bangumi", external_id: "12" }], people: { characters: [], persons: [] } };
  const preview = { ref, subject_id: "12", snapshot_id: "people-snapshot", preview_token: "people-token", changes: [] };
  const f = fixture(route => route === "detail" ? local : route === "people/preview" ? preview : route === "people/apply" ? { records: [{ ref, record }], warnings: ["生日月份无效"] } : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); f.controller.setDetailTab("people");
  assert.equal(f.calls.some(call => call.route.startsWith("people/")), false);
  await f.controller.previewPeople();
  assert.deepEqual(f.calls.find(call => call.route === "people/preview").body, { ref, subject_id: "12" });
  assert.equal(f.calls.some(call => call.route === "people/apply"), false);
  await f.controller.applyPeople(f.controller.state.people.preview);
  assert.deepEqual(f.calls.find(call => call.route === "people/apply").body, { ref, subject_id: "12", snapshot_id: "people-snapshot", preview_token: "people-token" });
  assert.equal(f.controller.state.people.preview, null);
  assert.match(f.notices.at(-1).value, /部分资料未完整获取/);
});

test("unbound people sources never issue network requests and multiple bindings require selection", async () => {
  const f = fixture(); await f.controller.activate(); await f.controller.openDetail(item); await f.controller.previewPeople("123");
  assert.equal(f.calls.some(call => call.route === "people/preview"), false); assert.match(f.notices.at(-1).value, /关联/);
  f.controller.state.detail.sources = [{ provider: "bangumi", external_id: "1" }, { provider: "bangumi", external_id: "2" }];
  await f.controller.previewPeople(); await f.controller.previewPeople("3");
  assert.equal(f.calls.some(call => call.route === "people/preview"), false);
});

test("TMDB people refresh and apply retain their exact source binding without a Bangumi request", async () => {
  const source = { provider: "tmdb", binding_id: "sb_tmdb", external_id: "12", season_number: 2 }, local = { ...detail(), sources: [source] };
  const preview = { provider: "tmdb", binding_id: "sb_tmdb", ref, snapshot_id: "snapshot", preview_token: "token", changes: [], people: { cast: [] } };
  const f = fixture(route => route === "detail" ? local : route === "people/preview" ? preview : route === "people/apply" ? { records: [{ ref, record }], people_updated: true } : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); await f.controller.previewPeople();
  assert.deepEqual(f.calls.find(call => call.route === "people/preview").body, { ref, provider: "tmdb", binding_id: "sb_tmdb" });
  await f.controller.applyPeople(f.controller.state.people.preview);
  assert.deepEqual(f.calls.find(call => call.route === "people/apply").body, { ref, provider: "tmdb", binding_id: "sb_tmdb", snapshot_id: "snapshot", preview_token: "token" });
  assert.equal(f.controller.previewPeopleBirthdays, undefined);
});

test("disabled actor sources never guess another provider", async () => {
  for (const sources of [[{ provider: "wikipedia", external_id: "12", enabled: false }], [{ provider: "tmdb", external_id: "12", binding_id: "disabled", enabled: false }]]) {
    const f = fixture(route => route === "detail" ? { ...detail(), sources } : undefined);
    await f.controller.activate(); await f.controller.openDetail(item); await f.controller.previewPeople();
    assert.equal(f.calls.some(row => row.route === "people/preview"), false); assert.match(f.notices.at(-1).value, /关联支持人物资料/);
  }
});

test("Wikipedia-only works request their explicit provider without requiring TMDB", async () => {
  const f = fixture(route => route === "detail" ? { ...detail(), sources: [{ provider: "wikipedia", external_id: "12" }] } : route === "people/preview" ? { ref, provider: "wikipedia", preview_token: "signed" } : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); await f.controller.previewPeople();
  assert.deepEqual(f.calls.find(row => row.route === "people/preview").body, { ref, provider: "wikipedia", external_id: "12" });
});

test("explicit Wikipedia pages preview independently and preserve confirmation selections", async () => {
  const preview = { ref, provider: "wikipedia", snapshot_id: "wiki", preview_token: "signed", mode: "people-import", entries: [] };
  const f = fixture(route => route === "people/wikipedia-preview" ? preview : route === "people/apply" ? { records: [{ ref, record }] } : undefined);
  await f.controller.activate(); await f.controller.openDetail(item);
  const pages = [{ page: "https://zh.wikipedia.org/wiki/演员", role: "演员", character_name: "角色" }];
  await f.controller.previewWikipediaPeople(pages);
  assert.deepEqual(f.calls.find(row => row.route === "people/wikipedia-preview").body, { ref, pages });
  assert.equal(f.calls.some(row => row.route === "people/apply"), false);
  await f.controller.applyPeople(f.controller.state.people.preview);
  assert.deepEqual(f.calls.find(row => row.route === "people/apply").body.people_selections, { wiki: [] });
});

test("pending people target prevents source save before the busy lifecycle redraw", async () => {
  const preview = { ref, snapshot_id: "source", preview_token: "signed", provider: "bangumi", diff: [], people_preview: {
    mode: "people-import", snapshot_id: "people", entries: [{ external_id: "person:1", action: "reuse", target_id: "target", _loading: true, targets: [] }]
  } };
  const f = fixture(route => route === "provider/preview" ? preview : undefined);
  await f.controller.activate(); await f.controller.openDetail(item);
  await f.controller.previewProvider({ subject_id: "1" });
  await assert.rejects(f.controller.applyProvider(f.controller.state.provider.preview), /仍在读取/);
  assert.equal(f.controller.state.busy, false);
  assert.equal(f.calls.some(row => row.route === "provider/apply"), false);
});

test("people selection distinguishes same numeric ID across providers and chapter scopes", async () => {
  const sources = [{ provider: "bangumi", binding_id: "bgm_a", external_id: "12", scope: "episodes:1-12" }, { provider: "bangumi", binding_id: "bgm_b", external_id: "12", scope: "episodes:13-24" }, { provider: "tmdb", binding_id: "tmdb_a", external_id: "12", season_number: 1 }];
  const f = fixture(route => route === "detail" ? { ...detail(), sources } : route === "people/preview" ? { ref, preview_token: "token" } : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); await f.controller.previewPeople("12");
  assert.equal(f.calls.some(row => row.route === "people/preview"), false);
  f.controller.choosePeopleSource(f.window.NimdaPeoplePanel.sourceKey(sources[1])); await f.controller.previewPeople();
  assert.deepEqual(f.calls.at(-1).body, { ref, provider: "bangumi", binding_id: "bgm_b" });
  f.controller.choosePeopleSource(f.window.NimdaPeoplePanel.sourceKey(sources[2])); assert.equal(f.controller.state.people.preview, null); await f.controller.previewPeople();
  assert.deepEqual(f.calls.at(-1).body, { ref, provider: "tmdb", binding_id: "tmdb_a" });
});

test("failed optional people reads report retained data without claiming new profiles were saved", async () => {
  const local = { ...detail(), sources: [{ provider: "bangumi", external_id: "12" }] };
  const preview = { ref, subject_id: "12", snapshot_id: "people-snapshot", preview_token: "people-token", warnings: ["人物接口超时"] };
  const f = fixture(route => route === "detail" ? local : route === "people/preview" ? preview : route === "people/apply" ?
    { records: [{ ref, record }], people_updated: false, warnings: ["人物接口超时"] } : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); await f.controller.previewPeople();
  await f.controller.applyPeople(f.controller.state.people.preview);
  assert.match(f.notices.at(-1).value, /现有资料已保留.*部分资料未完整获取/);
  assert.doesNotMatch(f.notices.at(-1).value, /已保存|已同步/);
});

test("cancelled and stale people previews cannot be applied or replace another work", async () => {
  const pending = deferred(), local = { ...detail(), sources: [{ provider: "bangumi", external_id: "12" }] };
  const f = fixture(route => route === "detail" ? local : route === "people/preview" ? pending.promise : undefined);
  await f.controller.activate(); await f.controller.openDetail(item);
  const task = f.controller.previewPeople(); await f.controller.previewPeople();
  assert.equal(f.calls.filter(call => call.route === "people/preview").length, 1);
  f.controller.cancelPeoplePreview(); pending.resolve({ ref, subject_id: "12", snapshot_id: "old", preview_token: "old" }); await task;
  assert.equal(f.controller.state.people.preview, null); assert.equal(f.controller.state.people.loading, false);
  await assert.rejects(f.controller.applyPeople(), /预览已变化/);
  assert.equal(f.calls.some(call => call.route === "people/apply"), false);
});

function fixture(handler, extra = {}) {
  const registrations = [], calls = [], notices = [], changes = [], requestOptions = [];
  const window = { setTimeout, clearTimeout, NimdaCommon: { ensureFeatureRegistry: () => ({ register: value => registrations.push(value) }) }, ...extra.window };
  const globals = { window, ...extra.globals };
  vm.runInNewContext(PRESS_SUMMARY, globals);
  vm.runInNewContext(CARD_LAYOUT, globals);
  vm.runInNewContext(CARD_POPOVER, globals);
  vm.runInNewContext(WORK_FORM, globals);
  vm.runInNewContext(PEOPLE_RETRY, globals);
  vm.runInNewContext(RATING_BROWSER, globals);
  vm.runInNewContext(PEOPLE_SELECTION, globals);
  vm.runInNewContext(PEOPLE_PANEL, globals);
  vm.runInNewContext(CHAPTER_NUMBERS, globals);
  vm.runInNewContext(CHAPTER_TITLES, globals);
  vm.runInNewContext(SOURCE, globals);
  async function request(url, options) {
    const route = url.replace("/api/catalog-library/", ""), body = JSON.parse(options.body); calls.push({ route, body }); requestOptions.push(options);
    if (handler) { const result = await handler(route, body); if (result !== undefined) return result; }
    if (route === "browse") return browse();
    if (route === "detail") return detail();
    if (route === "edits/preview") return { ok: true, edits: body.edits, issues: [] };
    if (route === "edits/apply") return { ok: true, db_committed: true, records: [{ ref: { ...ref, source_sha256: "c".repeat(64) }, record }] };
    throw Error("Unexpected route " + route);
  }
  const controller = window.NimdaCatalogLibrary.createController({ request, setStatus: (value, error) => notices.push({ value, error }), onChange: (_state, reason) => changes.push(reason) });
  return { window, registrations, calls, notices, changes, requestOptions, controller, request };
}

function retryPeopleProposal() {
  return { ok: true, mode: "people-import", provider: "tmdb", ref, snapshot_id: "people-old", preview_token: "people-token-old", entries: [],
    retry_failures: [{ id: "person-detail:4064461", object_id: "4064461", label: "演员", path: "/person/4064461", reason: "SSLEOFError", retryable: true }] };
}
test("individual people retry requests only failed IDs and never writes local people", async () => {
  const pending = deferred(), old = retryPeopleProposal();
  const f = fixture(route => route === "people/retry" ? pending.promise : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); f.controller.setDetailTab("people"); f.controller.state.people.preview = old;
  const task = f.controller.retryPeopleFailures("people", old, old, ["person-detail:4064461"]);
  assert.equal(f.controller.state.people.loading, true); assert.equal(f.controller.state.peopleRetrying, true);
  await assert.rejects(f.controller.applyPeople(old), /预览已变化/);
  await assert.rejects(f.controller.applyProvider(), /页面或预览/);
  assert.deepEqual(f.calls.at(-1), { route: "people/retry", body: { ref, preview_token: "people-token-old", snapshot_id: "people-old", failure_ids: ["person-detail:4064461"] } });
  pending.resolve({ ok: true, preview_patch: { ...retryPeopleProposal(), snapshot_id: "people-new", preview_token: "people-token-new", retry_failures: [] }, snapshot_replacements: { "people-old": "people-new" } }); await task;
  assert.equal(f.controller.state.people.preview, old); assert.equal(old.preview_token, "people-token-new");
  assert.equal(f.controller.state.peopleRetrying, false); assert.equal(f.controller.state.people.loading, false);
  assert.equal(f.calls.some(call => /apply$/.test(call.route)), false); assert.match(f.notices.at(-1).value, /未修改本地资料/);
});
test("work-source retry preserves work choices and leaves failed preview available on request errors", async () => {
  let attempts = 0;
  const f = fixture(route => { if (route === "people/retry") { if (++attempts === 1) throw Error("TLS again"); return { preview_patch: { preview_token: "work-new", people_preview: { ...retryPeopleProposal(), snapshot_id: "people-new", retry_failures: [] } }, snapshot_replacements: { "people-old": "people-new" } }; } });
  await f.controller.activate(); await f.controller.openDetail(item); f.controller.setDetailTab("sources");
  const child = retryPeopleProposal(), root = { ref, preview_token: "work-old", diff: [{ field: "name", after: "New title" }], people_preview: child };
  const provider = f.controller.state.provider; provider.preview = root; provider.selected = ["name"]; provider.fieldChoices = { name: "wiki-binding" };
  await f.controller.retryPeopleFailures("provider", root, child);
  assert.equal(provider.preview, root); assert.equal(root.preview_token, "work-old"); assert.match(child._retryError, /TLS again/);
  await f.controller.retryPeopleFailures("provider", root, child);
  assert.equal(root.preview_token, "work-new"); assert.deepEqual(plain(provider.selected), ["name"]); assert.deepEqual(plain(provider.fieldChoices), { name: "wiki-binding" });
  assert.deepEqual(plain(root.diff), [{ field: "name", after: "New title" }]); assert.equal(f.calls.at(-1).body.failure_ids, undefined);
});
test("cancel, switching tabs, leaving work and deactivation discard late retry responses", async () => {
  for (const action of [controller => controller.cancelPeopleRetry(), controller => controller.setDetailTab("overview"), controller => controller.closeDetail(), controller => controller.deactivate()]) {
    const pending = deferred(), old = retryPeopleProposal();
    const f = fixture(route => route === "people/retry" ? pending.promise : undefined);
    await f.controller.activate(); await f.controller.openDetail(item); f.controller.setDetailTab("people"); f.controller.state.people.preview = old;
    const task = f.controller.retryPeopleFailures("people", old, old); action(f.controller);
    assert.equal(f.controller.state.peopleRetrying, false);
    pending.resolve({ preview_patch: { ...old, snapshot_id: "late", preview_token: "late-token" } }); await task;
    assert.equal(old.preview_token, "people-token-old"); assert.equal(f.notices.length, 0);
  }
});
test("unknown failures, stale child previews and concurrent retries cannot issue extra calls", async () => {
  const pending = deferred(), old = retryPeopleProposal();
  const f = fixture(route => route === "people/retry" ? pending.promise : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); f.controller.setDetailTab("people"); f.controller.state.people.preview = old;
  await f.controller.retryPeopleFailures("people", old, old, ["unknown"]);
  await f.controller.retryPeopleFailures("people", old, plain(old));
  assert.equal(f.calls.filter(call => call.route === "people/retry").length, 0);
  const task = f.controller.retryPeopleFailures("people", old, old); await f.controller.retryPeopleFailures("people", old, old);
  assert.equal(f.calls.filter(call => call.route === "people/retry").length, 1);
  f.controller.cancelPeopleRetry(); pending.resolve({}); await task;
});

test("work preview expands failures outside the collapsed no-confirmation people group", async () => {
  const pending = deferred(), child = retryPeopleProposal(), root = { ok: true, ref, provider: "tmdb", preview_token: "root", diff: [], people_preview: { sources: [child] } };
  const f = domFixture(route => route === "detail" ? { ...detail(), sources: [{ provider: "tmdb", binding_id: "tmdb-bound", external_id: "123" }] } : route === "provider/sync-preview" ? root : route === "people/retry" ? pending.promise : undefined);
  f.feature.init(f.context); f.feature.activate(f.context); await flush(); f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
  f.findButton("刷新作品数据").emit("click"); await flush();
  assert.equal(f.nodes().find(node => node.className === "library-source-people-preview").open, true);
  const failurePanel = f.nodes().find(node => node.className === "library-people-retry");
  assert.ok(failurePanel); assert.equal(failurePanel.parentNode.className, "library-people-preview library-people-selection");
  assert.equal(f.nodes().find(node => node.className === "library-people-auto-group").open, false);
  f.findButton("重试此项").emit("click"); await flush();
  assert.deepEqual(f.calls.find(call => call.route === "people/retry").body.failure_ids, ["person-detail:4064461"]);
  assert.equal(f.findButton("确认本次作品数据刷新").disabled, true); assert.ok(f.findButton("取消重试"));
  pending.resolve({ preview_patch: { preview_token: "root-new", people_preview: { sources: [{ ...child, snapshot_id: "people-new", retry_failures: [] }] } }, snapshot_replacements: { "people-old": "people-new" } }); await flush(); await flush();
  assert.equal(f.findButton("重试此项"), undefined); assert.equal(f.findButton("确认本次作品数据刷新").disabled, false);
  assert.equal(f.calls.some(call => /apply$/.test(call.route)), false);
  f.feature.dispose();
});

test("isolated feature registration and controller construction do not initialize data", async () => {
  const f = fixture(); assert.equal(f.registrations[0].id, "catalog-library"); assert.equal(f.registrations[0].order, 5);
  assert.equal(f.calls.length, 0); await f.controller.activate();
  assert.deepEqual(f.calls.map(call => call.route), ["browse"]); assert.equal(f.controller.state.identityMissing, 1);
  assert.equal(f.controller.state.classificationRevision, "revision-1");
});

test("source operation labels follow the selected provider without changing API bodies", async () => {
  const f = fixture(route => route === "provider/search" ? { items: [] } : undefined);
  await f.controller.activate(); await f.controller.openDetail(item);
  for (const [provider, label] of [["bangumi", "Bangumi"], ["tmdb", "TMDB"], ["wikipedia", "中文维基百科"], ["douban", "豆瓣评分"]]) {
    f.controller.chooseProvider(provider); await f.controller.searchProvider("作品");
    assert.equal(f.requestOptions.at(-1).operationTitle, "搜索 " + label + " 来源");
    assert.equal(Object.hasOwn(f.calls.at(-1).body, "operationTitle"), false);
  }
});

test("display settings use their shared versioned endpoint instead of editing work records", async () => {
  const settings = { config: { schema_version: 1, default_priority: "original", chapter_titles: [] }, revision: "display-revision", collection_types: [] };
  const f = fixture(route => route.startsWith("display-settings") ? settings : undefined);
  await f.controller.activate(); const result = await f.controller.readDisplaySettings();
  await f.controller.saveDisplaySettings({ revision: result.revision, config: result.config });
  assert.deepEqual(f.calls.map(row => row.route), ["browse", "display-settings", "display-settings/save"]);
  assert.equal(f.calls.at(-1).body.revision, "display-revision");
  assert.match(f.notices.at(-1).value, /显示设置已保存/);
});

test("filter requests ignore older success and failure responses", async () => {
  const old = deferred(), newer = deferred();
  const f = fixture((route, body) => route === "browse" && body.query ? body.query === "old" ? old.promise : newer.promise : undefined);
  await f.controller.activate(); const one = f.controller.setFilters({ query: "old" }); const two = f.controller.setFilters({ query: "new" });
  newer.resolve({ ...browse(), items: [{ ...item, name: "Newest" }] }); await two;
  old.reject(Error("Stale failure")); await one;
  assert.equal(f.controller.state.items[0].name, "Newest"); assert.equal(f.controller.state.error, ""); assert.equal(f.controller.state.loading, false);
});

test("leaving a feature invalidates pending browsing and reloads on return", async () => {
  const pending = deferred(); let first = true;
  const f = fixture(route => { if (route === "browse" && first) { first = false; return pending.promise; } });
  const active = f.controller.activate(); f.controller.deactivate(); pending.resolve({ ...browse(), items: [{ ...item, name: "Stale" }] }); await active;
  assert.equal(f.controller.state.items.length, 0); assert.equal(f.controller.state.loading, false);
  await f.controller.activate(); assert.equal(f.controller.state.items[0].name, "Test");
});

test("legacy detail navigation carries full source and record version references", async () => {
  const f = fixture(); await f.controller.activate(); await f.controller.openDetail(item);
  const call = f.calls.find(call => call.route === "detail"); assert.deepEqual(call.body, { id: item.id, ref });
});

test("late detail result never replaces another selected work", async () => {
  const pending = deferred();
  const f = fixture((route, body) => route === "detail" && body.id === "slow" ? pending.promise : undefined);
  await f.controller.activate(); const slow = f.controller.openDetail({ id: "slow" }); await f.controller.openDetail(item);
  pending.resolve({ ...detail(), item: { ...item, name: "Wrong" } }); await slow;
  assert.equal(f.controller.state.detail.item.name, "Test"); assert.equal(f.controller.state.detailLoading, false);
});

test("record save preserves shared full record payload and uses fresh returned ref", async () => {
  const f = fixture(); await f.controller.activate(); await f.controller.openDetail(item); await f.controller.saveRecord(record, ref);
  const writes = f.calls.filter(call => call.route === "edits/apply"); assert.equal(writes.length, 1); assert.deepEqual(writes[0].body.edits[0].record, record);
  const lastDetail = f.calls.filter(call => call.route === "detail").at(-1); assert.equal(lastDetail.body.ref.source_sha256, "c".repeat(64));
});

test("blocking preview does not apply an edit and displays failure", async () => {
  const f = fixture(route => route === "edits/preview" ? { ok: true, issues: [{ blocking: true, message: "conflict" }] } : undefined);
  await f.controller.activate(); await assert.rejects(f.controller.saveRecord(record, ref), /conflict/);
  assert.equal(f.calls.some(call => call.route === "edits/apply"), false); assert.equal(f.notices.at(-1).error, true); assert.equal(f.controller.state.busy, false);
});

test("only one write can execute while a mutation is pending", async () => {
  const pending = deferred();
  const f = fixture(route => route === "edits/preview" ? pending.promise : undefined); await f.controller.activate();
  const saving = f.controller.saveRecord(record, ref); await f.controller.saveRecord(record, ref);
  pending.resolve({ ok: true, edits: [{ ref, record }], issues: [] }); await saving;
  assert.equal(f.calls.filter(call => call.route === "edits/apply").length, 1);
});

test("deactivation between edit preview and apply stops the write", async () => {
  const pending = deferred(); const f = fixture(route => route === "edits/preview" ? pending.promise : undefined); await f.controller.activate();
  const saving = f.controller.saveRecord(record, ref); f.controller.deactivate(); pending.resolve({ edits: [{ ref, record }], issues: [] });
  await assert.rejects(saving, /页面已变化/); assert.equal(f.calls.some(call => call.route === "edits/apply"), false);
});

test("stable IDs require explicit preview then apply using common edits route", async () => {
  const f = fixture(route => route === "identities/preview" ? { ok: true, edits: [{ ref, record: { ...record, id: "work_new" } }], issues: [] } : undefined);
  await f.controller.activate(); await f.controller.previewIdentities(); assert.equal(f.calls.some(call => call.route === "edits/apply"), false);
  await f.controller.applyIdentities(); assert.equal(f.calls.filter(call => call.route === "edits/apply").length, 1); assert.equal(f.controller.state.identityPreview, null);
});

test("classification membership previews scoped ref and saves through common writer", async () => {
  const f = fixture(route => route === "classification-edits/preview" ? { ok: true, edits: [{ ref, record }], issues: [] } : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); await f.controller.assignClassification("series_1", "assign");
  assert.deepEqual(f.calls.find(call => call.route === "classification-edits/preview").body, { refs: [ref], value_id: "series_1", action: "assign" });
  assert.equal(f.calls.filter(call => call.route === "edits/apply").length, 1);
});

test("classification creation and rename carry dictionary revision", async () => {
  const f = fixture(route => route === "classifications" ? { ok: true } : undefined); await f.controller.activate();
  await f.controller.saveClassification("New series"); await f.controller.saveClassification("Renamed", "series_1");
  assert.deepEqual(f.calls.filter(call => call.route === "classifications").map(call => call.body), [
    { revision: "revision-1", type: "series", name: "New series" }, { revision: "revision-1", type: "series", name: "Renamed", id: "series_1" }]);
  await assert.rejects(f.controller.saveClassification("  "), /请输入系列名称/); assert.equal(f.notices.at(-1).error, true);
});

test("provider confirmation keeps snapshot token and exact preview ref; no fields preselected", async () => {
  const imported = { ...ref, record_sha256: "e".repeat(64) };
  const f = fixture((route, body) => {
    if (route === "provider/preview") return { ok: true, ref: imported, snapshot_id: "snapshot", preview_token: "verified", diff: [
      { field: "metadata.summary", selectable: true, changed: true, before: "old", after: "new" }, { field: "path", selectable: false, changed: true }] };
    if (route === "provider/apply") return { ok: true, records: [{ ref, record }] };
  });
  await f.controller.activate(); await f.controller.openDetail(item); await f.controller.previewProvider("123");
  assert.deepEqual(plain(f.controller.state.provider.selected), []);
  f.controller.selectProviderField("path", true); f.controller.selectProviderField("arbitrary", true); f.controller.selectProviderField("metadata.summary", true);
  assert.deepEqual(plain(f.controller.state.provider.selected), ["metadata.summary"]);
  await f.controller.applyProvider(); assert.deepEqual(f.calls.find(call => call.route === "provider/apply").body,
    { ref: imported, snapshot_id: "snapshot", preview_token: "verified", selected_fields: ["metadata.summary"] });
});

test("provider supports explicit source-only confirmation without replacing fields", async () => {
  const f = fixture(route => route === "provider/preview" ? { ref, snapshot_id: "snapshot", preview_token: "verified", diff: [] } : route === "provider/apply" ? { records: [{ ref, record }] } : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); await f.controller.previewProvider(123); await f.controller.applyProvider();
  assert.deepEqual(f.calls.find(call => call.route === "provider/apply").body.selected_fields, []);
});

test("provider uses safe defaults for aliases and empty fields but respects unchecking", async () => {
  const f = fixture(route => route === "provider/preview" ? { ref, snapshot_id: "snapshot", preview_token: "verified",
    selected_fields: ["metadata.aliases", "metadata.summary", "metadata.cover"], diff: [
      { field: "metadata.aliases", before: ["A1", "A2"], after: ["A1", "A2", "A3"], selectable: true, changed: true, default_selected: true },
      { field: "metadata.summary", before: "", after: "New summary", selectable: true, changed: true, default_selected: true },
      { field: "metadata.cover", before: {}, after: {}, selectable: false, changed: false },
      { field: "name", before: "Old", after: "New", selectable: true, changed: true, default_selected: false }]
  } : route === "provider/apply" ? { records: [{ ref, record }] } : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); await f.controller.previewProvider(123);
  assert.deepEqual(plain(f.controller.state.provider.selected), ["metadata.aliases", "metadata.summary"]);
  f.controller.selectProviderField("metadata.summary", false);
  await f.controller.applyProvider();
  assert.deepEqual(f.calls.find(call => call.route === "provider/apply").body.selected_fields, ["metadata.aliases"]);
});

test("provider supports per-field safe defaults when preview omits selected_fields", async () => {
  const f = fixture(route => route === "provider/preview" ? { ref, snapshot_id: "snapshot", preview_token: "verified", diff: [
    { field: "metadata.summary", before: "", after: "New", changed: true, selectable: true, default_selected: true },
    { field: "name", before: "Old", after: "New", changed: true, selectable: true, default_selected: false }]
  } : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); await f.controller.previewProvider(123);
  assert.deepEqual(plain(f.controller.state.provider.selected), ["metadata.summary"]);
});

test("closed detail ignores late provider results", async () => {
  const pending = deferred(); const f = fixture(route => route === "provider/search" ? pending.promise : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); const searching = f.controller.searchProvider("Test"); f.controller.closeDetail();
  pending.resolve({ results: [{ id: 123, name: "Late" }] }); await searching;
  assert.equal(f.controller.state.provider.results.length, 0); assert.equal(f.controller.state.provider.loading, false);
});

test("episode range is optional and bounded preview sends finite numeric limits only", async () => {
  const f = fixture(route => route === "provider/preview" ? { ref, snapshot_id: "snapshot", preview_token: "token", diff: [] } : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); await f.controller.previewProvider(123);
  assert.equal(Object.hasOwn(f.calls.find(call => call.route === "provider/preview").body, "episode_range"), false);
  f.controller.setEpisodeRange("0", "12.5"); await f.controller.previewProvider(123);
  assert.deepEqual(f.calls.filter(call => call.route === "provider/preview").at(-1).body.episode_range, { start: 0, end: 12.5 });
  assert.deepEqual(plain(f.controller.state.provider.selected), []);
});

test("incomplete, reversed and nonfinite episode ranges display validation without fetching", async () => {
  const f = fixture(); await f.controller.activate(); await f.controller.openDetail(item);
  for (const [start, end] of [["1", ""], ["", "12"], ["-1", "12"], ["13", "12"], ["0", "Infinity"], ["NaN", "12"], ["1", "1e309"], ["0x10", "20"]]) {
    f.controller.setEpisodeRange(start, end); await f.controller.previewProvider(123);
    assert.ok(f.controller.state.provider.error, `Expected range validation for ${start},${end}`); assert.equal(f.controller.state.provider.loading, false);
  }
  assert.equal(f.calls.some(call => call.route === "provider/preview"), false);
});

test("range changes invalidate displayed token and selected fields before apply", async () => {
  const f = fixture(route => route === "provider/preview" ? { ref, snapshot_id: "snapshot", preview_token: "token", diff: [{ field: "metadata.episodes", changed: true, selectable: true }] } : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); await f.controller.previewProvider(123);
  f.controller.selectProviderField("metadata.episodes", true); f.controller.setEpisodeRange("1", "12");
  assert.equal(f.controller.state.provider.preview, null); assert.deepEqual(plain(f.controller.state.provider.selected), []);
  await assert.rejects(f.controller.applyProvider(), /预览差异/); assert.equal(f.calls.some(call => call.route === "provider/apply"), false);
});

test("range changes discard in-flight preview results and never restore stale token", async () => {
  const pending = deferred(); const f = fixture(route => route === "provider/preview" ? pending.promise : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); f.controller.setEpisodeRange("1", "12");
  const previewing = f.controller.previewProvider(123); f.controller.setEpisodeRange("13", "24");
  pending.resolve({ ref, snapshot_id: "old", preview_token: "old-token", diff: [], episode_range: { start: 1, end: 12 } }); await previewing;
  assert.equal(f.controller.state.provider.preview, null); assert.equal(f.controller.state.provider.rangeStart, "13"); assert.equal(f.controller.state.provider.loading, false);
});

test("resource action reuses existing endpoint and is single-flight without preflight scans", async () => {
  const pending = deferred(); const f = fixture(route => route === "/api/collection-detail/press/open" ? pending.promise : undefined);
  await f.controller.activate(); await f.controller.openDetail(item);
  const opening = f.controller.openResource("T:/Test", "BDRip"); await f.controller.openResource("T:/Test", "BDRip");
  assert.deepEqual(f.calls.filter(call => call.route === "/api/collection-detail/press/open"), [{ route: "/api/collection-detail/press/open", body: { path: "T:/Test", press_path: "BDRip" } }]);
  pending.resolve({ ok: false, error: "目录不可用" }); await opening;
  assert.equal(f.notices.at(-1).value, "目录不可用"); assert.equal(f.notices.at(-1).error, true); assert.equal(f.controller.state.resourceOpening, false);
  assert.equal(f.calls.some(call => /scan|inventory/.test(call.route)), false);
});

test("cover gate rejects remote, data, encoded, traversal and scheme-relative URLs", () => {
  const f = fixture(), cover = f.window.NimdaCatalogLibrary.localCover;
  for (const value of ["https://example.com/a.jpg", "//example.com/a.jpg", "data:image/svg+xml,x", "javascript:alert(1)", "/api/catalog-library/assets/../x", "/api/catalog-library/assets/%2e%2e/x", "/api/catalog-library/assets//x"]) assert.equal(cover(value), "");
  for (const extension of ["jpg", "png", "gif", "webp"]) { const url = "/api/catalog-library/assets/" + "a".repeat(64) + "." + extension; assert.equal(cover(url), url); }
  for (const value of ["/api/catalog-library/assets/abc123.jpg", "/api/catalog-library/assets/" + "a".repeat(64) + ".svg", "/api/catalog-library/assets/" + "a".repeat(64) + ".jpg?remote=x"]) assert.equal(cover(value), "");
});

test("page grids and detail panels use one main scroll, with narrow and theme styles", () => {
  assert.match(CSS, /max-height:\s*none;\s*overflow:\s*visible/);
  // Only the floating details panel may scroll; cards and page panels still
  // use the main page scrollbar rather than independent scroll containers.
  assert.doesNotMatch(CSS.replace(/\.library-card-popover\s*\{[^}]*\}/g, ""), /overflow(?:-y)?:\s*(?:auto|scroll)/);
  assert.match(CSS, /max-width: 480px/); assert.match(CSS, /var\(--text/); assert.match(CSS, /:focus-visible/);
  assert.doesNotMatch(SOURCE, /innerHTML\s*=/);
});

test("round search action remains a labeled submit button inside the search field", async () => {
  const f = domFixture(); f.feature.init(f.context); f.feature.activate(f.context); await flush();
  const search = f.nodes().find(node => node.className.includes("library-search-submit"));
  assert.equal(search.tagName, "button"); assert.equal(search.type, "submit");
  assert.equal(search.attributes["aria-label"], "搜索"); assert.equal(search.title, "搜索作品");
  assert.equal(search.parentNode.className, "library-search-control");
  assert.equal(search.children[0].className, "library-search-icon"); assert.equal(search.children[0].attributes["aria-hidden"], "true");
  const input = f.nodes().find(node => node.type === "search"); input.value = "按名称查找"; input.emit("input");
  f.nodes().find(node => node.className === "library-filters").emit("submit"); await flush();
  assert.equal(f.calls.filter(call => call.route === "browse").at(-1).body.query, "按名称查找");
});

test("time browsing keeps every year in the dropdown without truncated shortcut buttons", async () => {
  const years = Array.from({ length: 40 }, (_, index) => ({ value: String(1987 + index), count: 1 }));
  const f = domFixture(route => route === "browse" ? { ...browse(), facets: { ...browse().facets, years } } : undefined);
  f.feature.init(f.context); f.feature.activate(f.context); await flush();
  const section = f.nodes().find(node => node.tagName === "section" && node.children.some(child => child.textContent === "时间与分类"));
  assert.ok(section);
  const yearSelect = f.nodes().find(node => node.tagName === "select" && node.children.some(child => child.value === "1987"));
  assert.equal(yearSelect.children.length, 41);
  assert.equal(f.nodes().some(node => node.className.includes("library-year-links")), false);
  assert.equal(f.nodes().some(node => node.tagName === "button" && /^\d{4}$/.test(node.textContent)), false);
  yearSelect.value = "1987"; yearSelect.emit("change"); await flush();
  assert.equal(f.calls.filter(call => call.route === "browse").at(-1).body.year, "1987");
  assert.doesNotMatch(CSS, /library-year-links/);
});

test("quick filters submit four independent enums and reset pagination without losing search", async () => {
  const f = fixture(); await f.controller.activate();
  await f.controller.setFilters({ query: "Korean", page: 4 });
  await f.controller.setFilters({ country: "korea", release_type: "tv", press_group: "ANK", press_format: "WEBRip" });
  const body = f.calls.filter(call => call.route === "browse").at(-1).body;
  assert.equal(body.page, 1); assert.equal(body.query, "Korean");
  for (const [key, value] of Object.entries({ country: "korea", release_type: "tv", press_group: "ANK", press_format: "WEBRip" })) assert.equal(body[key], value);
});

test("quick filters use shared canonical dropdowns, include legacy values, and expose removable chips", async () => {
  const facets = { ...browse().facets,
    countries: [{ value: "korea", label: "korea", count: 2 }], release_types: [{ value: "tv", label: "TV", count: 3 }],
    press_groups: [{ value: "ANK", label: "ANK", count: 1 }, { value: "Old", label: "Old", count: 1 }, { value: "__none__", label: "----（未填写）", count: 1 }],
    press_formats: [{ value: "BDRip", label: "BDRip", count: 2 }] };
  const f = domFixture(route => route === "browse" ? { ...browse(), facets } : undefined);
  f.context.config = { enum_options: { country: ["japan", "korea"], release_type: ["tv", "movie"], press_group: ["ANK"], press_format: ["BDRip", "WEBRip"] },
    enum_labels: { country: { korea: "韩国", japan: "日本" }, release_type: { movie: "电影" } } };
  f.feature.init(f.context); f.feature.activate(f.context); await flush();
  const country = f.document.getElementById("library-filter-country"), group = f.document.getElementById("library-filter-press_group");
  for (const field of ["country", "release_type", "press_group", "press_format"]) assert.equal(f.document.getElementById("library-filter-" + field).tagName, "select");
  assert.equal(country.attributes["data-filter-active"], "false");
  assert.deepEqual(country.children.map(node => node.textContent), ["全部国家", "日本", "韩国 · 2"]);
  assert.deepEqual(group.children.map(node => node.value), ["", "ANK", "Old", "__none__"]);
  country.value = "korea"; country.emit("change"); await flush();
  assert.equal(f.calls.filter(call => call.route === "browse").at(-1).body.country, "korea");
  assert.ok(f.findButton("国家：韩国 ×")); assert.equal(f.findButton("清除筛选").disabled, false);
  assert.equal(country.attributes["data-filter-active"], "true");
  assert.equal(f.findButton("国家：韩国 ×").attributes["data-filter-active"], "true");
  group.value = "__none__"; group.emit("change"); await flush();
  assert.ok(f.findButton("压制组：----（未填写） ×"));
  f.findButton("国家：韩国 ×").emit("click"); await flush();
  assert.equal(f.calls.filter(call => call.route === "browse").at(-1).body.country, "");
  assert.equal(f.calls.filter(call => call.route === "browse").at(-1).body.press_group, "__none__");
  assert.equal(country.attributes["data-filter-active"], "false");
  assert.equal(group.attributes["data-filter-active"], "true");
  f.findButton("清除筛选").emit("click"); await flush();
  for (const field of ["country", "release_type", "press_group", "press_format"]) assert.equal(f.calls.filter(call => call.route === "browse").at(-1).body[field], "");
  assert.equal(f.findButton("清除筛选").disabled, true);
  for (const field of ["country", "release_type", "press_group", "press_format"]) assert.equal(f.document.getElementById("library-filter-" + field).attributes["data-filter-active"], "false");
});

test("submitted search, year and classification filters share active states without styling unsubmitted drafts", async () => {
  const facets = { ...browse().facets, years: ["2026"], classifications: [{ id: "series-test", name: "测试系列", type: "series" }] };
  const f = domFixture(route => route === "browse" ? { ...browse(), facets } : undefined);
  f.feature.init(f.context); f.feature.activate(f.context); await flush();
  const search = f.nodes().find(node => node.type === "search"), form = f.nodes().find(node => node.className === "library-filters");
  const year = f.nodes().find(node => node.tagName === "select" && node.children.some(option => option.value === "2026"));
  const series = f.nodes().find(node => node.tagName === "select" && node.children.some(option => option.value === "series-test"));
  for (const control of [search, year, series]) assert.equal(control.attributes["data-filter-active"], "false");
  search.value = "草稿"; search.emit("input"); await flush(); assert.equal(search.attributes["data-filter-active"], "false");
  form.emit("submit"); await flush(); assert.equal(search.attributes["data-filter-active"], "true");
  year.value = "2026"; year.emit("change"); await flush(); assert.equal(year.attributes["data-filter-active"], "true");
  series.value = "series-test"; series.emit("change"); await flush(); assert.equal(series.attributes["data-filter-active"], "true");
  f.findButton("清除筛选").emit("click"); await flush();
  for (const control of [search, year, series]) assert.equal(control.attributes["data-filter-active"], "false");
});

test("quick-filter empty results explain filtering rather than an empty library", async () => {
  const f = domFixture((route, body) => route === "browse" && body.country ? { ...browse(), items: [], total: 0 } : undefined);
  f.feature.init(f.context); f.feature.activate(f.context); await flush();
  const country = f.document.getElementById("library-filter-country"); country.value = "korea"; country.emit("change"); await flush();
  assert.ok(f.nodes().some(node => node.textContent === "没有符合筛选条件的作品。"));
  assert.ok(f.findButton("国家：korea ×"));
  assert.doesNotMatch(CSS, /library-quick-filters/);
});

test("six browse filters share the right sidebar's three consistent groups", async () => {
  const f = domFixture(); f.feature.init(f.context); f.feature.activate(f.context); await flush();
  const content = f.document.getElementById("library-sidebar-content");
  const sections = content.children.filter(node => node.className === "library-sidebar-section");
  assert.deepEqual(sections.map(node => node.children[0].textContent), ["作品信息", "时间与分类", "本地资源"]);
  assert.deepEqual(sections.map(node => node.children.slice(1).map(label => label.children[1].id)), [
    ["library-filter-country", "library-filter-release_type"], ["library-filter-year", "library-filter-series"],
    ["library-filter-press_group", "library-filter-press_format"]
  ]);
  assert.equal(content.parentNode.tagName, "aside");
  assert.equal(f.nodes().some(node => node.className === "library-quick-filters"), false);
  const search = f.nodes().find(node => node.className === "library-filters");
  assert.equal(search.parentNode.className, "library-browse");
  const toggle = f.nodes().find(node => node.className.includes("library-sidebar-toggle") && node.tagName === "button");
  assert.equal(toggle.attributes["aria-expanded"], "true");
  assert.equal(toggle.attributes["aria-controls"], content.id);
});

test("collapsing sidebar preserves filters, search drafts, cards, and never reads the backend", async () => {
  const f = domFixture(), preferences = new Map();
  f.window.NimdaCommon.readPreference = key => preferences.get(key);
  f.window.NimdaCommon.writePreference = (key, value) => preferences.set(key, value);
  f.feature.init(f.context); f.feature.activate(f.context); await flush();
  const content = f.document.getElementById("library-sidebar-content");
  const toggle = f.nodes().find(node => node.className.includes("library-sidebar-toggle") && node.tagName === "button");
  const count = f.nodes().find(node => node.className === "library-sidebar-filter-count");
  const country = f.document.getElementById("library-filter-country"); country.value = "korea"; country.emit("change"); await flush();
  const year = f.document.getElementById("library-filter-year"); year.value = "2026"; year.emit("change"); await flush();
  const search = f.nodes().find(node => node.type === "search"); search.value = "未提交搜索"; search.emit("input");
  const cards = f.nodes().filter(node => node.className === "library-card"), calls = f.calls.length;
  toggle.emit("click"); await flush();
  assert.equal(content.hidden, true); assert.equal(toggle.attributes["aria-expanded"], "false");
  assert.equal(toggle.attributes["aria-label"], "展开筛选栏");
  assert.equal(count.textContent, "2"); assert.equal(count.hidden, false);
  assert.equal(search.value, "未提交搜索"); assert.equal(country.value, "korea"); assert.equal(year.value, "2026");
  assert.deepEqual(f.nodes().filter(node => node.className === "library-card"), cards);
  assert.equal(f.calls.length, calls);
  assert.equal(preferences.get("nimda.catalog-library.sidebar-collapsed"), "true");
  f.findButton("国家：korea ×").emit("click"); await flush();
  assert.equal(content.hidden, true); assert.equal(count.textContent, "1");
  f.findButton("2026 年 ×").emit("click"); await flush();
  assert.equal(content.hidden, true); assert.equal(count.hidden, true);
  assert.equal(preferences.get("nimda.catalog-library.sidebar-collapsed"), "true");
  toggle.emit("click"); await flush();
  assert.equal(content.hidden, false); assert.equal(toggle.attributes["aria-expanded"], "true");
  assert.equal(preferences.get("nimda.catalog-library.sidebar-collapsed"), "false");
});

test("sidebar preference survives remount and async browsing without entering filter payloads", async () => {
  const f = domFixture(), writes = [];
  f.window.NimdaCommon.readPreference = key => key === "nimda.catalog-library.sidebar-collapsed" ? "true" : null;
  f.window.NimdaCommon.writePreference = (key, value) => writes.push([key, value]);
  f.feature.init(f.context); f.feature.activate(f.context); await flush();
  assert.equal(f.document.getElementById("library-sidebar-content").hidden, true);
  assert.equal(writes.length, 0);
  f.findButton("刷新本地列表").emit("click"); await flush();
  assert.equal(f.document.getElementById("library-sidebar-content").hidden, true);
  f.feature.dispose(); f.feature.init(f.context); f.feature.activate(f.context); await flush();
  assert.equal(f.document.getElementById("library-sidebar-content").hidden, true);
  assert.ok(f.calls.every(call => call.route !== "display-settings/save"));
  assert.ok(f.calls.filter(call => call.route === "browse").every(call => !Object.hasOwn(call.body, "sidebarCollapsed")));
  f.feature.dispose();
});

function domFixture(handler) {
  let document;
  class Element {
    constructor(tag) { this.tagName = tag; this.children = []; this.listeners = {}; this.attributes = {}; this.style = {}; this.className = ""; this.value = ""; this.textContent = ""; this.hidden = false; this.disabled = false; }
    appendChild(node) { this.children.push(node); node.parentNode = this; return node; }
    removeChild(node) { this.children = this.children.filter(child => child !== node); node.parentNode = null; }
    remove() { if (this.parentNode) this.parentNode.removeChild(this); }
    get firstChild() { return this.children[0]; }
    get isConnected() { return this === document.body || !!(this.parentNode && this.parentNode.isConnected); }
    get classList() { return { add: value => { if (!this.className.split(" ").includes(value)) this.className += " " + value; } }; }
    setAttribute(key, value) { this.attributes[key] = String(value); }
    getAttribute(key) { return this.attributes[key] ?? null; }
    removeAttribute(key) { delete this.attributes[key]; }
    addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
    removeEventListener(name, callback) { this.listeners[name] = (this.listeners[name] || []).filter(value => value !== callback); }
    get ownerDocument() { return document; }
    contains(node) { return node === this || this.children.some(child => child.contains(node)); }
    querySelectorAll(selector) { return flatten(this).slice(1).filter(node => node.className.split(" ").includes(selector.slice(1))); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    getBoundingClientRect() {
      const width = this.hidden ? 0 : this._width ?? (this.className.includes("library-grid") ? 740 : /library-card-(resources|facts)/.test(this.className) ? 180 : Array.from(this.textContent).reduce((sum, char) => sum + (char.charCodeAt(0) > 255 ? 11 : 6), this.tagName === "button" ? 12 : 0));
      return { width, height: 22, top: 20, bottom: 42, left: 20, right: width + 20 };
    }
    focus() { document.activeElement = this; }
    emit(name, extra = {}) { const event = { target: this, preventDefault() {}, stopPropagation() {}, ...extra }; (this.listeners[name] || []).forEach(callback => callback(event)); }
  }
  const flatten = node => [node, ...node.children.flatMap(flatten)];
  document = { createElement: tag => new Element(tag), listeners: {}, addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); },
    removeEventListener(name, callback) { this.listeners[name] = (this.listeners[name] || []).filter(value => value !== callback); },
    getElementById(id) { return flatten(this.body).find(node => node.id === id) || null; },
    querySelector(selector) { if (selector === "dialog[open]") return null; return flatten(this.body).find(node => node.className.split(" ").includes(selector.slice(1))) || null; } };
  document.body = new Element("body"); const shell = document.body.appendChild(new Element("div")); shell.className = "app-shell";
  shell.appendChild(new Element("nav")).className = "app-tabs";
  let editor;
  const f = fixture((route, body) => handler && handler(route, body) || (route === "browse" ? { ...browse(), items: [{ ...item, name: "<img src=x onerror=alert(1)>" }] } : undefined),
    { window: { innerWidth: 1024, innerHeight: 768, requestAnimationFrame: callback => setImmediate(callback), cancelAnimationFrame: clearImmediate,
      addEventListener() {}, removeEventListener() {}, NimdaNewWorkDialog: { open(options) { editor = options; } } }, globals: { document } });
  const feature = f.registrations[0], context = { fetchJson: f.request, config: {}, setStatus(value, error) { f.notices.push({ value, error }); } };
  const nodes = () => flatten(document.body);
  const findButton = label => nodes().find(node => node.tagName === "button" && node.textContent === label);
  return { ...f, feature, context, document, nodes, findButton, editor: () => editor };
}

test("work card keeps country year media type and score together with no placeholder rating", async () => {
  const first = { ...item, country_label: "日本", year: "2014", release_type_label: "OVA", ratings: { douban: { score: 8.2 } } };
  const f = domFixture(route => route === "browse" ? { ...browse(), items: [first, { ...first, id: "other", ratings: {} }] } : undefined);
  f.feature.init(f.context); f.feature.activate(f.context); await flush();
  const facts = f.nodes().filter(node => node.className.includes("library-card-facts"));
  assert.deepEqual(facts[0].children.map(node => node.textContent), ["日本", "·", "2014", "·", "OVA", "·", "豆瓣 8.2", "…"]);
  assert.deepEqual(facts[1].children.map(node => node.textContent), ["日本", "·", "2014", "·", "OVA", "…"]);
  assert.ok(facts[0].querySelectorAll(".library-card-dot").every(node => node.getAttribute("aria-hidden") === "true"));
  assert.match(CSS, /\.library-card-facts\s*\{[^}]*flex-wrap:\s*nowrap/);
  assert.match(CSS, /\.library-card-fact\s*\{[^}]*white-space:\s*nowrap/);
  assert.match(CSS, /\.library-card-rating\s*\{[^}]*white-space:\s*nowrap/);
  f.feature.dispose();
});

test("work card formats deduplicate groups for hover and preserve every resource in click details", async () => {
  const rows = [
    { press_format: "R2Jraw", press_group: "RawGroup" },
    { press_format: "BDRip", press_group: "VCB", press_path: "Work_BDRip(VCB)" },
    { press_format: "BDRip", press_group: "ANK" },
    { press_format: "BDRip", press_group: "VCB", press_path: "Work_BDRip(VCB)-2" },
    { press_format: "1080p", press_group: "----" },
  ];
  const f = domFixture(route => route === "browse" ? { ...browse(), items: [{ ...item, press_count: 5, press_summary: rows }] } : undefined);
  f.context.config = { enum_options: { press_format: ["BDRip", "1080p"] }, enum_labels: { press_format: { BDRip: "BDRip" }, press_group: { VCB: "VCB-Studio" } }, group_registry: { release_groups: [{ code: "VCB", names: ["VCB-Studio"] }, { code: "ANK", names: ["ANK-Raws"] }] } };
  f.feature.init(f.context); f.feature.activate(f.context); await flush(); await flush();
  const chips = f.nodes().filter(node => node.className === "library-press-chip");
  assert.deepEqual(chips.map(node => node.textContent), ["BDRip", "1080p", "R2Jraw"]);
  assert.equal(chips[0].getAttribute("data-card-tooltip"), "ANK·ANK-Raws\nVCB·VCB-Studio");
  assert.equal(chips[1].getAttribute("data-card-tooltip"), "----");
  assert.equal(chips[2].getAttribute("data-card-tooltip"), "RawGroup");
  assert.ok(chips.every(node => !node.title));
  const resourceRow = chips[0].parentNode; resourceRow._width = 110; f.findButton("小").emit("click"); await flush(); await flush();
  const hidden = chips.map(node => node.hidden); assert.ok(hidden.some(Boolean));
  assert.equal(f.nodes().some(node => node.className === "library-resource-count"), false);
  const before = f.calls.length, more = f.findButton("+" + hidden.filter(Boolean).length); assert.equal(more.attributes["aria-expanded"], "false");
  assert.equal(more.getAttribute("data-card-tooltip"), "BDRip\n1080p\nR2Jraw");
  more.emit("click"); await flush(); assert.equal(f.calls.length, before);
  assert.deepEqual(chips.map(node => node.hidden), hidden);
  const popup = f.nodes().find(node => node.className === "library-card-popover"); assert.equal(popup.parentNode, f.document.body);
  assert.equal(popup.querySelectorAll(".library-card-popover__section").length, 5);
  assert.ok(popup.querySelectorAll(".library-card-popover__field-value").some(node => node.textContent === "Work_BDRip(VCB)-2"));
  assert.equal(more.attributes["aria-expanded"], "true");
  more.emit("click"); await flush(); assert.equal(more.attributes["aria-expanded"], "false"); assert.equal(popup.isConnected, false);
  for (const node of f.nodes().filter(node => node.tagName === "button")) {
    for (let parent = node.parentNode; parent; parent = parent.parentNode) assert.notEqual(parent.tagName, "button");
  }
  assert.deepEqual(rows.map(row => row.press_group), ["RawGroup", "VCB", "ANK", "VCB", "----"]);
  f.feature.dispose();
});

test("resource row shows plain formats and adds overflow only below the complete dotted row width", async () => {
  const press_summary = [{ press_format: "BDRip", press_group: "CK" }, { press_format: "DVDRip", press_group: "CK" }, { press_format: "R2Jraw", press_group: "VCB" }];
  const f = domFixture(route => route === "browse" ? { ...browse(), items: [{ ...item, resource_count: 3, press_summary }] } : undefined);
  f.context.config = { enum_options: { press_format: ["BDRip", "DVDRip", "R2Jraw"] }, group_registry: { translation_groups: [{ code: "CK", names: ["澄空学园"] }], release_groups: [{ code: "VCB", names: ["VCB-Studio"] }] } };
  f.feature.init(f.context); f.feature.activate(f.context); await flush(); await flush();
  const row = f.nodes().find(node => node.className === "library-card-resources"), chips = row.querySelectorAll(".library-press-chip");
  const more = row.querySelector(".library-press-more"); const before = f.calls.length;
  row._width = 1000; f.findButton("大").emit("click"); await flush(); await flush();
  assert.deepEqual(chips.map(node => node.textContent), ["BDRip", "DVDRip", "R2Jraw"]);
  assert.equal(chips[0].getAttribute("data-card-tooltip"), "CK·澄空学园"); assert.equal(chips[1].getAttribute("data-card-tooltip"), "CK·澄空学园");
  assert.equal(row.querySelector(".library-resource-count"), null);
  assert.equal(more.hidden, true); assert.ok(chips.every(node => !node.hidden));
  const dots = row.querySelectorAll(".library-card-dot"), fullWidth = chips.reduce((sum, chip) => sum + chip.getBoundingClientRect().width, 0) + dots.reduce((sum, dot) => sum + dot.getBoundingClientRect().width, 0) + (chips.length + dots.length - 1) * 5;
  row._width = fullWidth; f.findButton("中").emit("click"); await flush(); await flush();
  assert.equal(more.hidden, true); assert.ok(chips.every(node => !node.hidden));
  row._width = fullWidth - 1; f.findButton("小").emit("click"); await flush(); await flush();
  assert.equal(more.hidden, false); assert.equal(more.textContent, "+1");
  assert.deepEqual(chips.map(node => node.hidden), [false, false, true]);
  row._width = fullWidth; f.findButton("大").emit("click"); await flush(); await flush();
  assert.equal(more.hidden, true); assert.ok(chips.every(node => !node.hidden)); assert.equal(f.calls.length, before); f.feature.dispose();
});

test("card size is local, defaults to medium, and switches without replacing cards or fetching data", async () => {
  const f = domFixture(), preferences = new Map();
  f.window.NimdaCommon.readPreference = key => preferences.get(key);
  f.window.NimdaCommon.writePreference = (key, value) => preferences.set(key, value);
  f.feature.init(f.context); f.feature.activate(f.context); await flush(); await flush();
  const grid = f.nodes().find(node => node.className === "library-grid"), cards = grid.children.slice(), before = f.calls.length;
  assert.equal(grid.getAttribute("data-card-size"), "medium"); assert.equal(f.findButton("中").getAttribute("aria-pressed"), "true");
  for (const [label, size] of [["小", "small"], ["大", "large"], ["自动", "auto"], ["中", "medium"]]) {
    f.findButton(label).emit("click"); await flush(); await flush();
    assert.equal(grid.getAttribute("data-card-size"), size); assert.equal(f.findButton(label).getAttribute("aria-pressed"), "true");
    assert.equal(preferences.get("nimda.catalog-library.card-size"), size);
    assert.deepEqual(grid.children, cards); assert.equal(f.calls.length, before);
    if (size === "auto") assert.match(grid.style.gridTemplateColumns, /repeat\(\d+, minmax\(0, 1fr\)\)/);
    else assert.ok(!grid.style.gridTemplateColumns);
  }
  assert.match(CSS, /library-card-min: 126px/); assert.match(CSS, /data-card-size="small"[^}]*100px/); assert.match(CSS, /data-card-size="large"[^}]*240px/);
  assert.match(CSS, /library-card-link\s*\{[^}]*text-overflow:\s*ellipsis;[^}]*white-space:\s*nowrap/);
  f.feature.dispose();
});

test("card density survives list mode, deactivation and remount without entering API payloads", async () => {
  const preferences = new Map([["nimda.catalog-library.card-size", "small"]]), f = domFixture();
  f.window.NimdaCommon.readPreference = key => preferences.get(key);
  f.window.NimdaCommon.writePreference = (key, value) => preferences.set(key, value);
  f.feature.init(f.context); f.feature.activate(f.context); await flush(); await flush();
  assert.equal(f.findButton("小").getAttribute("aria-pressed"), "true");
  f.findButton("列表").emit("click"); await flush(); await flush();
  assert.equal(f.nodes().find(node => node.className === "library-card-sizes").hidden, true);
  f.findButton("卡片").emit("click"); await flush(); await flush();
  assert.equal(f.nodes().find(node => node.className === "library-grid").getAttribute("data-card-size"), "small");
  f.feature.deactivate(); f.feature.activate(f.context); await flush(); await flush();
  assert.equal(f.findButton("小").getAttribute("aria-pressed"), "true");
  f.feature.dispose(); f.feature.init(f.context); f.feature.activate(f.context); await flush(); await flush();
  assert.equal(f.findButton("小").getAttribute("aria-pressed"), "true");
  assert.ok(f.calls.filter(call => call.route === "browse").every(call => !Object.hasOwn(call.body, "cardSize")));
  f.feature.dispose(); preferences.set("nimda.catalog-library.card-size", "invalid");
  f.feature.init(f.context); assert.equal(f.findButton("中").getAttribute("aria-pressed"), "true"); f.feature.dispose();
});

test("cached card layout resumes immediately while a return-to-tab refresh is pending", async () => {
  const pending = deferred(); let reads = 0;
  const f = domFixture(route => route === "browse" && ++reads > 1 ? pending.promise : undefined);
  f.window.NimdaCommon.readPreference = key => key === "nimda.catalog-library.card-size" ? "auto" : null;
  f.feature.init(f.context); f.feature.activate(f.context); await flush(); await flush();
  const grid = f.nodes().find(node => node.className === "library-grid"); assert.ok(grid.style.gridTemplateColumns);
  f.feature.deactivate(); assert.ok(!grid.style.gridTemplateColumns);
  f.feature.activate(f.context); await flush(); await flush(); assert.ok(grid.style.gridTemplateColumns);
  const before = f.calls.length; f.findButton("列表").emit("click"); await flush(); await flush();
  assert.equal(grid.className, "library-grid is-list"); assert.ok(!grid.style.gridTemplateColumns);
  f.findButton("卡片").emit("click"); await flush(); await flush();
  assert.equal(grid.className, "library-grid"); assert.ok(grid.style.gridTemplateColumns); assert.equal(f.calls.length, before);
  pending.resolve(browse()); await flush(); f.feature.dispose();
});

test("classification information stays accessible from wide cards even when basic facts all fit", async () => {
  const f = domFixture(route => route === "browse" ? { ...browse(), items: [{ ...item, classifications: [{ name: "收藏系列" }] }] } : undefined);
  f.feature.init(f.context); f.feature.activate(f.context); await flush(); await flush();
  const more = f.nodes().find(node => node.className.includes("library-facts-more")); assert.equal(more.hidden, false);
  more.emit("click"); await flush(); assert.ok(f.nodes().some(node => node.textContent === "分类：收藏系列")); f.feature.dispose();
});

test("overflow facts and press tooltips are safe and close when browsing leaves the feature", async () => {
  const f = domFixture(route => route === "browse" ? { ...browse(), items: [{ ...item, country_label: "日本", release_type_label: "OVA", year: "2014", ratings: { douban: { score: 8.2 } }, press_summary: [{ press_format: "BDRip", press_group: "<script>" }] }] } : undefined);
  f.feature.init(f.context); f.feature.activate(f.context); await flush(); await flush();
  const facts = f.nodes().find(node => node.className.includes("library-card-facts")); facts._width = 120;
  f.findButton("小").emit("click"); await flush(); await flush();
  const visible = facts.children.filter(node => !node.hidden).map(node => node.textContent);
  assert.ok(visible.includes("2014")); assert.ok(visible.includes("豆瓣 8.2")); assert.ok(!visible.includes("日本"));
  facts.querySelector(".library-facts-more").emit("click"); await flush();
  assert.ok(f.nodes().some(node => node.className === "library-card-popover__line" && node.textContent === "国家：日本"));
  f.feature.deactivate(); assert.equal(f.nodes().some(node => node.className === "library-card-popover"), false);
  f.feature.activate(f.context); await flush(); await flush();
  const chip = f.nodes().find(node => node.className === "library-press-chip"); chip.emit("click");
  assert.ok(f.nodes().some(node => node.className === "library-card-popover__section-title" && node.textContent.includes("<script>")));
  assert.equal(f.calls.some(call => call.route === "detail"), false);
  f.feature.dispose(); assert.equal(f.nodes().some(node => node.className === "library-card-popover"), false);
});

test("resource batch entry reuses the single versioned work save and preserves complete record", async () => {
  const local = { ...detail(), work_directory: "T:/Test" };
  const f = domFixture(route => route === "detail" ? local : undefined);
  let dialog, disposed = 0;
  f.window.NimdaBatchResourceDialog = { open(options) { dialog = options; }, dispose() { disposed++; } };
  f.context.config = { enum_options: { press_format: ["BDRip"] }, enum_labels: { press_format: { BDRip: "BDRip" } } };
  f.feature.init(f.context); f.feature.activate(f.context); await flush();
  f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
  f.findButton("资源").emit("click"); await flush();
  f.findButton("批量关联资源目录").emit("click"); await flush();
  assert.equal(dialog.workRoot, "T:/Test");
  assert.equal(dialog.enumOptions, f.context.config.enum_options);
  assert.equal(f.calls.some(call => call.route === "edits/apply"), false);
  const next = f.window.NimdaWorkRecordForm.applyPatch(dialog.record, { collectioned_ordered: [
    { press_format: "BDRip", press_group: "VCB", press_path: "Test_BDRip(VCB)", segment: "main" },
    { press_format: "1080p", press_group: "", press_path: "Test_1080p", segment: "main" }
  ] });
  await dialog.onSubmit(next);
  assert.equal(f.calls.filter(call => call.route === "edits/preview").length, 1);
  assert.equal(f.calls.filter(call => call.route === "edits/apply").length, 1);
  const edit = f.calls.find(call => call.route === "edits/apply").body.edits[0];
  assert.deepEqual(edit.ref, ref);
  assert.deepEqual(edit.record.metadata, record.metadata);
  assert.deepEqual(edit.record.extension, record.extension);
  assert.equal(edit.record.attributes.find(attr => attr.type === "collection-type").data.collectioned.length, 2);
  assert.deepEqual(edit.record.attributes.find(attr => attr.type === "collection-type").data.continuations,
    record.attributes.find(attr => attr.type === "collection-type").data.continuations);
  f.feature.deactivate(); assert.equal(disposed, 1);
});

test("resource batch cannot write a stale dialog after leaving its work", async () => {
  const f = domFixture(); let dialog;
  f.window.NimdaBatchResourceDialog = { open(options) { dialog = options; }, dispose() {} };
  f.feature.init(f.context); f.feature.activate(f.context); await flush();
  f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
  f.findButton("资源").emit("click"); await flush();
  f.findButton("批量关联资源目录").emit("click"); await flush();
  f.feature.deactivate();
  assert.throws(() => dialog.onSubmit(dialog.record), /页面已变化/);
  assert.equal(f.calls.some(call => call.route === "edits/preview" || call.route === "edits/apply"), false);
});

test("TMDB season preview and confirmation retain the provider and signed reference", async () => {
  const f = fixture(route => route === "provider/search" ? { results: [{ id: 94796 }] } : route === "provider/preview" ?
    { provider: "tmdb", ref, subject_id: 94796, season_number: 2, snapshot_id: "tmdb-snapshot", preview_token: "signed", diff: [] } :
    route === "provider/apply" ? { records: [{ ref, record }] } : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); f.controller.chooseProvider("tmdb");
  f.controller.setProviderSeason("2"); await f.controller.searchProvider("爱的迫降"); await f.controller.previewProvider(94796);
  assert.deepEqual(f.calls.find(call => call.route === "provider/search").body, { provider: "tmdb", query: "爱的迫降" });
  assert.deepEqual(f.calls.find(call => call.route === "provider/preview").body, { provider: "tmdb", ref, subject_id: "94796", season_number: 2 });
  assert.equal(f.calls.some(call => call.route === "provider/apply"), false);
  await f.controller.applyProvider();
  assert.deepEqual(f.calls.find(call => call.route === "provider/apply").body, { provider: "tmdb", ref, snapshot_id: "tmdb-snapshot", preview_token: "signed", selected_fields: [] });
});

test("changing provider or season retires outstanding preview and never crosses providers", async () => {
  const pending = deferred(), f = fixture(route => route === "provider/preview" ? pending.promise : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); f.controller.chooseProvider("tmdb");
  const loading = f.controller.previewProvider(123); f.controller.chooseProvider("wikipedia");
  pending.resolve({ provider: "tmdb", ref, snapshot_id: "old", preview_token: "old", diff: [] }); await loading;
  assert.equal(f.controller.state.provider.provider, "wikipedia"); assert.equal(f.controller.state.provider.preview, null);
  f.controller.chooseProvider("tmdb"); f.controller.state.provider.preview = { preview_token: "stale" };
  f.controller.setProviderSeason("3"); assert.equal(f.controller.state.provider.preview, null);
  await assert.rejects(f.controller.applyProvider(), /预览差异/);
});

test("refresh TMDB binding preserves exact season and Wikipedia does not inherit episode ranges", async () => {
  for (const source of [{ provider: "tmdb", external_id: "10", season_number: 3, scope: "season:3" }, { provider: "wikipedia", external_id: "20" }]) {
    const f = fixture(route => route === "detail" ? { ...detail(), sources: [source] } : route === "provider/preview" ? { provider: source.provider, ref, diff: [] } : undefined);
    await f.controller.activate(); await f.controller.openDetail(item); await f.controller.refreshProvider();
    const body = f.calls.find(call => call.route === "provider/preview").body;
    assert.equal(body.provider, source.provider); assert.equal(body.subject_id, source.external_id); assert.equal(body.episode_range, undefined);
    if (source.provider === "tmdb") assert.equal(body.season_number, 3);
  }
});

test("source links are canonical safe external navigation and retain literal percent title URLs", () => {
  const sourceLink = fixture().window.NimdaCatalogLibrary.sourceLink;
  assert.equal(sourceLink({ provider: "bangumi", external_id: "1989", source_url: "javascript:alert(1)" }), "https://bangumi.tv/subject/1989");
  assert.equal(sourceLink({ provider: "tmdb", external_id: "10", season_number: 2 }), "https://www.themoviedb.org/tv/10/season/2");
  const url = "https://zh.wikipedia.org/wiki/1%25%E7%9A%84%E5%8F%AF%E8%83%BD%E6%80%A7_(2016%E5%B9%B4%E9%9B%BB%E8%A6%96%E5%8A%87)";
  assert.equal(sourceLink({ provider: "wikipedia", external_id: "123", source_url: url }), url);
  for (const bad of ["javascript:alert(1)", "https://zh.wikipedia.org.evil/wiki/x", "//zh.wikipedia.org/wiki/x", "https://evil@zh.wikipedia.org/wiki/x"]) {
    assert.equal(sourceLink({ provider: "wikipedia", source_url: bad }), "");
  }
});

test("TMDB refresh retains bounded episodes and refuses missing or conflicting season identity", async () => {
  const source = { provider: "tmdb", external_id: "10", season_number: 3, scope: "season:3;episodes:13-24", episode_range: { start: 13, end: 24 } };
  const f = fixture(route => route === "detail" ? { ...detail(), sources: [source] } : route === "provider/preview" ? { ref, provider: "tmdb", diff: [] } : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); await f.controller.refreshProvider();
  assert.deepEqual(f.calls.find(call => call.route === "provider/preview").body, { ref, provider: "tmdb", subject_id: "10", season_number: 3, episode_range: { start: 13, end: 24 } });
  for (const invalid of [{ ...source, season_number: undefined }, { ...source, season_number: 2 }, { ...source, episode_range: null }, { ...source, scope: "season:3" }]) {
    f.controller.state.detail.sources = [invalid]; const before = f.calls.length; await f.controller.refreshProvider();
    assert.equal(f.calls.length, before); assert.equal(f.controller.state.provider.validation, true); assert.equal(f.controller.state.provider.preview, null);
  }
});

test("TMDB cover uses same provider and season without requesting Bangumi", async () => {
  const source = { provider: "tmdb", external_id: "10", season_number: 3, scope: "season:3" };
  const f = domFixture(route => route === "detail" ? { ...detail(), sources: [source] } : route === "provider/cover-preview" ?
    { ref, provider: "tmdb", subject_id: "10", season_number: 3, cover_only: true, diff: [], snapshot_id: "c", preview_token: "c" } : undefined);
  f.feature.init(f.context); f.feature.activate(f.context); await flush(); f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
  f.findButton("更新封面").emit("click"); await flush();
  assert.deepEqual(f.calls.find(call => call.route === "provider/cover-preview").body, { ref, provider: "tmdb", subject_id: "10", season_number: 3 });
  assert.equal(f.findButton("匹配 Bangumi 封面"), undefined);
});

test("source panel provides provider selection and actual links for results preview and bindings", async () => {
  const wikiUrl = "https://zh.wikipedia.org/wiki/Example";
  const f = domFixture(route => route === "detail" ? { ...detail(), sources: [{ provider: "wikipedia", external_id: "12", source_url: wikiUrl }] } : route === "provider/search" ?
    { results: [{ id: 13, provider: "wikipedia", name: "中文作品", source_url: wikiUrl }] } : route === "provider/preview" ?
    { provider: "wikipedia", subject_id: 13, source_url: wikiUrl, ref, diff: [], snapshot_id: "w", preview_token: "w" } : undefined);
  f.feature.init(f.context); f.feature.activate(f.context); await flush(); f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
  f.findButton("来源").emit("click"); await flush();
  f.findButton("添加来源").emit("click"); await flush();
  const choice = f.document.getElementById("library-provider-choice"); assert.equal(choice.children.length, 4);
  choice.value = "wikipedia"; choice.emit("change");
  f.document.getElementById("library-provider-query").value = "中文作品";
  f.nodes().find(node => node.tagName === "form" && node.className === "library-provider-search").emit("submit"); await flush();
  f.findButton("预览差异").emit("click"); await flush();
  const links = f.nodes().filter(node => node.tagName === "a" && node.href === wikiUrl);
  assert.equal(links.length, 3); for (const link of links) { assert.equal(link.target, "_blank"); assert.equal(link.rel, "noopener noreferrer"); }
  assert.equal(f.document.getElementById("library-episode-start"), null);
});

function installChapterPlayer(fixture) {
  const mounted = [];
  fixture.window.NimdaChapterPlayer = { mount(parent, options) {
    const instance = { parent, options, disposed: 0, dispose() { this.disposed++; } }; mounted.push(instance); return instance;
  } };
  return mounted;
}

test("work tabs follow overview people chapters resources classifications sources including keyboard navigation", async () => {
  const f = domFixture(); f.feature.init(f.context); f.feature.activate(f.context); await flush();
  f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
  const tabs = f.nodes().filter(node => node.attributes.role === "tab" && (node.id || "").startsWith("library-detail-tab-"));
  assert.deepEqual(tabs.map(node => node.textContent), ["概览", "人物", "章节", "资源", "分类", "来源"]);
  for (const [from, key, to] of [["overview", "ArrowRight", "people"], ["people", "ArrowRight", "chapters"],
    ["chapters", "ArrowRight", "resources"], ["resources", "ArrowLeft", "chapters"],
    ["chapters", "End", "sources"], ["sources", "Home", "overview"]]) {
    f.document.getElementById("library-detail-tab-" + from).emit("keydown", { key });
    const target = f.document.getElementById("library-detail-tab-" + to);
    assert.equal(target.attributes["aria-selected"], "true");
    assert.equal(f.document.activeElement, target);
  }
});

test("people tab renders local character and CV and saves only the explicitly confirmed preview", async () => {
  const localPeople = { characters: [{ id: "character-1", name: "本地角色", type: "character", actors: [{ id: "person-1", name: "本地声优", type: "person" }] }], persons: [] };
  const proposal = { ref, subject_id: 12, snapshot_id: "people-snapshot", preview_token: "people-token", changes: [], people: localPeople };
  const f = domFixture(route => route === "detail" ? { ...detail(), sources: [{ provider: "bangumi", external_id: "12" }], people: localPeople } :
    route === "people/preview" ? proposal : route === "people/apply" ? { records: [{ ref, record }] } : undefined);
  f.feature.init(f.context); f.feature.activate(f.context); await flush();
  f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
  f.findButton("人物").emit("click"); await flush();
  assert.ok(f.nodes().some(node => node.tagName === "button" && node.textContent === "本地角色"));
  assert.ok(f.nodes().some(node => node.tagName === "button" && node.textContent === "本地声优"));
  assert.equal(f.calls.some(call => call.route.startsWith("people/")), false);
  f.findButton("更新人物信息").emit("click"); await flush();
  assert.deepEqual(f.calls.find(call => call.route === "people/preview").body, { ref, subject_id: "12" });
  assert.ok(f.nodes().some(node => node.attributes["aria-label"] === "人物更新预览"));
  assert.equal(f.calls.some(call => call.route === "people/apply"), false);
  f.findButton("确认保存人物与关联").emit("click"); await flush(); await flush();
  assert.deepEqual(f.calls.find(call => call.route === "people/apply").body, { ref, subject_id: 12, snapshot_id: "people-snapshot", preview_token: "people-token" });
  assert.equal(f.findButton("确认保存人物与关联"), undefined);
  assert.ok(f.nodes().some(node => node.tagName === "button" && node.textContent === "本地声优"));
});

test("TMDB people tab offers the bound source, renders actor cards and confirms one exact preview", async () => {
  const binding = { provider: "tmdb", binding_id: "sb_korea", external_id: "66816", season_number: 1, name: "1%的可能性" };
  const local = { ...detail(), sources: [binding], people: { characters: [], persons: [], cast: [{ id: "person_h", type: "person", name: "河锡辰", character_name: "李载仁" }] } };
  const proposal = { ref, provider: "tmdb", binding_id: "sb_korea", snapshot_id: "people-tmdb", preview_token: "token-tmdb", people: local.people, changes: [{ id: "person_h", name: "河锡辰", action: "update" }] };
  const f = domFixture(route => route === "detail" ? local : route === "people/preview" ? proposal : route === "people/apply" ? { records: [{ ref, record }], people_updated: true } : undefined);
  f.feature.init(f.context); f.feature.activate(f.context); await flush();
  f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush(); f.findButton("人物").emit("click"); await flush();
  assert.equal(f.findButton("关联 Bangumi 来源"), undefined); assert.equal(f.findButton("检查本地生日资料"), undefined);
  assert.ok(f.nodes().some(node => node.textContent === "饰演 李载仁"));
  f.findButton("更新人物信息").emit("click"); await flush();
  assert.deepEqual(f.calls.find(call => call.route === "people/preview").body, { ref, provider: "tmdb", binding_id: "sb_korea" });
  assert.equal(f.calls.some(call => call.route === "people/apply"), false);
  f.findButton("确认保存人物与关联").emit("click"); await flush();
  assert.deepEqual(f.calls.find(call => call.route === "people/apply").body, { ref, preview_token: "token-tmdb", provider: "tmdb", binding_id: "sb_korea", snapshot_id: "people-tmdb" });
});

test("people tab and embedded work-refresh preview inherit the resolved chapter language setting", async () => {
  const actor = { id: "actor", name: "하석진", name_cn: "河锡辰", type: "person", character_name: "李载仁" };
  const character = { id: "character", name: "ちぃ", name_cn: "叽", type: "character", actors: [{ id: "cv", name: "田中理恵", name_cn: "田中理惠" }] };
  const people = { cast: [actor], characters: [character], persons: [] };
  for (const preference of ["chinese", "original"]) {
    const binding = { provider: "tmdb", binding_id: "sb_tmdb", external_id: "66816", season_number: 1 };
    const preview = { ref, provider: "tmdb", binding_id: "sb_tmdb", snapshot_id: "snapshot", preview_token: "token", people, changes: [{ id: actor.id, name: actor.name, action: "update" }] };
    const local = { ...detail(), chapter_title_preference: preference, sources: [binding], people };
    const f = domFixture(route => route === "detail" ? local : route === "people/preview" ? preview : route === "provider/sync-preview" ? { ref, preview_token: "combined", diff: [], people_preview: preview } : undefined);
    const preferred = preference === "chinese" ? "name_cn" : "name", alternative = preference === "chinese" ? "name" : "name_cn";
    f.feature.init(f.context); f.feature.activate(f.context); await flush();
    f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
    f.findButton("人物").emit("click"); await flush();
    for (const entity of [actor, character, character.actors[0]]) assert.equal(f.findButton(entity[preferred]).title, entity[alternative]);
    f.findButton(actor[preferred]).emit("click");
    assert.equal(f.nodes().find(node => node.className === "library-person-dialog-name").textContent, actor[preferred]);
    f.findButton("关闭").emit("click"); await flush();
    f.findButton("更新人物信息").emit("click"); await flush();
    assert.equal(f.nodes().find(node => node.textContent === "更新 · " + actor[preferred]).title, actor[alternative]);
    f.findButton("刷新作品数据").emit("click"); await flush();
    assert.ok(f.nodes().some(node => node.className === "library-source-people-preview"));
    for (const entity of [actor, character, character.actors[0]]) assert.equal(f.findButton(entity[preferred]).title, entity[alternative]);
    assert.equal(f.nodes().find(node => node.textContent === "更新 · " + actor[preferred]).title, actor[alternative]);
    f.findButton(character[preferred]).emit("click");
    const heading = f.nodes().find(node => node.className === "library-person-dialog-name");
    assert.equal(heading.textContent, character[preferred]); assert.equal(heading.title, character[alternative]);
    assert.equal(f.calls.some(call => /apply$/.test(call.route)), false);
    f.feature.dispose();
  }
});

async function openChapterPanel(fixture) {
  fixture.feature.init(fixture.context); fixture.feature.activate(fixture.context); await flush();
  fixture.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
  fixture.findButton("章节").emit("click"); await flush();
}

test("chapter player mounts only in its panel and disposes on tab change, back, deactivation and unmount", async () => {
  for (const action of ["tab", "back", "deactivate", "dispose"]) {
    const f = domFixture(), mounted = installChapterPlayer(f); await openChapterPanel(f);
    assert.equal(mounted.length, 1); assert.equal(mounted[0].disposed, 0); assert.deepEqual(plain(mounted[0].options.detail.ref), ref);
    assert.equal(f.calls.some(call => call.route.startsWith("playback/")), false);
    if (action === "tab") { f.findButton("资源").emit("click"); await flush(); }
    else if (action === "back") { f.findButton("← 返回作品库").emit("click"); await flush(); }
    else if (action === "deactivate") f.feature.deactivate();
    else f.feature.dispose();
    assert.equal(mounted[0].disposed, 1); f.feature.dispose(); assert.equal(mounted[0].disposed, 1);
  }
});

test("playback save refreshes full detail and cached card refs without remounting the active player", async () => {
  const f = domFixture(), mounted = installChapterPlayer(f); await openChapterPanel(f);
  const freshRef = { ...ref, source_sha256: "d".repeat(64), record_sha256: "e".repeat(64) };
  const freshDetail = { ...detail(), ref: freshRef, item: { ...item, name: "Updated work", ref: freshRef }, record: { ...plain(record), playback: { saved: true } } };
  const requests = f.calls.length; mounted[0].options.onSaved({ ok: true, db_committed: true, ref: freshRef, detail: freshDetail }); await flush();
  assert.equal(mounted.length, 1); assert.equal(mounted[0].disposed, 0); assert.equal(f.calls.length, requests);
  assert.deepEqual(plain(mounted[0].options.detail.record.playback), { saved: true });
  f.findButton("← 返回作品库").emit("click"); await flush();
  assert.ok(f.nodes().some(node => node.className.includes("library-card-link") && node.textContent === "Updated work" && node.parentNode.tagName === "h3"));
  f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
  assert.deepEqual(f.calls.filter(call => call.route === "detail").at(-1).body.ref, freshRef);
});

test("retired chapter player callbacks cannot overwrite a different panel or inactive feature", async () => {
  for (const retire of ["tab", "deactivate", "dispose"]) {
    const f = domFixture(), mounted = installChapterPlayer(f); await openChapterPanel(f); const oldDetail = mounted[0].options.detail;
    if (retire === "tab") { f.findButton("来源").emit("click"); await flush(); }
    else if (retire === "deactivate") f.feature.deactivate(); else f.feature.dispose();
    const count = f.calls.length;
    mounted[0].options.onSaved({ ok: true, db_committed: true, detail: { ...detail(), record: { playback: "stale" } }, ref: { ...ref, record_sha256: "f".repeat(64) } }); await flush();
    assert.equal(oldDetail.record.playback, undefined); assert.equal(f.calls.length, count);
  }
});

test("committed playback receipt with only a fresh ref reloads the full record before editing", async () => {
  const freshRef = { ...ref, source_sha256: "c".repeat(64) }, freshRecord = { ...plain(record), playback: { receipt: true } };
  const f = domFixture((route, body) => route === "detail" && body.ref.source_sha256 === freshRef.source_sha256 ? { ...detail(), ref: freshRef, item: { ...item, ref: freshRef }, record: freshRecord } : undefined);
  const mounted = installChapterPlayer(f); await openChapterPanel(f);
  mounted[0].options.onSaved({ ok: true, db_committed: true, requires_rescan: true, ref: freshRef }); await flush();
  assert.equal(mounted[0].disposed, 1); assert.equal(mounted.length, 2); assert.deepEqual(plain(mounted[1].options.detail.record.playback), { receipt: true });
  assert.deepEqual(f.calls.filter(call => call.route === "detail").at(-1).body.ref, freshRef);
  f.findButton("← 返回作品库").emit("click"); await flush(); f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
  assert.deepEqual(f.calls.filter(call => call.route === "detail").at(-1).body.ref, freshRef);
});

test("committed playback receipt without a ref retires cached legacy refs until browse reloads", async () => {
  const pending = deferred(); let reads = 0;
  const f = domFixture(route => route === "browse" && ++reads > 1 ? pending.promise : undefined), mounted = installChapterPlayer(f); await openChapterPanel(f);
  const detailCalls = f.calls.filter(call => call.route === "detail").length;
  mounted[0].options.onSaved({ ok: true, db_committed: true, requires_rescan: true }); await flush();
  assert.equal(mounted[0].disposed, 1); assert.equal(f.nodes().some(node => node.className.includes("library-card-link")), false);
  assert.equal(f.calls.filter(call => call.route === "detail").length, detailCalls);
  const freshRef = { ...ref, record_sha256: "c".repeat(64) };
  pending.resolve({ ...browse(), items: [{ ...item, ref: freshRef }] }); await flush();
  f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
  assert.deepEqual(f.calls.filter(call => call.route === "detail").at(-1).body.ref, freshRef);
});

test("controller accepts playback receipt in place and ignores older in-flight browse refs", async () => {
  const pending = deferred(); let reads = 0;
  const f = fixture(route => route === "browse" ? ++reads === 1 ? plain(browse()) : pending.promise : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); const browsing = f.controller.browse();
  const detailObject = f.controller.state.detail, freshRef = { ...ref, record_sha256: "c".repeat(64) };
  const freshDetail = { ...detail(), item: { ...item, ref: freshRef }, ref: freshRef, record: { ...plain(record), playback: "saved" } };
  f.controller.acceptPlaybackSave(detailObject, { ok: true, db_committed: true, detail: freshDetail });
  assert.equal(f.controller.state.detail, detailObject); assert.deepEqual(plain(f.controller.state.detailSelector.ref), freshRef);
  assert.deepEqual(plain(f.controller.state.items[0].ref), freshRef);
  pending.resolve(plain(browse())); await browsing;
  assert.deepEqual(plain(f.controller.state.items[0].ref), freshRef); assert.equal(f.controller.state.loading, false);
});

test("DOM init is passive, display text is safe, keyboard detail back restores card focus", async () => {
  const f = domFixture(); f.feature.init(f.context); assert.equal(f.calls.length, 0); f.feature.activate(f.context); await flush();
  const unsafe = f.nodes().find(node => node.textContent === "<img src=x onerror=alert(1)>"); assert.equal(unsafe.tagName, "button"); assert.equal(unsafe.parentNode.tagName, "h3");
  assert.equal(f.nodes().some(node => node.tagName === "img"), false);
  const card = f.nodes().find(node => node.className.includes("library-card-link")); card.focus(); card.emit("click"); await flush();
  assert.equal(f.document.activeElement.tagName, "h1");
  const tab = f.document.getElementById("library-detail-tab-overview"); tab.emit("keydown", { key: "ArrowRight" });
  assert.equal(f.document.activeElement.id, "library-detail-tab-people");
  f.document.listeners.keydown[0]({ key: "Escape", preventDefault() {} });
  assert.ok(f.document.activeElement.className.includes("library-card-link"));
  f.feature.dispose(); assert.equal(f.document.listeners.keydown.length, 0);
});

test("library removes decorative copy while keeping the actual work summary", async () => {
  const f = domFixture(); f.feature.init(f.context); f.feature.activate(f.context); await flush();
  assert.ok(f.nodes().some(node => node.tagName === "h1" && node.textContent === "我的作品库"));
  assert.equal(f.nodes().some(node => /library-subtitle|library-notice/.test(node.className)), false);
  assert.equal(f.nodes().some(node => node.textContent.includes("动画、音乐与影像")), false);
  f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
  assert.equal(f.nodes().find(node => node.className === "library-summary").textContent, "Preserve");
  f.findButton("分类").emit("click"); await flush();
  assert.equal(f.nodes().some(node => node.textContent.includes("系列由本地分类标签聚合")), false);
  const name = f.nodes().find(node => node.placeholder === "系列名称"); assert.equal(name.required, true);
  f.findButton("来源").emit("click"); await flush();
  assert.equal(f.nodes().some(node => node.textContent.includes("先保存来源快照")), false);
});

test("browse and detail failures report once in the shared center and retain local retries", async () => {
  for (const failedRoute of ["browse", "detail"]) {
    let attempts = 0;
    const failure = failedRoute + " unavailable";
    const f = domFixture(route => route === failedRoute && ++attempts === 1 ? { ok: false, error: failure } : undefined);
    f.feature.init(f.context); f.feature.activate(f.context); await flush();
    if (failedRoute === "detail") { f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush(); }
    assert.deepEqual(f.notices, [{ value: failure, error: true }]);
    assert.equal(f.nodes().some(node => node.textContent.includes(failure) || node.className === "library-notice"), false);
    assert.ok(f.findButton("重试")); f.findButton("重试").emit("click"); await flush();
    assert.equal(attempts, 2); assert.equal(f.findButton("重试"), undefined);
    assert.equal(f.notices.length, 1);
  }
});

test("failed mutation does not replace library cards with a duplicate error panel", async () => {
  const f = domFixture(route => route === "edits/apply" ? { ok: false, error: "写入冲突" } : undefined);
  f.feature.init(f.context); f.feature.activate(f.context); await flush();
  f.findButton("＋ 新增作品").emit("click"); await flush();
  await assert.rejects(f.editor().onSubmit({ name: "New work" }), /写入冲突/);
  assert.deepEqual(f.notices, [{ value: "写入冲突", error: true }]);
  assert.ok(f.nodes().some(node => node.className.includes("library-card-link")));
  assert.equal(f.nodes().some(node => node.className === "library-notice" || node.textContent === "写入冲突"), false);
  assert.equal(f.findButton("重试"), undefined);
});

test("provider request failures use the shared center while retry keeps the exact operation", async () => {
  for (const route of ["provider/search", "provider/preview", "provider/cover-preview"]) {
    let attempts = 0;
    const f = domFixture(requestRoute => requestRoute === "detail" ? { ...detail(), sources: [{ provider: "bangumi", external_id: "123" }] } : requestRoute === route ?
      (++attempts === 1 ? { ok: false, error: "来源连接失败" } : route === "provider/search" ? { results: [{ id: 123, name: "Source" }] } : { ref, snapshot_id: "retry", preview_token: "retry", diff: [] }) : undefined);
    f.feature.init(f.context); f.feature.activate(f.context); await flush();
    f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
    if (route === "provider/search") {
      f.findButton("来源").emit("click"); await flush();
      f.findButton("添加来源").emit("click"); await flush();
      f.document.getElementById("library-provider-query").value = "Test";
      f.nodes().find(node => node.className === "library-provider-search").emit("submit");
    } else f.findButton(route === "provider/preview" ? "刷新作品数据" : "更新封面").emit("click");
    await flush();
    assert.deepEqual(f.notices, [{ value: "来源连接失败", error: true }]);
    assert.equal(f.nodes().some(node => node.textContent === "来源连接失败" || node.className === "library-notice"), false);
    assert.ok(f.findButton("重试")); f.findButton("重试").emit("click"); await flush();
    const requests = f.calls.filter(call => call.route === route); assert.equal(requests.length, 2);
    assert.deepEqual(requests[0].body, requests[1].body); assert.equal(f.findButton("重试"), undefined);
    assert.equal(f.notices.length, 1);
  }
});

test("shared work form edits preserve local metadata and continuation structure", async () => {
  const f = domFixture(); f.feature.init(f.context); f.feature.activate(f.context); await flush();
  f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
  f.findButton("编辑作品").emit("click"); await flush(); const editor = f.editor(); assert.ok(editor); assert.equal(editor.initialData.date.start, "20260000");
  await editor.onSubmit({ name: "Changed" });
  const saved = f.calls.find(call => call.route === "edits/apply").body.edits[0].record;
  assert.deepEqual(saved.extension, record.extension); assert.deepEqual(saved.metadata, record.metadata);
  assert.deepEqual(saved.attributes.find(attr => attr.type === "collection-type").data.continuations, record.attributes[3].data.continuations);
});

test("shared editor rejects submit after leaving the feature", async () => {
  const f = domFixture(); f.feature.init(f.context); f.feature.activate(f.context); await flush();
  f.findButton("＋ 新增作品").emit("click"); await flush(); f.feature.deactivate();
  await assert.rejects(f.editor().onSubmit({ name: "Stale" }), /页面已变化/); assert.equal(f.calls.some(call => call.route === "edits/apply"), false);
});

test("source UI searches once and folds long diffs without preselecting provider fields", async () => {
  const chapters = Array.from({ length: 500 }, (_, index) => ({ title: "Episode " + index }));
  const f = domFixture(route => route === "provider/search" ? { results: [{ id: 123, name: "<script>source</script>" }] } : route === "provider/preview" ? {
    ref, snapshot_id: "snapshot", preview_token: "token", diff: [{ field: "metadata.episodes", label: "章节", before: [], after: chapters, selectable: true, changed: true }] } : undefined);
  f.feature.init(f.context); f.feature.activate(f.context); await flush();
  f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
  f.findButton("来源").emit("click"); await flush();
  const input = f.document.getElementById("library-provider-query"); input.value = "Test"; input.focus();
  f.nodes().find(node => node.className === "library-provider-search").emit("submit"); await flush();
  assert.equal(f.calls.filter(call => call.route === "provider/search").length, 1); assert.equal(f.document.activeElement.id, "library-provider-query");
  f.findButton("预览差异").emit("click"); await flush();
  assert.equal(f.nodes().find(node => node.type === "checkbox").checked, false);
  const expanded = f.nodes().find(node => node.tagName === "details" && node.children[0].textContent.includes("500 条记录")); assert.ok(expanded); assert.equal(expanded.open, undefined);
  assert.ok(f.findButton("仅确认来源关联")); assert.equal(f.nodes().some(node => node.tagName === "script"), false);
  assert.equal(f.calls.some(call => call.route === "provider/apply"), false);
});

test("source range controls show selected scope/counts and retire preview on edit", async () => {
  const f = domFixture((route, body) => route === "provider/search" ? { results: [{ id: 123, name: "Source" }] } : route === "provider/preview" ? {
    ref, snapshot_id: "snapshot", preview_token: "token", episode_range: body.episode_range || null, episode_counts: { selected: body.episode_range ? 12 : 26, total: 26 }, diff: [] } : undefined);
  f.feature.init(f.context); f.feature.activate(f.context); await flush();
  f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush(); f.findButton("来源").emit("click"); await flush();
  const search = f.document.getElementById("library-provider-query"); search.value = "Source";
  f.nodes().find(node => node.className === "library-provider-search").emit("submit"); await flush();
  f.findButton("预览差异").emit("click"); await flush(); assert.match(f.nodes().find(node => node.className === "library-preview-scope").textContent, /完整条目.*26 \/ 26/);
  let start = f.document.getElementById("library-episode-start"); start.value = "1"; start.focus(); start.emit("input");
  assert.equal(f.document.activeElement.id, "library-episode-start"); assert.equal(f.findButton("仅确认来源关联"), undefined);
  f.findButton("预览差异").emit("click"); await flush(); assert.ok(f.nodes().find(node => node.className === "library-error" && node.textContent.includes("同时填写")));
  assert.equal(f.notices.length, 0, "range validation stays next to its controls, not in the operation center");
  const end = f.document.getElementById("library-episode-end"); end.value = "12"; end.emit("input");
  f.findButton("预览差异").emit("click"); await flush(); assert.match(f.nodes().find(node => node.className === "library-preview-scope").textContent, /正片第 1–12 集.*12 \/ 26/);
  assert.deepEqual(f.calls.filter(call => call.route === "provider/preview").at(-1).body.episode_range, { start: 1, end: 12 });
});

test("technical identity is folded and only fully bound resources offer open directory", async () => {
  const f = domFixture(route => route === "detail" ? { ...detail(), resources: [{ press_format: "BDRip", press_path: "BDRip" }, { press_format: "WEB", press_path: "" }] } : undefined);
  f.feature.init(f.context); f.feature.activate(f.context); await flush(); f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
  const identity = f.nodes().find(node => node.className === "library-data-identity"); assert.ok(identity); assert.equal(identity.tagName, "details"); assert.equal(identity.open, undefined);
  assert.equal(identity.children[0].textContent, "数据标识"); f.findButton("资源").emit("click"); await flush();
  assert.equal(f.nodes().filter(node => node.tagName === "button" && node.textContent === "打开目录").length, 1);
  assert.equal(f.calls.some(call => call.route === "/api/collection-detail/press/open"), false);
});

test("each resource is one compact row with full tooltip paths, state and directory action", async () => {
  const longPath = "T:/Test/" + "长目录名".repeat(100) + "/BDRip";
  const resources = [
    { press_format: "BDRip", press_group: "Group", label: "Season 1", resolved_path: longPath, press_path: "BDRip", continuation_title: "Extras", state: "complete", state_label: "已收集" },
    { press_format: "WEB", label: "Season 2", path: "", press_path: "" }
  ];
  const f = domFixture(route => route === "detail" ? { ...detail(), resources } : route === "/api/collection-detail/press/open" ? { ok: true } : undefined);
  f.feature.init(f.context); f.feature.activate(f.context); await flush();
  f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush(); f.findButton("资源").emit("click"); await flush();
  const rows = f.nodes().filter(node => node.className === "library-resource-row"); assert.equal(rows.length, 2);
  assert.ok(rows.every(row => row.children.length === 7));
  const [format, group, heading, resourcePath, status, playback, actions] = rows[0].children;
  assert.equal(format.textContent, "BDRip"); assert.equal(group.textContent, "Group");
  assert.equal(heading.textContent, "Extras"); assert.equal(heading.title, heading.textContent);
  assert.equal(resourcePath.textContent, longPath); assert.equal(resourcePath.title, longPath);
  assert.equal(status.textContent, "已收集"); assert.equal(actions.children[0].textContent, "打开目录");
  assert.equal(playback.children[0].textContent, "设为播放资源");
  assert.equal(rows[1].children[1].textContent, "—"); assert.equal(rows[1].children[2].textContent, "—");
  assert.equal(rows[1].children[4].textContent, "未绑定"); assert.equal(rows[1].children[5].children[0].disabled, true); assert.equal(rows[1].children[6].children.length, 0);
  assert.deepEqual(f.nodes().filter(node => node.attributes.role === "columnheader").map(node => node.textContent), ["压制格式", "压制组", "分段标题", "目录", "状态", "播放资源", "操作"]);
  const directory = f.nodes().find(node => node.className === "library-work-directory"); assert.equal(directory.children[1].title, "T:/Test");
  f.findButton("打开目录").emit("click"); await flush();
  assert.deepEqual(f.calls.find(call => call.route === "/api/collection-detail/press/open").body, { path: "T:/Test", press_path: "BDRip" });
  assert.deepEqual(f.notices, [{ value: "已请求打开资源目录。", error: false }]);
  assert.equal(f.nodes().some(node => node.className === "library-notice" || node.textContent === "已请求打开资源目录。"), false);
});

test("resource format and group occupy separate columns without the generated duplicate label", async () => {
  const resources = [
    { press_format: "BDRip", press_group: "ANK", label: "BDRip-ANK", press_path: "BDRip(ANK)" },
    { press_format: "R2Jraw", press_group: "  ----  ", label: "R2Jraw", press_path: "R2Jraw" },
    { press_format: "BDRip", press_group: "Group----Name", label: "BDRip-Group----Name" },
    { press_format: "WEB", press_group: null }
  ];
  const f = domFixture(route => route === "detail" ? { ...detail(), resources } : undefined);
  f.feature.init(f.context); f.feature.activate(f.context); await flush();
  f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush(); f.findButton("资源").emit("click"); await flush();
  const rows = f.nodes().filter(node => node.className === "library-resource-row");
  assert.equal(rows.length, 4); assert.ok(rows.every(row => row.children.length === 6));
  assert.deepEqual(rows.map(row => row.children.slice(0, 2).map(cell => cell.textContent)), [["BDRip", "ANK"], ["R2Jraw", "—"], ["BDRip", "Group----Name"], ["WEB", "—"]]);
  assert.deepEqual(f.nodes().filter(node => node.attributes.role === "columnheader").map(node => node.textContent), ["压制格式", "压制组", "目录", "状态", "播放资源", "操作"]);
  assert.ok(rows.every(row => row.attributes.role === "row" && row.children.every(cell => cell.attributes.role === "cell")));
  assert.equal(f.nodes().some(node => node.textContent === "BDRip-ANK" || node.className === "library-resource-title"), false);
  assert.equal(rows[0].children[1].title, "ANK");
});

test("resource rows constrain intrinsic sizes and ellipsize long paths on desktop and narrow screens", () => {
  assert.match(CSS, /\.library-resource-row\s*\{[^}]*grid-template-columns:\s*minmax\(0,[^}]*min-width:\s*0/s);
  assert.match(CSS, /\.library-resource-format, \.library-resource-group, \.library-resource-title, \.library-resource-path, \.library-resource-state[^}]*\{[^}]*min-width:\s*0;[^}]*overflow:\s*hidden;[^}]*text-overflow:\s*ellipsis;[^}]*white-space:\s*nowrap/s);
  assert.match(CSS, /@media \(max-width: 720px\)\s*\{\s*\.library-resource-row\s*\{[^}]*grid-template-columns:\s*minmax\(0,/s);
  assert.doesNotMatch(CSS, /\.library-notice|\.library-subtitle/);
});

function playbackDetail(selected = 0, nextRef = ref) {
  return { ...detail(), ref: nextRef, item: { ...item, ref: nextRef }, resources: [
    { press_format: "BDRip", press_group: "ANK", press_path: "BDRip", is_playback_resource: selected === 0 },
    { press_format: "DVD", press_group: "Other", press_path: "DVD", is_playback_resource: selected === 1 }
  ], playback: { ref: nextRef, resource_index: selected, resource_key: "r" + selected, revision: "revision-" + selected, files: [{ relative_path: "Disc/01.mkv" }], chapters: [{ chapter_key: "c1", number: 1, status: "automatic", relative_path: "Disc/01.mkv", duration_seconds: null }] } };
}

async function openResourcePanel(f) {
  f.feature.init(f.context); f.feature.activate(f.context); await flush(); f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush(); f.findButton("资源").emit("click"); await flush();
}

function continuousPlaybackDetail(selected = 0, mode = "local", nextRef = ref) {
  const data = playbackDetail(selected, nextRef);
  data.chapter_numbering = { has_continuous: true, local_start: "1", continuous_start: "14" };
  data.resources.forEach((resource, index) => { resource.numbering_mode = index === selected ? mode : "local"; });
  data.playback.numbering_mode = mode;
  return data;
}

test("resource numbering changes remain drafts until explicitly applied and retain current persisted mode while pending", async () => {
  const pending = deferred(), freshRef = { ...ref, record_sha256: "9".repeat(64) };
  const f = domFixture(route => route === "detail" ? continuousPlaybackDetail() : route === "playback/select" ? pending.promise : undefined);
  await openResourcePanel(f);
  let select = f.document.getElementById("library-resource-numbering-0");
  assert.equal(select.value, "local"); assert.deepEqual(select.children.map(node => node.textContent), ["本作(1起)", "续接(14起)"]);
  assert.equal(f.findButton("应用"), undefined);
  select.focus(); select.value = "continuous"; select.emit("change"); await flush();
  assert.equal(f.calls.filter(call => call.route === "playback/select").length, 0);
  assert.equal(f.document.activeElement.id, "library-resource-numbering-0");
  assert.ok(f.nodes().some(node => node.className.includes("library-playback-current") && node.textContent === "应用" && node.title.includes("已保存：本作集数")));
  assert.equal(f.nodes().some(node => node.className === "library-playback-saved-numbering"), false);
  f.findButton("应用").emit("click"); f.findButton("应用").emit("click"); await flush();
  assert.deepEqual(f.calls.filter(call => call.route === "playback/select").map(call => call.body), [{ ref, resource_index: 0, numbering_mode: "continuous" }]);
  assert.equal(f.document.getElementById("library-resource-numbering-0").disabled, true);
  assert.equal(f.findButton("章节").disabled, true); assert.ok(f.nodes().some(node => node.className.includes("library-playback-current")));
  const saved = continuousPlaybackDetail(0, "continuous", freshRef);
  pending.resolve({ ok: true, db_committed: true, ...saved.playback, detail: saved }); await flush();
  assert.equal(f.document.getElementById("library-resource-numbering-0").value, "continuous");
  assert.equal(f.findButton("应用"), undefined);
  assert.equal(f.nodes().some(node => node.className === "library-playback-saved-numbering"), false);
  assert.equal(f.findButton("章节").disabled, false);
});

test("failed mode switch retains old current resource and draft mode for deliberate retry", async () => {
  const f = domFixture(route => route === "detail" ? continuousPlaybackDetail() : route === "playback/select" ? { ok: false, error: "识别失败" } : undefined);
  await openResourcePanel(f);
  const select = f.document.getElementById("library-resource-numbering-0"); select.value = "continuous"; select.emit("change"); await flush();
  f.findButton("应用").emit("click"); await flush();
  assert.equal(f.document.getElementById("library-resource-numbering-0").value, "continuous");
  assert.equal(f.findButton("应用").disabled, false);
  assert.ok(f.nodes().some(node => node.className.includes("library-playback-current") && node.title.includes("已保存：本作集数")));
  assert.deepEqual(f.notices.at(-1), { value: "识别失败", error: true });
  f.findButton("应用").emit("click"); await flush();
  assert.deepEqual(f.calls.filter(call => call.route === "playback/select").map(call => call.body), [
    { ref, resource_index: 0, numbering_mode: "continuous" }, { ref, resource_index: 0, numbering_mode: "continuous" }
  ]);
  const restored = f.document.getElementById("library-resource-numbering-0"); restored.value = "local"; restored.emit("change"); await flush();
  assert.equal(f.findButton("应用"), undefined);
});

test("unselected resource uses its own draft numbering and ordinary works offer no numbering selector", async () => {
  const f = domFixture(route => route === "detail" ? continuousPlaybackDetail() : route === "playback/select" ? { ok: false, error: "测试拒绝写入" } : undefined);
  await openResourcePanel(f);
  const select = f.document.getElementById("library-resource-numbering-1"); select.value = "continuous"; select.emit("change"); await flush();
  assert.equal(f.document.getElementById("library-resource-numbering-0").value, "local");
  assert.equal(f.calls.filter(call => call.route === "playback/select").length, 0);
  f.findButton("设为播放").emit("click"); await flush();
  assert.deepEqual(f.calls.find(call => call.route === "playback/select").body, { ref, resource_index: 1, numbering_mode: "continuous" });
  const ordinary = domFixture(route => route === "detail" ? playbackDetail() : undefined); await openResourcePanel(ordinary);
  assert.equal(ordinary.nodes().some(node => node.className === "library-resource-numbering"), false);
});

test("mode selection rejects invalid values and identical current choices without writes", async () => {
  const f = fixture(route => route === "detail" ? continuousPlaybackDetail() : undefined);
  await f.controller.activate(); await f.controller.openDetail(item);
  for (const mode of [undefined, "local", "bad", null]) await f.controller.selectPlaybackResource(0, mode);
  assert.equal(f.calls.some(call => call.route === "playback/select"), false);
  f.controller.setPlaybackNumbering(0, "bad"); f.controller.setPlaybackNumbering(99, "continuous");
  assert.deepEqual(plain(f.controller.state.playbackNumberingDrafts), {});
  f.controller.setPlaybackNumbering(0, "continuous"); assert.equal(f.controller.state.playbackNumberingDrafts[0], "continuous");
  f.controller.closeDetail(); assert.deepEqual(plain(f.controller.state.playbackNumberingDrafts), {});
});

test("persisted continuous numbering can be switched back to local after source continuation numbers disappear", async () => {
  const local = continuousPlaybackDetail(0, "continuous"); local.chapter_numbering = { has_continuous: false, local_start: "", continuous_start: "" };
  const f = domFixture(route => route === "detail" ? local : route === "playback/select" ? { ok: false, error: "只验证请求" } : undefined);
  await openResourcePanel(f);
  const select = f.document.getElementById("library-resource-numbering-0");
  assert.equal(select.value, "continuous"); assert.equal(select.children[1].disabled, true); assert.equal(select.children[1].textContent, "续接(不可用)");
  assert.equal(select.children[0].textContent, "本作");
  select.value = "local"; select.emit("change"); await flush();
  assert.ok(f.nodes().some(node => node.className.includes("library-playback-current") && node.title.includes("已保存：续接集数")));
  f.findButton("应用").emit("click"); await flush();
  assert.deepEqual(f.calls.find(call => call.route === "playback/select").body, { ref, resource_index: 0, numbering_mode: "local" });
});

test("read-only chapter fallback uses the shared compact local and continuation label", async () => {
  const data = continuousPlaybackDetail(); data.chapters = [{ ep: 1, sort: 14, local_number: "1", continuous_number: "14", has_continuous_number: true, name: "第一集" }];
  const f = domFixture(route => route === "detail" ? data : undefined); await openResourcePanel(f);
  f.findButton("章节").emit("click"); await flush();
  const row = f.nodes().find(node => node.tagName === "li" && node.className === "library-chapter");
  assert.deepEqual(row.children.map(node => node.textContent), ["01(#14)", "第一集"]);
  assert.match(row.children[0].title, /1.*14/);
  assert.equal(f.nodes().some(node => node.textContent === "续接集数"), false);
  assert.equal(f.calls.some(call => call.route.startsWith("playback/")), false);
});

test("numbering selector current marker and apply action stay in one compact resource row", async () => {
  const f = domFixture(route => route === "detail" ? continuousPlaybackDetail() : undefined); await openResourcePanel(f);
  let cells = f.nodes().filter(node => node.className === "library-resource-playback");
  assert.deepEqual(cells[0].children.map(node => node.tagName), ["select", "button"]);
  assert.equal(cells[0].children[1].textContent, "当前播放");
  assert.equal(cells[0].children[1].disabled, true);
  assert.deepEqual(cells[1].children.map(node => node.tagName), ["select", "button"]);
  assert.equal(cells[1].children[1].textContent, "设为播放");
  assert.equal(cells[1].children[1].attributes["aria-label"], "设为播放资源并识别文件");
  const select = f.document.getElementById("library-resource-numbering-0"); select.value = "continuous"; select.emit("change"); await flush();
  cells = f.nodes().filter(node => node.className === "library-resource-playback");
  assert.deepEqual(cells[0].children.map(node => node.tagName), ["select", "button"]);
  assert.equal(cells[0].children[1].textContent, "应用");
  assert.equal(cells[0].children[1].disabled, false);
  assert.match(cells[0].children[1].title, /已保存：本作集数.*应用编号并重新识别文件/);
  assert.match(CSS, /has-numbering \.library-resource-playback\s*\{[^}]*flex-direction: row;[^}]*flex-wrap: nowrap;[^}]*align-items: center;/);
  assert.match(CSS, /\.library-resource-numbering\s*\{[^}]*height: 28px;/);
  assert.doesNotMatch(CSS, /has-numbering \.library-resource-playback\s*\{[^}]*flex-direction: column/);
  assert.equal(f.calls.some(call => call.route.startsWith("playback/")), false);
});

test("retired mode-change controls and pending commits cannot overwrite another work", async () => {
  const pending = deferred(), f = fixture(route => route === "detail" ? continuousPlaybackDetail() : route === "playback/select" ? pending.promise : undefined);
  await f.controller.activate(); await f.controller.openDetail(item);
  f.controller.setPlaybackNumbering(0, "continuous"); const selecting = f.controller.selectPlaybackResource(0, "continuous");
  f.controller.deactivate();
  pending.resolve({ ok: true, db_committed: true, detail: continuousPlaybackDetail(0, "continuous"), ...continuousPlaybackDetail(0, "continuous").playback }); await selecting;
  assert.equal(f.controller.state.detail, null); assert.equal(f.notices.length, 0);
  const ui = domFixture(route => route === "detail" ? continuousPlaybackDetail() : undefined); await openResourcePanel(ui);
  const oldSelect = ui.document.getElementById("library-resource-numbering-0");
  ui.findButton("← 返回作品库").emit("click"); await flush(); oldSelect.value = "continuous"; oldSelect.emit("change"); await flush();
  assert.equal(ui.calls.some(call => call.route === "playback/select"), false);
});

test("resource selection changes only after successful persistence and null duration remains a success", async () => {
  const pending = deferred(), freshRef = { ...ref, record_sha256: "e".repeat(64) }, freshDetail = playbackDetail(1, freshRef);
  const f = domFixture(route => route === "detail" ? playbackDetail() : route === "playback/select" ? pending.promise : undefined), mounted = installChapterPlayer(f); await openResourcePanel(f);
  let rows = f.nodes().filter(node => node.className === "library-resource-row");
  assert.equal(rows[0].children[4].children[0].textContent, "当前播放资源"); assert.equal(rows[1].children[4].children[0].textContent, "设为播放资源");
  assert.equal(rows[0].children[4].children[0].tagName, "button");
  assert.equal(rows[0].children[4].children[0].disabled, true);
  assert.ok(rows[0].children[4].children[0].className.includes("library-select-playback"));
  rows[0].children[4].children[0].emit("click"); await flush();
  assert.equal(f.calls.filter(call => call.route === "playback/select").length, 0);
  f.findButton("设为播放资源").emit("click"); f.findButton("设为播放资源").emit("click"); await flush();
  assert.equal(f.calls.filter(call => call.route === "playback/select").length, 1); assert.deepEqual(f.calls.find(call => call.route === "playback/select").body, { ref, resource_index: 1 });
  rows = f.nodes().filter(node => node.className === "library-resource-row"); assert.equal(rows[0].children[4].children[0].textContent, "当前播放资源"); assert.equal(f.findButton("设为播放资源").disabled, true); assert.equal(f.findButton("章节").disabled, true);
  pending.resolve({ ok: true, db_committed: true, ...freshDetail.playback, detail: freshDetail }); await flush();
  rows = f.nodes().filter(node => node.className === "library-resource-row"); assert.equal(rows[1].children[4].children[0].textContent, "当前播放资源"); assert.equal(rows[0].children[4].children[0].textContent, "设为播放资源"); assert.equal(f.notices.at(-1).error, false);
  f.findButton("章节").emit("click"); await flush(); assert.equal(mounted.length, 1); assert.equal(mounted[0].options.detail.playback.resource_index, 1); assert.equal(mounted[0].options.detail.playback.chapters[0].duration_seconds, null);
  assert.deepEqual(plain(mounted[0].options.detail.ref), freshRef); assert.equal(f.calls.some(call => /playback\/(?:scan|durations)/.test(call.route)), false);
  f.findButton("← 返回作品库").emit("click"); await flush(); f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush(); assert.deepEqual(f.calls.filter(call => call.route === "detail").at(-1).body.ref, freshRef);
});

test("failed resource selection retains prior current marker and allows a deliberate retry", async () => {
  const f = domFixture(route => route === "detail" ? playbackDetail() : route === "playback/select" ? { ok: false, error: "资源不可访问" } : undefined); await openResourcePanel(f);
  f.findButton("设为播放资源").emit("click"); await flush(); const rows = f.nodes().filter(node => node.className === "library-resource-row");
  assert.equal(rows[0].children[4].children[0].textContent, "当前播放资源"); assert.equal(rows[1].children[4].children[0].textContent, "设为播放资源"); assert.equal(f.findButton("设为播放资源").disabled, false);
  assert.deepEqual(f.notices.at(-1), { value: "资源不可访问", error: true }); assert.equal(f.nodes().some(node => node.textContent === "资源不可访问"), false);
  f.findButton("← 返回作品库").emit("click"); await flush(); f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush(); assert.deepEqual(f.calls.filter(call => call.route === "detail").at(-1).body.ref, ref);
});

test("committed selection lacking a fresh ref forcibly retires detail despite the mutation busy guard", async () => {
  const pending = deferred(); let browses = 0;
  const f = fixture(route => route === "browse" ? ++browses === 1 ? plain(browse()) : pending.promise : route === "detail" ? playbackDetail() : route === "playback/select" ? { ok: true, db_committed: true, requires_rescan: true, message: "已保存，请重新加载" } : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); const selecting = f.controller.selectPlaybackResource(1); await flush();
  assert.equal(f.controller.state.busy, true); assert.equal(f.controller.state.detail, null); assert.equal(f.controller.state.detailSelector, null); assert.deepEqual(plain(f.controller.state.items), []);
  pending.resolve(plain(browse())); await selecting; assert.equal(f.controller.state.busy, false); assert.equal(f.controller.state.playbackSelecting, false); assert.equal(f.notices.at(-1).error, false);
});

test("chapter chooser switches to resources and disposed callbacks cannot navigate afterward", async () => {
  const f = domFixture(), mounted = installChapterPlayer(f); await openChapterPanel(f); mounted[0].options.onChooseResource(); await flush();
  assert.equal(mounted[0].disposed, 1); assert.equal(f.document.getElementById("library-detail-tab-resources").attributes["aria-selected"], "true");
  f.findButton("概览").emit("click"); await flush(); mounted[0].options.onChooseResource(); assert.equal(f.document.getElementById("library-detail-tab-overview").attributes["aria-selected"], "true");
});

test("retired resource selection result cannot replace details or report stale success", async () => {
  const pending = deferred(), f = fixture(route => route === "detail" ? playbackDetail() : route === "playback/select" ? pending.promise : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); const selected = f.controller.selectPlaybackResource(1); f.controller.deactivate();
  pending.resolve({ ok: true, db_committed: true, detail: playbackDetail(1), ...playbackDetail(1).playback }); await selected;
  assert.equal(f.controller.state.detail, null); assert.equal(f.controller.state.detailSelector, null); assert.deepEqual(plain(f.controller.state.items), []); assert.equal(f.notices.length, 0); assert.equal(f.controller.state.busy, false);
});

test("returning during pending resource selection waits for commit before browsing fresh refs", async () => {
  const pending = deferred(), freshRef = { ...ref, record_sha256: "f".repeat(64) }; let committed = false;
  const f = fixture(route => route === "browse" ? { ...browse(), items: [{ ...item, ref: committed ? freshRef : ref }] } : route === "detail" ? playbackDetail() : route === "playback/select" ? pending.promise : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); const selecting = f.controller.selectPlaybackResource(1); f.controller.deactivate(); await f.controller.activate();
  assert.equal(f.controller.state.detail, null); assert.equal(f.controller.state.detailSelector, null); assert.equal(f.controller.state.loading, true); assert.deepEqual(plain(f.controller.state.items), []);
  assert.equal(f.calls.filter(call => call.route === "browse").length, 1, "do not fetch a pre-commit list on early reactivation");
  committed = true; pending.resolve({ ok: true, db_committed: true, detail: playbackDetail(1, freshRef), ...playbackDetail(1, freshRef).playback }); await selecting;
  assert.equal(f.calls.filter(call => call.route === "browse").length, 2); assert.deepEqual(plain(f.controller.state.items[0].ref), freshRef);
  assert.equal(f.controller.state.busy, false); assert.equal(f.controller.state.loading, false); assert.equal(f.controller.state.playbackSelecting, false); assert.equal(f.notices.length, 0);
});

test("cover-only preview is explicit, single-flight and applies the signed snapshot only after selection", async () => {
  const pending = deferred(), f = fixture(route => route === "provider/cover-preview" ? pending.promise : route === "provider/apply" ? { records: [{ ref, record }] } : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); f.controller.setEpisodeRange("1", "12");
  const previewing = f.controller.previewCover("123"); await f.controller.previewCover("123");
  assert.deepEqual(f.calls.filter(call => call.route === "provider/cover-preview"), [{ route: "provider/cover-preview", body: { ref, subject_id: "123" } }]);
  pending.resolve({ ref, snapshot_id: "cover-snapshot", preview_token: "cover-token", cover_only: true, diff: [{ field: "metadata.cover", changed: true, selectable: true }] }); await previewing;
  assert.equal(f.controller.state.detailTab, "sources"); assert.deepEqual(plain(f.controller.state.provider.selected), []);
  await assert.rejects(f.controller.applyProvider(), /勾选封面/); assert.equal(f.calls.some(call => call.route === "provider/apply"), false);
  f.controller.selectProviderField("metadata.cover", true); await f.controller.applyProvider();
  assert.deepEqual(f.calls.find(call => call.route === "provider/apply").body, { ref, snapshot_id: "cover-snapshot", preview_token: "cover-token", selected_fields: ["metadata.cover"] });
});

test("closing a work discards late cover preview and failure never replaces current cover", async () => {
  const pending = deferred(), coverUrl = "/api/catalog-library/assets/" + "a".repeat(64) + ".jpg";
  const f = fixture(route => route === "provider/cover-preview" ? pending.promise : route === "detail" ? { ...detail(), item: { ...item, cover_url: coverUrl } } : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); const previewing = f.controller.previewCover();
  assert.equal(f.controller.state.detail.item.cover_url, coverUrl); f.controller.closeDetail();
  pending.resolve({ ref, snapshot_id: "late", preview_token: "late", diff: [] }); await previewing;
  assert.equal(f.controller.state.provider.preview, null); assert.equal(f.controller.state.detail, null);
  const failed = fixture(route => route === "provider/cover-preview" ? { ok: false, error: "封面下载失败：TLS 连接被中断" } : route === "detail" ? { ...detail(), item: { ...item, cover_url: coverUrl } } : undefined);
  await failed.controller.activate(); await failed.controller.openDetail(item); await failed.controller.previewCover();
  assert.match(failed.controller.state.provider.error, /TLS/); assert.equal(failed.controller.state.detail.item.cover_url, coverUrl);
});

test("bound work has explicit cover update; preview renders local images with fallback and no automatic apply", async () => {
  const before = "/api/catalog-library/assets/" + "a".repeat(64) + ".jpg", after = "/api/catalog-library/assets/" + "b".repeat(64) + ".png";
  const f = domFixture(route => route === "detail" ? { ...detail(), item: { ...item, cover_url: before }, sources: [{ provider: "bangumi", external_id: "123" }] } : route === "provider/cover-preview" ? {
    ref, snapshot_id: "cover", preview_token: "token", cover_only: true, diff: [{ field: "metadata.cover", label: "封面", before_url: before, after_url: after, selectable: true, changed: true }] } : undefined);
  f.feature.init(f.context); f.feature.activate(f.context); await flush(); f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
  f.findButton("更新封面").emit("click"); await flush();
  assert.equal(f.findButton("确认更新封面").disabled, true); assert.equal(f.calls.some(call => call.route === "provider/apply"), false);
  const images = f.nodes().filter(node => node.tagName === "img"); assert.ok(images.some(image => image.src === after));
  assert.ok(images.every(image => image.loading === "lazy" && image.width > 0 && image.height > 0 && image.alt));
  const candidate = images.find(image => image.src === after), container = candidate.parentNode; candidate.emit("error");
  assert.equal(container.children.some(node => node.tagName === "img"), false); assert.ok(container.children.some(node => node.className === "library-cover-placeholder"));
  const checkbox = f.nodes().find(node => node.type === "checkbox"); checkbox.checked = true; checkbox.emit("change"); assert.equal(f.findButton("确认更新封面").disabled, false);
});

test("malicious remote preview image URLs remain text-only placeholders", async () => {
  const f = domFixture(route => route === "detail" ? { ...detail(), sources: [{ provider: "bangumi", external_id: "123" }] } : route === "provider/cover-preview" ? {
    ref, snapshot_id: "cover", preview_token: "token", diff: [{ field: "metadata.cover", before_url: "https://example.com/old.jpg", after_url: "//example.com/new.jpg", selectable: false, changed: false }] } : undefined);
  f.feature.init(f.context); f.feature.activate(f.context); await flush(); f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
  f.findButton("更新封面").emit("click"); await flush(); assert.equal(f.nodes().some(node => node.tagName === "img" && node.src !== "/features/catalog-library/tmdb-logo.svg"), false); assert.equal(f.findButton("确认更新封面").disabled, true);
});

test("browse keeps cards during pending refresh and renders bounded pagination in a sidebar layout", async () => {
  const pending = deferred(); let requests = 0;
  const f = domFixture(route => route === "browse" ? (++requests === 1 ? { ...browse(), total: 1520 } : pending.promise) : undefined);
  f.feature.init(f.context); f.feature.activate(f.context); await flush();
  const card = f.nodes().find(node => node.className.includes("library-card-link"));
  const sidebar = f.nodes().find(node => node.className === "library-browse-sidebar"), layout = f.nodes().find(node => node.className === "library-browse-layout");
  assert.equal(sidebar.parentNode, layout); assert.ok(sidebar.children.some(node => node.className === "library-sidebar-content" && node.children.some(child => child.className === "library-sidebar-section")));
  const pagination = f.nodes().find(node => node.className === "library-pagination"); assert.ok(pagination.children.length <= 10); assert.ok(pagination.children.some(node => node.attributes["aria-current"] === "page"));
  f.findButton("刷新本地列表").emit("click"); await flush(); assert.ok(f.nodes().includes(card)); assert.equal(f.findButton("刷新本地列表").disabled, true);
  pending.resolve({ ...browse(), total: 1520 }); await flush(); assert.equal(f.findButton("刷新本地列表").disabled, false);
});

test("appearance can recolor the library without theme-specific geometry or remote assets", () => {
  assert.match(CSS, /max-width:\s*1100px/); assert.match(CSS, /grid-template-columns:\s*minmax\(0, 1fr\) 240px/);
  assert.match(CSS, /data-sidebar-collapsed="true"[^}]*grid-template-columns:\s*minmax\(0, 1fr\) var\(--library-edge-rail-width\)/);
  assert.doesNotMatch(CSS, /data-theme|data-appearance|theme-|--selected-bg|--panel-bg|https?:|url\(/);
  for (const variable of ["--text", "--muted", "--accent", "--panel", "--border", "--code-bg"]) assert.ok(CSS.includes("var(" + variable));
  for (const variable of ["--ui-control-height", "--ui-control-padding", "--ui-control-font-size"]) assert.ok(CSS.includes(variable));
  assert.doesNotMatch(CSS, /\.library-button\.is-primary\s*\{/);
  assert.doesNotMatch(SOURCE, /fetch\([^)]*(?:bangumi|lain\.bgm)/);
});

test("initial browse completion preserves an unsubmitted search draft and submits that exact text", async () => {
  const initial = deferred(); let requests = 0;
  const f = domFixture(route => route === "browse" && ++requests === 1 ? initial.promise : undefined);
  f.feature.init(f.context); f.feature.activate(f.context); await flush();
  const search = f.nodes().find(node => node.tagName === "input" && node.type === "search"), form = f.nodes().find(node => node.className === "library-filters");
  search.value = "Little Busters"; search.emit("input"); search.focus();
  initial.resolve({ ...browse(), total: 1520 }); await flush();
  assert.equal(search.value, "Little Busters"); assert.equal(f.document.activeElement, search);
  form.emit("submit"); await flush();
  assert.equal(f.calls.filter(call => call.route === "browse").at(-1).body.query, "Little Busters"); assert.equal(search.value, "Little Busters");
});

test("search submitted during initial loading wins over the late unfiltered page", async () => {
  const initial = deferred(), searched = deferred();
  const f = domFixture((route, body) => route === "browse" ? body.query ? searched.promise : initial.promise : undefined);
  f.feature.init(f.context); f.feature.activate(f.context); await flush();
  const search = f.nodes().find(node => node.tagName === "input" && node.type === "search"); search.value = "Little Busters"; search.emit("input");
  f.nodes().find(node => node.className === "library-filters").emit("submit"); await flush();
  searched.resolve({ ...browse(), items: [{ ...item, name: "Little Busters" }], total: 3 }); await flush();
  initial.resolve({ ...browse(), total: 1520 }); await flush();
  assert.equal(search.value, "Little Busters"); assert.equal(f.nodes().find(node => node.className === "library-total").textContent, "3 部作品");
});

test("refresh, detail navigation and reactivation preserve a newer unsubmitted search draft", async () => {
  const refreshing = deferred(), detailPending = deferred(); let requests = 0;
  const f = domFixture(route => route === "browse" && ++requests === 2 ? refreshing.promise : route === "detail" ? detailPending.promise : undefined);
  f.feature.init(f.context); f.feature.activate(f.context); await flush();
  const search = f.nodes().find(node => node.tagName === "input" && node.type === "search");
  f.findButton("刷新本地列表").emit("click"); await flush(); search.value = "Draft while refreshing"; search.emit("input");
  refreshing.resolve(browse()); await flush(); assert.equal(search.value, "Draft while refreshing");
  f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
  detailPending.resolve(detail()); await flush(); f.findButton("← 返回作品库").emit("click"); await flush();
  assert.equal(search.value, "Draft while refreshing");
  f.feature.deactivate(); f.feature.activate(f.context); await flush(); assert.equal(search.value, "Draft while refreshing");
  assert.ok(f.calls.filter(call => call.route === "browse").every(call => call.body.query === ""));
  f.nodes().find(node => node.className === "library-filters").emit("submit"); await flush();
  assert.equal(f.calls.filter(call => call.route === "browse").at(-1).body.query, "Draft while refreshing");
  f.findButton("清除筛选").emit("click"); await flush(); assert.equal(search.value, "");
});

test("refresh without a Bangumi binding prepares named search without network or a stale preview", async () => {
  const f = fixture(route => route === "provider/preview" ? { ref, snapshot_id: "old", preview_token: "old", diff: [{ field: "name", selectable: true, changed: true }] } : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); await f.controller.previewProvider(123); f.controller.selectProviderField("name", true);
  const before = f.calls.length; await f.controller.refreshProvider();
  assert.equal(f.controller.state.detailTab, "sources"); assert.equal(f.controller.state.provider.query, "Test");
  assert.match(f.controller.state.provider.guidance, /尚未关联/); assert.equal(f.calls.length, before);
  assert.equal(f.controller.state.provider.preview, null); assert.deepEqual(plain(f.controller.state.provider.selected), []);
  await assert.rejects(f.controller.applyProvider(), /预览差异/);
});

test("single binding refresh uses shared preview and its exact range, without any selected fields or writes", async () => {
  const binding = { provider: "bangumi", external_id: "123", scope: "episodes:13-24", episode_range: { start: 13, end: 24 } };
  const f = fixture((route, body) => route === "detail" ? { ...detail(), sources: [binding] } : route === "provider/preview" ? {
    ref, snapshot_id: "new", preview_token: "new", episode_range: body.episode_range, diff: [{ field: "name", changed: true, selectable: true }] } : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); f.controller.setEpisodeRange("1", "12"); await f.controller.refreshProvider();
  assert.deepEqual(f.calls.find(call => call.route === "provider/preview").body, { ref, subject_id: "123", episode_range: { start: 13, end: 24 } });
  assert.equal(f.controller.state.provider.rangeStart, "13"); assert.equal(f.controller.state.provider.rangeEnd, "24");
  assert.deepEqual(plain(f.controller.state.provider.selected), []); assert.equal(f.calls.some(call => /apply$/.test(call.route)), false);
});

test("multiple bindings including the same subject with different scopes always require explicit selection", async () => {
  for (const secondId of ["123", "456"]) {
    const sources = [{ provider: "bangumi", external_id: "123", scope: "episodes:1-12", episode_range: { start: 1, end: 12 } },
      { provider: "bangumi", external_id: secondId, scope: "episodes:13-24", episode_range: { start: 13, end: 24 } }];
    const f = fixture(route => route === "detail" ? { ...detail(), sources } : route === "provider/preview" ? { ref, snapshot_id: "new", preview_token: "new", diff: [] } : undefined);
    await f.controller.activate(); await f.controller.openDetail(item); await f.controller.refreshProvider();
    assert.match(f.controller.state.provider.guidance, /多个来源或章节范围/); assert.equal(f.calls.some(call => call.route.startsWith("provider/")), false);
    await f.controller.refreshProvider(f.controller.state.detail.sources[1]);
    assert.deepEqual(f.calls.find(call => call.route === "provider/preview").body, { ref, subject_id: secondId, episode_range: { start: 13, end: 24 } });
  }
});

test("unscoped refresh clears old range and invalid scoped associations never broaden to all chapters", async () => {
  const unscoped = { provider: "bangumi", external_id: "123", scope: "", episode_range: null };
  const f = fixture(route => route === "detail" ? { ...detail(), sources: [unscoped] } : route === "provider/preview" ? { ref, snapshot_id: "new", preview_token: "new", diff: [] } : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); f.controller.setEpisodeRange("13", "24"); await f.controller.refreshProvider();
  assert.equal(f.controller.state.provider.rangeStart, ""); assert.equal(f.controller.state.provider.rangeEnd, "");
  assert.deepEqual(f.calls.find(call => call.route === "provider/preview").body, { ref, subject_id: "123" });
  for (const association of [
    { scope: "episodes:1-12" }, { scope: "episodes:1-12", episode_range: {} },
    { scope: "episodes:1-12", episode_range: { start: 12, end: 1 } },
    { scope: "episodes:1-12", episode_range: { start: 13, end: 24 } },
    { scope: "custom", episode_range: { start: 1, end: 12 } }
  ]) {
    const invalid = fixture(route => route === "detail" ? { ...detail(), sources: [{ provider: "bangumi", external_id: "123", ...association }] } : undefined);
    await invalid.controller.activate(); await invalid.controller.openDetail(item); await invalid.controller.refreshProvider();
    assert.ok(invalid.controller.state.provider.error); assert.equal(invalid.calls.some(call => call.route.startsWith("provider/")), false);
  }
});

test("refresh is single-flight and a failed refresh cannot apply the older signed preview", async () => {
  const pending = deferred(); let previewCount = 0;
  const f = fixture(route => route === "detail" ? { ...detail(), sources: [{ provider: "bangumi", external_id: "123" }] } : route === "provider/preview" ? ++previewCount === 1 ? {
    ref, snapshot_id: "old", preview_token: "old", diff: [{ field: "name", changed: true, selectable: true }] } : pending.promise : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); await f.controller.refreshProvider();
  const old = f.controller.state.provider.preview; f.controller.selectProviderField("name", true);
  const refreshing = f.controller.refreshProvider(); await f.controller.refreshProvider(); await f.controller.previewProvider(456);
  assert.equal(previewCount, 2); assert.equal(f.controller.state.provider.preview, null);
  await assert.rejects(f.controller.applyProvider(old), /预览已变化/);
  pending.reject(Error("Bangumi 请求失败：连接超时")); await refreshing;
  assert.match(f.controller.state.provider.error, /连接超时/); await assert.rejects(f.controller.applyProvider(old), /预览差异/);
  assert.equal(f.calls.some(call => /apply$/.test(call.route)), false);
});

test("closed, inactive and disposed detail ignore late refresh and cannot apply or launch more provider work", async () => {
  for (const action of ["closeDetail", "deactivate", "dispose"]) {
    const pending = deferred();
    const f = fixture(route => route === "detail" ? { ...detail(), sources: [{ provider: "bangumi", external_id: "123" }] } : route === "provider/preview" ? pending.promise : undefined);
    await f.controller.activate(); await f.controller.openDetail(item); const refreshing = f.controller.refreshProvider(); f.controller[action]();
    pending.resolve({ ref, snapshot_id: "late", preview_token: "late", diff: [] }); await refreshing;
    assert.equal(f.controller.state.provider.preview, null); await assert.rejects(f.controller.applyProvider(), /页面或预览已变化/);
    const calls = f.calls.length; await f.controller.refreshProvider(); await f.controller.previewProvider(123); await f.controller.previewCover(123); await f.controller.searchProvider("Test"); assert.equal(f.calls.length, calls);
  }
});

test("busy saves suppress refresh and stale source selections or old preview controls cannot apply", async () => {
  const pending = deferred(); let version = 0;
  const f = fixture(route => route === "detail" ? { ...detail(), sources: [{ provider: "bangumi", external_id: "123" }] } : route === "edits/preview" ? pending.promise : route === "provider/preview" ? {
    ref, snapshot_id: "snapshot" + ++version, preview_token: "token" + version, diff: [] } : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); await f.controller.refreshProvider(); const old = f.controller.state.provider.preview;
  await f.controller.refreshProvider(); await assert.rejects(f.controller.applyProvider(old), /旧的预览/);
  await f.controller.refreshProvider({ provider: "bangumi", external_id: "999" }); assert.match(f.controller.state.provider.error, /来源关联已变化/);
  const saving = f.controller.saveRecord(record, ref), calls = f.calls.length; await f.controller.refreshProvider(); assert.equal(f.calls.length, calls);
  pending.resolve({ issues: [{ message: "blocked", blocking: true }] }); await assert.rejects(saving, /blocked/);
  assert.equal(f.calls.some(call => call.route === "provider/apply"), false);
});

test("detail refresh button opens and focuses a prefilled search for an unbound work", async () => {
  const f = domFixture(); f.feature.init(f.context); f.feature.activate(f.context); await flush();
  assert.ok(f.findButton("刷新本地列表")); f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
  f.findButton("刷新作品数据").emit("click"); await flush();
  const search = f.document.getElementById("library-provider-query"); assert.equal(search.value, "Test"); assert.equal(f.document.activeElement, search);
  assert.equal(f.calls.some(call => call.route.startsWith("provider/")), false);
});

test("detail refresh shows each same-subject binding and refreshes only the chosen episode scope", async () => {
  const sources = [{ provider: "bangumi", external_id: "123", scope: "episodes:1-12", episode_range: { start: 1, end: 12 } },
    { provider: "bangumi", external_id: "123", scope: "episodes:13-24", episode_range: { start: 13, end: 24 } }];
  const pending = deferred(), f = domFixture(route => route === "detail" ? { ...detail(), sources } : route === "provider/preview" ? pending.promise : undefined);
  f.feature.init(f.context); f.feature.activate(f.context); await flush(); f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
  f.findButton("刷新作品数据").emit("click"); await flush();
  assert.equal(f.document.activeElement.id, "library-source-choices"); assert.equal(f.calls.some(call => call.route.startsWith("provider/")), false);
  const buttons = f.nodes().filter(node => node.tagName === "button" && node.textContent === "刷新此来源数据"); assert.equal(buttons.length, 2);
  assert.equal(f.nodes().filter(node => node.tagName === "button" && node.textContent === "更新此来源封面").length, 2);
  buttons[1].emit("click"); await flush(); assert.equal(f.findButton("刷新作品数据").disabled, true);
  assert.deepEqual(f.calls.find(call => call.route === "provider/preview").body.episode_range, { start: 13, end: 24 });
  pending.resolve({ ref, snapshot_id: "chosen", preview_token: "chosen", diff: [{ field: "name", changed: true, selectable: true }], episode_range: { start: 13, end: 24 } }); await flush();
  assert.equal(f.nodes().find(node => node.type === "checkbox").checked, false); assert.equal(f.findButton("刷新作品数据").disabled, false);
  assert.equal(f.calls.some(call => call.route === "provider/apply"), false);
});

const aliasPreview = (options = {}) => ({ ref, snapshot_id: "alias-snapshot", preview_token: "alias-token", automatic_aliases: ["Original title"], diff: [
  { field: "name", label: "作品名", before: "Test", after: "Original title", changed: true, selectable: true },
  { field: "metadata.aliases", label: "别名", before: ["Existing"], after: ["Existing", "Original title", "中文名"], after_when_name_selected: ["Existing", "中文名"], changed: true, selectable: true }
], ...options });

test("source-only confirmation reports automatic aliases actually added by the server", async () => {
  for (const added of [["Original title"], []]) {
    const f = fixture(route => route === "provider/preview" ? aliasPreview() : route === "provider/apply" ? { records: [{ ref, record }], automatic_aliases_added: added } : undefined);
    await f.controller.activate(); await f.controller.openDetail(item); await f.controller.previewProvider(123);
    assert.deepEqual(plain(f.controller.state.provider.selected), []); assert.equal(f.calls.some(call => call.route === "provider/apply"), false);
    await f.controller.applyProvider();
    assert.deepEqual(f.calls.find(call => call.route === "provider/apply").body.selected_fields, []);
    if (added.length) { assert.match(f.notices.at(-1).value, /自动追加别名：Original title/); assert.doesNotMatch(f.notices.at(-1).value, /未修改|未覆盖/); }
    else { assert.match(f.notices.at(-1).value, /未修改作品字段/); assert.doesNotMatch(f.notices.at(-1).value, /追加别名/); }
  }
});

test("name selection prunes an alias selection whose merged variant is empty or unchanged", async () => {
  for (const variant of [[], ["Existing"]]) {
    const preview = aliasPreview(); preview.diff[1].after_when_name_selected = variant;
    const f = fixture(route => route === "provider/preview" ? preview : route === "provider/apply" ? { records: [{ ref, record }], automatic_aliases_added: [] } : undefined);
    await f.controller.activate(); await f.controller.openDetail(item); await f.controller.previewProvider(123);
    f.controller.selectProviderField("metadata.aliases", true); assert.deepEqual(plain(f.controller.state.provider.selected), ["metadata.aliases"]);
    f.controller.selectProviderField("name", true); assert.deepEqual(plain(f.controller.state.provider.selected), ["name"]);
    f.controller.selectProviderField("metadata.aliases", true); assert.deepEqual(plain(f.controller.state.provider.selected), ["name"]);
    await f.controller.applyProvider(); assert.deepEqual(f.calls.find(call => call.route === "provider/apply").body.selected_fields, ["name"]);
  }
});

test("name variant updates alias eligibility and deselecting name enables the merged full list again", async () => {
  const preview = aliasPreview(); preview.diff[1].after_when_name_selected = ["Existing"];
  const f = fixture(route => route === "provider/preview" ? preview : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); await f.controller.previewProvider(123);
  f.controller.selectProviderField("name", true); f.controller.selectProviderField("metadata.aliases", true); assert.deepEqual(plain(f.controller.state.provider.selected), ["name"]);
  f.controller.selectProviderField("name", false); f.controller.selectProviderField("metadata.aliases", true); assert.deepEqual(plain(f.controller.state.provider.selected), ["metadata.aliases"]);
});

async function openAliasPreview(preview, applyResult) {
  const f = domFixture(route => route === "detail" ? { ...detail(), sources: [{ provider: "bangumi", external_id: "123" }] } :
    route === "provider/preview" ? preview : route === "provider/apply" ? applyResult || { records: [{ ref, record }], automatic_aliases_added: [] } : undefined);
  f.feature.init(f.context); f.feature.activate(f.context); await flush(); f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
  f.findButton("刷新作品数据").emit("click"); await flush(); return f;
}

test("automatic alias preview is explicit without selecting fields and uses the correct confirmation label", async () => {
  const f = await openAliasPreview(aliasPreview(), { records: [{ ref, record }], automatic_aliases_added: ["Original title"] });
  const notice = f.nodes().find(node => node.className.includes("library-automatic-aliases"));
  assert.equal(notice.hidden, false); assert.match(notice.textContent, /Original title/); assert.match(notice.textContent, /无需勾选.*已有别名会保留并去重/);
  assert.ok(f.nodes().filter(node => node.type === "checkbox").every(node => node.checked === false));
  assert.ok(f.findButton("确认来源并追加别名")); assert.equal(f.findButton("仅确认来源关联"), undefined);
  assert.equal(f.calls.some(call => call.route === "provider/apply"), false);
  assert.ok(f.nodes().some(node => node.textContent.includes("别名会与已有内容合并去重")));
  f.findButton("确认来源并追加别名").emit("click"); await flush();
  assert.deepEqual(f.calls.find(call => call.route === "provider/apply").body.selected_fields, []);
  assert.match(f.notices.at(-1).value, /自动追加别名：Original title/);
  assert.equal(f.nodes().some(node => node.className === "library-notice"), false);
});

test("name checkbox switches alias merged preview and hides automatic addition then restores both", async () => {
  const f = await openAliasPreview(aliasPreview()), notice = f.nodes().find(node => node.className.includes("library-automatic-aliases"));
  const row = f.nodes().find(node => node.className === "library-diff" && node.children.some(child => child.textContent === "作品名"));
  const name = row.children.find(node => node.type === "checkbox"), aliasAfter = f.nodes().find(node => node.className.includes("library-aliases-after"));
  assert.deepEqual(JSON.parse(aliasAfter.children[0].textContent), ["Existing", "Original title", "中文名"]);
  name.checked = true; name.emit("change"); assert.equal(notice.hidden, true);
  assert.deepEqual(JSON.parse(aliasAfter.children[0].textContent), ["Existing", "中文名"]); assert.ok(f.findButton("确认写入所选字段"));
  name.checked = false; name.emit("change"); assert.equal(notice.hidden, false);
  assert.deepEqual(JSON.parse(aliasAfter.children[0].textContent), ["Existing", "Original title", "中文名"]); assert.ok(f.findButton("确认来源并追加别名"));
  assert.equal(f.calls.some(call => call.route === "provider/apply"), false);
});

test("selecting aliases keeps automatic addition disclosed while changing the confirmation to selected fields", async () => {
  const f = await openAliasPreview(aliasPreview()), notice = f.nodes().find(node => node.className.includes("library-automatic-aliases"));
  const row = f.nodes().find(node => node.className === "library-diff" && node.children.some(child => child.textContent === "别名"));
  const aliases = row.children.find(node => node.type === "checkbox"); aliases.checked = true; aliases.emit("change");
  assert.equal(notice.hidden, false); assert.match(notice.textContent, /保留并去重/); assert.ok(f.findButton("确认写入所选字段"));
  assert.equal(f.calls.some(call => call.route === "provider/apply"), false);
});

test("alias checkbox is unchecked and disabled when the selected-name variant has no merge changes", async () => {
  const preview = aliasPreview(); preview.diff[1].after_when_name_selected = ["Existing"];
  const f = await openAliasPreview(preview), rows = f.nodes().filter(node => node.className === "library-diff");
  const name = rows[0].children.find(node => node.type === "checkbox"), aliases = rows[1].children.find(node => node.type === "checkbox");
  aliases.checked = true; aliases.emit("change"); assert.equal(aliases.checked, true);
  name.checked = true; name.emit("change"); assert.equal(aliases.checked, false); assert.equal(aliases.disabled, true);
  name.checked = false; name.emit("change"); assert.equal(aliases.checked, false); assert.equal(aliases.disabled, false);
});

test("no automatic alias retains source-only label and cover-only never offers automatic aliases", async () => {
  const noAlias = await openAliasPreview(aliasPreview({ automatic_aliases: [] }));
  assert.ok(noAlias.findButton("仅确认来源关联")); assert.equal(noAlias.nodes().find(node => node.className.includes("library-automatic-aliases")).hidden, true);
  const f = domFixture(route => route === "detail" ? { ...detail(), sources: [{ provider: "bangumi", external_id: "123" }] } : route === "provider/cover-preview" ? {
    ref, snapshot_id: "cover", preview_token: "cover", cover_only: true, automatic_aliases: ["Should not appear"], diff: [{ field: "metadata.cover", changed: true, selectable: true }] } : undefined);
  f.feature.init(f.context); f.feature.activate(f.context); await flush(); f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
  f.findButton("更新封面").emit("click"); await flush();
  assert.equal(f.nodes().find(node => node.className.includes("library-automatic-aliases")).hidden, true);
  assert.equal(f.findButton("确认来源并追加别名"), undefined); assert.ok(f.findButton("确认更新封面"));
});

test("work update shows the shared people preview and confirms work and people in one signed request", async () => {
  const proposal = aliasPreview({ people_preview: { changes: [{ action: "create", name: "角色A", changed_fields: ["name"] }],
    people: { characters: [{ name: "角色A", actors: [{ name: "声优A" }] }], persons: [] } } });
  const f = await openAliasPreview(proposal, { records: [{ ref, record }], automatic_aliases_added: ["Original title"], people_updated: true, warnings: ["生日月份无效"] });
  const embedded = f.nodes().find(node => node.className === "library-source-people-preview");
  assert.ok(embedded); assert.notEqual(embedded.open, true);
  assert.ok(f.nodes().some(node => node.textContent === "人物资料 · 随本次确认保存"));
  assert.ok(f.nodes().some(node => node.tagName === "button" && node.textContent === "声优A"));
  assert.equal(f.findButton("确认保存人物与关联"), undefined);
  assert.equal(f.calls.some(call => call.route.startsWith("people/")), false);
  f.findButton("确认来源与人物资料").emit("click"); await flush(); await flush();
  assert.deepEqual(f.calls.find(call => call.route === "provider/apply").body, { ref, snapshot_id: "alias-snapshot", preview_token: "alias-token", selected_fields: [] });
  assert.match(f.notices.at(-1).value, /人物资料与关联已同步/);
  assert.match(f.notices.at(-1).value, /部分资料未完整获取/);
  assert.equal(f.calls.filter(call => /apply$/.test(call.route)).length, 1);
});

test("partial people warnings do not disable work confirmation or silently claim people were saved", async () => {
  const f = await openAliasPreview(aliasPreview({ warnings: ["人物资料读取失败，已保留本地资料"], people_preview: null }),
    { records: [{ ref, record }], automatic_aliases_added: [], people_updated: false });
  const apply = f.findButton("确认来源并追加别名"); assert.ok(apply); assert.equal(apply.disabled, false);
  apply.emit("click"); await flush(); await flush();
  assert.equal(f.calls.filter(call => call.route === "provider/apply").length, 1);
  assert.doesNotMatch(f.notices.at(-1).value, /人物资料与关联已同步/);
});

function managedSources() {
  return [{ binding_id: "tmdb-1", provider: "tmdb", external_id: "100", name: "韩剧原名", season_number: 1, role: "primary", enabled: true },
    { binding_id: "wiki-2", provider: "wikipedia", external_id: "200", name: "韩剧中文名", role: "supplement", enabled: true }];
}
function combinedPreview() {
  return { ref, preview_token: "combined-signed", selected_fields: ["metadata.summary"],
    sources: [{ binding_id: "tmdb-1", provider: "tmdb", status: "ok" }, { binding_id: "wiki-2", provider: "wikipedia", status: "ok" }],
    diff: [{ field: "name", before: "人工名称", after: "来源名称", changed: true, selectable: true, default_selected: false, binding_id: "tmdb-1", provider: "tmdb" },
      { field: "metadata.summary", before: "", after: "TMDB 简介", changed: true, selectable: true, default_selected: true, binding_id: "tmdb-1", provider: "tmdb",
        choices: [{ binding_id: "tmdb-1", provider: "tmdb", after: "TMDB 简介", changed: true, selectable: true }, { binding_id: "wiki-2", provider: "wikipedia", after: "中文简介", changed: true, selectable: true }] }] };
}

test("managed sources refresh together and apply only the signed aggregate after explicit confirmation", async () => {
  const preview = combinedPreview(), f = fixture(route => route === "detail" ? { ...detail(), sources: managedSources() } :
    route === "provider/sync-preview" ? preview : route === "provider/sync-apply" ? { records: [{ ref, record }] } : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); await f.controller.refreshProvider();
  assert.deepEqual(f.calls.find(call => call.route === "provider/sync-preview").body, { ref });
  assert.deepEqual(plain(f.controller.state.provider.selected), ["metadata.summary"]);
  assert.equal(f.calls.some(call => /apply$/.test(call.route)), false);
  f.controller.chooseProviderField("metadata.summary", "wiki-2"); await f.controller.applyProvider(preview);
  assert.deepEqual(f.calls.find(call => call.route === "provider/sync-apply").body, { ref, preview_token: "combined-signed", selected_fields: ["metadata.summary"], field_choices: { "metadata.summary": "wiki-2" } });
  assert.equal(f.calls.some(call => call.route === "provider/apply" || call.route === "provider/preview"), false);
});

test("individual managed source refresh uses its exact stable binding and disabled sources cannot refresh", async () => {
  const sources = managedSources(), f = fixture(route => route === "detail" ? { ...detail(), sources } : route === "provider/sync-preview" ? combinedPreview() : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); await f.controller.refreshProvider(sources[1]);
  assert.deepEqual(f.calls.find(call => call.route === "provider/sync-preview").body, { ref, binding_ids: ["wiki-2"] });
  sources[1].enabled = false; const before = f.calls.length; await f.controller.refreshProvider(sources[1]);
  assert.equal(f.calls.length, before); assert.match(f.controller.state.provider.guidance, /启用/);
  sources[0].enabled = false; await f.controller.refreshProvider(); assert.equal(f.calls.length, before);
  await f.controller.refreshProvider({ ...sources[0] }); assert.match(f.controller.state.provider.error, /关联已变化/);
});

test("managed refresh is single flight and closing or deactivating discards late aggregate tokens", async () => {
  for (const action of ["closeDetail", "deactivate", "dispose"]) {
    const pending = deferred(), f = fixture(route => route === "detail" ? { ...detail(), sources: managedSources() } : route === "provider/sync-preview" ? pending.promise : undefined);
    await f.controller.activate(); await f.controller.openDetail(item); const task = f.controller.refreshProvider(); await f.controller.refreshProvider();
    assert.equal(f.calls.filter(call => call.route === "provider/sync-preview").length, 1);
    f.controller[action](); pending.resolve(combinedPreview()); await task;
    assert.equal(f.controller.state.provider.preview, null); await assert.rejects(f.controller.applyProvider(), /页面或预览已变化/);
  }
});

test("aggregate failure replaces a previous preview and retry cannot accidentally apply its old token", async () => {
  let attempt = 0; const f = fixture(route => route === "detail" ? { ...detail(), sources: managedSources() } : route === "provider/sync-preview" ?
    ++attempt === 1 ? combinedPreview() : { ok: false, error: "来源暂时不可用" } : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); await f.controller.refreshProvider(); const old = f.controller.state.provider.preview;
  await f.controller.refreshProvider(); assert.equal(f.controller.state.provider.preview, null); assert.equal(typeof f.controller.state.provider.retry, "function");
  assert.match(f.notices.at(-1).value, /来源暂时不可用/); await assert.rejects(f.controller.applyProvider(old), /预览差异/);
});

test("replacement keeps its intended binding across provider changes while adding a source clears it", async () => {
  const sources = managedSources(), f = fixture(route => route === "detail" ? { ...detail(), sources } : route === "provider/preview" ? { ref, snapshot_id: "p", preview_token: "p", diff: [] } : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); f.controller.beginSourceSelection(sources[0]); f.controller.chooseProvider("bangumi");
  await f.controller.previewProvider(321);
  assert.deepEqual(f.calls.find(call => call.route === "provider/preview").body, { ref, subject_id: "321", replace_binding_id: "tmdb-1", binding_role: "primary" });
  assert.equal(f.calls.some(call => /apply$/.test(call.route)), false);
  f.controller.beginSourceSelection(null, "wikipedia", "supplement"); await f.controller.previewProvider(654);
  assert.deepEqual(f.calls.filter(call => call.route === "provider/preview").at(-1).body, { ref, provider: "wikipedia", subject_id: "654", binding_role: "supplement" });
});

test("source policy preselects the platform without a network search or overriding manual sources", async () => {
  const policy = { primary_provider: "tmdb", supplement_providers: ["wikipedia"], custom: false, missing_providers: ["tmdb", "wikipedia"] };
  const f = fixture(route => route === "detail" ? { ...detail(), source_policy: policy } : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); assert.equal(f.controller.state.provider.provider, "tmdb");
  await f.controller.refreshProvider(); assert.equal(f.controller.state.provider.provider, "tmdb"); assert.equal(f.controller.state.provider.query, "Test");
  assert.equal(f.calls.some(call => call.route.startsWith("provider/")), false);
  f.controller.beginSourceSelection(null, "wikipedia", "supplement"); assert.equal(f.controller.state.provider.provider, "wikipedia");
  const source = managedSources()[1]; f.controller.state.detail.sources = [source];
  f.controller.beginSourceSelection(source); assert.equal(f.controller.state.provider.provider, "wikipedia"); assert.equal(f.controller.state.provider.replaceBindingId, source.binding_id);
});

test("binding management sends only the current scoped reference and retires any old refresh preview", async () => {
  for (const [action, role] of [["disable"], ["enable"], ["remove"], ["set-role", "supplement"]]) {
    const sources = managedSources(), f = fixture(route => route === "detail" ? { ...detail(), sources } : route === "provider/sync-preview" ? combinedPreview() :
      route === "provider/binding-update" ? { records: [{ ref, record }] } : undefined);
    await f.controller.activate(); await f.controller.openDetail(item); await f.controller.refreshProvider(); const old = f.controller.state.provider.preview;
    await f.controller.updateSourceBinding(sources[0], action, role);
    assert.deepEqual(f.calls.find(call => call.route === "provider/binding-update").body, { ref, binding_id: "tmdb-1", action, ...(role ? { role } : {}) });
    assert.equal(f.controller.state.provider.preview, null); await assert.rejects(f.controller.applyProvider(old), /预览差异/);
  }
});

async function openManagedSources(f) {
  f.feature.init(f.context); f.feature.activate(f.context); await flush(); f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
  f.findButton("来源").emit("click"); await flush();
}

test("source cards expose title link role state and separate replace add and confirmed removal actions", async () => {
  const f = domFixture(route => route === "detail" ? { ...detail(), sources: managedSources() } : route === "provider/binding-update" ? { records: [{ ref, record }] } : undefined);
  await openManagedSources(f);
  for (const title of ["韩剧原名", "韩剧中文名"]) assert.ok(f.nodes().some(node => node.tagName === "h3" && node.textContent === title));
  assert.ok(f.nodes().some(node => node.tagName === "a" && node.href === "https://www.themoviedb.org/tv/100/season/1"));
  assert.equal(f.document.getElementById("library-source-role-1").value, "supplement");
  assert.ok(f.findButton("添加来源")); assert.equal(f.nodes().filter(node => node.tagName === "button" && node.textContent === "重新选择").length, 2);
  f.findButton("移除关联").emit("click"); await flush(); assert.equal(f.calls.some(call => call.route === "provider/binding-update"), false);
  assert.ok(f.nodes().some(node => node.textContent.includes("保留已经保存的作品资料")));
  f.findButton("取消").emit("click"); await flush(); assert.equal(f.findButton("确认移除"), undefined);
  f.findButton("移除关联").emit("click"); await flush(); f.findButton("确认移除").emit("click"); await flush();
  assert.deepEqual(f.calls.find(call => call.route === "provider/binding-update").body, { ref, binding_id: "tmdb-1", action: "remove" });
});

test("aggregate UI presents one preview partial failures and field candidates without a second error stack", async () => {
  const preview = combinedPreview(); preview.sources[1] = { binding_id: "wiki-2", provider: "wikipedia", status: "error", error: "provider diagnostic details" }; preview.warnings = ["provider diagnostic details"];
  const f = domFixture(route => route === "detail" ? { ...detail(), sources: managedSources() } : route === "provider/sync-preview" ? preview : route === "provider/sync-apply" ? { records: [{ ref, record }] } : undefined);
  await openManagedSources(f); f.findButton("刷新作品数据").emit("click"); await flush();
  assert.equal(f.nodes().filter(node => node.className === "library-sync-preview").length, 1);
  assert.ok(f.nodes().some(node => node.textContent.includes("读取失败，保留原数据")));
  assert.equal(f.nodes().some(node => node.textContent.includes("provider diagnostic details")), false);
  const select = f.document.getElementById("library-field-source-metadata-summary"); assert.equal(select.children.length, 2); select.value = "wiki-2"; select.emit("change");
  assert.ok(f.nodes().some(node => node.tagName === "pre" && node.textContent === "中文简介"));
  assert.equal(f.findButton("确认本次作品数据刷新").disabled, false); assert.equal(f.calls.some(call => /apply$/.test(call.route)), false);
  f.findButton("确认本次作品数据刷新").emit("click"); await flush();
  assert.deepEqual(f.calls.find(call => call.route === "provider/sync-apply").body.field_choices, { "metadata.summary": "wiki-2" });
});

test("manual source policy never offers defaults that the user already removed", async () => {
  const f = domFixture(route => route === "detail" ? { ...detail(), sources: [], source_policy: { custom: true, primary_provider: "tmdb", supplement_providers: ["wikipedia"], missing_providers: ["tmdb", "wikipedia"] } } : undefined);
  await openManagedSources(f); assert.equal(f.findButton("选择 TMDB 主来源"), undefined); assert.equal(f.findButton("选择 中文维基百科 补充来源"), undefined);
  assert.ok(f.findButton("添加来源")); assert.ok(f.nodes().some(node => node.textContent === "手动来源"));
});

test("all failed sources remain inspectable but cannot submit an absent aggregate token", async () => {
  const preview = { ...combinedPreview(), diff: [], selected_fields: [], can_apply: false, preview_token: null };
  const f = domFixture(route => route === "detail" ? { ...detail(), sources: managedSources() } : route === "provider/sync-preview" ? preview : undefined);
  await openManagedSources(f); f.findButton("刷新作品数据").emit("click"); await flush(); assert.equal(f.findButton("确认本次作品数据刷新").disabled, true);
  const controller = fixture(route => route === "detail" ? { ...detail(), sources: managedSources() } : route === "provider/sync-preview" ? preview : undefined);
  await controller.controller.activate(); await controller.controller.openDetail(item); await controller.controller.refreshProvider();
  await assert.rejects(controller.controller.applyProvider(), /没有可确认/); assert.equal(controller.calls.some(call => /apply$/.test(call.route)), false);
});

test("deselecting a field drops its candidate assignment from the aggregate confirmation payload", async () => {
  const f = fixture(route => route === "detail" ? { ...detail(), sources: managedSources() } : route === "provider/sync-preview" ? combinedPreview() : route === "provider/sync-apply" ? { records: [{ ref, record }] } : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); await f.controller.refreshProvider();
  f.controller.chooseProviderField("metadata.summary", "wiki-2"); f.controller.selectProviderField("metadata.summary", false); await f.controller.applyProvider();
  assert.deepEqual(f.calls.find(call => call.route === "provider/sync-apply").body, { ref, preview_token: "combined-signed", selected_fields: [] });
});

test("aggregate refresh includes a shared people change preview without a separate apply operation", async () => {
  const preview = combinedPreview(); preview.people_preview = { changes: [{ action: "update", name: "人物A", changed_fields: ["summary"] }], warnings: [] };
  const f = domFixture(route => route === "detail" ? { ...detail(), sources: managedSources() } : route === "provider/sync-preview" ? preview : route === "provider/sync-apply" ? { records: [{ ref, record }], people_updated: true } : undefined);
  await openManagedSources(f); f.findButton("刷新作品数据").emit("click"); await flush();
  assert.ok(f.nodes().some(node => node.className === "library-source-people-preview")); assert.ok(f.nodes().some(node => node.textContent.includes("人物A")));
  assert.equal(f.findButton("确认保存人物与关联"), undefined); f.findButton("确认本次作品数据刷新").emit("click"); await flush();
  assert.equal(f.calls.filter(call => /apply$/.test(call.route)).length, 1); assert.match(f.notices.at(-1).value, /人物资料与关联已同步/);
});

test("existing sources keep selection controls collapsed until add or replace and cancel retires the draft", async () => {
  const f = domFixture(route => route === "detail" ? { ...detail(), sources: managedSources() } : undefined);
  await openManagedSources(f); assert.equal(f.document.getElementById("library-provider-query"), null);
  f.findButton("重新选择").emit("click"); await flush();
  assert.equal(f.document.getElementById("library-provider-choice").value, "tmdb"); assert.ok(f.nodes().some(node => node.textContent === "重新选择来源条目"));
  f.findButton("取消选择").emit("click"); await flush(); assert.equal(f.document.getElementById("library-provider-query"), null);
  f.findButton("添加来源").emit("click"); await flush(); assert.ok(f.document.getElementById("library-provider-query"));
  assert.ok(f.nodes().some(node => node.textContent === "添加来源条目")); assert.equal(f.calls.some(call => call.route.startsWith("provider/")), false);
});

test("aggregate aliases and automatic additions follow the selected name source", async () => {
  const preview = combinedPreview(); preview.default_name_binding_id = "tmdb-1";
  preview.automatic_aliases = ["来源名称", "中文名称"];
  preview.automatic_aliases_by_name_binding = { "tmdb-1": ["中文名称"], "wiki-2": ["来源名称"] };
  preview.diff[0].choices = [{ binding_id: "tmdb-1", provider: "tmdb", after: "来源名称", changed: true, selectable: true }, { binding_id: "wiki-2", provider: "wikipedia", after: "中文名称", changed: true, selectable: true }];
  preview.diff.push({ field: "metadata.aliases", label: "别名", before: ["原别名"], after: ["原别名", "来源名称", "中文名称"], changed: true, selectable: true,
    default_name_binding_id: "tmdb-1", after_when_name_selected: ["原别名", "中文名称"], after_by_name_binding: { "tmdb-1": ["原别名", "中文名称"], "wiki-2": ["原别名", "来源名称"] } });
  const f = domFixture(route => route === "detail" ? { ...detail(), sources: managedSources() } : route === "provider/sync-preview" ? preview : undefined);
  await openManagedSources(f); f.findButton("刷新作品数据").emit("click"); await flush();
  const nameRow = f.nodes().find(node => node.className === "library-diff" && node.children.some(child => child.textContent === "作品名"));
  nameRow.children[0].checked = true; nameRow.children[0].emit("change");
  let aliasValue = f.nodes().find(node => node.className.includes("library-aliases-after"));
  assert.deepEqual(JSON.parse(aliasValue.children[0].textContent), ["原别名", "中文名称"]);
  const choice = f.document.getElementById("library-field-source-name"); choice.value = "wiki-2"; choice.emit("change");
  aliasValue = f.nodes().find(node => node.className.includes("library-aliases-after")); assert.deepEqual(JSON.parse(aliasValue.children[0].textContent), ["原别名", "来源名称"]);
  const note = f.nodes().find(node => node.className.includes("library-automatic-aliases")); assert.equal(note.hidden, false);
  assert.match(note.textContent, /「来源名称」/); assert.doesNotMatch(note.textContent, /「中文名称」/);
});

test("switching a selected name candidate prunes an alias variant that no longer changes data", async () => {
  const preview = combinedPreview(); preview.diff[0].choices = [{ binding_id: "tmdb-1", provider: "tmdb", after: "Name", changed: true, selectable: true }, { binding_id: "wiki-2", provider: "wikipedia", after: "Other", changed: true, selectable: true }];
  preview.diff.push({ field: "metadata.aliases", before: ["Old"], after: ["Old", "Other"], changed: true, selectable: true, default_name_binding_id: "tmdb-1", after_by_name_binding: { "tmdb-1": ["Old", "Other"], "wiki-2": ["Old"] } });
  const f = fixture(route => route === "detail" ? { ...detail(), sources: managedSources() } : route === "provider/sync-preview" ? preview : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); await f.controller.refreshProvider();
  f.controller.selectProviderField("name", true); f.controller.selectProviderField("metadata.aliases", true);
  assert.ok(f.controller.state.provider.selected.includes("metadata.aliases")); f.controller.chooseProviderField("name", "wiki-2");
  assert.equal(f.controller.state.provider.selected.includes("metadata.aliases"), false);
});

test("adding the outstanding default source chooses its platform and role without manual provider selection", async () => {
  const f = fixture(route => route === "detail" ? { ...detail(), sources: [managedSources()[0]], source_policy: { primary_provider: "tmdb", supplement_providers: ["wikipedia"], missing_providers: ["wikipedia"], custom: false } } : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); f.controller.beginSourceSelection();
  assert.equal(f.controller.state.provider.provider, "wikipedia"); assert.equal(f.controller.state.provider.bindingRole, "supplement");
  assert.equal(f.controller.state.provider.replaceBindingId, "");
});

test("Douban links allow only canonical movie subjects and never arbitrary URLs", () => {
  const sourceLink = fixture().window.NimdaCatalogLibrary.sourceLink;
  const canonical = "https://movie.douban.com/subject/26339248/";
  assert.equal(sourceLink({ provider: "douban", external_id: "26339248", source_url: "javascript:alert(1)" }), canonical);
  assert.equal(sourceLink({ provider: "douban", source_url: canonical }), canonical);
  for (const source_url of ["javascript:alert(1)", "//movie.douban.com/subject/1/", "http://movie.douban.com/subject/1/", "https://movie.douban.com.evil/subject/1/", "https://evil@movie.douban.com/subject/1/", "https://movie.douban.com:443/subject/1/", "https://movie.douban.com/subject/%31/", "https://movie.douban.com/subject/1/?next=evil", "https://movie.douban.com/subject/1/#x", "https://movie.douban.com/subject/../1/", "https://movie.douban.com/subject/0/"]) {
    assert.equal(sourceLink({ provider: "douban", source_url }), "", source_url);
  }
});

test("Douban and Wikipedia cannot request covers while TMDB retains its exact cover target", async () => {
  const douban = { provider: "douban", external_id: "26339248", binding_id: "douban-1", role: "supplement" };
  const f = fixture(); await f.controller.activate(); await f.controller.openDetail(item);
  await f.controller.previewCover("26339248", douban); await f.controller.previewCover("12", { provider: "wikipedia", external_id: "12" });
  assert.equal(f.calls.some(call => call.route === "provider/cover-preview"), false);
  const tmdb = { provider: "tmdb", external_id: "66816", binding_id: "tmdb-1", season_number: 1 };
  const g = domFixture(route => route === "detail" ? { ...detail(), sources: [douban, tmdb] } : route === "provider/cover-preview" ? { ref, preview_token: "cover", diff: [] } : undefined);
  g.feature.init(g.context); g.feature.activate(g.context); await flush(); g.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
  assert.ok(g.findButton("更新封面")); g.findButton("更新封面").emit("click"); await flush();
  assert.deepEqual(g.calls.find(call => call.route === "provider/cover-preview").body, { ref, subject_id: "66816", provider: "tmdb", season_number: 1 });
  g.feature.dispose();
});

test("Douban overview ratings are compact, linked, timestamped and never render missing scores as zero", async () => {
  const rating = { score: 8.7, scale: 10, votes: 12345, external_id: "26339248", fetched_at: "2026-10-06T10:20:30Z" };
  for (const value of [rating, ...[null, "", 0, -1, 11, false, "8.7", "<img src=x onerror=alert(1)>"].map(score => ({ score, votes: "<script>bad</script>", source_url: "javascript:alert(1)", fetched_at: "<img src=x>" }))]) {
    const f = domFixture(route => route === "detail" ? { ...detail(), metadata: { ratings: { douban: value } } } : undefined);
    f.feature.init(f.context); f.feature.activate(f.context); await flush(); f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
    const box = f.nodes().find(node => node.className === "library-douban-rating library-meta"); assert.ok(box);
    if (value === rating) {
      assert.equal(box.children[0].textContent, "豆瓣评分 8.7 / 10 · 12345人评价"); assert.equal(box.title, "更新于 2026-10-06T10:20:30Z");
      const link = box.children.find(node => node.tagName === "a"); assert.equal(link.href, "https://movie.douban.com/subject/26339248/");
      assert.equal(link.target, "_blank"); assert.equal(link.rel, "noopener noreferrer");
    } else {
      assert.equal(box.children[0].textContent, "豆瓣评分 暂无评分"); assert.equal(box.children.some(node => node.tagName === "a"), false);
      assert.equal(box.title, "更新于 <img src=x>");
    }
    assert.equal(f.nodes().some(node => ["script", "iframe"].includes(node.tagName) || node.tagName === "img" && /^https?:|^data:|^\/\//.test(node.src || "")), false);
    f.feature.dispose();
  }
  assert.match(CSS, /\.library-douban-rating\s*\{[^}]*border:\s*1px solid var\(--border\)/);
});

test("Douban rating links cannot switch provider through untrusted rating metadata", async () => {
  const f = domFixture(route => route === "detail" ? { ...detail(), metadata: { ratings: { douban: { score: 8.5, provider: "wikipedia", source_url: "https://zh.wikipedia.org/wiki/Example" } } } } : undefined);
  f.feature.init(f.context); f.feature.activate(f.context); await flush(); f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
  assert.equal(f.nodes().find(node => node.className === "library-douban-rating library-meta").children.some(node => node.tagName === "a"), false);
  f.feature.dispose();
});





test("Wikipedia single-source preview includes bilingual TMDB people and commits through one parent confirmation", async () => {
  const sources = managedSources(), actor = { id: "tmdb-person-1", type: "person", name: "하석진", name_cn: "河锡辰", character_name: "이재인", character_name_cn: "李宰仁" };
  const changes = [{ id: actor.id, name: actor.name, action: "update", changed_fields: ["name_cn"] }];
  const tmdbPeople = { provider: "tmdb", binding_id: "tmdb-1", subject_id: "100", season_number: 1, snapshot_id: "people-snapshot", preview_token: "nested-people-token", people: { cast: [actor] }, changes };
  const proposal = { ref, provider: "wikipedia", subject_id: "200", source_url: "https://zh.wikipedia.org/wiki/Example", snapshot_id: "wiki-snapshot", preview_token: "wiki-with-people-token",
    diff: [{ field: "metadata.summary", label: "简介", before: "", after: "维基中文简介", changed: true, selectable: true }],
    people_preview: { sources: [tmdbPeople], changes, warnings: [] } };
  const before = JSON.stringify(proposal);
  const f = domFixture(route => route === "detail" ? { ...detail(), sources, chapter_title_preference: "chinese" } :
    route === "provider/search" ? { results: [{ id: "200", provider: "wikipedia", name: "维基作品", source_url: proposal.source_url }] } :
    route === "provider/preview" ? proposal : route === "provider/apply" ? { records: [{ ref, record }], people_updated: true } : undefined);
  f.feature.init(f.context); f.feature.activate(f.context); await flush();
  f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
  f.findButton("来源").emit("click"); await flush();
  f.nodes().filter(node => node.tagName === "button" && node.textContent === "重新选择")[1].emit("click"); await flush();
  assert.equal(f.document.getElementById("library-provider-choice").value, "wikipedia");
  const input = f.document.getElementById("library-provider-query"); input.value = proposal.source_url;
  f.nodes().find(node => node.className === "library-provider-search").emit("submit"); await flush();
  f.findButton("预览差异").emit("click"); await flush();
  assert.deepEqual(f.calls.find(call => call.route === "provider/preview").body, { ref, provider: "wikipedia", subject_id: "200", replace_binding_id: "wiki-2", binding_role: "supplement" });
  assert.ok(f.nodes().some(node => node.className === "library-source-people-preview"));
  assert.ok(f.nodes().some(node => node.textContent === "TMDB · 条目 100 · 第 1 季 · 人物更新预览"));
  assert.equal(f.findButton("河锡辰").title, "하석진");
  assert.ok(f.nodes().some(node => node.textContent === "饰演 李宰仁" && node.title === "이재인"));
  assert.ok(f.nodes().some(node => node.textContent === "更新 · 河锡辰 · 中文名" && node.title === "하석진"));
  f.findButton("河锡辰").emit("click");
  const heading = f.nodes().find(node => node.className === "library-person-dialog-name");
  assert.equal(heading.textContent, "河锡辰"); assert.equal(heading.title, "하석진");
  assert.ok(f.nodes().some(node => node.className === "library-meta" && node.textContent === "하석진"));
  f.findButton("关闭").emit("click"); await flush();
  assert.equal(f.findButton("确认保存人物与关联"), undefined); assert.ok(f.findButton("确认来源与人物资料"));
  assert.equal(f.calls.some(call => call.route.startsWith("people/") || /apply$/.test(call.route)), false);
  const summary = f.nodes().find(node => node.type === "checkbox" && node.attributes["aria-label"] === "采用简介");
  assert.equal(summary.checked, false); summary.checked = true; summary.emit("change");
  f.findButton("确认写入所选字段与人物资料").emit("click"); await flush(); await flush();
  assert.deepEqual(f.calls.find(call => call.route === "provider/apply").body, { ref, provider: "wikipedia", snapshot_id: "wiki-snapshot", preview_token: "wiki-with-people-token", selected_fields: ["metadata.summary"] });
  assert.equal(f.calls.filter(call => /apply$/.test(call.route)).length, 1);
  assert.equal(f.calls.some(call => call.route.startsWith("people/") || call.route === "provider/sync-apply"), false);
  assert.match(f.notices.at(-1).value, /人物资料与关联已同步/);
  assert.equal(JSON.stringify(proposal), before); f.feature.dispose();
});

test("source save button explains an invalid people field and clears the reason after correction", async () => {
  const entry = { external_id: "character:1", entity_type: "character", candidate: { name: "金部长", type: "character" }, action: "reuse", target_id: "role", identity_owner_id: "role",
    selected_fields: ["summary"], selected_relations: [], relations: [], targets: [{ id: "role", name: "金部长", diff: [
      { field: "summary", label: "简介", before: "", after: "", merge_action: "empty", selectable: true, must_override: true }
    ] }] };
  const proposal = { ref, preview_token: "aggregate", diff: [], people_preview: { sources: [{ mode: "people-import", provider: "tmdb", snapshot_id: "people-source", entries: [entry] }] } };
  const f = domFixture(route => route === "detail" ? { ...detail(), sources: [{ provider: "tmdb", binding_id: "tmdb-bound", external_id: "123" }] } : route === "provider/sync-preview" ? proposal : undefined);
  f.feature.init(f.context); f.feature.activate(f.context); await flush(); f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
  f.findButton("刷新作品数据").emit("click"); await flush();
  const apply = f.findButton("确认本次作品数据刷新"), issue = f.nodes().find(node => node.className.includes("library-source-apply-issue"));
  assert.equal(apply.disabled, true); assert.equal(issue.hidden, false); assert.match(issue.textContent, /暂不能保存.*手动填写/);
  assert.equal(apply.title, issue.textContent.replace("暂不能保存：", "")); assert.equal(apply.getAttribute("aria-describedby"), issue.id);
  assert.equal(issue.parentNode.children.at(-2), issue); assert.equal(issue.parentNode.children.at(-1), apply);
  const choice = f.nodes().find(node => node.getAttribute("aria-label") === "采用简介：金部长"); choice.checked = false; choice.emit("change");
  assert.equal(apply.disabled, false); assert.equal(issue.hidden, true); assert.equal(issue.textContent, ""); assert.equal(apply.title, ""); assert.equal(apply.getAttribute("aria-describedby"), null);
  assert.equal(f.calls.some(call => /apply$/.test(call.route)), false); f.feature.dispose();
});

test("work source previews expose manual people conflicts but collapse fill-only people", async () => {
  for (const manual of [false, true]) {
    const entry = { external_id: "person:1", entity_type: "person", candidate: { name: "演员甲", name_cn: "演员甲" }, action: "reuse", target_id: "actor", identity_owner_id: "actor", selected_fields: manual ? [] : ["summary"], selected_relations: [], relations: [], targets: [{ id: "actor", name: "演员甲", diff: [{ field: "summary", before: manual ? "本地简介" : "", after: "来源简介", merge_action: manual ? "conflict" : "fill", requires_confirmation: manual, default_selected: !manual, selectable: true, changed: true }] }] };
    const proposal = { ref, preview_token: "aggregate", diff: [], people_preview: { sources: [{ mode: "people-import", provider: "tmdb", snapshot_id: "people-source", entries: [entry] }] } };
    const f = domFixture(route => route === "detail" ? { ...detail(), sources: [{ provider: "tmdb", binding_id: "tmdb-bound", external_id: "123" }] } : route === "provider/sync-preview" ? proposal : route === "provider/sync-apply" ? { records: [{ ref, record }], people_updated: true } : undefined);
    f.feature.init(f.context); f.feature.activate(f.context); await flush(); f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
    f.findButton("刷新作品数据").emit("click"); await flush();
    assert.equal(f.nodes().find(node => node.className === "library-source-people-preview").open, manual);
    assert.equal(f.nodes().find(node => node.className === "library-people-manual-group").hidden, !manual);
    assert.equal(f.nodes().find(node => node.className === "library-people-auto-group").open, false);
    assert.equal(f.calls.some(call => /apply$/.test(call.route)), false);
    f.findButton("确认本次作品数据刷新").emit("click"); await flush(); await flush();
    assert.deepEqual(f.calls.find(call => call.route === "provider/sync-apply").body.people_selections, { "people-source": [{ external_id: "person:1", action: "reuse", target_id: "actor", fields: manual ? [] : ["summary"], overrides: {}, relations: [] }] });
    f.feature.dispose();
  }
});





test("Douban search links encode keywords and accept only the exact official category URL", () => {
  const make = fixture().window.NimdaCatalogLibrary.doubanSearchUrl;
  const base = "https://www.douban.com/search?cat=1002&q=";
  for (const query of ["爱的迫降 & <script>", "javascript:alert(1)", "https://www.douban.com.evil/search?cat=1002&q=x", "https://evil@www.douban.com/search?cat=1002&q=x", "https://www.douban.com/search?cat=1065&q=x", "https://www.douban.com/search?cat=1002&q=%zz"])
    assert.equal(make(query), base + encodeURIComponent(query));
  assert.equal(make(base + "%E7%88%B1%E7%9A%84%E8%BF%AB%E9%99%8D"), base + encodeURIComponent("爱的迫降"));
  assert.equal(make("https://www.douban.com/search?q=a+b&cat=1002"), base + "a%20b");
  assert.equal(make("河锡辰", "1065"), "https://www.douban.com/search?cat=1002&q=" + encodeURIComponent("河锡辰"), "retired person search category cannot be selected");
  assert.equal(make("x", "javascript:foo"), base + "x");
});

test("Douban keyword results display disambiguation as text and keep a safe search fallback after failures", async () => {
  let fail = false;
  const f = domFixture(route => route === "provider/search" ? (fail ? Promise.reject(Error("验证失败")) : { results: [
    { id: 1, name: "<img src=x>", type: "电影", year: "2016", description: "<script>电影演员</script>", cover_url: "https://evil/image.jpg" },
    { id: 2, name: "同名剧", media_type: "tv", year: "2026", description: "演员甲 / 演员乙" }
  ], search_url: "javascript:alert(1)" }) : undefined);
  f.feature.init(f.context); f.feature.activate(f.context); await flush();
  f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush();
  f.findButton("来源").emit("click"); await flush();
  const provider = f.document.getElementById("library-provider-choice"); provider.value = "douban"; provider.emit("change");
  const input = f.document.getElementById("library-provider-query"); assert.match(input.placeholder, /作品名.*搜索链接/);
  input.value = "同名 & 作品"; input.emit("input");
  assert.equal(f.nodes().find(node => node.textContent === "在豆瓣打开搜索").href, "https://www.douban.com/search?cat=1002&q=" + encodeURIComponent(input.value));
  assert.ok(f.findButton("搜索 豆瓣评分"));
  f.nodes().find(node => node.className === "library-provider-search").emit("submit"); await flush();
  for (const text of ["电影 · 2016", "电视剧 · 2026", "<script>电影演员</script>"]) assert.ok(f.nodes().some(node => node.textContent === text));
  assert.equal(f.nodes().some(node => node.tagName === "script" || node.src === "https://evil/image.jpg"), false);
  const oldResult = f.findButton("预览差异"), freshInput = f.document.getElementById("library-provider-query");
  freshInput.value = "新词"; freshInput.emit("input"); oldResult.emit("click"); await flush();
  assert.equal(f.findButton("预览差异"), undefined); assert.equal(f.calls.some(call => call.route === "provider/preview"), false);
  fail = true; f.nodes().find(node => node.className === "library-provider-search").emit("submit"); await flush();
  assert.ok(f.nodes().some(node => node.textContent.includes("搜索暂不可用")));
  assert.equal(f.nodes().find(node => node.textContent === "在豆瓣打开搜索").href, "https://www.douban.com/search?cat=1002&q=" + encodeURIComponent("新词"));
  f.feature.dispose();
});

test("editing a Douban query retires in-flight results and unavailable results never look like a successful empty search", async () => {
  const pending = deferred();
  const f = fixture(route => route === "provider/search" ? pending.promise : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); f.controller.chooseProvider("douban");
  const search = f.controller.searchProvider("旧词"); f.controller.setProviderQuery("新词");
  pending.resolve({ results: [{ id: 1 }], unavailable: true, warnings: ["安全验证"] }); await search;
  assert.deepEqual(plain(f.controller.state.provider.results), []); assert.equal(f.controller.state.provider.query, "新词");
  await f.controller.searchProvider("新词");
  assert.equal(f.controller.state.provider.unavailable, true); assert.deepEqual(plain(f.controller.state.provider.searchWarnings), ["安全验证"]);
});

test("Douban is a rating-only supplement and cannot submit work names aliases or people", async () => {
  const rating = { score: 8.7, votes: 88, scale: 10, external_id: "123" };
  const preview = { ref, provider: "douban", snapshot_id: "rating", preview_token: "signed-rating", automatic_aliases: ["Source alias"], diff: [
    { field: "name", before: "Local", after: "Source name", changed: true, selectable: true },
    { field: "metadata.ratings.douban", before: null, after: rating, changed: true, selectable: true }
  ], people_preview: { mode: "people-import", provider: "douban", snapshot_id: "forbidden", entries: [] } };
  const f = fixture(route => route === "provider/preview" ? preview : route === "provider/apply" ? { records: [{ ref, record }] } : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); f.controller.beginSourceSelection(null, "douban", "primary");
  assert.equal(f.controller.state.provider.bindingRole, "supplement"); await f.controller.previewProvider("123");
  assert.deepEqual(f.calls.at(-1).body, { ref, provider: "douban", subject_id: "123", binding_role: "supplement" });
  f.controller.selectProviderField("name", true); assert.deepEqual(Array.from(f.controller.state.provider.selected), []);
  f.controller.selectProviderField("metadata.ratings.douban", true); f.controller.state.provider.selected.push("name"); await f.controller.applyProvider();
  assert.deepEqual(f.calls.find(call => call.route === "provider/apply").body, { ref, provider: "douban", snapshot_id: "rating", preview_token: "signed-rating", selected_fields: ["metadata.ratings.douban"] });
  assert.equal(f.controller.createPeopleSupplement, undefined); assert.equal(f.controller.previewDoubanPeopleRepair, undefined);
});

test("Douban rating UI exposes only score differences and no people preview or aliases", async () => {
  const binding = { provider: "douban", external_id: "123", binding_id: "rating-binding", name: "评分来源", role: "supplement" };
  const f = domFixture(route => route === "detail" ? { ...detail(), sources: [binding] } : route === "provider/preview" ? { ref, provider: "douban", preview_token: "rating", diff: [
    { field: "name", before: "Local", after: "Must not display", selectable: true },
    { field: "metadata.ratings.douban", before: null, after: { score: 8.1, scale: 10, external_id: "123" }, selectable: true }
  ], automatic_aliases: ["Must not append"], people_preview: { mode: "people-import", provider: "douban", entries: [{ name: "Must not import" }] } } : route === "provider/search" ? { results: [{ id: "123", name: "评分作品" }] } : undefined);
  f.feature.init(f.context); f.feature.activate(f.context); await flush(); f.nodes().find(node => node.className.includes("library-card-link")).emit("click"); await flush(); f.findButton("来源").emit("click"); await flush();
  assert.ok(f.findButton("刷新此来源数据")); assert.ok(f.findButton("浏览器读取评分")); f.findButton("重新选择").emit("click"); await flush();
  assert.ok(f.document.getElementById("library-provider-choice").children.some(node => node.value === "douban" && node.textContent === "豆瓣评分"));
  f.nodes().find(node => node.tagName === "form" && node.className === "library-provider-search").emit("submit"); await flush(); f.findButton("预览差异").emit("click"); await flush();
  assert.equal(f.nodes().filter(node => node.type === "checkbox").length, 1);
  assert.equal(f.nodes().some(node => node.textContent.includes("Must not")), false);
  assert.equal(f.nodes().some(node => node.className === "library-source-people-preview"), false); f.feature.dispose();
});

test("rating browser is explicit and cancellation retires late results without saving", async () => {
  const pending = deferred(), binding = { provider: "douban", external_id: "123", binding_id: "rating" };
  const f = fixture(route => route === "detail" ? { ...detail(), sources: [binding] } : route === "douban/browser/start" ? pending.promise : route === "douban/browser/cancel" ? {} : undefined);
  await f.controller.activate(); await f.controller.openDetail(item); const reading = f.controller.startRatingBrowser("123", f.controller.state.detail.sources[0]);
  assert.deepEqual(f.calls.at(-1).body, { ref, subject_id: "123", binding_role: "supplement", replace_binding_id: "rating" });
  f.controller.setDetailTab("people"); pending.resolve({ session_id: "late", status: "complete", preview: { provider: "douban", preview_token: "late" } }); await reading; await flush();
  assert.equal(f.controller.state.provider.preview, null); assert.equal(f.calls.at(-1).route, "douban/browser/cancel"); assert.equal(f.calls.some(call => call.route.includes("apply")), false);
});

function ratingClock() {
  const timers = new Map(); let id = 0, now = 0;
  return { timers, window: { setTimeout(fn, delay) { timers.set(++id, { fn, at: now + delay }); return id; }, clearTimeout(id) { timers.delete(id); } },
    async tick() { const row = Array.from(timers.entries()).sort((a, b) => a[1].at - b[1].at)[0]; if (row) { timers.delete(row[0]); now = row[1].at; row[1].fn(); await flush(); } } };
}

test("rating controller automatically collects the same async task while only locking active requests", async () => {
  const clock = ratingClock(), pending = deferred(), binding = { provider: "douban", external_id: "123", binding_id: "rating" }; let steps = 0;
  const preview = { provider: "douban", preview_token: "rating-only", diff: [{ field: "metadata.ratings.douban" }] };
  const f = fixture(route => route === "detail" ? { ...detail(), sources: [binding] } :
    route === "douban/browser/start" ? { session_id: "s", status: "loading", auto_continue: true } :
    route === "douban/browser/step" ? ++steps === 1 ? pending.promise : { session_id: "s", status: "complete", preview } : undefined,
    { window: clock.window });
  await f.controller.activate(); await f.controller.openDetail(item);
  await f.controller.startRatingBrowser("123", f.controller.state.detail.sources[0]);
  assert.equal(f.controller.state.provider.loading, false); assert.equal(f.controller.state.provider.browser.polling, true);
  await f.controller.startRatingBrowser("123", f.controller.state.detail.sources[0]);
  await f.controller.stepRatingBrowser("continue"); assert.equal(f.calls.filter(row => row.route.startsWith("douban/browser/")).length, 1);
  await clock.tick(); assert.equal(f.controller.state.provider.loading, true);
  pending.resolve({ session_id: "s", status: "loading", auto_continue: true }); await flush();
  assert.equal(f.controller.state.provider.loading, false); await clock.tick();
  assert.equal(f.controller.state.provider.preview.preview_token, "rating-only"); assert.equal(f.controller.state.provider.loading, false);
  assert.equal(clock.timers.size, 0); assert.equal(f.calls.some(row => /people\/|apply/.test(row.route)), false);
  assert.deepEqual(f.calls.filter(row => row.route === "douban/browser/step").map(row => row.body), Array(2).fill({ ref, session_id: "s", action: "continue" }));
  f.controller.dispose();
});

test("rating pause states never keep the source controller locked", async () => {
  for (const status of ["verification", "closed", "failed"]) {
    const clock = ratingClock(), binding = { provider: "douban", external_id: "123", binding_id: "rating" };
    const f = fixture(route => route === "detail" ? { ...detail(), sources: [binding] } : route === "douban/browser/start" ? { session_id: "s", status } : route === "douban/browser/cancel" ? {} : undefined, { window: clock.window });
    await f.controller.activate(); await f.controller.openDetail(item); await f.controller.startRatingBrowser("123", f.controller.state.detail.sources[0]);
    assert.equal(f.controller.state.provider.loading, false); assert.equal(clock.timers.size, 0);
    f.controller.setDetailTab("people"); await flush(); assert.equal(f.controller.state.detailTab, "people"); assert.equal(f.controller.state.provider.browser, null);
    assert.equal(f.calls.at(-1).route, "douban/browser/cancel"); f.controller.dispose();
  }
});

test("detail switching closing deactivation and disposal retire scheduled rating reads", async () => {
  for (const action of [controller => controller.setDetailTab("people"), controller => controller.closeDetail(), controller => controller.deactivate(), controller => controller.dispose()]) {
    const clock = ratingClock(), binding = { provider: "douban", external_id: "123", binding_id: "rating" };
    const f = fixture(route => route === "detail" ? { ...detail(), sources: [binding] } : route === "douban/browser/start" ? { session_id: "s", status: "loading", auto_continue: true } : route === "douban/browser/cancel" ? {} : undefined, { window: clock.window });
    await f.controller.activate(); await f.controller.openDetail(item); await f.controller.startRatingBrowser("123", f.controller.state.detail.sources[0]);
    action(f.controller); await flush(); assert.equal(clock.timers.size, 0); await clock.tick();
    assert.equal(f.calls.some(row => row.route === "douban/browser/step"), false); assert.equal(f.calls.at(-1).route, "douban/browser/cancel");
    assert.equal(f.controller.state.provider.loading, false); assert.equal(f.controller.state.provider.preview, null); f.controller.dispose();
  }
});

test("starting from a paused rating session waits for old cancellation before opening one new window", async () => {
  const clock = ratingClock(), cancelled = deferred(), binding = { provider: "douban", external_id: "123", binding_id: "rating" }; let starts = 0;
  const f = fixture(route => route === "detail" ? { ...detail(), sources: [binding] } : route === "douban/browser/start" ?
    { session_id: "s-" + ++starts, status: "verification" } : route === "douban/browser/cancel" ? cancelled.promise : undefined, { window: clock.window });
  await f.controller.activate(); await f.controller.openDetail(item); const source = f.controller.state.detail.sources[0];
  await f.controller.startRatingBrowser("123", source); const restarting = f.controller.startRatingBrowser("123", source);
  await flush(); assert.equal(starts, 1); assert.equal(f.controller.state.provider.loading, true);
  assert.match(f.controller.state.provider.browser.message, /结束上次评分读取/);
  await f.controller.startRatingBrowser("123", source); assert.equal(starts, 1);
  cancelled.resolve({}); await restarting;
  assert.equal(starts, 2); assert.equal(f.controller.state.provider.loading, false); assert.equal(clock.timers.size, 0);
  assert.deepEqual(f.calls.filter(row => row.route.startsWith("douban/browser/")).map(row => row.route), ["douban/browser/start", "douban/browser/cancel", "douban/browser/start"]);
  f.controller.dispose();
});

test("a slow rating cancellation has a bounded wait and retry cannot overtake the still-pending cancellation", async () => {
  const clock = ratingClock(), cancelled = deferred(), binding = { provider: "douban", external_id: "123", binding_id: "rating" }; let starts = 0;
  const f = fixture(route => route === "detail" ? { ...detail(), sources: [binding] } : route === "douban/browser/start" ?
    { session_id: "s-" + ++starts, status: "verification" } : route === "douban/browser/cancel" ? cancelled.promise : undefined, { window: clock.window });
  await f.controller.activate(); await f.controller.openDetail(item); const source = f.controller.state.detail.sources[0];
  await f.controller.startRatingBrowser("123", source); const restarting = f.controller.startRatingBrowser("123", source);
  await clock.tick(); await restarting;
  assert.equal(starts, 1); assert.equal(f.controller.state.provider.loading, false); assert.equal(clock.timers.size, 0);
  assert.match(f.controller.state.provider.browser.message, /取消尚未确认.*未打开新窗口/);
  const retry = f.controller.startRatingBrowser("123", source); await flush(); assert.equal(starts, 1);
  cancelled.resolve({}); await retry; assert.equal(starts, 2); assert.equal(clock.timers.size, 0); f.controller.dispose();
});

test("cancelling or leaving while an old rating session closes cannot open a late replacement window", async () => {
  for (const action of [controller => controller.cancelRatingBrowser(), controller => controller.setDetailTab("people"), controller => controller.closeDetail(), controller => controller.deactivate(), controller => controller.dispose()]) {
    const clock = ratingClock(), cancelled = deferred(), binding = { provider: "douban", external_id: "123", binding_id: "rating" }; let starts = 0;
    const f = fixture(route => route === "detail" ? { ...detail(), sources: [binding] } : route === "douban/browser/start" ?
      { session_id: "s-" + ++starts, status: "verification" } : route === "douban/browser/cancel" ? cancelled.promise : undefined, { window: clock.window });
    await f.controller.activate(); await f.controller.openDetail(item); const source = f.controller.state.detail.sources[0];
    await f.controller.startRatingBrowser("123", source); const restarting = f.controller.startRatingBrowser("123", source);
    action(f.controller); await restarting; assert.equal(clock.timers.size, 0);
    cancelled.resolve({}); await flush(); assert.equal(starts, 1); assert.equal(f.controller.state.provider.loading, false);
    assert.ok(f.controller.state.provider.browser == null); f.controller.dispose();
  }
});
