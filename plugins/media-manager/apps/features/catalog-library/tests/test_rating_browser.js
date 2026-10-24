"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../frontend/rating-browser.js"), "utf8");
const flush = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise(yes => { resolve = yes; }); return { promise, resolve }; };
function fixture(handler) {
  const calls = [], timers = new Map(), previews = [], changes = []; let nextTimer = 0, active = true, now = 0;
  const window = { setTimeout(fn, delay) { assert.ok([750, 30000].includes(delay)); timers.set(++nextTimer, { fn, at: now + delay }); return nextTimer; }, clearTimeout(id) { timers.delete(id); } };
  vm.runInNewContext(source, { window });
  const capture = window.NimdaRatingBrowser.create({ payload: { ref: { record: "version" }, kind: "work", subject_id: "1" },
    current: () => active, onChange: state => changes.push(state), onComplete: value => previews.push(value),
    async request(route, body) { calls.push({ route, body: JSON.parse(JSON.stringify(body)) }); return handler(route, body); }
  });
  async function tick() {
    const entry = Array.from(timers.entries()).sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
    if (entry) { timers.delete(entry[0]); now = entry[1].at; entry[1].fn(); await flush(); }
  }
  return { calls, timers, previews, changes, capture, retire() { active = false; }, tick,
    async advance(ms) { const target = now + ms; while (Array.from(timers.values()).some(timer => timer.at <= target)) await tick(); now = target; } };
}
test("browser capture starts only explicitly, pauses on verification and produces a preview without applying", async () => {
  const f = fixture(route => route.endsWith("start") ? { session_id: "s", status: "verification", message: "请登录", auto_continue: false } : { session_id: "s", status: "complete", preview: { provider: "douban", preview_token: "signed" } });
  assert.equal(f.calls.length, 0); await f.capture.start();
  assert.equal(f.timers.size, 0); assert.equal(f.previews.length, 0);
  await f.capture.step("continue"); assert.equal(f.previews[0].preview_token, "signed");
  assert.deepEqual(f.calls.map(row => row.route), ["douban/browser/start", "douban/browser/step"]);
  assert.deepEqual(f.calls[1].body, { ref: { record: "version" }, session_id: "s", action: "continue" });
});
test("cancel discards late start responses and closes the newly returned remote session", async () => {
  const wait = deferred(), f = fixture(route => route.endsWith("start") ? wait.promise : {});
  const start = f.capture.start(); await f.capture.cancel();
  wait.resolve({ session_id: "late", status: "complete", preview: { provider: "douban", preview_token: "stale" } }); await start; await flush();
  assert.equal(f.previews.length, 0); assert.equal(f.calls.at(-1).route, "douban/browser/cancel");
  assert.equal(f.calls.at(-1).body.session_id, "late"); assert.equal(f.timers.size, 0);
});
test("skip and finish require explicit backend capability and unsupported errors remain visible", async () => {
  const f = fixture(() => ({ session_id: "s", status: "verification", can_finish: false, can_skip: false }));
  await f.capture.start(); await f.capture.step("skip"); await f.capture.step("finish"); assert.equal(f.calls.length, 1);
  const failed = fixture(() => { throw Error("仅桌面应用支持浏览器采集"); });
  await failed.capture.start(); assert.equal(failed.capture.state().status, "failed"); assert.match(failed.capture.state().message, /仅桌面应用/);
});
test("reopen explicitly returns to the selected item with the same session and no implicit save", async () => {
  for (const status of ["verification", "failed", "closed"]) {
    const wait = deferred(), f = fixture(route => route.endsWith("start") ? { session_id: "selected", status, completed: 0, total: 1 } : wait.promise);
    await f.capture.start(); assert.equal(f.calls.length, 1); assert.equal(f.timers.size, 0);
    const reopening = f.capture.step("reopen"); await f.capture.step("reopen");
    assert.equal(f.calls.length, 2); assert.deepEqual(f.calls[1], { route: "douban/browser/step", body: { ref: { record: "version" }, session_id: "selected", action: "reopen" } });
    wait.resolve({ session_id: "selected", status: "verification", completed: 0, total: 1 }); await reopening;
    assert.equal(f.capture.state().completed, 0); assert.equal(f.capture.state().can_finish, undefined);
    assert.equal(f.previews.length, 0); assert.equal(f.timers.size, 0);
  }
  for (const status of ["loading", "ready", "complete"]) {
    const f = fixture(() => ({ session_id: "selected", status, preview: status === "complete" ? { provider: "douban", preview_token: "signed" } : undefined }));
    await f.capture.start(); await f.capture.step("reopen"); assert.equal(f.calls.length, 1);
  }
});
test("reopen button is limited to paused session states and stays disabled while busy", async () => {
  function render(state) {
    const nodes = [], calls = [], window = {};
    const document = { createElement(tag) { const element = { tag, children: [], handlers: {}, appendChild(child) { this.children.push(child); }, setAttribute() {}, addEventListener(name, handler) { this.handlers[name] = handler; } }; nodes.push(element); return element; } };
    vm.runInNewContext(source, { window, document });
    window.NimdaRatingBrowser.render(document.createElement("section"), state, { step: action => calls.push(action), cancel() {} });
    return { calls, button: nodes.find(node => node.tag === "button" && node.textContent === "重新打开所选条目") };
  }
  for (const status of ["verification", "failed", "closed"]) {
    for (const busy of [false, true]) {
      const f = render({ session_id: "selected", status, busy }); assert.ok(f.button); assert.equal(f.button.disabled, busy);
      f.button.handlers.click(); await flush(); assert.deepEqual(f.calls, busy ? [] : ["reopen"]);
    }
    assert.equal(render({ status }).button, undefined);
  }
  for (const status of ["loading", "ready", "complete"]) assert.equal(render({ session_id: "selected", status }).button, undefined);
});

test("rating reader strips non-rating scope and never follows ready/unsupported capabilities into another page or people reads", async () => {
  const f = fixture(() => ({ session_id: "rating", status: "ready", auto_continue: true, can_skip: true, can_finish: true }));
  await f.capture.start(); assert.deepEqual(f.calls[0].body, { ref: { record: "version" }, subject_id: "1", binding_role: "supplement" });
  await f.tick(); await f.capture.step("skip"); await f.capture.step("finish"); assert.equal(f.calls.length, 1); assert.equal(f.timers.size, 0);
  await f.capture.cancel(); assert.equal(f.calls.at(-1).route, "douban/browser/cancel");
});

test("one explicit start consumes the asynchronous host result without another click or save", async () => {
  let captures = 0;
  const f = fixture(route => route.endsWith("start") ? { session_id: "same-work", status: "loading", auto_continue: true } : ++captures === 1 ?
    { session_id: "same-work", status: "loading", auto_continue: true, message: "正在读取作品评分" } :
    { session_id: "same-work", status: "complete", preview: { provider: "douban", diff: [{ field: "metadata.ratings.douban" }] } });
  await f.capture.start(); assert.equal(f.capture.state().busy, false); assert.equal(f.capture.state().polling, true);
  await f.capture.start(); await f.capture.step("continue"); assert.equal(f.calls.length, 1);
  await f.tick(); assert.equal(f.capture.state().status, "loading"); await f.tick();
  assert.equal(f.capture.state().status, "complete"); assert.equal(f.previews.length, 1); assert.equal(f.timers.size, 0);
  assert.deepEqual(f.calls.map(call => call.route), ["douban/browser/start", "douban/browser/step", "douban/browser/step"]);
  assert.deepEqual(f.calls.slice(1).map(call => call.body), Array(2).fill({ ref: { record: "version" }, session_id: "same-work", action: "continue" }));
  await f.capture.step("continue"); await f.tick(); assert.equal(f.previews.length, 1); assert.equal(f.calls.length, 3);
});

test("verification failure and closed results stop polling until explicit continue or reopen", async () => {
  for (const status of ["verification", "failed", "closed"]) {
    let reads = 0;
    const f = fixture(route => route.endsWith("start") ? { session_id: "s", status: "loading", auto_continue: true } :
      ++reads === 1 ? { session_id: "s", status, auto_continue: true } :
      reads === 2 ? { session_id: "s", status: "loading", auto_continue: true } :
      { session_id: "s", status: "complete", preview: { provider: "douban" } });
    await f.capture.start(); await f.tick(); assert.equal(f.capture.state().status, status); assert.equal(f.timers.size, 0);
    await f.advance(60000); assert.equal(f.calls.length, 2);
    await f.capture.step(status === "verification" ? "continue" : "reopen"); await f.tick();
    assert.equal(f.capture.state().status, "complete"); assert.equal(f.previews.length, 1); assert.equal(f.timers.size, 0);
    assert.equal(f.calls[2].body.action, status === "verification" ? "continue" : "reopen");
    assert.equal(f.calls[3].body.action, "continue");
  }
});

test("loading has a total 30 second budget and a new explicit continue gets a fresh budget", async () => {
  const f = fixture(() => ({ session_id: "s", status: "loading", auto_continue: true }));
  await f.capture.start(); await f.advance(30000);
  assert.equal(f.capture.state().status, "failed"); assert.equal(f.capture.state().busy, false); assert.equal(f.capture.state().polling, false);
  assert.match(f.capture.state().message, /30 秒/); assert.equal(f.timers.size, 0); assert.ok(f.calls.length <= 41);
  const before = f.calls.length; await f.advance(60000); assert.equal(f.calls.length, before);
  await f.capture.step("continue"); assert.equal(f.capture.state().polling, true); await f.capture.cancel(); assert.equal(f.timers.size, 0);
});

test("a stalled start releases the UI at the deadline and closes its eventual late session", async () => {
  const wait = deferred(), f = fixture(route => route.endsWith("start") ? wait.promise : {});
  const start = f.capture.start(); await f.advance(30000); await start;
  assert.equal(f.capture.state().status, "failed"); assert.equal(f.capture.state().busy, false); assert.equal(f.timers.size, 0);
  wait.resolve({ session_id: "late", status: "loading", auto_continue: true }); await flush();
  assert.equal(f.capture.state().status, "failed"); assert.equal(f.calls.at(-1).route, "douban/browser/cancel");
  assert.equal(f.calls.at(-1).body.session_id, "late"); assert.equal(f.timers.size, 0);
});

test("cancel and lifecycle changes stop timers and discard a late capture without delivering previews", async () => {
  for (const method of ["cancel", "retire"]) {
    const wait = deferred(), f = fixture(route => route.endsWith("start") ? { session_id: "s", status: "loading", auto_continue: true } : route.endsWith("step") ? wait.promise : {});
    await f.capture.start(); await f.tick(); assert.equal(f.capture.state().busy, true);
    if (method === "cancel") await f.capture.cancel(); else f.retire();
    wait.resolve({ session_id: "s", status: "complete", preview: { provider: "douban" } }); await flush();
    assert.equal(f.previews.length, 0); assert.equal(f.timers.size, 0); assert.equal(f.calls.at(-1).route, "douban/browser/cancel");
  }
  const paused = fixture(() => ({ session_id: "s", status: "loading", auto_continue: true }));
  await paused.capture.start(); paused.retire(); await paused.tick(); assert.equal(paused.timers.size, 0);
  assert.deepEqual(paused.calls.map(row => row.route), ["douban/browser/start", "douban/browser/cancel"]);
});

test("a request error stops timers and shows a recoverable failure", async () => {
  const f = fixture(route => { if (route.endsWith("start")) return { session_id: "s", status: "loading", auto_continue: true }; throw Error("评分连接失败"); });
  await f.capture.start(); await f.tick(); assert.equal(f.capture.state().status, "failed");
  assert.match(f.capture.state().message, /评分连接失败/); assert.equal(f.timers.size, 0); assert.equal(f.previews.length, 0);
});

test("a timed-out capture cannot overwrite a newer explicit recovery or cancel its session", async () => {
  let steps = 0; const old = deferred();
  const f = fixture(route => route.endsWith("start") ? { session_id: "s", status: "loading", auto_continue: true } : ++steps === 1 ? old.promise :
    { session_id: "s", status: "complete", preview: { provider: "douban", preview_token: "new" } });
  await f.capture.start(); await f.tick(); await f.advance(29250); assert.equal(f.capture.state().status, "failed");
  await f.capture.step("continue"); assert.equal(f.previews.length, 1); assert.equal(f.previews[0].preview_token, "new");
  old.resolve({ session_id: "s", status: "complete", preview: { provider: "douban", preview_token: "old" } }); await flush();
  assert.equal(f.previews.length, 1); assert.equal(f.capture.state().preview.preview_token, "new"); assert.equal(f.timers.size, 0);
  assert.equal(f.calls.some(row => row.route.endsWith("cancel")), false);
});

test("rating browser rejects non-rating fields and people previews before confirmation", async () => {
  for (const preview of [{ provider: "tmdb" }, { provider: "douban", diff: [{ field: "name" }] }, { provider: "douban", people_preview: {} }, { provider: "douban", automatic_aliases: ["Unwanted"] }]) {
    const f = fixture(() => ({ session_id: "s", status: "complete", preview })); await f.capture.start();
    assert.equal(f.previews.length, 0); assert.equal(f.capture.state().status, "failed"); assert.match(f.capture.state().message, /非评分资料/);
  }
});
