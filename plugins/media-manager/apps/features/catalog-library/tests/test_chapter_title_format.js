"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), test = require("node:test"), vm = require("node:vm");
const window = {};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../frontend/chapter-title-format.js"), "utf8"), { window });
const display = (row, preference, fallback) => JSON.parse(JSON.stringify(window.NimdaChapterTitles.display(row, preference, fallback)));

test("original-first and Chinese-first swap title and hover without changing the chapter", () => {
  const chapter = { name: "한국어 제목", name_cn: "中文标题" }, before = JSON.stringify(chapter);
  assert.deepEqual(display(chapter, "chinese"), { text: "中文标题", tooltip: "한국어 제목" });
  assert.deepEqual(display(chapter, "original"), { text: "한국어 제목", tooltip: "中文标题" });
  assert.equal(JSON.stringify(chapter), before);
});

test("missing and whitespace-only language values fall back to the available title", () => {
  assert.deepEqual(display({ name: "  ", name_cn: "中文" }, "original"), { text: "中文", tooltip: "中文" });
  assert.deepEqual(display({ title: "旧标题" }, "chinese"), { text: "旧标题", tooltip: "旧标题" });
  assert.deepEqual(display({}, "chinese"), { text: "未命名章节", tooltip: "未命名章节" });
});

test("shared language formatting supports a caller-specific missing-name fallback", () => {
  assert.deepEqual(display({ name: " ", name_cn: "\t" }, "chinese", "人物资料缺失"), { text: "人物资料缺失", tooltip: "人物资料缺失" });
  assert.deepEqual(display(null, "original", " person-1 "), { text: "person-1", tooltip: "person-1" });
  assert.deepEqual(display({ name: " 原名 ", name_cn: " 中文名 " }, "chinese", "人物资料缺失"), { text: "中文名", tooltip: "原名" });
  assert.deepEqual(display({}, "original"), { text: "未命名章节", tooltip: "未命名章节" });
});
