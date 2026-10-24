"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const feature = fs.readFileSync(path.resolve(__dirname, "../frontend/style.css"), "utf8");
const chapters = fs.readFileSync(path.resolve(__dirname, "../frontend/chapter-player.css"), "utf8");
const app = fs.readFileSync(path.resolve(__dirname, "../../../framework/frontend/styles/app.css"), "utf8");

test("card format summaries are borderless text and hover matches click panel theme", () => {
  const chip = feature.match(/\.library-press-chip\s*\{([^}]+)\}/)[1];
  assert.match(chip, /border:\s*0;/); assert.match(chip, /padding:\s*0;/); assert.match(chip, /background:\s*transparent;/);
  const hover = feature.match(/\.library-card-tooltip\s*\{([^}]+)\}/)[1];
  const click = feature.match(/\.library-card-popover\s*\{([^}]+)\}/)[1];
  for (const rule of [hover, click]) { assert.match(rule, /background:\s*var\(--panel\)/); assert.match(rule, /color:\s*var\(--text\)/); }
  assert.match(hover, /white-space:\s*pre-line/); assert.match(hover, /text-align:\s*left/);
  assert.doesNotMatch(feature, /\.library-resource-count/);
});

test("alternating chapter rows use a small current-theme tint without changing the header", () => {
  for (const [css, parent, row] of [[chapters, "chapter-player-list", "chapter-player-row"], [feature, "library-chapters", "library-chapter"]]) {
    assert.match(css, new RegExp("\\." + parent + " > \\." + row + ":nth-child\\(odd\\) \\{ background-color: transparent; \\}"));
    const stripe = css.match(new RegExp("\\." + parent + " > \\." + row + ":nth-child\\(even\\) \\{([^}]+)\\}"))[1];
    assert.match(stripe, /color-mix\(in srgb, var\(--text\) 4%, var\(--bg\)\)/);
    assert.doesNotMatch(stripe, /#[0-9a-f]|rgba?\(/i);
  }
  assert.match(chapters, /\.chapter-player-row \{[^}]*padding: 9px 6px/);
  assert.match(chapters, /\.chapter-player-head \{ padding: 6px;/);
});

test("current and selectable playback resource buttons share fixed dimensions including continued numbering", () => {
  assert.match(feature, /\.library-resource-playback > \.library-select-playback \{ width: 100%; height: 28px; min-height: 28px;/);
  assert.match(feature, /\.library-resource-table\.has-numbering \.library-resource-playback > \.library-select-playback \{ flex: 0 0 68px; width: 68px;/);
  assert.match(feature, /\.library-resource-playback \.library-playback-current:disabled \{[^}]*opacity: \.7; cursor: not-allowed;/);
  assert.doesNotMatch(feature, /\.library-playback-current \{ display: inline-block/);
});

test("full-width search control visually contains its input and action while retaining keyboard focus indication", () => {
  assert.match(feature, /\.library-filters \{ display: block; width: 100%; margin: 0 0 16px; padding: 0;/);
  assert.match(feature, /\.library-search-control \{[^}]*width: 100%;[^}]*border: 1px solid var\(--border\)/);
  assert.match(feature, /\.library-search-control:focus-within \{ border-color: var\(--accent\)/);
  assert.match(feature, /body\.nimda-ui \.library-search-control input\[type="search"\] \{[^}]*border: 0;/);
  assert.match(feature, /\.library-search-control > \.library-button \{ flex: 0 0 auto;/);
});

test("round search and sidebar actions share one width and right inset across sidebar states", () => {
  assert.match(feature, /\.library-browse \{[^}]*--library-edge-action-width: 36px;[^}]*--library-edge-action-inset: 4px;/);
  assert.match(feature, /\.library-search-control > \.library-search-submit \{[^}]*width: var\(--library-edge-action-width\);[^}]*height: var\(--library-edge-action-width\);[^}]*--ui-radius: 50%;/);
  assert.match(feature, /\.library-browse-sidebar \.library-sidebar-toggle \{[^}]*width: var\(--library-edge-action-width\);/);
  assert.match(feature, /\.library-sidebar-rail \{[^}]*padding-right: var\(--library-edge-action-inset\);/);
  assert.match(feature, /\.library-search-control \{[^}]*padding: calc\(var\(--library-edge-action-inset\) - 1px\);/);
  assert.match(feature, /\.library-search-icon::before \{[^}]*border: 1\.6px solid currentColor;[^}]*border-radius: 50%;/);
  assert.doesNotMatch(feature, /\.library-sidebar-toggle \{[^}]*width: auto/);
});

test("larger brand remains theme independent and preserves the wrapping toolbar layout", () => {
  assert.match(app, /body\.nimda-ui \.top \.app-brand-name \{\s*font-size: 32px;\s*line-height: 1\.1;/);
  assert.match(app, /max-width: 640px[^}]*body\.nimda-ui \.top \.app-brand-name \{ font-size: 28px;/);
  assert.match(app, /\.top \{[^}]*display: flex;[^}]*flex-wrap: wrap;/);
  assert.match(app, /\.top > \.appearance-toolbar \{\s*margin: 0 0 0 auto;/);
});

test("unified sidebar stays aligned to the browse grid and never gains an independent scroll area", () => {
  for (const selector of ["library-browse-sidebar", "library-sidebar-content", "library-sidebar-section", "library-sidebar-rail"]) {
    const rules = [...feature.matchAll(new RegExp("\\." + selector + "[^{}]*\\{([^}]*)\\}", "g"))];
    assert.ok(rules.length);
    for (const rule of rules) assert.doesNotMatch(rule[1], /overflow(?:-y)?:\s*(?:scroll|auto)|max-height:/);
  }
  assert.match(feature, /\.library-sidebar-header \{[^}]*display: flex;[^}]*justify-content: space-between/);
});
