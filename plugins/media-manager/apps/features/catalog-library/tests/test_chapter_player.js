"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), test = require("node:test"), vm = require("node:vm");
const SOURCE = fs.readFileSync(path.join(__dirname, "../frontend/chapter-player.js"), "utf8"), CSS = fs.readFileSync(path.join(__dirname, "../frontend/chapter-player.css"), "utf8");
const NUMBER_SOURCE = fs.readFileSync(path.join(__dirname, "../frontend/chapter-number-format.js"), "utf8");
const TITLE_SOURCE = fs.readFileSync(path.join(__dirname, "../frontend/chapter-title-format.js"), "utf8");
const BATCH_SOURCE = fs.readFileSync(path.join(__dirname, "../frontend/chapter-batch-dialog.js"), "utf8");
const flush = () => new Promise(resolve => setImmediate(resolve)), plain = value => JSON.parse(JSON.stringify(value));
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const REF = { yaml_source_rel: "test.yaml", index_in_file: 0, source_sha256: "a".repeat(64), record_sha256: "b".repeat(64) };
const CHAPTERS = [
  { chapter_key: "c1", number: 1, title: "First", air_date: "20260102", relative_path: "Disc 1/01.mkv", status: "automatic", candidates: ["Disc 1/01.mkv"], duration_seconds: 1467 },
  { chapter_key: "c2", number: 2, title: "Second", air_date: "20260109", relative_path: "", status: "ambiguous", candidates: ["Disc 1/02.mkv", "Disc 2/02.mkv"], duration_seconds: null }
];
const RESOURCES = [{ press_format: "WEB", press_path: "", is_playback_resource: false }, { press_format: "BDRip", press_group: "ANK", press_path: "BDRip-ANK", is_playback_resource: true }, { press_format: "DVD", press_group: "----", press_path: "DVD", is_playback_resource: false }];
function scanResult(index = 1, patch = {}) { return { ok: true, ref: REF, resource_index: index, resource_key: "r" + index, revision: "v1", scanned_at: "2026-10-05", duration_available: true, warnings: [], chapters: plain(CHAPTERS), files: ["Disc 1/01.mkv", "Disc 1/02.mkv", "Disc 2/02.mkv"].map(relative_path => ({ relative_path })), ...patch }; }
function fixture(handler, detailOverride) {
  let document;
  class Element {
    constructor(tag) { this.tagName = tag; this.children = []; this.listeners = {}; this.attributes = {}; this.className = ""; this.textContent = ""; this.value = ""; this.hidden = this.disabled = this.open = false; }
    appendChild(node) { this.children.push(node); node.parentNode = this; return node; }
    removeChild(node) { this.children = this.children.filter(child => child !== node); node.parentNode = null; }
    remove() { if (this.parentNode) this.parentNode.removeChild(this); }
    get firstChild() { return this.children[0]; }
    setAttribute(key, value) { this.attributes[key] = String(value); }
    removeAttribute(key) { delete this.attributes[key]; }
    addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
    focus() { document.activeElement = this; }
    showModal() { this.open = true; }
    close() { this.open = false; this.emit("close"); }
    emit(name) { const event = { preventDefault() { this.defaultPrevented = true; } }; (this.listeners[name] || []).forEach(callback => callback(event)); return event; }
  }
  const flatten = node => [node, ...node.children.flatMap(flatten)]; document = { createElement: tag => new Element(tag) }; document.body = new Element("body");
  const parent = document.body.appendChild(new Element("section")), window = {}, calls = [], notices = [], saved = [], chosen = [];
  const detail = detailOverride || { ref: REF, resources: plain(RESOURCES), chapters: plain(CHAPTERS), playback: scanResult() };
  vm.runInNewContext(NUMBER_SOURCE + "\n" + TITLE_SOURCE + "\n" + BATCH_SOURCE + "\n" + SOURCE, { window, document });
  const component = window.NimdaChapterPlayer.mount(parent, { detail, setStatus(value, error) { notices.push({ value, error }); }, onSaved(value) { saved.push(value); }, onChooseResource() { chosen.push(true); }, async request(url, options) {
    const route = url.replace("/api/catalog-library/playback/", ""), body = JSON.parse(options.body); calls.push({ route, body, method: options.method });
    if (handler) { const result = await handler(route, body); if (result !== undefined) return result; }
    if (route === "scan") return scanResult(body.resource_index, { db_committed: true });
    if (route === "open") return { ok: true };
    throw Error("Unexpected route " + route);
  } });
  const nodes = () => flatten(document.body), byClass = value => nodes().filter(node => node.className.split(" ").includes(value)), button = value => nodes().find(node => node.tagName === "button" && node.textContent === value), accessible = value => nodes().find(node => node.attributes["aria-label"] === value);
  return { component, parent, document, window, detail, calls, notices, saved, chosen, nodes, byClass, button, accessible,
    async scan() { button("重新识别文件").emit("click"); await flush(); }, async settings(number = 1) { accessible("设置第 " + number + " 集匹配").emit("click"); await flush(); },
    form() { return byClass("chapter-player-match-form")[0]; }, input() { return nodes().find(node => node.tagName === "input"); } };
}

test("persisted cache renders immediately without scanning, selecting or probing", () => {
  const f = fixture(); assert.equal(f.calls.length, 0); assert.equal(f.notices.length, 0); assert.equal(f.accessible("播放第 1 集").disabled, false); assert.equal(f.accessible("播放第 2 集").disabled, true);
  assert.equal(f.accessible("播放第 2 集").title, "待选：有多个候选视频，请在设置中确认");
  assert.equal(f.byClass("chapter-player-duration")[0].textContent, "24:27"); assert.equal(f.byClass("chapter-player-current-resource")[0].textContent, "BDRip · ANK");
  assert.equal(f.button("选择播放资源").hidden, true); assert.equal(f.button("重新识别文件").hidden, false); assert.equal(f.nodes().some(node => node.tagName === "select"), false); assert.equal(f.button("读取时长"), undefined);
  assert.doesNotMatch(SOURCE, /readDurations|request\("durations"|chapter-player-path|chapter-player-resource/);
});
test("chapter list contains five columns with play next to number and no local file paths", () => {
  const f = fixture(), heading = f.byClass("chapter-player-head")[0]; assert.deepEqual(heading.children.map(node => node.textContent), ["集数", "章节", "播出日期", "时长", "设置"]);
  assert.ok(f.byClass("chapter-player-row").every(row => row.children.length === 5)); const episode = f.byClass("chapter-player-episode")[0]; assert.equal(episode.children[0].textContent, "01"); assert.equal(episode.children[1], f.accessible("播放第 1 集"));
  assert.equal(f.nodes().some(node => node.textContent.includes("Disc 1/01.mkv") || node.textContent === "本地文件"), false);
  assert.match(CSS, /grid-template-columns: 66px minmax\(0, 1fr\) 88px 54px 42px/); assert.match(CSS, /max-width: 580px/); assert.doesNotMatch(CSS, /chapter-player-head-file|chapter-player-path/);
});

test("projected local and continuous numbering share one compact column before and after resource recognition", () => {
  const chapters = CHAPTERS.map((chapter, index) => ({ ...chapter, number: index + 1, local_number: String(index + 1), continuous_number: String(index + 14), has_continuous_number: true }));
  const numbering = { has_continuous: true, local_start: "1", continuous_start: "14" };
  for (const playback of [null, scanResult(1, { chapters, numbering_mode: "continuous" })]) {
    const f = fixture(null, { ref: REF, resources: plain(RESOURCES), chapter_numbering: numbering, chapters, playback });
    assert.deepEqual(f.byClass("chapter-player-head")[0].children.map(node => node.textContent), ["集数", "章节", "播出日期", "时长", "设置"]);
    assert.deepEqual(f.byClass("chapter-player-number").map(node => node.textContent), ["01(#14)", "02(#15)"]);
    assert.equal(f.byClass("chapter-player-number")[0].title, "本作第 1 集；续接第 14 集");
    assert.equal(f.byClass("chapter-player-continuous-number").length, 0);
    assert.ok(f.byClass("chapter-player-row").every(row => row.children.length === 5));
    assert.equal(f.byClass("chapter-player-episode")[0].children[1], f.accessible("播放第 1 集"));
    assert.equal(f.nodes().some(node => node.textContent.includes("Disc 1/01.mkv")), false);
    assert.equal(f.calls.length, 0);
  }
  assert.match(CSS, /has-continuous-numbering[^}]*grid-template-columns: 110px minmax\(0, 1fr\)/);
  assert.match(CSS, /max-width: 580px[^\n]*has-continuous-numbering[^}]*grid-template-columns: 98px minmax\(0, 1fr\)/);
});

test("dual numbering uses backend local numbers without inventing missing continuation values", () => {
  const chapters = [{ ep: 1, sort: 14, number: 9, local_number: "1", continuous_number: "14", has_continuous_number: true },
    { number: 2, local_number: "2", continuous_number: "", has_continuous_number: false }];
  const f = fixture(null, { ref: REF, resources: [], chapters });
  assert.deepEqual(f.byClass("chapter-player-number").map(node => node.textContent), ["01(#14)", "02"]);
  assert.equal(f.byClass("chapter-player-continuous-number").length, 0);
  assert.equal(f.accessible("播放第 1 集").disabled, true);
});

test("shared chapter labels retain zero fractional and large numbers without fabricating continuation", () => {
  const numbers = fixture().window.NimdaChapterNumbers;
  for (const [row, expected] of [
    [{ ep: 1 }, "01"], [{ number: 0 }, "00"], [{ number: 2.5 }, "02.5"], [{ number: 100 }, "100"],
    [{ number: 1, has_continuous_number: true, continuous_number: "14.5" }, "01(#14.5)"],
    [{ number: 1, has_continuous_number: true, continuous_number: "1" }, "01"],
    [{ number: 1, continuous_number: "14" }, "01"],
    [{ number: 1, has_continuous_number: true, continuous_number: "invalid" }, "01"]
  ]) assert.equal(numbers.label(row, 0), expected);
});

test("continuous resource playback keeps backend chapter keys and exact paths rather than substituting episode numbers", async () => {
  const chapter = { ...CHAPTERS[0], number: 1, local_number: "1", continuous_number: "14", has_continuous_number: true, relative_path: "Disc/14.mkv" };
  const f = fixture(null, { ref: REF, resources: plain(RESOURCES), chapters: [], playback: scanResult(1, { chapters: [chapter], numbering_mode: "continuous" }) });
  f.accessible("播放第 1 集").emit("click"); await flush();
  assert.deepEqual(f.calls[0].body, { ref: REF, resource_index: 1, revision: "v1", chapter_key: "c1", relative_path: "Disc/14.mkv" });
});
test("legacy chapters without current playback never select a resource automatically", async () => {
  const f = fixture(null, { ref: REF, resources: plain(RESOURCES), chapters: plain(CHAPTERS), playback: null }); assert.equal(f.accessible("播放第 1 集").disabled, true); assert.equal(f.accessible("设置第 1 集匹配").disabled, true);
  assert.equal(f.accessible("播放第 1 集").title, "未绑定：请在资源页签选择播放资源"); assert.equal(f.byClass("chapter-player-duration")[0].textContent, "24:27"); assert.equal(f.button("重新识别文件").hidden, true); assert.equal(f.button("选择播放资源").hidden, false);
  f.button("选择播放资源").emit("click"); await flush(); assert.equal(f.chosen.length, 1); assert.equal(f.calls.length, 0);
});
test("invalid and retired cached resource indexes never enable playback", () => {
  for (const playback of [scanResult(99), scanResult(0), null]) { const f = fixture(null, { ref: REF, resources: plain(RESOURCES), chapters: plain(CHAPTERS), playback }); assert.equal(f.accessible("播放第 1 集").disabled, true); assert.equal(f.button("选择播放资源").hidden, false); }
  const empty = fixture(null, { ref: REF, resources: [], chapters: [] }); assert.equal(empty.byClass("chapter-player-empty")[0].textContent, "暂无章节信息。"); assert.equal(empty.calls.length, 0);
});
test("initial chapter numbers titles and dates follow backend projection", () => {
  const chapters = [{ ep: 0, sort: 9, name_cn: "中文标题", name: "日本語", title: "Fallback", air_date: "20260102" }, { sort: 2.5, name: "Original", date: "20260109" }, { ep: "0", sort: 0, type: 1, title: "Special", airdate: "20260116" }, { number: "invalid", ep: 3 }, { ep: "100001", sort: 5 }];
  const f = fixture(null, { ref: REF, resources: plain(RESOURCES), chapters, playback: null }); for (const number of [9, 2.5, 0, 3, 5]) assert.equal(f.accessible("播放第 " + number + " 集").disabled, true);
  assert.deepEqual(f.byClass("chapter-player-title").slice(0, 3).map(node => node.textContent), ["日本語", "Original", "Special"]); assert.equal(f.byClass("chapter-player-title")[0].title, "中文标题"); assert.deepEqual(f.byClass("chapter-player-date").slice(0, 3).map(node => node.textContent), ["2026-01-02", "2026-01-09", "2026-01-16"]);
});
test("cached automatic and confirmed rows play with current detail ref and exact saved revision", async () => {
  const statuses = ["automatic", "confirmed", "ambiguous", "missing", "unmatched", "unknown"], freshRef = { ...REF, source_sha256: "c".repeat(64) };
  const f = fixture(null, { ref: freshRef, resources: plain(RESOURCES), chapters: [], playback: scanResult(1, { chapters: statuses.map((status, index) => ({ ...CHAPTERS[0], number: index + 1, chapter_key: "c" + index, status })) }) });
  statuses.forEach((_, index) => { const play = f.accessible("播放第 " + (index + 1) + " 集"); assert.equal(play.disabled, index > 1); }); f.accessible("播放第 1 集").emit("click"); await flush();
  assert.deepEqual(f.calls[0].body, { ref: freshRef, resource_index: 1, revision: "v1", chapter_key: "c0", relative_path: "Disc 1/01.mkv" }); assert.equal(f.calls[0].route, "open"); assert.match(f.notices[0].value, /系统默认播放器/);
});
test("unavailable play buttons stay visible, explain the reason accessibly and never make requests", async () => {
  const cases = [
    { playback: null, reason: "未绑定：请在资源页签选择播放资源" },
    { playback: { resource_index: 1 }, reason: "未识别：请重新识别文件" },
    { status: "ambiguous", reason: "待选：有多个候选视频，请在设置中确认" },
    { status: "missing", reason: "缺失：绑定的视频文件已缺失，请重新识别或修改匹配" },
    ...["unmatched", "unknown", "automatic", "confirmed"].map(status => ({ status, reason: "无资源：未找到该章节的对应视频，可在设置中手动匹配" }))
  ];
  for (const item of cases) {
    const chapters = CHAPTERS.map(chapter => ({ ...chapter, status: item.status, relative_path: "" }));
    const playback = Object.hasOwn(item, "playback") ? item.playback : scanResult(1, { chapters });
    const f = fixture(null, { ref: REF, resources: plain(RESOURCES), chapters, playback }), buttons = f.byClass("chapter-player-play");
    assert.equal(buttons.length, 2);
    for (const [index, play] of buttons.entries()) {
      assert.equal(play.tagName, "button"); assert.equal(play.type, "button"); assert.equal(play.textContent, "▶");
      assert.equal(play.hidden, false); assert.equal(play.disabled, true); assert.equal(play.title, item.reason);
      assert.equal(play.attributes["aria-label"], "播放第 " + (index + 1) + " 集");
      const descriptions = f.nodes().filter(node => node.id === play.attributes["aria-describedby"]);
      assert.equal(descriptions.length, 1); assert.equal(descriptions[0].textContent, item.reason); assert.equal(descriptions[0].hidden, true);
      play.emit("click"); play.emit("click");
    }
    assert.notEqual(buttons[0].attributes["aria-describedby"], buttons[1].attributes["aria-describedby"]);
    await flush(); assert.equal(f.calls.length, 0); assert.equal(f.notices.length, 0);
  }
  assert.match(CSS, /\.chapter-player-play:disabled[^}]*color: var\(--muted\)[^}]*opacity: \.45[^}]*cursor: not-allowed/s);
});
test("a successful scan enables previously unrecognized playback and removes the unavailable description", async () => {
  const f = fixture(null, { ref: REF, resources: plain(RESOURCES), chapters: plain(CHAPTERS), playback: { resource_index: 1 } });
  const unavailable = f.accessible("播放第 1 集"); assert.equal(unavailable.disabled, true); unavailable.emit("click"); await flush(); assert.equal(f.calls.length, 0);
  await f.scan();
  const play = f.accessible("播放第 1 集"); assert.equal(play.disabled, false); assert.equal(play.title, "使用系统默认播放器播放"); assert.equal(play.attributes["aria-describedby"], undefined);
  play.emit("click"); await flush(); assert.deepEqual(f.calls.map(call => call.route), ["scan", "open"]);
});
test("cached and unbound chapter titles prefer the original language with Chinese hover text", () => {
  const chapters = [{ ...CHAPTERS[0], title: "はじまりの朝", name_cn: "开始的早晨" }, { ...CHAPTERS[1], title: "", name_cn: "只有中文" }];
  for (const playback of [null, scanResult(1, { chapters })]) {
    const f = fixture(null, { ref: REF, resources: plain(RESOURCES), chapters, playback });
    assert.deepEqual(f.byClass("chapter-player-title").map(node => node.textContent), ["はじまりの朝", "只有中文"]);
    assert.deepEqual(f.byClass("chapter-player-title").map(node => node.title), ["开始的早晨", "只有中文"]);
  }
});
test("source durations are a display-only fallback and local duration takes priority", () => {
  const chapters = [
    { ...CHAPTERS[0], duration_seconds: 1467, source_duration_seconds: 1440, source_duration: "00:24:00" },
    { ...CHAPTERS[1], duration_seconds: null, source_duration_seconds: 1440, source_duration: "00:24:00" },
    { ...CHAPTERS[1], number: 3, duration_seconds: null, source_duration_seconds: 0, source_duration: "24分钟" },
    { ...CHAPTERS[1], number: 4, duration_seconds: null, source_duration_seconds: 0, source_duration: "00:00:00" }
  ];
  const f = fixture(null, { ref: REF, resources: plain(RESOURCES), chapters: [], playback: scanResult(1, { chapters }) }), durations = f.byClass("chapter-player-duration");
  assert.deepEqual(durations.map(node => node.textContent), ["24:27", "24:00", "24分钟", "—"]);
  assert.deepEqual(durations.map(node => node.attributes["data-duration-source"]), ["local", "source", "source", "none"]);
  assert.equal(durations[0].title, "本地视频时长"); assert.match(durations[1].title, /来源章节时长/);
  assert.equal(f.detail.playback.chapters[1].duration_seconds, null); assert.equal(f.calls.length, 0);
});
test("unbound chapters show provider seconds or raw duration with disabled playback buttons", () => {
  const chapters = [{ number: 1, duration_seconds: 1440 }, { number: 2, duration_seconds: 0, duration: "约 24 分钟" }, { number: 3, duration_seconds: 0 }];
  const f = fixture(null, { ref: REF, resources: [], chapters });
  assert.deepEqual(f.byClass("chapter-player-duration").map(node => node.textContent), ["24:00", "约 24 分钟", "—"]);
  assert.deepEqual(f.byClass("chapter-player-duration").map(node => node.attributes["data-duration-source"]), ["source", "source", "none"]);
  assert.equal(f.byClass("chapter-player-play").length, 3); assert.ok(f.byClass("chapter-player-play").every(button => button.disabled)); assert.equal(f.calls.length, 0);
});
test("rescan persists refreshed snapshot and publishes its fresh detail through onSaved", async () => {
  const freshRef = { ...REF, record_sha256: "c".repeat(64) }, freshDetail = { ref: freshRef, record: { playback: "fresh" } };
  const f = fixture(route => route === "scan" ? { res: { ok: true }, data: scanResult(1, { ref: freshRef, revision: "v2", db_committed: true, detail: freshDetail, chapters: [{ ...CHAPTERS[0], duration_seconds: 1500 }], warnings: ["部分文件无时长"] }) } : undefined);
  await f.scan(); assert.deepEqual(f.calls[0].body, { ref: REF, resource_index: 1 }); assert.equal(f.calls[0].method, "POST"); assert.deepEqual(plain(f.saved[0].detail), freshDetail); assert.equal(f.byClass("chapter-player-duration")[0].textContent, "25:00");
  assert.match(f.notices[0].value, /重新识别并保存.*部分文件无时长/); assert.equal(f.notices[0].error, false); assert.equal(f.nodes().some(node => node.textContent.includes("部分文件无时长")), false);
  f.accessible("播放第 1 集").emit("click"); await flush(); assert.deepEqual(f.calls.at(-1).body.ref, freshRef); assert.equal(f.calls.at(-1).body.revision, "v2");
});
test("duration null remains a successful rescan with no standalone duration requests", async () => {
  const f = fixture(route => route === "scan" ? scanResult(1, { db_committed: true, duration_available: false, chapters: [{ ...CHAPTERS[0], duration_seconds: null }] }) : undefined); await f.scan();
  assert.equal(f.byClass("chapter-player-duration")[0].textContent, "—"); assert.equal(f.notices[0].error, false); assert.equal(f.saved.length, 1); assert.equal(f.accessible("播放第 1 集").disabled, false); assert.deepEqual(f.calls.map(call => call.route), ["scan"]);
});
test("rescan single-flight preserves cached duration and matching through a failed refresh", async () => {
  const pending = deferred(), f = fixture(route => route === "scan" ? pending.promise : undefined), refresh = f.button("重新识别文件"); refresh.emit("click"); refresh.emit("click"); await flush();
  assert.equal(f.calls.length, 1); assert.equal(f.accessible("播放第 1 集").disabled, true); assert.equal(f.byClass("chapter-player-duration")[0].textContent, "24:27"); pending.reject(Error("刷新失败")); await flush();
  assert.equal(f.notices[0].value, "刷新失败"); assert.equal(f.saved.length, 0); assert.equal(f.accessible("播放第 1 集").disabled, false); assert.equal(f.byClass("chapter-player-duration")[0].textContent, "24:27"); assert.equal(f.button("重新识别文件").disabled, false);
});
test("malformed or mismatched rescan results cannot replace persisted cache", async () => {
  for (const response of [{ ok: true, chapters: [], files: [] }, scanResult(2)]) { const f = fixture(route => route === "scan" ? response : undefined); await f.scan(); assert.equal(f.accessible("播放第 1 集").disabled, false); assert.equal(f.byClass("chapter-player-duration")[0].textContent, "24:27"); assert.equal(f.saved.length, 0); assert.equal(f.notices[0].error, true); }
});
test("opening is single-flight and failure releases controls without altering cached data", async () => {
  const pending = deferred(), f = fixture(route => route === "open" ? pending.promise : undefined), play = f.accessible("播放第 1 集"); play.emit("click"); play.emit("click"); await flush(); assert.equal(f.calls.length, 1); assert.equal(f.button("重新识别文件").disabled, true);
  pending.resolve({ ok: false, error: "文件已变化" }); await flush(); assert.equal(f.notices[0].error, true); assert.equal(f.accessible("播放第 1 集").disabled, false);
});
test("binding_allowed false blocks manual settings", async () => {
  const f = fixture(null, { ref: REF, resources: plain(RESOURCES), chapters: [], playback: scanResult(1, { chapters: [{ ...CHAPTERS[1], binding_allowed: false }] }) }), setting = f.accessible("设置第 2 集匹配"); assert.equal(setting.disabled, true); assert.match(setting.title, /唯一标识|集数重复/); setting.emit("click"); await flush(); assert.equal(f.byClass("chapter-player-dialog").length, 0); assert.equal(f.calls.length, 0);
});
test("paths appear only in matching dialog and unsaved drafts cannot alter playback", async () => {
  const f = fixture(); await f.settings(2); assert.equal(f.byClass("chapter-player-dialog")[0].open, true); assert.ok(f.nodes().some(node => node.tagName === "option" && node.textContent === "候选 · Disc 2/02.mkv")); f.input().value = "Disc 2/02.mkv"; f.input().emit("input"); assert.equal(f.calls.length, 0); assert.equal(f.accessible("播放第 2 集").disabled, true);
  f.button("取消").emit("click"); await flush(); assert.equal(f.byClass("chapter-player-dialog").length, 0); assert.equal(f.document.activeElement, f.accessible("设置第 2 集匹配"));
});
test("unsafe or unscanned paths fail inline validation without requests or status banners", async () => {
  const f = fixture(); await f.settings(); for (const value of ["C:/Outside.mkv", "/outside.mkv", "../outside.mkv", "Disc 1/../outside.mkv", "Disc 1/new.mkv"]) { f.input().value = value; f.form().emit("submit"); await flush(); assert.equal(f.byClass("chapter-player-validation")[0].hidden, false); } assert.equal(f.calls.length, 0); assert.equal(f.notices.length, 0); f.input().emit("input"); assert.equal(f.byClass("chapter-player-validation")[0].hidden, true);
});
test("manual save uses one confirmed assignment and playback switches to its fresh ref", async () => {
  const freshRef = { ...REF, record_sha256: "c".repeat(64) }, freshDetail = { ref: freshRef, record: { playback: true } };
  const f = fixture((route, body) => route === "save" ? scanResult(1, { db_committed: true, ref: freshRef, revision: "v2", detail: freshDetail, chapters: [CHAPTERS[0], { ...CHAPTERS[1], status: "confirmed", relative_path: body.assignments[0].relative_path }] }) : undefined);
  await f.settings(2); f.input().value = "Disc 2\\02.mkv"; f.form().emit("submit"); await flush(); assert.deepEqual(f.calls[0].body, { ref: REF, resource_index: 1, revision: "v1", assignments: [{ chapter_key: "c2", relative_path: "Disc 2/02.mkv" }] });
  assert.deepEqual(plain(f.saved[0].detail), freshDetail); assert.equal(f.accessible("设置第 2 集匹配").title, "已确认"); f.accessible("播放第 2 集").emit("click"); await flush(); assert.deepEqual(f.calls.at(-1).body, { ref: freshRef, resource_index: 1, revision: "v2", chapter_key: "c2", relative_path: "Disc 2/02.mkv" }); assert.equal(f.detail.ref.record_sha256, REF.record_sha256);
});
test("restoring automatic matching requires explicit confirmation of an empty assignment", async () => {
  const f = fixture(route => route === "save" ? scanResult() : undefined); await f.settings(); const select = f.byClass("chapter-player-match-field")[0].children[1]; select.value = ""; select.emit("change"); assert.equal(f.calls.length, 0); f.form().emit("submit"); await flush(); assert.equal(f.calls[0].body.assignments[0].relative_path, ""); assert.match(f.notices[0].value, /恢复自动匹配/);
});
test("committed rescan or save requiring reload clears stale playback without claiming save failure", async () => {
  for (const operation of ["scan", "save"]) for (const freshRef of [{ ...REF, source_sha256: "d".repeat(64) }, undefined]) {
    const response = { ok: true, db_committed: true, requires_rescan: true, message: "播放信息已保存，请重新加载作品。", ref: freshRef, revision: "", files: [], chapters: [] }, f = fixture(route => route === operation ? response : undefined);
    if (operation === "scan") await f.scan(); else { await f.settings(); f.form().emit("submit"); await flush(); } assert.equal(f.notices[0].value, response.message); assert.equal(f.notices[0].error, false); assert.equal(f.accessible("播放第 1 集").disabled, true); assert.equal(f.accessible("播放第 1 集").title, "未识别：请重新识别文件"); assert.equal(f.saved.length, 1); assert.equal(f.button("重新识别文件").disabled, !freshRef); assert.equal(f.byClass("chapter-player-dialog").length, 0);
  }
});
test("manual save is single-flight, prevents modal closing and preserves failed drafts", async () => {
  const pending = deferred(), f = fixture(route => route === "save" ? pending.promise : undefined); await f.settings(2); f.input().value = "Disc 1/02.mkv"; const form = f.form(); form.emit("submit"); form.emit("submit"); await flush(); assert.equal(f.calls.length, 1);
  const dialog = f.byClass("chapter-player-dialog")[0]; assert.equal(dialog.emit("cancel").defaultPrevented, true); assert.equal(dialog.open, true); pending.reject(Error("版本已变化")); await flush(); assert.equal(f.input().disabled, false); assert.equal(f.input().value, "Disc 1/02.mkv"); assert.equal(f.saved.length, 0);
});
test("missing durations render em dash and valid zero does not trigger probing", () => {
  const f = fixture(); for (const value of [null, undefined, "", -1, Infinity, NaN, "bad"]) assert.equal(f.window.NimdaChapterPlayer.durationLabel(value), "—"); assert.equal(f.window.NimdaChapterPlayer.durationLabel(3601.5), "1:00:01"); assert.equal(f.window.NimdaChapterPlayer.durationLabel(0), "0:00"); assert.equal(f.calls.length, 0);
});
test("dispose invalidates pending scan open and save and suppresses late result or failure", async () => {
  for (const operation of ["scan", "open", "save"]) for (const reject of [false, true]) {
    const pending = deferred(), f = fixture(route => route === operation ? pending.promise : undefined); if (operation === "scan") f.button("重新识别文件").emit("click"); else if (operation === "open") f.accessible("播放第 1 集").emit("click"); else { await f.settings(); f.form().emit("submit"); } await flush(); f.component.dispose(); f.component.dispose();
    if (reject) pending.reject(Error("late")); else pending.resolve(operation === "open" ? { ok: true } : scanResult()); await flush(); assert.equal(f.notices.length, 0); assert.equal(f.saved.length, 0); assert.equal(f.parent.children.length, 0); assert.equal(f.byClass("chapter-player-dialog").length, 0);
  }
});
test("untrusted names and file options render as literal text", async () => {
  const dangerous = '<img src=x onerror="alert(1)">', f = fixture(null, { ref: REF, resources: plain(RESOURCES), chapters: [], playback: scanResult(1, { chapters: [{ ...CHAPTERS[0], title: dangerous, relative_path: dangerous, candidates: [dangerous] }], files: [{ relative_path: dangerous }] }) }); assert.equal(f.byClass("chapter-player-title")[0].textContent, dangerous); await f.settings(); assert.equal(f.nodes().some(node => node.tagName === "img"), false); assert.ok(f.nodes().some(node => node.tagName === "option" && node.textContent.includes(dangerous))); assert.doesNotMatch(SOURCE, /innerHTML\s*=|window\.open|fetch\(/);
});

test("batch settings require a selected scanned resource and never implicitly scan", async () => {
  for (const playback of [null, { resource_index: 1 }, scanResult(1, { files: [] }), scanResult(1, { chapters: [{ ...CHAPTERS[0], binding_allowed: false }] })]) {
    const f = fixture(null, { ref: REF, resources: plain(RESOURCES), chapters: [], playback });
    assert.equal(f.button("批量设置").disabled, true); f.button("批量设置").emit("click"); await flush(); assert.equal(f.calls.length, 0); assert.equal(f.byClass("chapter-batch-dialog").length, 0);
  }
});

async function openBatch(f) { f.button("批量设置").emit("click"); await flush(); return f.byClass("chapter-batch-dialog")[0]; }
function pick(f, label, checked = true) { const node = f.accessible(label); assert.ok(node, label); node.checked = checked; node.emit("change"); }
function chooseBatchDefaults(f) {
  pick(f, "选择章节 01 · First"); pick(f, "选择章节 02 · Second");
  pick(f, "选择文件 Disc 1/01.mkv"); pick(f, "选择文件 Disc 1/02.mkv");
  f.button("预览对应关系").emit("click");
}

test("batch matching previews ordered selected chapters and files then saves once with unchanged ref guards", async () => {
  const freshRef = { ...REF, record_sha256: "e".repeat(64) }, f = fixture(route => route === "save" ? scanResult(1, { ref: freshRef, revision: "v2", db_committed: true }) : undefined);
  await openBatch(f); assert.equal(f.button("预览对应关系").disabled, true);
  pick(f, "选择章节 02 · Second"); pick(f, "选择章节 01 · First"); pick(f, "选择文件 Disc 1/02.mkv");
  assert.equal(f.button("预览对应关系").disabled, true); pick(f, "选择文件 Disc 1/01.mkv");
  assert.equal(f.calls.length, 0); f.button("预览对应关系").emit("click");
  assert.deepEqual(f.byClass("chapter-batch-pair-name").map(node => node.textContent), ["01 · First", "Disc 1/01.mkv", "02 · Second", "Disc 1/02.mkv"]);
  assert.equal(f.calls.length, 0); f.byClass("chapter-batch-form")[0].emit("submit"); await flush();
  assert.deepEqual(f.calls, [{ route: "save", method: "POST", body: { ref: REF, resource_index: 1, revision: "v1", assignments: [{ chapter_key: "c1", relative_path: "Disc 1/01.mkv" }, { chapter_key: "c2", relative_path: "Disc 1/02.mkv" }] } }]);
  assert.equal(f.byClass("chapter-batch-dialog").length, 0); assert.match(f.notices[0].value, /批量保存 2 项/); assert.equal(f.saved.length, 1); assert.equal(f.document.activeElement, f.button("批量设置"));
  f.accessible("播放第 1 集").emit("click"); await flush(); assert.equal(f.calls.at(-1).body.revision, "v2"); assert.deepEqual(f.calls.at(-1).body.ref, freshRef);
});

test("batch selection supports explicit file and chapter ordering without guessing ambiguous candidates", async () => {
  const f = fixture(route => route === "save" ? scanResult() : undefined); await openBatch(f); chooseBatchDefaults(f);
  f.accessible("上移第 2 项文件").emit("click");
  assert.deepEqual(f.byClass("chapter-batch-pair-name").map(node => node.textContent), ["01 · First", "Disc 1/02.mkv", "02 · Second", "Disc 1/01.mkv"]);
  f.accessible("上移第 2 项章节").emit("click"); assert.equal(f.byClass("chapter-batch-pair-name")[0].textContent, "02 · Second");
  f.button("恢复默认顺序").emit("click"); assert.equal(f.byClass("chapter-batch-pair-name")[0].textContent, "01 · First");
  f.button("返回选择").emit("click"); assert.equal(f.accessible("选择章节 02 · Second").checked, true); assert.equal(f.accessible("选择文件 Disc 2/02.mkv").checked, false); assert.equal(f.calls.length, 0);
  f.button("预览对应关系").emit("click"); f.byClass("chapter-batch-form")[0].emit("submit"); await flush(); assert.equal(f.calls[0].body.assignments[1].relative_path, "Disc 1/02.mkv");
});

test("batch choices persist across pages and paths use natural numeric sorting", async () => {
  const chapters = Array.from({ length: 12 }, (_, index) => ({ ...CHAPTERS[0], chapter_key: "c" + (index + 1), number: index + 1, title: "Chapter " + (index + 1) }));
  const files = Array.from({ length: 12 }, (_, index) => ({ relative_path: "Disc/" + (12 - index) + ".mkv" }));
  const f = fixture(null, { ref: REF, resources: plain(RESOURCES), chapters, playback: scanResult(1, { chapters, files }) }); await openBatch(f);
  assert.deepEqual(f.byClass("chapter-batch-picker")[1].children.at(-1).children.map(node => node.children[1].textContent), Array.from({ length: 8 }, (_, index) => "Disc/" + (index + 1) + ".mkv"));
  pick(f, "选择章节 01 · Chapter 1"); pick(f, "选择文件 Disc/1.mkv");
  f.byClass("chapter-batch-pager")[0].children[2].emit("click"); f.byClass("chapter-batch-pager")[1].children[2].emit("click");
  pick(f, "选择章节 12 · Chapter 12"); pick(f, "选择文件 Disc/12.mkv");
  f.button("预览对应关系").emit("click"); assert.deepEqual(f.byClass("chapter-batch-pair-name").map(node => node.textContent), ["01 · Chapter 1", "Disc/1.mkv", "12 · Chapter 12", "Disc/12.mkv"]);
  f.button("返回选择").emit("click"); assert.equal(f.accessible("选择章节 12 · Chapter 12").checked, true); assert.equal(f.accessible("选择文件 Disc/12.mkv").checked, true); assert.equal(f.calls.length, 0);
});

test("batch save only sends changed assignments and excludes unbindable chapters", async () => {
  const chapters = [{ ...CHAPTERS[0], status: "confirmed" }, CHAPTERS[1], { ...CHAPTERS[0], chapter_key: "invalid", number: 3, binding_allowed: false }];
  const f = fixture(route => route === "save" ? scanResult() : undefined, { ref: REF, resources: plain(RESOURCES), chapters, playback: scanResult(1, { chapters }) });
  await openBatch(f); assert.equal(f.accessible("选择章节 03 · First"), undefined); chooseBatchDefaults(f); assert.ok(f.button("确认保存 1 项"));
  f.byClass("chapter-batch-form")[0].emit("submit"); await flush(); assert.deepEqual(f.calls[0].body.assignments, [{ chapter_key: "c2", relative_path: "Disc 1/02.mkv" }]);
});

test("batch save is single flight, protects modal closing, and preserves choices after failed validation", async () => {
  const pending = deferred(), f = fixture(route => route === "save" ? pending.promise : undefined), dialog = await openBatch(f); chooseBatchDefaults(f);
  const form = f.byClass("chapter-batch-form")[0]; form.emit("submit"); form.emit("submit"); await flush(); assert.equal(f.calls.length, 1); assert.equal(f.button("取消").disabled, true); assert.equal(dialog.emit("cancel").defaultPrevented, true); assert.equal(dialog.open, true);
  pending.reject(Error("作品数据或文件已变化")); await flush(); assert.equal(f.byClass("chapter-batch-dialog").length, 1); assert.equal(f.button("确认保存 2 项").disabled, false); assert.equal(f.byClass("chapter-batch-pair-name")[3].textContent, "Disc 1/02.mkv"); assert.equal(f.saved.length, 0); assert.equal(f.notices[0].error, true);
  f.button("取消").emit("click"); assert.equal(f.byClass("chapter-batch-dialog").length, 0); assert.equal(f.document.activeElement, f.button("批量设置"));
});

test("batch disposal suppresses late responses and removes its independent dialog", async () => {
  for (const reject of [true, false]) {
    const pending = deferred(), f = fixture(route => route === "save" ? pending.promise : undefined); await openBatch(f); chooseBatchDefaults(f); f.byClass("chapter-batch-form")[0].emit("submit"); await flush();
    f.component.dispose(); assert.equal(f.byClass("chapter-batch-dialog").length, 0); if (reject) pending.reject(Error("late")); else pending.resolve(scanResult()); await flush(); assert.equal(f.notices.length, 0); assert.equal(f.saved.length, 0);
  }
});

test("batch display reuses configured Chinese preference and renders paths as literal text", async () => {
  const dangerous = '<img onerror="bad()">.mkv', chapters = [{ ...CHAPTERS[0], name: "Original", name_cn: "中文章节" }];
  const f = fixture(null, { ref: REF, resources: plain(RESOURCES), chapter_title_preference: "chinese", chapters, playback: scanResult(1, { chapters, files: [{ relative_path: dangerous }] }) });
  await openBatch(f); assert.ok(f.accessible("选择章节 01 · 中文章节")); assert.ok(f.accessible("选择文件 " + dangerous)); assert.equal(f.nodes().some(node => node.tagName === "img"), false); assert.doesNotMatch(BATCH_SOURCE, /innerHTML|fetch\(|window\.open/);
});

test("batch preview makes replacement of confirmed file bindings explicit", async () => {
  const chapters = [{ ...CHAPTERS[0], status: "confirmed" }, CHAPTERS[1]];
  const f = fixture(null, { ref: REF, resources: plain(RESOURCES), chapters, playback: scanResult(1, { chapters }) }); await openBatch(f); chooseBatchDefaults(f);
  assert.equal(f.byClass("chapter-batch-existing").length, 0);
  f.accessible("上移第 2 项文件").emit("click");
  assert.equal(f.byClass("chapter-batch-existing")[0].textContent, "将替换已确认文件：Disc 1/01.mkv");
  assert.equal(f.byClass("chapter-batch-existing")[0].title, "将替换已确认文件：Disc 1/01.mkv");
  assert.equal(f.calls.length, 0);
});

test("batch reorder restores keyboard focus to the moved item including page boundaries", async () => {
  const chapters = Array.from({ length: 10 }, (_, index) => ({ ...CHAPTERS[0], chapter_key: "c" + index, number: index + 1, title: "Chapter " + index }));
  const files = Array.from({ length: 10 }, (_, index) => ({ relative_path: "Disc/" + (index + 1) + ".mkv" }));
  const f = fixture(null, { ref: REF, resources: plain(RESOURCES), chapters, playback: scanResult(1, { chapters, files }) }); await openBatch(f);
  f.byClass("chapter-batch-picker-heading")[0].children[1].emit("click"); f.byClass("chapter-batch-picker-heading")[1].children[1].emit("click"); f.button("预览对应关系").emit("click");
  f.accessible("下移第 8 项文件").emit("click");
  assert.equal(f.document.activeElement, f.accessible("下移第 9 项文件")); assert.equal(f.byClass("chapter-batch-pair-name")[1].textContent, "Disc/8.mkv");
  f.accessible("下移第 9 项文件").emit("click"); assert.equal(f.document.activeElement, f.accessible("上移第 10 项文件"));
  f.accessible("上移第 9 项章节").emit("click"); assert.equal(f.document.activeElement, f.accessible("上移第 8 项章节"));
  assert.equal(f.calls.length, 0);
});
