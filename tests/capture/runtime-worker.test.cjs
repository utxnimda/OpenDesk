const assert = require('node:assert/strict');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const test = require('node:test');
let startCaptureWorker;
test.before(async () => {
  ({startCaptureWorker} = await import(pathToFileURL(path.resolve(__dirname, '../../apps/features/tower_assistant/capture/runtime-worker.js')).href));
});
class FixtureEvent {
  constructor(type, init) { this.type = type; Object.assign(this, init); this.stopped = false; }
  stopImmediatePropagation() { this.stopped = true; }
}
function fixture(mode = 'normal') {
  const handlers = new Map(); const reports = []; const gameEvents = []; const imports = [];
  const scope = {console, setTimeout, clearTimeout, postMessage: data => reports.push(data),
    addEventListener(type, handler) { handlers.set(type, [...(handlers.get(type) || []), handler]); },
    removeEventListener(type, handler) { handlers.set(type, handlers.get(type).filter(h => h !== handler)); },
    dispatchEvent(event) { for (const handler of [...(handlers.get(event.type) || [])]) { handler(event); if (event.stopped) break; } },
  };
  const load = async specifier => {
    imports.push(specifier);
    if (specifier.endsWith('observer.js')) {
      if (mode === 'import-failure') throw new Error('synthetic observer module error');
      return {installTowerCapture(actualScope, send, profile) {
        if (mode === 'install-failure') throw new Error('synthetic observer install error');
        assert.equal(actualScope, scope); assert.equal(profile, 'audited-2026-10-25-r2');
      }};
    }
    scope.addEventListener('message', event => gameEvents.push(event));
    return {};
  };
  const post = (data, ports = []) => scope.dispatchEvent(new FixtureEvent('message', {data, ports}));
  const handshake = () => post({type: 'opendesk-capture-init', schemaVersion: 1,
    channel: '01234567-89ab-cdef-0123-456789abcdef', ruleProfile: 'audited-2026-10-25-r2'});
  return {scope, load, reports, gameEvents, imports, post, handshake};
}
for (const mode of ['normal', 'import-failure', 'install-failure']) {
  test(`worker ${mode} preserves queued init and excludes its control frame`, async () => {
    const f = fixture(mode); const started = startCaptureWorker(f.scope, f.load);
    f.handshake(); const data = {type: 'init-runtime', canvas: {}}; const ports = [{}]; f.post(data, ports);
    await started;
    assert.deepEqual(f.imports, ['./opendesk-capture/observer.js', './workermain.js']);
    assert.equal(f.gameEvents.length, 1); assert.equal(f.gameEvents[0].data, data); assert.equal(f.gameEvents[0].ports, ports);
    assert.equal(f.reports.at(-1).record.type, 'bootstrap-ready');
    if (mode !== 'normal') assert.match(f.reports[0].record.error, /观察器加载失败.*synthetic observer/);
  });
}
test('worker handshake timeout still loads the original game and replays its initialization', async () => {
  const f = fixture(); const started = startCaptureWorker(f.scope, f.load, 5);
  const data = {type: 'init-runtime'}; f.post(data);
  await started;
  assert.deepEqual(f.imports, ['./workermain.js']); assert.equal(f.gameEvents[0].data, data);
});
test('runtime promise rejection becomes a specific diagnostic without suppressing the original error', async () => {
  const f = fixture(); const started = startCaptureWorker(f.scope, f.load); f.handshake(); await started;
  f.scope.dispatchEvent(new FixtureEvent('unhandledrejection', {reason: new Error('relative asset failed')}));
  assert.match(f.reports.at(-1).record.error, /运行时初始化失败.*relative asset failed/);
});
