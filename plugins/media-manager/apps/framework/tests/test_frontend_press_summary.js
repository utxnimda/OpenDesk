"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../frontend/src/common/press-summary.js"), "utf8");
const window = {};
vm.runInNewContext(source, { window });
const formatter = window.NimdaPressSummary;
const plain = value => JSON.parse(JSON.stringify(value));

test("shared press summary preserves case, configured enum labels and unknown format insertion order", () => {
  const items = [{ press_format: "R2Jraw", press_group: "XGroup" }, { press_format: "BDRip", press_group: "VCB" },
    { press_format: "Custom", press_group: "Unknown" }, { press_format: "BDRip", press_group: "ANK" }];
  const options = { formatOrder: ["1080p", { value: "BDRip" }], labels: { press_group: { VCB: "VCB-Studio" } } };
  assert.deepEqual(plain(formatter.compact(items, options)).map(item => item.label), ["BDRip / ANK", "BDRip / VCB-Studio", "R2Jraw / XGroup", "Custom / Unknown"]);
  assert.deepEqual(plain(formatter.columns(items, options.formatOrder)), ["1080p", "BDRip", "R2Jraw", "Custom"]);
});

test("table aggregate keeps source rows and indices while cards compact equivalent versions", () => {
  const items = [{ press_format: "BDRip", press_group: "----", extra: "untouched" },
    { press_format: "BDRip", press_group: "VCB" }, { press_format: "BDRip", press_group: "" }];
  const before = JSON.stringify(items), options = { splitVisible: { BDRip: true } };
  const aggregate = formatter.aggregate(items, options);
  assert.deepEqual(plain(aggregate).map(item => item._sourceOrd), [1, 0, 2]);
  assert.equal(aggregate[1].itCellAg, items[0]);
  assert.equal(aggregate[0].splitOn, true);
  const compact = plain(formatter.compact(items, options));
  assert.deepEqual(compact.map(item => [item.label, item.count]), [["BDRip / VCB", 1], ["BDRip / —", 2]]);
  assert.equal(JSON.stringify(items), before);
});

test("shared press summary treats dictionary-like enum and group strings as data", () => {
  const items = [{ press_format: "__proto__", press_group: "constructor" }, { press_format: "__proto__", press_group: "toString" }];
  const compact = plain(formatter.compact(items, { labels: { press_group: {} } }));
  assert.deepEqual(compact.map(item => item.label), ["__proto__ / constructor", "__proto__ / toString"]);
  assert.deepEqual(plain(formatter.compact([], {})), []);
});

const registry = {
  release_groups: [{ code: "VCB", kind: "release", names: ["VCB-Studio"], classifier_family: "VCB" },
    { code: "ANK", kind: "release", names: ["ANK-Raws"] }],
  translation_groups: [{ code: "CK", kind: "translation", names: ["澄空学园"] },
    { code: "NK", kind: "translation", names: ["Nekomoe kissaten", "喵萌奶茶屋"] },
    { code: "SUB", kind: "translation", names: ["测试字幕组"] }],
  combinations: [{ code: "VCBM", kind: "combination", members: ["VCB", "NK"], release_groups: ["VCB"], translation_groups: ["NK"], classifier_family: "VCB" },
    { code: "MULTI", kind: "combination", members: ["VCBM", "ANK", "SUB", "UNKNOWN"], release_groups: ["ANK"], translation_groups: ["SUB"] }],
  catalog_only_groups: [{ code: "VCBX", kind: "catalog-only", names: [], classifier_family: "VCB" }],
  options: [{ code: "VCB", label: "VCB · VCB-Studio", members: ["VCB"], kind: "release" }],
};

test("card abbreviations preserve raw codes while details resolve release and translation combinations", () => {
  const items = [{ press_format: "BDRip", press_group: "VCBM" }, { press_format: "BDRip", press_group: "VCB" },
    { press_format: "R2Jraw", press_group: "vcbm" }, { press_format: "BDRip", press_group: "VCBX" }];
  const rows = plain(formatter.cardEntries(items, { formatOrder: ["BDRip", "R2Jraw"], groupRegistry: registry,
    labels: { press_format: { BDRip: "蓝光压制" }, press_group: { VCB: "VCB · VCB-Studio", VCBM: "VCBM = VCB + NK" } } }));
  assert.deepEqual(rows.map(row => row.abbreviation), ["VCB", "VCBM", "VCBX", "vcbm"]);
  assert.deepEqual(rows.map(row => row.card_label), ["BDRip/VCB", "BDRip/VCBM", "BDRip/VCBX", "R2Jraw/vcbm"]);
  assert.equal(rows[0].detail, "BDRip/VCB·VCB-Studio");
  assert.equal(rows[1].detail, "BDRip/VCBM·VCB-Studio + Nekomoe kissaten / 喵萌奶茶屋");
  assert.equal(rows[2].detail, "BDRip/VCBX");
  assert.equal(rows[3].detail, "R2Jraw/vcbm·VCB-Studio + Nekomoe kissaten / 喵萌奶茶屋");
});

test("card rows retain compact ordering, duplicate counts and blank-group behavior without mutating input", () => {
  const items = [{ press_format: "BDRip", press_group: "VCBM" }, { press_format: "BDRip", press_group: "VCBM" },
    { press_format: "BDRip", press_group: "vcbm" }, { press_format: "BDRip", press_group: "----" },
    { press_format: "BDRip", press_group: "" }, { press_format: "DVDRip", press_group: "VCBM" }, { press_group: "VCB" }];
  const options = { formatOrder: ["DVDRip", "BDRip"], groupRegistry: registry };
  const before = JSON.stringify({ items, registry, options });
  const rows = plain(formatter.cardEntries(items, options));
  assert.deepEqual(rows.map(({ abbreviation, card_label, detail, ...rest }) => rest), plain(formatter.compact(items, options)));
  assert.deepEqual(rows.map(row => [row.format, row.abbreviation, row.count]), [
    ["DVDRip", "VCBM", 1], ["BDRip", "VCBM", 2], ["BDRip", "vcbm", 1], ["BDRip", "—", 2],
  ]);
  assert.equal(rows.at(-1).detail, "BDRip/—");
  assert.equal(rows.reduce((sum, row) => sum + row.count, 0), formatter.aggregate(items, options).length);
  assert.equal(JSON.stringify({ items, registry, options }), before);
});

test("card details expand nested combinations and retain unknown member codes", () => {
  const [row] = formatter.cardEntries([{ press_format: "Custom", press_group: "MULTI" }], { groupRegistry: registry });
  assert.equal(row.abbreviation, "MULTI");
  assert.equal(row.detail, "Custom/MULTI·VCB-Studio + Nekomoe kissaten / 喵萌奶茶屋 + ANK-Raws + 测试字幕组 + UNKNOWN");
});

test("card details safely handle missing registries, option-only records, cycles and dictionary-like codes", () => {
  const items = [{ press_format: "__proto__", press_group: "constructor" }];
  assert.equal(formatter.cardEntries(items)[0].detail, "__proto__/constructor");
  assert.deepEqual(plain(formatter.cardEntries(null)), []);
  const custom = { release_groups: [{ code: "constructor", names: ["真实组名"] }],
    combinations: [{ code: "LOOP", members: ["LOOP"], kind: "combination" }],
    options: [{ code: "OPT", kind: "release", members: ["OPT"] }] };
  const rows = formatter.cardEntries([...items, { press_format: "BDRip", press_group: "LOOP" }, { press_format: "BDRip", press_group: "OPT" }], { groupRegistry: custom });
  assert.equal(rows.find(row => row.group === "constructor").detail, "__proto__/constructor·真实组名");
  assert.equal(rows.find(row => row.group === "LOOP").detail, "BDRip/LOOP");
  assert.equal(rows.find(row => row.group === "OPT").detail, "BDRip/OPT");
});

test("case-insensitive registry lookup never guesses across ambiguous identities", () => {
  const groupRegistry = { release_groups: [{ code: "AB", names: ["大写组"] }, { code: "Ab", names: ["混合组"] }] };
  const rows = formatter.cardEntries(["AB", "Ab", "ab"].map(press_group => ({ press_group, press_format: "BDRip" })), { groupRegistry });
  assert.equal(rows.find(row => row.group === "AB").detail, "BDRip/AB·大写组");
  assert.equal(rows.find(row => row.group === "Ab").detail, "BDRip/Ab·混合组");
  assert.equal(rows.find(row => row.group === "ab").detail, "BDRip/ab");
});

test("card format/group labels use raw case despite enum display labels and keep each format distinct", () => {
  const items = [{ press_format: "BDRip", press_group: "CK" }, { press_format: "BDRip", press_group: "CK" },
    { press_format: "R2Jraw", press_group: "CK" }, { press_format: "DVDRip", press_group: "CK" }];
  const options = { formatOrder: ["BDRip", "DVDRip", "R2Jraw"], groupRegistry: registry,
    labels: { press_format: { BDRip: "蓝光压制", R2Jraw: "二区原盘" }, press_group: { CK: "澄空学园字幕组" } } };
  const rows = plain(formatter.cardEntries(items, options));
  assert.deepEqual(rows.map(row => [row.card_label, row.detail, row.count]), [
    ["BDRip/CK", "BDRip/CK·澄空学园", 2], ["DVDRip/CK", "DVDRip/CK·澄空学园", 1], ["R2Jraw/CK", "R2Jraw/CK·澄空学园", 1],
  ]);
  assert.equal(rows[0].format_label, "蓝光压制");
  assert.equal(rows[0].label, "蓝光压制 / 澄空学园字幕组");
  assert.deepEqual(rows.map(({ abbreviation, card_label, detail, ...row }) => row), plain(formatter.compact(items, options)));
});

test("unknown and empty card groups do not add duplicate identifiers or invented names", () => {
  const rows = formatter.cardEntries([{ press_format: "BDRip", press_group: "XGroup" },
    { press_format: "BDRip", press_group: "----" }, { press_format: "BDRip", press_group: "" }], { groupRegistry: registry });
  assert.deepEqual(plain(rows).map(row => [row.card_label, row.detail, row.abbreviation, row.count]), [
    ["BDRip/XGroup", "BDRip/XGroup", "XGroup", 1], ["BDRip/—", "BDRip/—", "—", 2],
  ]);
});

test("combination details preserve unnamed member codes in source order without duplicate members", () => {
  const groupRegistry = { combinations: [{ code: "MIX", kind: "combination", members: ["X", "Y", "X"],
    release_groups: ["Y"], translation_groups: ["Z"] }], release_groups: [{ code: "Y", names: ["Y"] }] };
  const [row] = formatter.cardEntries([{ press_format: "BDRip", press_group: "MIX" }], { groupRegistry });
  assert.equal(row.card_label, "BDRip/MIX");
  assert.equal(row.detail, "BDRip/MIX·X + Y + Z");
});

test("card format summaries use one raw format label and one hover line per group", () => {
  const items = [{ press_format: "R2Jraw", press_group: "VCB", press_path: "raw" },
    { press_format: "BDRip", press_group: "VCB", press_path: "first" },
    { press_format: "BDRip", press_group: "CK", press_path: "subs" },
    { press_format: "BDRip", press_group: "VCB", press_path: "second" },
    { press_format: "DVDRip", press_group: "VCB", press_path: "dvd" }];
  const options = { formatOrder: ["1080p", "DVDRip", "BDRip"], groupRegistry: registry,
    labels: { press_format: { BDRip: "蓝光压制", R2Jraw: "二区原盘" } } };
  const before = JSON.stringify({ items, options });
  const rows = plain(formatter.cardFormats(items, options));
  assert.deepEqual(rows.map(row => [row.format, row.label]), [["DVDRip", "DVDRip"], ["BDRip", "BDRip"], ["R2Jraw", "R2Jraw"]]);
  const bdrip = rows[1];
  assert.deepEqual(bdrip.tooltip_lines, ["CK·澄空学园", "VCB·VCB-Studio"]);
  assert.deepEqual(bdrip.sections.map(section => section.title), ["BDRip/CK", "BDRip/VCB", "BDRip/VCB"]);
  assert.deepEqual(bdrip.sections.map(section => section.fields.at(-1).value), ["subs", "first", "second"]);
  assert.deepEqual(bdrip.sections[0].fields, [{ label: "压制组", value: "未填写" },
    { label: "字幕组", value: "澄空学园" }, { label: "资源目录", value: "subs" }]);
  assert.deepEqual(bdrip.sections[1].fields, [{ label: "压制组", value: "VCB-Studio" },
    { label: "字幕组", value: "未填写" }, { label: "资源目录", value: "first" }]);
  assert.equal(JSON.stringify({ items, options }), before);
});

test("card format summaries preserve all click records while normalizing blank hover groups to four dashes", () => {
  const items = [{ press_format: "BDRip", press_group: "----" },
    { press_format: "BDRip", press_group: " " }, { press_format: "BDRip" },
    { press_format: "BDRip", press_group: "----", press_path: " " }];
  const [row] = plain(formatter.cardFormats(items, { groupRegistry: registry }));
  assert.deepEqual(row.tooltip_lines, ["----"]);
  assert.equal(row.sections.length, items.length);
  row.sections.forEach(section => {
    assert.equal(section.title, "BDRip/----");
    assert.deepEqual(section.fields, [{ label: "压制组", value: "未填写" },
      { label: "字幕组", value: "未填写" }, { label: "资源目录", value: "未填写" }]);
  });
  assert.deepEqual(plain(formatter.cardFormats(null)), []);
  assert.deepEqual(plain(formatter.cardFormats([null, {}, { press_group: "VCB" }])), []);
});

test("card format summaries retain raw unknown and same-name codes without guessing classifier ownership", () => {
  const groupRegistry = { ...registry, release_groups: [...registry.release_groups, { code: "Jsum", names: ["Jsum"] }] };
  const [row] = plain(formatter.cardFormats(["XGroup", "VCBX", "Jsum"].map(press_group =>
    ({ press_format: "BDRip", press_group })), { groupRegistry }));
  assert.deepEqual(row.tooltip_lines, ["Jsum", "VCBX", "XGroup"]);
  assert.equal(row.sections[0].fields[0].value, "Jsum");
  [row.sections[1], row.sections[2]].forEach(section => {
    assert.equal(section.fields[0].value, "未填写");
    assert.equal(section.fields[1].value, "未填写");
  });
});

test("card format combination details reuse nested registry membership and preserve source spelling", () => {
  const [row] = plain(formatter.cardFormats([{ press_format: "BDRip", press_group: "MULTI" },
    { press_format: "BDRip", press_group: "vcbm" }], { groupRegistry: registry }));
  assert.deepEqual(row.tooltip_lines, [
    "MULTI·VCB-Studio + Nekomoe kissaten / 喵萌奶茶屋 + ANK-Raws + 测试字幕组 + UNKNOWN",
    "vcbm·VCB-Studio + Nekomoe kissaten / 喵萌奶茶屋",
  ]);
  assert.equal(row.sections[0].fields[0].value, "VCB-Studio + ANK-Raws");
  assert.equal(row.sections[0].fields[1].value, "Nekomoe kissaten / 喵萌奶茶屋 + 测试字幕组");
  assert.equal(row.sections[1].title, "BDRip/vcbm");
  assert.equal(row.sections[1].fields[0].value, "VCB-Studio");
  assert.equal(row.sections[1].fields[1].value, "Nekomoe kissaten / 喵萌奶茶屋");
});

test("card format summaries keep case-distinct formats and groups and reject ambiguous case lookups", () => {
  const groupRegistry = { release_groups: [{ code: "AB", names: ["大写组"] }, { code: "Ab", names: ["混合组"] }] };
  const rows = plain(formatter.cardFormats([{ press_format: "BDRip", press_group: "AB" },
    { press_format: "BDRip", press_group: "Ab" }, { press_format: "BDRip", press_group: "ab" },
    { press_format: "bdrip", press_group: "AB" }], { groupRegistry }));
  assert.deepEqual(rows.map(row => row.label), ["BDRip", "bdrip"]);
  assert.deepEqual(rows[0].tooltip_lines, ["AB·大写组", "Ab·混合组", "ab"]);
  assert.equal(rows[0].sections[2].fields[0].value, "未填写");
});

test("card format summaries safely handle cycles, explicit unknown member roles and dictionary-like data", () => {
  const groupRegistry = { combinations: [{ code: "LOOP", kind: "combination", members: ["LOOP"] },
    { code: "MIX", kind: "combination", members: ["X", "Y"], release_groups: ["X"], translation_groups: ["Y"] }],
    release_groups: [{ code: "constructor", names: ["真实组名"] }] };
  const rows = plain(formatter.cardFormats([{ press_format: "__proto__", press_group: "constructor" },
    { press_format: "BDRip", press_group: "LOOP" }, { press_format: "BDRip", press_group: "MIX" }], { groupRegistry }));
  assert.deepEqual(rows[0].tooltip_lines, ["constructor·真实组名"]);
  assert.deepEqual(rows[1].tooltip_lines, ["LOOP", "MIX·X + Y"]);
  assert.equal(rows[1].sections[0].fields[0].value, "未填写");
  assert.equal(rows[1].sections[1].fields[0].value, "X");
  assert.equal(rows[1].sections[1].fields[1].value, "Y");
});
