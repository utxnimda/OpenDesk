"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const frontend = path.join(__dirname, "../frontend");
const css = fs.readFileSync(path.join(frontend, "styles/controls.css"), "utf8");
const html = fs.readFileSync(path.join(frontend, "index.html"), "utf8");

test("shared control tokens are loaded after all feature styles and opt in on the app body", () => {
  const styles = [...html.matchAll(/href="([^"]+\.css\?v=\d+)"/g)].map(match => match[1]);
  assert.equal(styles.at(-1), "./styles/controls.css?v=168");
  assert.match(html, /<body class="nimda-ui">/);
  for (const token of ["--ui-control-height", "--ui-control-padding", "--ui-control-font-size", "--ui-radius", "--ui-action-border", "--ui-active-bg", "--ui-hover-bg", "--ui-active-border"]) assert.ok(css.includes(token));
  assert.match(css, /--ui-control-height:\s*30px/);
});

test("ordinary commands have a quiet outline without changing compact geometry", () => {
  assert.match(css, /--ui-action-border: color-mix\(in srgb, var\(--muted\) 40%, var\(--panel\)\)/);
  assert.match(css, /border: 1px solid var\(--ui-action-border\)/);
  assert.match(css, /body\.nimda-ui \.app-tab \{ border: 1px solid var\(--ui-action-border\)/);
  assert.match(css, /button\.sheet-edit-trigger \{ box-shadow: inset 0 0 0 1px var\(--ui-action-border\)/);
  assert.match(css, /\.library-maintenance > summary[^}]*border: 1px solid var\(--ui-action-border\)/);
  assert.match(css, /border-color: color-mix\(in srgb, var\(--status-err\) 35%, var\(--panel\)\)/);
});

test("selected state is not hover or action primary state and works with theme tokens", () => {
  for (const marker of ['aria-pressed="true"', 'aria-selected="true"', 'aria-current="page"', 'data-filter-active="true"']) assert.ok(css.includes(marker));
  assert.match(css, /border-color:\s*var\(--ui-active-border\)/);
  assert.match(css, /background-color:\s*var\(--ui-active-bg\)/);
  assert.match(css, /\.is-primary, \.ui-primary, \.organizer-primary-action\)[\s\S]*?background-color:\s*transparent/);
  assert.doesNotMatch(css, /#[0-9a-f]{3,8}\b/i, "control palettes must follow current theme, not hardcoded colors");
});

test("shared forms and exceptional controls preserve their feature contract", () => {
  assert.match(css, /select, textarea\):not\(:where\(table\.sheet \*, \[data-ui-custom\], \.organizer-json-editor\)\)[\s\S]*?padding: var\(--ui-control-padding\)/);
  assert.match(css, /\.library-pagination button\[aria-current="page"\]:disabled \{ opacity: 1/);
  assert.match(css, /\.library-maintenance\[open\] > \.library-button \{ background: var\(--panel\)/);
  assert.match(css, /\.collection-record-delete\):hover:not\(:where\(:disabled, \[aria-disabled="true"\]\)\)/);
  assert.match(css, /pointer: coarse[\s\S]*?\.organizer-child-name, \.library-card-link/);
  assert.match(css, /:hover:not\(:where\(:disabled, \[aria-disabled="true"\]\)\)/, "disabled guards must not increase ordinary hover specificity above danger hover");
  assert.match(css, /body\.nimda-ui select\.theme-select[^}]*padding-right: 1\.75rem/);
  const organizer = fs.readFileSync(path.join(frontend, "../../features/directory-organizer/frontend/style.css"), "utf8");
  assert.match(organizer, /\.directory-organizer-view \.organizer-json-editor[^}]*min-height: 180px[^}]*Consolas, monospace/);
});

test("dense and structural hit targets retain geometry while focus and disabled states stay visible", () => {
  for (const selector of [".library-card-link", ".organizer-child-name", ".library-person-name", ".nimda-directory-toggle", ".link-tree-toggle", ".press-open-folder-btn", ".sheet-th-sort", ".operation-history-item"]) assert.ok(css.includes(selector));
  assert.match(css, /:focus-visible[\s\S]*?outline:\s*2px solid var\(--ui-focus-ring\)/);
  assert.match(css, /:disabled/);
  assert.match(css, /pointer:\s*coarse/);
  assert.match(css, /prefers-reduced-motion:\s*reduce/);
  assert.match(css, /var\(--app-font-family\)/);
});

test("directory browser surfaces no longer force a light palette", () => {
  const directory = fs.readFileSync(path.join(frontend, "styles/directory-browser.css"), "utf8");
  assert.doesNotMatch(directory, /#[0-9a-f]{3,8}\b/i);
  for (const token of ["var(--panel)", "var(--text)", "var(--accent)", "var(--border)"]) assert.ok(directory.includes(token));
  assert.match(directory, /\.nimda-directory-toggle[\s\S]*?height:\s*15px/);
});
