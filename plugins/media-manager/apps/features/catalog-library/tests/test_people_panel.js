"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");
const source = fs.readFileSync(path.join(__dirname, "../frontend/people-panel.js"), "utf8");
const chapterTitles = fs.readFileSync(path.join(__dirname, "../frontend/chapter-title-format.js"), "utf8");
const flush = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
function render(options = {}) {
  let activeElement;
  class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; this.handlers = {}; this.textContent = ""; }
    appendChild(child) { this.children.push(child); child.parent = this; return child; }
    setAttribute(key, value) { this.attrs[key] = value; }
    addEventListener(key, handler) { this.handlers[key] = handler; }
    remove() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); this.isConnected = false; }
    focus() { activeElement = this; }
    showModal() { this.open = true; }
    close() { this.open = false; if (this.handlers.close) this.handlers.close(); }
  }
  const window = {}, document = { createElement: tag => new Element(tag), body: new Element("body"), get activeElement() { return activeElement; } }, parent = new Element("section");
  document.body.appendChild(parent);
  vm.runInNewContext(chapterTitles, { window });
  vm.runInNewContext(source, { window, document });
  window.NimdaPeoplePanel.render(parent, { detail: {}, state: {}, bindings: [], ...options });
  const nodes = (node = document.body) => [node, ...node.children.flatMap(child => nodes(child))];
  return { parent, document, window, all: nodes, get nodes() { return nodes(); }, api: window.NimdaPeoplePanel, get focused() { return activeElement; }, button: label => nodes().find(node => node.tag === "button" && node.textContent === label) };
}

test("localized Korean names honor preference and retain every explicitly classified language", () => {
  const entity = { name: "유인식", name_cn: "刘仁植", summary: "A director", aliases: ["유인식"], localized: {
    name: { "zh-Hans": "刘仁植", ko: "유인식", "ko-Latn": "Yu In-sik", en: "Yoo In-sik" },
    summary: { "zh-Hans": "韩国导演", ko: "대한민국 감독", en: "A director" }, aliases: { ko: ["유인식"], "zh-Hans": ["柳仁植"] }
  } };
  const f = render(), before = JSON.stringify(entity);
  assert.deepEqual(JSON.parse(JSON.stringify(f.api.entityDisplay(entity, "chinese"))), { text: "刘仁植", tooltip: "유인식" });
  assert.deepEqual(JSON.parse(JSON.stringify(f.api.entityDisplay(entity, "original"))), { text: "유인식", tooltip: "刘仁植" });
  assert.equal(f.api.summaryDisplay(entity, "chinese").text, "韩国导演");
  const fields = f.all(f.api.entityProfile(entity, "chinese"));
  for (const label of ["名称（韩语）", "名称（韩语罗马字）", "名称（英语）", "别名（韩语）", "简介（简体中文）", "简介（英语）"]) assert.ok(fields.some(row => row.textContent === label), label);
  assert.equal(f.api.localizedEntries(entity, "name", "original").filter(row => row.value === "Yoo In-sik").length, 1);
  assert.equal(JSON.stringify(entity), before);
});

test("shared actor cards retain distinct portrayal stages without inventing implicit ages", () => {
  const actor = { id: "person_actor", name: "Actor", name_cn: "演员" };
  const character = { id: "person_character", name: "Role", name_cn: "角色" };
  const f = render({ detail: { people: { cast: [
    { ...actor, character_name: "Role", character_name_cn: "角色", character, qualifier: { kind: "age", value: "teen", explicit: true } },
    { ...actor, character_name: "Role", character_name_cn: "角色", character, qualifier: { kind: "age", value: "adult", explicit: true } }
  ] } } });
  assert.deepEqual(f.nodes.filter(row => row.className === "library-meta library-portrayal-stage").map(row => row.textContent), ["少年", "成年"]);
  assert.equal(f.api.qualifierLabel({ kind: "age", value: "child", explicit: false }), "");
  assert.equal(f.api.qualifierLabel({ kind: "unknown", value: "child", explicit: true }), "");
});

test("official English is distinct from unclassified Latin and explicit empty language slots suppress fallback", () => {
  const f = render();
  const english = { name: "Lilly", name_cn: "莉莉", localized: { name: { en: "Lilly", "zh-Hans": "莉莉" } } };
  assert.equal(f.api.entityDisplay(english, "chinese").tooltip, "Lilly");
  assert.deepEqual(Array.from(f.api.localizedEntries({ name: "Yu In-sik", localized: {} }, "name"), row => row.language), ["und-Latn"]);
  const cleared = { name: "유인식", name_cn: "刘仁植", summary: "旧简介", aliases: ["유인식"], field_sources: { summary: { language: "zh-Hans" } }, localized: { name: { ko: "", "zh-Hans": "" }, summary: { "zh-Hans": "" }, aliases: { ko: [] } } };
  assert.equal(f.api.entityDisplay(cleared, "chinese").text, "人物资料缺失");
  assert.equal(f.api.summaryDisplay(cleared, "chinese").text, "");
  assert.equal(f.api.localizedEntries(cleared, "aliases").length, 0);
  assert.equal(f.api.fieldValue(cleared, "localized.name.ko"), "");
  assert.equal(f.api.fieldValue(cleared, "localized.name.__proto__"), undefined);
});

test("localized names summaries and aliases are text-only even when values contain markup", () => {
  const f = render(), profile = f.api.entityProfile({ name: "safe", localized: { name: { en: "<b>name</b>" }, summary: { en: "<script>bad</script>" }, aliases: { en: ["<img src=x>"] } } }, "original");
  assert.ok(f.all(profile).some(row => row.textContent === "<script>bad</script>"));
  assert.equal(f.all(profile).some(row => ["script", "img", "b"].includes(row.tag)), false);
});

test("traditional and unverified Chinese mirrors never create a guessed simplified duplicate", () => {
  const f = render();
  for (const language of ["zh-Hant", "und"]) {
    const entity = { name: "김부장", name_cn: "金部長", localized: { name: { ko: "김부장", [language]: "金部長" } } };
    const entries = f.api.localizedEntries(entity, "name", "chinese");
    assert.equal(entries.filter(row => row.value === "金部長").length, 1);
    assert.equal(entries.some(row => row.language === "zh-Hans"), false);
    assert.deepEqual(JSON.parse(JSON.stringify(f.api.entityDisplay(entity, "chinese"))), { text: "金部長", tooltip: "김부장" });
  }
  const verified = { name: "김부장", name_cn: "金部長", localized: { name: { ko: "김부장", "zh-Hans": "金部长", "zh-Hant": "金部長" } } };
  assert.equal(f.api.entityDisplay(verified, "chinese").text, "金部长");
});

test("original preference follows evidenced original language rather than a fixed Korean-first order", () => {
  const f = render();
  const japanese = { name: "田中理恵", name_cn: "田中理惠", localized: { name: { ja: "田中理恵", ko: "다나카 리에", "zh-Hans": "田中理惠" }, summary: { ja: "日本語", ko: "한국어" } } };
  assert.equal(f.api.entityDisplay(japanese, "original").text, "田中理恵");
  assert.equal(f.api.entityDisplay(japanese, "chinese").tooltip, "田中理恵");
  assert.equal(f.api.summaryDisplay(japanese, "original").text, "日本語");
  const english = { name: "Lilly", localized: { name: { en: "Lilly", ko: "릴리" } } };
  assert.equal(f.api.entityDisplay(english, "original").text, "Lilly");
  const korean = { name: "Yu In-sik", name_cn: "刘仁植", localized: { name: { ko: "유인식", "zh-Hans": "刘仁植", "und-Latn": "Yu In-sik" } } };
  assert.equal(f.api.entityDisplay(korean, "original").text, "유인식");
  assert.equal(f.api.entityDisplay(korean, "chinese").tooltip, "유인식");
  const sourceEvidence = { name: "田中理恵", source_refs: [{ provider: "bangumi" }], localized: { name: { ja: "新しい名", ko: "번역" } } };
  assert.equal(f.api.entityDisplay(sourceEvidence, "original").text, "新しい名");
});

test("classified aliases are not repeated under a guessed fallback language", () => {
  const f = render(), entity = { name: "김", aliases: ["Gim"], localized: { name: { ko: "김" }, aliases: { "ko-Latn": ["Gim"] } } };
  assert.deepEqual(Array.from(f.api.localizedEntries(entity, "aliases", "chinese"), row => row.language), ["ko-Latn"]);
});

test("quoted Korean names do not turn Chinese biography text into a Korean translation", () => {
  const f = render(), summary = "苏志燮（소지섭），韩国著名演员。";
  const localized = f.api.localizedEntries({ name: "소지섭", summary, field_sources: { summary: { language: "zh-Hans" } } }, "summary", "chinese");
  assert.equal(localized[0].language, "zh-Hans"); assert.equal(localized[0].value, summary);
  assert.equal(f.api.localizedEntries({ name: "소지섭", summary }, "summary", "chinese")[0].language, "und");
  const korean = f.api.localizedEntries({ name: "소지섭", summary: "소지섭(金某)，한국의 배우이다", field_sources: { summary: { language: "zh-Hans" } } }, "summary", "chinese");
  assert.equal(korean[0].language, "ko");
  assert.equal(f.api.localizedEntries({ name: "田中", summary: "日本の声優" }, "summary", "original")[0].language, "ja");
});

test("missing preferred languages fall back to retained evidence without dropping other language versions", () => {
  const f = render(), native = { name: "Manager Kim", localized: { name: { ko: "김부장", "und-Latn": "Manager Kim" }, summary: { en: "A manager" } } };
  assert.equal(f.api.entityDisplay(native, "chinese").text, "김부장");
  assert.equal(f.api.summaryDisplay(native, "chinese").text, "A manager");
  const chinese = { name: "金部长", localized: { name: { "zh-Hans": "金部长" } } };
  assert.equal(f.api.entityDisplay(chinese, "original").text, "金部长");
});

test("separate same-name role cards remain independently clickable until explicitly merged", () => {
  const person = { id: "person-one", type: "person", name: "演员" }, character = { id: "character-one", type: "character", name: "角色" };
  const first = { person, character, character_name: "角色" }, second = { ...first, character: { ...character, id: "character-two" } };
  const f = render({ detail: { people: { cast: [first, second, first] } } });
  const roles = f.nodes.filter(node => node.tag === "button" && node.textContent === "角色"); assert.equal(roles.length, 2);
  const opened = []; f.window.NimdaPersonProfile = { open(options) { opened.push(options.personId); } };
  roles.forEach(role => role.handlers.click()); assert.deepEqual(opened, ["character-one", "character-two"]);
});

test("cast role links retain work-specific names and explicit translation clears", () => {
  const character = { id: "character-one", type: "character", name: "Global Role", name_cn: "全库中文名" };
  const f = render({ preference: "chinese", detail: { chapter_title_preference: "chinese", people: { cast: [{ person: { id: "person-one", type: "person", name: "演员" }, character, character_name: "Manual role", character_name_cn: "" }] } } });
  assert.ok(f.button("Manual role")); assert.equal(f.button("全库中文名"), undefined);
  let selected; f.window.NimdaPersonProfile = { open(options) { selected = options.personId; } };
  f.button("Manual role").handlers.click(); assert.equal(selected, character.id);
});

test("cast role labels use shared localized names only with the explicit override projection contract", () => {
  const character = { id: "role-shared", type: "character", name: "Old Latin Role", name_cn: "旧角色名", localized: { name: { ko: "새 역할", "zh-Hans": "共享角色新名", "und-Latn": "Old Latin Role" } } };
  function cast(preference, overrides, values = {}) {
    const entry = { person: { id: "actor", name: "Actor" }, character, character_name: "Old Latin Role", character_name_cn: "旧作品角色名", ...values };
    if (overrides !== undefined) entry.character_name_overrides = overrides;
    return render({ detail: { chapter_title_preference: preference, people: { cast: [entry] } } });
  }
  for (const preference of ["chinese", "original"]) {
    const primary = preference === "chinese" ? "共享角色新名" : "새 역할", secondary = preference === "chinese" ? "새 역할" : "共享角色新名";
    const fresh = cast(preference, []); assert.equal(fresh.button(primary).title, secondary);
    const translated = cast(preference, ["character_name_cn"], { character_name_cn: "作品手填中文" });
    assert.equal(translated.button(preference === "chinese" ? "作品手填中文" : "새 역할").title, preference === "chinese" ? "새 역할" : "作品手填中文");
    const original = cast(preference, ["character_name"], { character_name: "Work-local name" });
    assert.equal(original.button(preference === "chinese" ? "共享角色新名" : "Work-local name").title, preference === "chinese" ? "Work-local name" : "共享角色新名");
    const clearedChinese = cast(preference, ["character_name_cn"], { character_name_cn: "" });
    assert.equal(clearedChinese.button("새 역할").title, "새 역할");
    const clearedOriginal = cast(preference, ["character_name"], { character_name: "" });
    assert.equal(clearedOriginal.button("共享角色新名").title, "共享角色新名");
    const oldResponse = cast(preference, undefined);
    assert.equal(oldResponse.button(preference === "chinese" ? "旧作品角色名" : "Old Latin Role").title, preference === "chinese" ? "Old Latin Role" : "旧作品角色名");
  }
  const bothCleared = cast("chinese", ["character_name", "character_name_cn"], { character_name: "", character_name_cn: "" });
  assert.equal(bothCleared.nodes.some(row => row.className === "library-actor-role"), false);
  const projectedOnly = cast("chinese", [], { character_name: "", character_name_cn: "" });
  assert.equal(projectedOnly.button("共享角色新名").title, "새 역할");
});

test("people tab opens character and CV details through a shared modal with safe text", () => {
  const f = render({ detail: { people: { characters: [{ name: "ちぃ", name_cn: "叽", role: "主角", summary: "<script>bad</script>", actors: [{ name: "田中理恵", summary: "声优资料", infobox: [{ key: "生日", value: "1月3日" }] }] }], persons: [] } } });
  assert.equal(f.button("ちぃ").title, "叽");
  assert.equal(f.button("ちぃ").attrs["aria-haspopup"], "dialog");
  assert.ok(f.button("田中理恵"));
  assert.equal(f.nodes.some(node => node.tag === "details"), false);
  f.button("ちぃ").handlers.click();
  assert.ok(f.nodes.some(node => node.textContent === "<script>bad</script>"));
  assert.equal(f.nodes.some(node => ["img", "script", "iframe"].includes(node.tag)), false);
  f.button("田中理恵").handlers.click();
  assert.equal(f.nodes.filter(node => node.tag === "dialog").length, 1);
  assert.ok(f.nodes.some(node => node.tag === "dd" && node.textContent === "1月3日"));
});

test("actors, characters, voice actors, staff and dialogs share chapter language priority", () => {
  const avatar_url = "/api/catalog-library/people/assets/" + "a".repeat(64) + ".jpg";
  const actor = { id: "actor", name: "하석진", name_cn: "河锡辰", avatar_url };
  const voiceActor = { id: "voice", name: "田中理恵", name_cn: "田中理惠", avatar_url };
  const character = { id: "character", name: "ちぃ", name_cn: "叽", actors: [voiceActor], avatar_url };
  const staff = { id: "staff", name: "김정호", name_cn: "金正浩", role: "导演", avatar_url };
  const people = { cast: [{ person: actor, character_name: "李载仁" }], characters: [character], persons: [staff] }, before = JSON.stringify(people);
  for (const preference of ["chinese", "original"]) {
    const preferred = preference === "chinese" ? "name_cn" : "name", alternative = preference === "chinese" ? "name" : "name_cn";
    const f = render({ detail: { chapter_title_preference: preference, people } });
    for (const entity of [actor, character, voiceActor, staff]) {
      const link = f.button(entity[preferred]); assert.ok(link); assert.equal(link.title, entity[alternative]);
      link.handlers.click();
      const heading = f.nodes.find(node => node.className === "library-person-dialog-name");
      assert.equal(heading.textContent, entity[preferred]); assert.equal(heading.title, entity[alternative]);
      const profile = f.nodes.find(node => node.className === "library-person-profile");
      assert.ok(profile.children.some(node => node.textContent === entity[alternative]));
      assert.equal(profile.children.some(node => node.textContent === entity[preferred]), false);
      const dialog = f.nodes.find(node => node.tag === "dialog");
      assert.ok(f.all(dialog).some(node => node.tag === "img" && node.alt === entity[preferred]));
    }
    f.api.dispose();
    assert.ok(f.nodes.filter(node => node.tag === "img").every(node => [actor[preferred], character[preferred]].includes(node.alt)));
    assert.ok(f.nodes.some(node => node.textContent === "饰演 李载仁"));
  }
  assert.equal(JSON.stringify(people), before);
});

test("missing people names fall back per language without chapter placeholders", () => {
  for (const preference of ["chinese", "original"]) {
    const f = render({ detail: { chapter_title_preference: preference, people: { persons: [
      { id: "cn", name: "  ", name_cn: "  中文名  " }, { id: "original", name: "  原名  ", name_cn: "\t" }, { id: "missing" }
    ] } } });
    for (const name of ["中文名", "原名", "人物资料缺失"]) {
      assert.equal(f.button(name).title, name); f.button(name).handlers.click();
      const heading = f.nodes.find(node => node.className === "library-person-dialog-name");
      assert.equal(heading.textContent, name); assert.equal(heading.title, name);
    }
    assert.equal(f.nodes.some(node => node.textContent.includes("未命名章节")), false);
  }
});

test("cast roles use the same language priority and official-name hover as people", () => {
  const cast = [
    { id: "korean", name: "하석진", name_cn: "河锡辰", character_name: "이재인", character_name_cn: "李宰仁" },
    { id: "english", name: "David Smith", name_cn: "大卫·史密斯", character_name: "Michael", character_name_cn: "迈克尔" },
    { id: "original-only", name: "Jane", character_name: "Jane" },
    { id: "chinese-only", name: "角色演员", character_name_cn: "中文角色" },
    { id: "missing", name: "未知角色演员" }
  ], before = JSON.stringify(cast);
  for (const preference of ["chinese", "original"]) {
    const f = render({ detail: { chapter_title_preference: preference, people: { cast } } });
    const roles = f.nodes.filter(node => node.className === "library-actor-role");
    assert.equal(roles.length, 4);
    assert.deepEqual(roles.map(node => [node.textContent, node.title]), preference === "chinese" ? [
      ["饰演 李宰仁", "이재인"], ["饰演 迈克尔", "Michael"], ["饰演 Jane", "Jane"], ["饰演 中文角色", "中文角色"]
    ] : [
      ["饰演 이재인", "李宰仁"], ["饰演 Michael", "迈克尔"], ["饰演 Jane", "Jane"], ["饰演 中文角色", "中文角色"]
    ]);
    assert.equal(f.button(preference === "chinese" ? "大卫·史密斯" : "David Smith").title,
      preference === "chinese" ? "David Smith" : "大卫·史密斯");
  }
  assert.equal(JSON.stringify(cast), before);
});

test("combined source previews display translated roles without merging distinct scopes", () => {
  const f = render(), preview = { sources: [1, 2].map(season => ({ provider: "tmdb", subject_id: 10, season_number: season,
    people: { cast: [{ id: "actor", name: "하석진", name_cn: "河锡辰", character_name: "이재인", character_name_cn: "李宰仁" }] },
    changes: [{ id: "actor", action: "update", name: "하석진" }] })) };
  f.api.renderPreview(f.parent, preview, "chinese");
  assert.equal(f.nodes.filter(node => node.textContent === "饰演 李宰仁" && node.title === "이재인").length, 2);
  assert.equal(f.nodes.filter(node => node.textContent === "更新 · 河锡辰" && node.title === "하석진").length, 2);
  for (const season of [1, 2]) assert.ok(f.nodes.some(node => node.textContent === "TMDB · 条目 10 · 第 " + season + " 季 · 人物更新预览"));
});

test("people previews use complete names by identity for every entity and keep raw diffs unchanged", () => {
  const actor = { id: "actor", name: "하석진", name_cn: "河锡辰" }, voice = { id: "voice", name: "田中理恵", name_cn: "田中理惠" };
  const character = { id: "character", name: "ちぃ", name_cn: "叽", actors: [voice] }, staff = { id: "staff", name: "김정호", name_cn: "金正浩" };
  const entities = [actor, voice, character, staff];
  const preview = { people: { cast: [{ person: actor }], characters: [character], persons: [staff] }, changes: entities.map(entity => ({ id: entity.id, name: entity.name, action: "update" })) };
  preview.changes.push({ id: "diff-only", name: "이름", action: "create", diff: [{ field: "name_cn", before: "旧中文名", after: "新中文名" }] });
  preview.changes.push({ id: "missing", action: "keep" });
  const before = JSON.stringify(preview);
  for (const preference of ["chinese", "original"]) {
    const f = render({ detail: { chapter_title_preference: preference }, state: { preview } });
    for (const entity of entities) {
      const preferred = preference === "chinese" ? entity.name_cn : entity.name, alternative = preference === "chinese" ? entity.name : entity.name_cn;
      const summary = f.nodes.find(node => node.textContent === "更新 · " + preferred);
      assert.ok(summary); assert.equal(summary.title, alternative); assert.equal(f.button(preferred).title, alternative);
    }
    const diffName = preference === "chinese" ? "新中文名" : "이름", diffHover = preference === "chinese" ? "이름" : "新中文名";
    assert.equal(f.nodes.find(node => node.textContent === "新增 · " + diffName).title, diffHover);
    assert.ok(f.nodes.some(node => node.tag === "pre" && node.textContent === "旧中文名"));
    assert.ok(f.nodes.some(node => node.tag === "pre" && node.textContent === "新中文名"));
    assert.equal(f.nodes.find(node => node.textContent === "保留 · missing").title, "missing");
  }
  assert.equal(JSON.stringify(preview), before);
});

test("embedded preview accepts the caller's language priority and dialogs retain it", () => {
  const f = render(), preview = { people: { cast: [{ id: "a", name: "하석진", name_cn: "河锡辰" }] }, changes: [{ id: "a", action: "update", name: "하석진" }] };
  f.api.renderPreview(f.parent, preview, "chinese");
  assert.equal(f.button("河锡辰").title, "하석진");
  assert.equal(f.nodes.find(node => node.textContent === "更新 · 河锡辰").title, "하석진");
  f.button("河锡辰").handlers.click();
  assert.equal(f.nodes.find(node => node.className === "library-person-dialog-name").textContent, "河锡辰");
});
test("without a source the panel offers association and never an enabled update", () => {
  const f = render(); assert.ok(f.button("关联作品来源")); assert.equal(f.button("关联 Bangumi 来源"), undefined); assert.equal(f.button("更新人物信息"), undefined);
  assert.ok(f.nodes.some(node => node.textContent === "暂无人物信息"));
});
test("multiple sources are explicit, deduplicated, and disable update before selection", () => {
  const f = render({ bindings: [{ external_id: "1" }, { external_id: "1" }, { external_id: "2" }] });
  assert.equal(f.nodes.filter(node => node.tag === "option").length, 3);
  assert.equal(f.button("更新人物信息").disabled, true);
});
test("preview confirmation is separate from fetch and uses the exact displayed preview", async () => {
  const preview = { changes: [{ action: "create", name: "角色A", changed_fields: ["name"] }], people: { characters: [], persons: [] } };
  let applied;
  const f = render({ state: { preview }, bindings: [{ external_id: "1" }], onApply: value => { applied = value; } });
  assert.equal(applied, undefined); f.button("确认保存人物与关联").handlers.click(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(applied, preview); assert.ok(f.nodes.some(node => node.textContent.includes("新增 · 角色A")));
});
test("pending operations disable source changes and both confirmation buttons", () => {
  const f = render({ busy: true, state: { preview: { changes: [] } }, bindings: [{ external_id: "1" }, { external_id: "2" }] });
  assert.ok(f.nodes.filter(node => node.tag === "button").every(node => node.disabled));
  assert.equal(f.nodes.find(node => node.tag === "select").disabled, true);
});

test("partial warnings are deduplicated and aggregated, with details in operation center", () => {
  const f = render({ state: { preview: { warnings: ["birth_mon 无效，已留空", "birth_mon 无效，已留空", { message: "声优详情超时，保留已有资料" }], changes: [] } }, bindings: [{ external_id: "1" }] });
  assert.ok(f.nodes.some(node => node.textContent === "读取提示（2）· 有效资料仍可保存，具体内容见处理详情。"));
  assert.equal(f.nodes.some(node => node.textContent.includes("birth_mon")), false);
  assert.equal(f.button("确认保存人物与关联").disabled, false);
});

test("roles use work relations with main/support/cameo ordering and deduplicate within each role", () => {
  const f = render({ detail: { people: {
    characters: [{ id: "c", name: "客串C", role: "客串" }, { id: "b", name: "配角B", relation: "配角" }, { id: "a", name: "主角A", role: "主角" }, { id: "a", name: "主角A", role: "主角" }],
    persons: [{ id: "p", name: "A", role: "分镜", career: ["director"] }, { id: "p", name: "A", role: "分镜" }, { id: "q", name: "B", role: "分镜" }, { id: "p", name: "A", role: "导演" }, { id: "r", name: "C", career: ["producer"] }]
  } } });
  assert.deepEqual(f.nodes.filter(node => node.tag === "h3").map(node => node.textContent), ["主角", "配角", "客串", "制作人员"]);
  assert.equal(f.nodes.filter(node => node.tag === "button" && node.textContent === "主角A").length, 1);
  assert.equal(f.nodes.filter(node => node.tag === "button" && node.textContent === "A").length, 2);
  assert.deepEqual(f.nodes.filter(node => node.className === "library-staff-role").map(node => node.textContent), ["分镜：", "导演：", "其他职员："]);
  assert.equal(f.nodes.some(node => node.textContent === "producer" || node.textContent === "director"), false);
});

test("long staff lists expand and collapse accessibly without removing names", async () => {
  const persons = Array.from({ length: 10 }, (_, i) => ({ id: "p" + i, name: "Staff " + i, role: "分镜" }));
  const f = render({ detail: { people: { persons } } }), toggle = f.button("展开全部（10）");
  const entries = f.nodes.filter(node => node.className === "library-staff-entry");
  assert.equal(entries.filter(node => !node.hidden).length, 6);
  assert.equal(toggle.attrs["aria-expanded"], "false");
  assert.ok(f.nodes.some(node => node.id === toggle.attrs["aria-controls"]));
  toggle.handlers.click(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(entries.filter(node => !node.hidden).length, 10); assert.equal(toggle.textContent, "收起");
  assert.equal(toggle.attrs["aria-expanded"], "true");
  toggle.handlers.click(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(entries.filter(node => !node.hidden).length, 6);
});

test("only local people assets create images and failed images reveal a placeholder", () => {
  const valid = "/api/catalog-library/people/assets/" + "a".repeat(64) + ".jpg";
  const urls = [valid, "https://lain.bgm.tv/pic/crt/a.jpg", "javascript:alert(1)", "data:image/svg+xml,bad", "//evil.invalid/a.jpg", valid + "?redirect=evil", valid + "#x", valid.replace("assets/", "assets/../"), valid.replace(".jpg", ".svg")];
  const f = render({ detail: { people: { characters: urls.map((avatar_url, i) => ({ name: "角色" + i, role: "主角", avatar_url })) } } });
  const images = f.nodes.filter(node => node.tag === "img"); assert.equal(images.length, 1); assert.equal(images[0].src, valid);
  assert.equal(images[0].loading, "lazy"); assert.equal(images[0].alt, "角色0");
  const fallback = images[0].parent.children.find(node => node.className === "library-person-photo-fallback");
  assert.equal(fallback.hidden, true); images[0].handlers.error(); assert.equal(images[0].hidden, true); assert.equal(fallback.hidden, false);
});

test("shared modal is labeled, traps focus, closes on Escape and restores its opener", () => {
  const f = render({ detail: { people: { persons: [{ name: "AAA", role: "分镜", summary: "简介" }] } } }), trigger = f.button("AAA");
  trigger.handlers.click();
  const dialog = f.nodes.find(node => node.tag === "dialog");
  assert.equal(dialog.open, true); assert.equal(dialog.attrs["role"], "dialog"); assert.equal(dialog.attrs["aria-modal"], "true");
  assert.equal(f.nodes.find(node => node.id === dialog.attrs["aria-labelledby"]).textContent, "AAA");
  assert.equal(f.focused, f.button("关闭"));
  let prevented = 0;
  dialog.handlers.keydown({ key: "Tab", preventDefault() { prevented++; } });
  assert.equal(prevented, 1); assert.equal(f.focused, f.button("关闭"));
  dialog.handlers.keydown({ key: "Escape", preventDefault() {} });
  assert.equal(f.nodes.some(node => node.tag === "dialog"), false); assert.equal(f.focused, trigger);
  trigger.handlers.click(); f.api.dispose(); assert.equal(f.nodes.some(node => node.tag === "dialog"), false);
});

test("birthday repair action stays removed even when a legacy callback is provided", () => {
  const f = render({ onRepairPreview() {} });
  assert.equal(f.button("检查本地生日资料"), undefined); assert.equal(f.button("正在检查生日…"), undefined);
});

test("birthday repair without changes cannot be confirmed and absent callback hides action", () => {
  const f = render({ state: { preview: { mode: "birthday_repair", changes: [{ action: "keep", name: "A" }] } } });
  assert.equal(f.button("确认修复生日资料").disabled, true); assert.equal(f.button("检查本地生日资料"), undefined);
  assert.ok(f.nodes.some(node => node.textContent === "没有需要修复的生日资料。"));
});

test("unsupported or disabled sources offer generic source association rather than guessed actor APIs", () => {
  for (const bindings of [[{ provider: "unknown", external_id: "42" }], [{ provider: "tmdb", external_id: "42", enabled: false }]]) {
    const f = render({ bindings }); assert.ok(f.button("关联作品来源")); assert.equal(f.button("更新人物信息"), undefined);
  }
  const f = render({ bindings: [{ provider: "wikipedia", external_id: "42" }] }); assert.ok(f.button("更新人物信息")); assert.ok(f.nodes.some(node => node.textContent.includes("中文维基百科")));
});

test("cast and staff follow the page scroll, responsive layout and existing theme tokens", () => {
  const css = fs.readFileSync(path.join(__dirname, "../frontend/people-panel.css"), "utf8");
  for (const selector of ["library-cast-list", "library-cast-row", "library-staff-list", "library-staff-row", "library-staff-names"]) {
    const blocks = [...css.matchAll(new RegExp("\\." + selector + "[^{}]*\\{([^}]*)\\}", "g"))];
    assert.ok(blocks.length, selector);
    for (const block of blocks) assert.doesNotMatch(block[1], /overflow(?:-y)?\s*:\s*(?:auto|scroll)|max-height\s*:/);
  }
  assert.match(css, /@media\s*\(max-width:\s*520px\)/);
  assert.match(css, /\.library-person-name[^{}]*\{[^}]*text-decoration:\s*none/);
  assert.doesNotMatch(css, /\.library-person-dialog\s+\.library-button\s*\{/, "dialogs share application button styles");
  assert.match(css, /\.library-person-dialog\s+:focus-visible/);
  for (const token of ["--text", "--panel", "--border", "--accent", "--muted"]) assert.ok(css.includes("var(" + token + ")"));
});

test("TMDB cast cards show actor portraits and performed roles rather than voice actor links", () => {
  const actor = { id: "actor1", type: "person", name: "하석진", name_cn: "河锡辰", character_name: "李载仁", avatar_url: "/api/catalog-library/people/assets/" + "c".repeat(64) + ".jpg", summary: "演员资料" };
  const f = render({ bindings: [{ provider: "tmdb", external_id: "1", season_number: 1 }], detail: { people: { cast: [actor, actor, { ...actor, character_name: "其他角色" }], persons: [{ name: "导演A", role: "导演" }] } } });
  assert.equal(f.nodes.filter(node => node.className === "library-cast-row library-actor-row").length, 2);
  assert.ok(f.nodes.some(node => node.textContent === "饰演 李载仁")); assert.equal(f.nodes.some(node => node.textContent === "CV："), false);
  assert.ok(f.nodes.some(node => node.className === "library-people-source" || node.className === "library-meta library-people-source"));
  assert.ok(f.nodes.some(node => node.tag === "img" && node.alt === "하석진")); f.button("하석진").handlers.click();
  assert.ok(f.nodes.some(node => node.textContent === "演员资料"));
});

test("actor sources keep provider and scope identity even when numeric IDs match", () => {
  const bindings = [{ provider: "bangumi", external_id: "12", scope: "episodes:1-12", episode_range: { start: 1, end: 12 } }, { provider: "bangumi", external_id: "12", scope: "episodes:13-24", episode_range: { start: 13, end: 24 } }, { provider: "tmdb", external_id: "12", season_number: 1 }, { provider: "tmdb", external_id: "12", season_number: 2 }];
  const f = render({ bindings: [...bindings, bindings[0]] }), options = f.nodes.filter(node => node.tag === "option");
  assert.equal(options.length, 5); assert.equal(new Set(options.map(row => row.value)).size, 5);
  assert.ok(options.some(row => row.textContent.includes("Bangumi") && row.textContent.includes("13–24")));
  assert.ok(options.some(row => row.textContent.includes("TMDB") && row.textContent.includes("第 2 季")));
});

test("staff responsibilities stay on one line in a shared content-sized column", () => {
  const f = render({ detail: { people: { persons: [{ name: "AAA", role: "OP・ED 演出" }, { name: "BBB", role: "导演" }] } } });
  assert.deepEqual(f.nodes.filter(node => node.className === "library-staff-role").map(node => node.textContent), ["OP・ED 演出：", "导演："]);
  const css = fs.readFileSync(path.join(__dirname, "../frontend/people-panel.css"), "utf8");
  assert.match(css, /\.library-staff-list\s*\{[^}]*grid-template-columns:\s*max-content minmax\(0, 1fr\)/);
  assert.match(css, /\.library-staff-role\s*\{[^}]*white-space:\s*nowrap/);
  assert.match(css, /\.library-staff-row\s*\{[^}]*display:\s*contents/);
  assert.doesNotMatch(css, /\.library-staff-(?:row|list)\s*\{[^}]*grid-template-columns:\s*\d+px/);
});


test("saved profiles delegate to the shared person card while missing entries retain fallback", () => {
  const person = { id: "person_1", type: "person", name: "Shared actor" }, missing = { id: "missing", type: "person", name: "Missing actor", missing: true };
  const f = render({ detail: { chapter_title_preference: "chinese", people: { persons: [person, missing] } } });
  const calls = [];
  f.window.NimdaPersonProfile = { open(options) { calls.push(options); }, dispose() { throw new Error("panel must not close shared card"); } };
  const trigger = f.button("Shared actor"); trigger.handlers.click();
  assert.equal(calls.length, 1); assert.equal(calls[0].personId, "person_1"); assert.equal(calls[0].preference, "chinese"); assert.equal(calls[0].trigger, trigger);
  assert.equal(f.nodes.some(node => node.tag === "dialog"), false);
  f.api.dispose(); assert.equal(calls.length, 1);
  f.button("Missing actor").handlers.click(); assert.ok(f.nodes.some(node => node.tag === "dialog"));
  for (const method of ["entityProfile", "portrait", "entityDisplay"]) assert.equal(typeof f.api[method], "function");
});


test("retired Douban stays out of actor refresh and supplemental UI while local data remains visible", () => {
  const binding = { provider: "douban", external_id: "123", binding_id: "legacy" };
  const actor = { id: "local-actor", type: "person", name: "Local actor", summary: "Saved biography", avatar_url: "/api/catalog-library/people/assets/" + "a".repeat(64) + ".jpg" };
  const f = render({ bindings: [binding], detail: { people: { cast: [{ person: actor }] } }, onRepairPreview() { throw Error("retired repair"); }, onSupplement() { throw Error("retired supplement"); } });
  assert.equal(f.api.sourcesForPeople([binding]).length, 0); assert.equal(f.button("更新人物信息"), undefined);
  assert.ok(f.button("关联作品来源")); assert.ok(f.button("Local actor"));
  f.button("Local actor").handlers.click(); assert.ok(f.nodes.some(node => node.textContent === "Saved biography"));
  assert.equal(f.button("豆瓣补充"), undefined); assert.equal(f.button("检查豆瓣重复关联"), undefined);
});
