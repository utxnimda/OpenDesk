"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../backend/work_catalog_yaml/common/source_browser.py"), "utf8");
const script = source.match(/_CAPTURE_SCRIPT = r"""([\s\S]*?)"""/)[1];
const workUrl = "https://movie.douban.com/subject/1292052/";

function capture({ current = workUrl, title = "作品 (豆瓣)", canonical = workUrl, ready = "complete", score = "8.2", count = "1,234", jsonld = [], challenge = false } = {}) {
  const elements = (value) => value == null ? [] : [{ textContent: value }];
  const area = { querySelectorAll(selector) { return selector === '[property="v:average"]' ? elements(score) : elements(count); } };
  const document = {
    title, readyState: ready,
    querySelector(selector) {
      if (selector === 'h1 span[property="v:itemreviewed"]') return { textContent: "作品" };
      return challenge ? { getClientRects() { return [{}]; } } : null;
    },
    querySelectorAll(selector) {
      if (selector === 'link[rel="canonical"],meta[property="og:url"]') return canonical ? [{ tagName: "LINK", getAttribute() { return canonical; } }] : [];
      if (selector === "#interest_sectl") return score == null ? [] : [area];
      if (selector === 'script[type="application/ld+json"]') return jsonld.map(value => ({ textContent: typeof value === "string" ? value : JSON.stringify(value) }));
      throw new Error("Unexpected document read: " + selector);
    },
  };
  Object.defineProperty(document, "body", { get() { throw new Error("Full page and people must not be read"); } });
  return JSON.parse(vm.runInNewContext(script.replace("__EXPECTED_URL__", JSON.stringify(workUrl)), { document, location: { href: current }, URL }, { timeout: 1000 }));
}

test("rating DOM projection contains only work identity and numeric public score", () => {
  assert.deepEqual(capture(), { status: "ready", url: workUrl, title: "作品 (豆瓣)", name: "作品", rating: "8.2", votes: "1234", diagnostics: { jsonld_blocks: 0, invalid_jsonld_blocks: 0, rating_source: "dom" } });
});

test("JSON-LD rating projection never includes actors descriptions or profile links", () => {
  const result = capture({ score: null, jsonld: [{ "@type": "TVSeries", url: workUrl, name: "作品", description: "PRIVATE_SUMMARY",
    actor: [{ "@type": "Person", name: "PRIVATE_PERSON", url: "https://www.douban.com/personage/7/" }],
    aggregateRating: { ratingValue: 9.1, ratingCount: 7654, bestRating: 10 } }] });
  assert.equal(result.rating, "9.1");
  assert.equal(result.votes, "7654");
  assert.doesNotMatch(JSON.stringify(result), /PRIVATE|actor|description|personage/);
});

test("person pages search URLs and changed work identity are not captured", () => {
  for (const current of ["https://www.douban.com/personage/7/", "https://movie.douban.com/celebrity/7/", "https://www.douban.com/search?q=x",
    "https://movie.douban.com/subject/7/", "https://accounts.douban.com/login?token=PRIVATE"]) {
    assert.deepEqual(capture({ current }), { status: "verification", code: "unexpected-url" });
  }
  assert.deepEqual(capture({ canonical: "https://movie.douban.com/subject/7/" }), { status: "failed", code: "identity-mismatch" });
});

test("verification and loading pages pause without extracting fields", () => {
  assert.deepEqual(capture({ ready: "loading" }), { status: "loading", code: "page-loading" });
  assert.deepEqual(capture({ title: "豆瓣登录 PRIVATE" }), { status: "verification", code: "page-verification" });
  assert.deepEqual(capture({ challenge: true }), { status: "verification", code: "page-verification" });
});

test("unrated or invalid scores are never materialized as a zero rating", () => {
  for (const score of [null, "", "暂无评分", "0", "11", "NaN", true]) {
    assert.deepEqual(capture({ score }), { status: "failed", code: "no-rating" });
  }
  assert.equal(capture({ score: "10.0", count: null }).rating, "10.0");
  assert.equal(capture({ score: "10.0", count: null }).votes, "");
});

test("only the selected work node can supply ratings", () => {
  const other = { "@type": "Movie", url: "https://movie.douban.com/subject/7/", aggregateRating: { ratingValue: 9, ratingCount: 10 } };
  assert.deepEqual(capture({ score: null, jsonld: [other] }), { status: "failed", code: "no-rating" });
  assert.equal(capture({ jsonld: [other] }).rating, "8.2");
  assert.deepEqual(capture({ score: null, jsonld: [{ "@type": "Person", url: workUrl, aggregateRating: { ratingValue: 9 } }] }), { status: "failed", code: "no-rating" });
});

test("invalid optional JSON-LD no longer blocks a valid visible rating", () => {
  const result = capture({ jsonld: ["not-json", '{"@type":"Movie","description":"bad\nJSON","aggregateRating":{"ratingValue":9.8}}'] });
  assert.equal(result.status, "ready"); assert.equal(result.rating, "8.2"); assert.equal(result.votes, "1234");
  assert.deepEqual(result.diagnostics, { jsonld_blocks: 2, invalid_jsonld_blocks: 2, rating_source: "dom" });
  assert.doesNotMatch(JSON.stringify(result), /description|bad|9\.8/);
});

test("malformed optional blocks do not skip later valid score or vote conflicts", () => {
  const work = { "@type": "Movie", url: workUrl, aggregateRating: { ratingValue: 9, ratingCount: 1234 } };
  for (const jsonld of [[work], ["not-json", work], [work, "not-json"], ["not-json", { ...work, aggregateRating: { ratingValue: 8.2, ratingCount: 999 } }]]) {
    assert.deepEqual(capture({ jsonld }), { status: "failed", code: "rating-conflict" });
  }
});

test("valid selected-work JSON-LD survives independent malformed metadata before or after it", () => {
  const work = { "@type": "TVSeries", url: workUrl, aggregateRating: { ratingValue: 8.2, ratingCount: 1234 } };
  for (const jsonld of [["not-json", work], [work, "not-json"]]) {
    const result = capture({ score: null, jsonld }); assert.equal(result.status, "ready"); assert.equal(result.rating, "8.2");
    assert.deepEqual(result.diagnostics, { jsonld_blocks: 2, invalid_jsonld_blocks: 1, rating_source: "jsonld" });
  }
  assert.deepEqual(capture({ jsonld: [work] }).diagnostics, { jsonld_blocks: 1, invalid_jsonld_blocks: 0, rating_source: "dom+jsonld" });
});

test("broken JSON-LD alone cannot fabricate a rating or bypass identity and verification", () => {
  assert.deepEqual(capture({ score: null, jsonld: ['{"ratingValue":9.8'] }), { status: "failed", code: "invalid-jsonld" });
  assert.deepEqual(capture({ score: null, jsonld: ["not-json", { "@type": "Person", aggregateRating: { ratingValue: 9 } }] }), { status: "failed", code: "invalid-jsonld" });
  assert.deepEqual(capture({ jsonld: ["not-json"], challenge: true }), { status: "verification", code: "page-verification" });
  assert.deepEqual(capture({ jsonld: ["not-json"], canonical: "https://movie.douban.com/subject/7/" }), { status: "failed", code: "identity-mismatch" });
});

test("structured input size and nesting are bounded", () => {
  assert.deepEqual(capture({ jsonld: Array.from({ length: 33 }, () => ({})) }), { status: "failed", code: "projection-limit" });
  assert.deepEqual(capture({ jsonld: [' '.repeat(1024 * 1024 + 1)] }), { status: "failed", code: "projection-limit" });
  assert.deepEqual(capture({ jsonld: [{ "@graph": [{ "@graph": [{ "@graph": [{ "@graph": [{ "@graph": [{ "@graph": [{ "@graph": [{}] }] }] }] }] }] }] }] }), { status: "failed", code: "projection-limit" });
  const work = { "@type": "Movie", url: workUrl, aggregateRating: { ratingValue: 8.2, ratingCount: 1234 } };
  assert.deepEqual(capture({ jsonld: ["not-json", [work, ...Array.from({ length: 129 }, () => ({}))]] }), { status: "failed", code: "projection-limit" });
});
