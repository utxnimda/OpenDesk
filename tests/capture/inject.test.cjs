const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync(path.resolve(__dirname, '../../apps/features/tower_assistant/capture/inject.js'), 'utf8');
const workerSource = fs.readFileSync(path.resolve(__dirname, '../../apps/features/tower_assistant/capture/runtime-worker.js'), 'utf8');
const moduleContext = {module: {exports: {}}, Buffer, Date};
vm.runInNewContext(source, moduleContext);
const {installTowerInjection} = moduleContext.module.exports;
const hashes = ['f6222404d982bdd5cb57d8e206b70ccf5e7c3f7b0f18891ca0d387c68ab92001',
  '11b5942035ae1fc3185abb5550f97abbe70e1451679698da2e169e9c7ee3753c'];
const r2Hashes = ['9e5697adeb11a0c1daf40be6086b56f58755562f3560ad1772d771445e167ed2',
  '2546ca0823f858f3ce2a2e1dd013ed70f7e4d330e8d6c33d7ce75fb2a75d6936'];
const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

function fixture(t, {profile = 'audited-2026-10-25', packageProfile = profile, wrongHash = false} = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'opendesk-capture-test-'));
  const directory = path.join(root, '.opendesk-capture'); fs.mkdirSync(directory);
  const token = 'a'.repeat(64);
  fs.writeFileSync(path.join(directory, 'config.json'), JSON.stringify({schemaVersion: 1, token, ruleProfile: profile}));
  const blobs = new Map();
  let renameFailures = 0;
  let renameCalls = 0;
  const captureFs = {...fs, promises: {...fs.promises, async rename(from, to) {
    renameCalls++;
    if (renameFailures > 0) { renameFailures--; const error = new Error('temporary sharing lock'); error.code = 'EPERM'; throw error; }
    return fs.promises.rename(from, to);
  }}};
  let nextBlob = 0;
  class FixtureURL extends URL {
    static createObjectURL(blob) { const url = `blob:fixture/${++nextBlob}`; blobs.set(url, blob.source); return url; }
    static revokeObjectURL(url) { blobs.delete(url); }
  }
  class FixtureWorker {
    static instances = [];
    static rejectCapture = false;
    constructor(url, options) {
      if (FixtureWorker.rejectCapture && String(url).endsWith('opendesk-capture-worker.js')) throw new Error('worker-start-failed');
      this.url = url; this.options = options; this.listeners = new Map(); this.sent = []; this.terminated = false;
      FixtureWorker.instances.push(this);
    }
    postMessage(...args) { this.sent.push(args); }
    terminate() { this.terminated = true; }
    addEventListener(type, fn) { const handlers = this.listeners.get(type) || []; handlers.push(fn); this.listeners.set(type, handlers); }
    dispatchEvent(event) {
      for (const listener of this.listeners.get(event.type) || []) { listener(event); if (event.stopped) break; }
      if (!event.stopped && this[`on${event.type}`]) this[`on${event.type}`](event);
    }
    emit(type, data) {
      this.dispatchEvent(new FixtureEvent(type, type === 'error' ? data : {data}));
    }
  }
  class FixtureEvent {
    constructor(type, init) { this.type = type; Object.assign(this, init); this.stopped = false; }
    stopImmediatePropagation() { this.stopped = true; }
  }
  class FixtureXHR {
    open(method, url, async) { assert.equal(method, 'GET'); assert.equal(async, false); this.url = url; }
    overrideMimeType(type) { assert.equal(type, 'text/plain; charset=x-user-defined'); }
    send() {
      this.status = 200;
      this.responseText = this.url.endsWith('data.json') ? 'data-bytes' : this.url.endsWith('c3runtime.js') ? 'runtime-bytes' :
        this.url.endsWith('opendesk-capture-worker.js') ? workerSource : 'function installTowerCapture(scope, send) {}';
    }
  }
  const host = {process: {execPath: path.join(root, 'game.exe')}, console: {error() {}},
    location: {href: 'chrome-extension://fixture/index.html'}, URL: FixtureURL,
    Blob: class { constructor(parts) { this.source = parts.join(''); } }, XMLHttpRequest: FixtureXHR, Worker: FixtureWorker,
    setInterval() { return 1; }, clearInterval() {}, setTimeout, clearTimeout};
  const fakeCrypto = {randomUUID: crypto.randomUUID, createHash(algorithm) {
    assert.equal(algorithm, 'sha256'); let bytes;
    return {update(value) { bytes = value.toString(); return this; }, digest(format) {
      assert.equal(format, 'hex'); return wrongHash ? 'b'.repeat(64) :
        (packageProfile === 'audited-2026-10-25-r2' ? r2Hashes : hashes)[bytes === 'data-bytes' ? 0 : 1];
    }};
  }};
  const controller = installTowerInjection(host, name => name === 'crypto' ? fakeCrypto : name === 'fs' ? captureFs : require(name));
  t.after(async () => { controller?.stop(); await sleep(25); fs.rmSync(root, {recursive: true, force: true}); });
  function runtime() {
    const worker = new host.Worker('workermain.js', {type: 'module', name: 'Runtime', isC3MainWorker: true});
    const control = worker.sent[0]?.[0];
    const channel = control?.channel;
    return {worker, bootstrap: workerSource, control, send: record => worker.emit('message', {captureChannel: channel, record})};
  }
  async function read() {
    await sleep(15); await controller.flush();
    return JSON.parse(fs.readFileSync(path.join(directory, 'stream.json'), 'utf8'));
  }
  return {root, directory, token, host, controller, runtime, read, FixtureWorker,
    lockRenames: count => { renameFailures = count; }, renameCalls: () => renameCalls};
}
const row = sequence => ({schemaVersion: 1, sequence, kind: 'work', personId: '7', personName: '甲', category: '采集', amount: 1000000});

test('fixed executable directory, initial snapshot and ordinary Worker behavior', async t => {
  const f = fixture(t);
  const plain = new f.host.Worker('other.js', {name: 'Other'});
  assert.equal(plain.url, 'other.js'); assert.ok(plain instanceof f.FixtureWorker);
  const snapshot = await f.read();
  assert.equal(snapshot.lastSequence, 0); assert.equal(snapshot.heartbeatMs, 0);
  assert.equal(snapshot.error, '采集引擎尚未初始化'); assert.equal(snapshot.token, f.token);
  assert.deepEqual(snapshot.events, []);
  assert.equal(fs.readdirSync(f.directory).some(name => name.endsWith('.tmp')), false);
});

test('Runtime uses a same-directory file and holds transfer objects until the original entry is ready', async t => {
  const f = fixture(t); const {worker, bootstrap, send} = f.runtime();
  assert.match(bootstrap, /new event\.constructor\('message'/);
  assert.match(bootstrap, /loadModule\('\.\/workermain\.js'\)/);
  assert.equal(worker.url, 'chrome-extension://fixture/opendesk-capture-worker.js');
  assert.equal(worker.options.type, 'module');
  const data = {type: 'init-runtime'}; const transfers = [{port: true}, {canvas: true}];
  worker.postMessage(data, transfers);
  assert.equal(worker.sent.length, 1);
  send({type: 'bootstrap-ready'});
  assert.equal(worker.sent[1][0], data); assert.equal(worker.sent[1][1], transfers);
  let gameMessages = 0; worker.addEventListener('message', () => { gameMessages++; });
  send({type: 'ready'}); send({type: 'event', event: row(1)}); send({type: 'heartbeat', ready: true});
  worker.emit('message', {normalGameMessage: true});
  const snapshot = await f.read();
  assert.equal(snapshot.error, null); assert.ok(snapshot.heartbeatMs > 0);
  assert.equal(snapshot.lastSequence, 1); assert.equal(snapshot.events[0].amount, 1000000);
  assert.equal(gameMessages, 1);
});

test('rolling expiration is bounded history; it does not increment dropped', async t => {
  const f = fixture(t); const {send} = f.runtime(); send({type: 'ready'});
  for (let sequence = 1; sequence <= 1050; sequence++) send({type: 'event', event: row(sequence)});
  const snapshot = await f.read();
  assert.equal(snapshot.events.length, 1000); assert.equal(snapshot.events[0].sequence, 51);
  assert.equal(snapshot.lastSequence, 1050); assert.equal(snapshot.dropped, 0);
  assert.ok(fs.statSync(path.join(f.directory, 'stream.json')).size <= 2 * 1024 * 1024);
});

test('serialized stream stays under two MiB even with large events', async t => {
  const f = fixture(t); const {send} = f.runtime(); send({type: 'ready'});
  for (let sequence = 1; sequence <= 100; sequence++) send({type: 'event', event: {...row(sequence), targetName: 'x'.repeat(50000)}});
  const snapshot = await f.read();
  assert.ok(snapshot.events.length < 100); assert.equal(snapshot.dropped, 0);
  assert.ok(fs.statSync(path.join(f.directory, 'stream.json')).size <= 2 * 1024 * 1024);
});

test('sequence gap and observer error remain visible after later heartbeat', async t => {
  const f = fixture(t); const {send} = f.runtime(); send({type: 'ready'});
  send({type: 'event', event: row(1)}); send({type: 'event', event: row(3)});
  send({type: 'heartbeat', ready: true});
  const snapshot = await f.read();
  assert.equal(snapshot.lastSequence, 1); assert.equal(snapshot.dropped, 1);
  assert.match(snapshot.error, /不连续/);
});

test('oversized individual event increments dropped and records its lost sequence', async t => {
  const f = fixture(t); const {send} = f.runtime(); send({type: 'ready'});
  send({type: 'event', event: {...row(1), targetName: 'x'.repeat(66000)}});
  send({type: 'event', event: row(2)});
  const snapshot = await f.read();
  assert.equal(snapshot.lastSequence, 2); assert.equal(snapshot.dropped, 1);
  assert.equal(snapshot.events[0].sequence, 2); assert.match(snapshot.error, /大小/);
});

test('unsupported profile and changed package hashes never install a Worker wrapper', async t => {
  const invalid = fixture(t, {profile: 'unknown'});
  assert.equal(invalid.controller, null); assert.equal(invalid.host.Worker, invalid.FixtureWorker);
  const changed = fixture(t, {wrongHash: true});
  assert.equal(changed.host.Worker, changed.FixtureWorker);
  const snapshot = await changed.read();
  assert.match(snapshot.error, /不匹配/); assert.equal(snapshot.heartbeatMs, 0);
});

test('a write failure preserves the original stream and becomes a sticky error on recovery', async t => {
  const f = fixture(t); const {send} = f.runtime(); send({type: 'ready'});
  const initial = await f.read();
  const blocker = path.join(f.directory, `.stream-${initial.streamId}.tmp`);
  fs.writeFileSync(blocker, 'unowned-existing-file');
  send({type: 'event', event: row(1)}); await f.controller.flush();
  assert.equal(JSON.parse(fs.readFileSync(path.join(f.directory, 'stream.json'))).lastSequence, 0);
  assert.equal(fs.readFileSync(blocker, 'utf8'), 'unowned-existing-file');
  fs.unlinkSync(blocker);
  const recovered = await f.read();
  assert.equal(recovered.lastSequence, 1); assert.match(recovered.error, /写入失败/);
});

test('same-directory Worker constructor failure leaves the original game Worker available', async t => {
  const f = fixture(t);
  f.FixtureWorker.rejectCapture = true;
  const worker = new f.host.Worker('workermain.js', {type: 'module', name: 'Runtime', isC3MainWorker: true});
  assert.equal(worker.url, 'workermain.js');
  const snapshot = await f.read();
  assert.match(snapshot.error, /原入口/);
});

test('temporary Windows rename sharing locks retry without reporting a failed capture', async t => {
  const f = fixture(t); const {send} = f.runtime(); send({type: 'ready'}); await f.read();
  const initialCalls = f.renameCalls();
  f.lockRenames(2); send({type: 'event', event: row(1)});
  await f.controller.flush();
  const snapshot = JSON.parse(fs.readFileSync(path.join(f.directory, 'stream.json'), 'utf8'));
  assert.equal(f.renameCalls() - initialCalls, 3);
  assert.equal(snapshot.lastSequence, 1); assert.equal(snapshot.error, null);
  assert.equal(f.controller.snapshot().error, null);
});

test('persistent Windows rename lock exhausts its bounded retry and preserves the previous snapshot', async t => {
  const f = fixture(t); const {send} = f.runtime(); send({type: 'ready'}); await f.read();
  const initialCalls = f.renameCalls();
  f.lockRenames(Infinity); send({type: 'event', event: row(1)});
  await f.controller.flush();
  assert.equal(f.renameCalls() - initialCalls, 5);
  const previous = JSON.parse(fs.readFileSync(path.join(f.directory, 'stream.json'), 'utf8'));
  assert.equal(previous.lastSequence, 0);
  assert.match(f.controller.snapshot().error, /写入失败/);
  assert.equal(fs.readdirSync(f.directory).some(name => name.endsWith('.tmp')), false);
  f.lockRenames(0);
  const recovered = await f.read();
  assert.equal(recovered.lastSequence, 1); assert.match(recovered.error, /写入失败/);
});

test('r2 package hashes select and pass only the explicit r2 observer profile', async t => {
  const f = fixture(t, {profile: 'audited-2026-10-25-r2'});
  const {control, send} = f.runtime();
  assert.equal(control.ruleProfile, 'audited-2026-10-25-r2');
  send({type: 'ready'}); send({type: 'event', event: row(1)});
  const snapshot = await f.read();
  assert.equal(snapshot.ruleProfile, 'audited-2026-10-25-r2');
  assert.equal(snapshot.error, null);
});

test('startup error falls back before transferring init and forwards onmessage plus listeners', async t => {
  const f = fixture(t); const {worker} = f.runtime();
  const data = {type: 'init-runtime'}; const transfers = [{port: true}, {canvas: true}];
  worker.postMessage(data, transfers);
  const received = [];
  worker.addEventListener('message', event => received.push(['listener', event.data, event.ports]));
  worker.onmessage = event => received.push(['onmessage', event.data, event.ports]);
  worker.emit('error', {message: 'capture-entry-missing'});
  const original = f.FixtureWorker.instances[1];
  assert.equal(worker.terminated, true); assert.equal(original.url, 'workermain.js');
  assert.equal(original.sent[0][0], data); assert.equal(original.sent[0][1], transfers);
  const reply = {started: true}; const ports = [{}];
  original.dispatchEvent(new (class {
    constructor(type, init) { this.type = type; Object.assign(this, init); }
    stopImmediatePropagation() { this.stopped = true; }
  })('message', {data: reply, ports}));
  assert.equal(received.length, 2); assert.equal(received[0][1], reply); assert.equal(received[1][2], ports);
  worker.postMessage({later: true}); assert.equal(original.sent.length, 2);
  worker.terminate(); assert.equal(original.terminated, true);
  assert.match(f.controller.snapshot().error, /启动失败.*原入口/);
});

test('handshake timeout falls back, while terminate cancels fallback without restarting', async t => {
  const f = fixture(t); f.host.setTimeout = (fn, delay) => setTimeout(fn, delay === 3000 ? 10 : delay);
  const {worker} = f.runtime(); worker.postMessage({type: 'init-runtime'}, [{}]);
  await sleep(25);
  assert.equal(f.FixtureWorker.instances.length, 2); assert.equal(f.FixtureWorker.instances[1].sent.length, 1);
  assert.match(f.controller.snapshot().error, /握手超时/);
  const stopped = fixture(t); stopped.host.setTimeout = (fn, delay) => setTimeout(fn, delay === 3000 ? 10 : delay);
  const running = stopped.runtime().worker; running.postMessage({type: 'init-runtime'}, [{}]); running.terminate();
  await sleep(25); assert.equal(stopped.FixtureWorker.instances.length, 1);
});

test('unknown runtime URL or worker type passes through without replacing its entry', t => {
  const f = fixture(t);
  const other = new f.host.Worker('sub/workermain.js', {type: 'module', name: 'Runtime', isC3MainWorker: true});
  assert.equal(other.url, 'sub/workermain.js'); assert.equal(other.sent.length, 0);
  const classic = new f.host.Worker('workermain.js', {name: 'Runtime', isC3MainWorker: true});
  assert.equal(classic.url, 'workermain.js'); assert.equal(classic.sent.length, 0);
});

test('a valid profile cannot be paired with the other audited package hashes', async t => {
  const f = fixture(t, {profile: 'audited-2026-10-25', packageProfile: 'audited-2026-10-25-r2'});
  assert.equal(f.host.Worker, f.FixtureWorker);
  const snapshot = await f.read();
  assert.match(snapshot.error, /不匹配/);
});
