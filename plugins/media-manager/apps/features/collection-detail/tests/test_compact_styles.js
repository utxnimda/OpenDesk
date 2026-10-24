"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const styles = fs.readFileSync(path.join(__dirname, "../frontend/style.css"), "utf8");
const details = fs.readFileSync(path.join(__dirname, "../frontend/work-detail-dialog.css"), "utf8");
const info = fs.readFileSync(path.join(__dirname, "../../collection-info/frontend/style.css"), "utf8");

function rule(css, selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = css.match(new RegExp("(?:^|\\n)" + escaped + "\\s*\\{([^}]+)\\}"));
  assert.ok(match, `Missing style rule: ${selector}`);
  return match[1];
}

test("ordinary collection dialog actions share compact transparent controls", () => {
  for (const [css, selector] of [[styles, ".new-work-dialog button"], [details, ".work-detail-dialog button"],
    [styles, ".link-index-browser-tool-btn"]]) {
    const value = rule(css, selector);
    assert.match(value, /min-height:\s*var\(--ui-control-height, 30px\)/);
    assert.match(value, /padding:\s*var\(--ui-control-padding, 4px 9px\)/);
    assert.match(value, /border:\s*1px solid transparent/);
    assert.match(value, /background:\s*transparent/);
    assert.match(value, /font:\s*inherit/);
  }
});

test("persistent tabs keep theme accent, faint fill and thin outline", () => {
  for (const [css, selector] of [[styles, ".collection-detail-subtab.is-active"],
    [details, '.work-detail-dialog .work-detail-tab[aria-selected="true"]']]) {
    const value = rule(css, selector);
    assert.match(value, /background:\s*var\(--ui-active-bg/);
    assert.match(value, /border-color:\s*var\(--ui-active-border/);
    assert.match(value, /color:\s*var\(--accent\)/);
  }
});

test("the index and resource browser uses theme colors instead of a fixed light palette", () => {
  const browser = styles.slice(styles.indexOf(".link-index-browser {"), styles.indexOf(".new-work-dialog {"));
  assert.doesNotMatch(browser, /(?:color|background(?:-color)?|border(?:-[a-z]+)?)\s*:[^;\n]*#[0-9a-f]{3,8}/i);
  assert.match(rule(styles, ".link-index-files-table tr:hover td"), /var\(--ui-hover-bg/);
  assert.match(rule(styles, ".link-index-browser .link-tree-folder-row.is-selected"), /var\(--ui-active-border/);
});

test("tree expanders and inline file links retain dense geometry and inherited font", () => {
  const toggle = rule(styles, ".link-index-browser .link-tree-toggle");
  assert.match(toggle, /min-height:\s*16px/);
  assert.match(toggle, /padding:\s*0/);
  assert.match(toggle, /background-image:\s*none/);
  assert.match(toggle, /color:\s*var\(--muted\)/);
  const filename = rule(styles, ".link-index-file-name");
  assert.match(filename, /padding:\s*0/);
  assert.match(filename, /font:\s*inherit/);
});

test("destructive collection actions remain clearly marked and keyboard accessible", () => {
  const deletion = rule(info, "body.nimda-ui .collection-record-delete");
  assert.match(deletion, /color:\s*var\(--status-err\)/);
  assert.match(deletion, /background:\s*transparent/);
  assert.match(rule(info, "body.nimda-ui .collection-record-delete:focus-visible"), /outline:\s*2px solid var\(--status-err\)/);
  assert.match(styles, /\.new-work-dialog button:focus-visible/);
  assert.match(details, /\.work-detail-dialog button:focus-visible/);
});
