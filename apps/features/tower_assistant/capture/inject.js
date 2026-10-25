/* Fixed NW entry point. No paths or scripts are accepted from frontend input. */
function installTowerInjection(host, nodeRequire) {
  'use strict';
  const fs = nodeRequire('fs');
  const path = nodeRequire('path');
  const crypto = nodeRequire('crypto');
  const profiles = {
    'audited-2026-10-25': {
      'data.json': 'f6222404d982bdd5cb57d8e206b70ccf5e7c3f7b0f18891ca0d387c68ab92001',
      'scripts/c3runtime.js': '11b5942035ae1fc3185abb5550f97abbe70e1451679698da2e169e9c7ee3753c',
    },
    'audited-2026-10-25-r2': {
      'data.json': '9e5697adeb11a0c1daf40be6086b56f58755562f3560ad1772d771445e167ed2',
      'scripts/c3runtime.js': '2546ca0823f858f3ce2a2e1dd013ed70f7e4d330e8d6c33d7ce75fb2a75d6936',
    },
  };
  const gameRoot = path.dirname(host.process.execPath);
  const directory = path.join(gameRoot, '.opendesk-capture');
  const configPath = path.join(directory, 'config.json');
  const streamPath = path.join(directory, 'stream.json');
  const maximumBytes = 2 * 1024 * 1024;
  let config;
  let realDirectory;
  try {
    if (fs.lstatSync(directory).isSymbolicLink() || fs.lstatSync(configPath).isSymbolicLink()) throw new Error('linked-path');
    realDirectory = fs.realpathSync(directory);
    if (path.dirname(realDirectory) !== fs.realpathSync(gameRoot)) throw new Error('unexpected-directory');
    if (fs.statSync(configPath).size > 16384) throw new Error('config-too-large');
    config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
    if (config.schemaVersion !== 1 || !Object.hasOwn(profiles, config.ruleProfile) || !/^[a-f0-9]{64}$/.test(config.token)) throw new Error('invalid-config');
  } catch (_) {
    host.console.error('OpenDesk capture configuration is unavailable or unsupported; game continues without capture');
    return null;
  }
  const profile = config.ruleProfile;
  const hashes = profiles[profile];
  const streamId = crypto.randomUUID();
  let lastSequence = 0;
  let dropped = 0;
  let heartbeatMs = 0;
  let captureError = null;
  let ready = false;
  let writing = false;
  let stopped = false;
  let ringBytes = 0;
  const events = [];
  const eventSizes = [];
  const fail = message => { if (!captureError) captureError = message; };
  const snapshot = () => ({schemaVersion: 1, token: config.token, ruleProfile: profile, streamId,
    lastSequence, dropped, heartbeatMs, error: captureError || (ready ? null : '采集引擎尚未初始化'), events});
  async function flush() {
    if (writing || stopped) return;
    writing = true;
    const temporary = path.join(directory, `.stream-${streamId}.tmp`);
    let created = false;
    try {
      if (fs.lstatSync(directory).isSymbolicLink() || fs.realpathSync(directory) !== realDirectory) throw new Error('directory-changed');
      if (fs.existsSync(streamPath) && fs.lstatSync(streamPath).isSymbolicLink()) throw new Error('linked-stream');
      let encoded = JSON.stringify(snapshot());
      while (Buffer.byteLength(encoded, 'utf8') > maximumBytes && events.length) {
        events.shift(); ringBytes -= eventSizes.shift(); encoded = JSON.stringify(snapshot());
      }
      const file = await fs.promises.open(temporary, 'wx');
      created = true;
      try { await file.writeFile(encoded, 'utf8'); } finally { await file.close(); }
      // Windows readers/antivirus may briefly hold a handle without delete
      // sharing. Keep the old complete snapshot until atomic replacement works;
      // never unlink the destination. The entire retry budget is 150 ms.
      const delays = [10, 20, 40, 80];
      for (let attempt = 0; ; attempt++) {
        try { await fs.promises.rename(temporary, streamPath); break; }
        catch (error) {
          if (!['EPERM', 'EACCES', 'EBUSY'].includes(error.code) || attempt >= delays.length) throw error;
          await new Promise(resolve => host.setTimeout(resolve, delays[attempt]));
        }
      }
      created = false;
    } catch (_) {
      fail('采集文件写入失败，请检查游戏目录权限或采集文件占用');
      host.console.error('OpenDesk capture stream write failed');
    } finally {
      if (created) {
        try { await fs.promises.unlink(temporary); }
        catch (_) { fail('采集临时文件清理失败'); }
      }
      writing = false;
    }
  }
  function accept(message) {
    if (!message || typeof message !== 'object') { dropped++; fail('采集消息格式无效'); return; }
    if (message.type === 'error') { fail(String(message.error).slice(0, 240)); return; }
    if (message.type === 'ready') { ready = true; heartbeatMs = Date.now(); return; }
    if (message.type === 'heartbeat') { if (ready && message.ready) heartbeatMs = Date.now(); return; }
    if (message.type !== 'event') { dropped++; fail('未知采集消息'); return; }
    const row = message.event;
    if (!row || row.schemaVersion !== 1 || !Number.isSafeInteger(row.sequence) || row.sequence !== lastSequence + 1 ||
        !['work', 'damage'].includes(row.kind) || typeof row.personId !== 'string' || typeof row.personName !== 'string' ||
        typeof row.category !== 'string' || !Number.isFinite(row.amount) || row.amount < 0) {
      dropped++; fail('采集事件不连续或内容无效'); return;
    }
    const size = Buffer.byteLength(JSON.stringify(row), 'utf8');
    lastSequence = row.sequence;
    if (size > 65536) { dropped++; fail('单次采集事件超出大小限制'); return; }
    events.push(row); eventSizes.push(size); ringBytes += size;
    // Rolling history expiration is not loss. Readers detect falling behind by
    // sequence gap. dropped is reserved for actual rejected/lost capture data.
    while (events.length > 1000 || ringBytes > maximumBytes - 4096) {
      events.shift(); ringBytes -= eventSizes.shift();
    }
  }
  function readPackageBytes(relative) {
    const request = new host.XMLHttpRequest();
    request.open('GET', new host.URL(relative, host.location.href).href, false);
    request.overrideMimeType('text/plain; charset=x-user-defined');
    request.send();
    if (request.status !== 200 && request.status !== 0) throw new Error('package-read-failed');
    return Buffer.from(request.responseText, 'latin1');
  }
  try {
    for (const [name, expected] of Object.entries(hashes)) {
      if (crypto.createHash('sha256').update(readPackageBytes(name)).digest('hex') !== expected) throw new Error('unsupported-game-hash');
    }
    const observerSource = readPackageBytes('opendesk-capture/observer.js').toString('utf8');
    if (!observerSource.includes('function installTowerCapture(')) throw new Error('missing-observer');
    const entrySource = readPackageBytes('opendesk-capture-worker.js').toString('utf8');
    if (!entrySource.includes('function startCaptureWorker(')) throw new Error('missing-worker-entry');
  } catch (_) { fail('游戏规则或采集脚本与已核验版本不匹配，请重新检查采集入口'); }
  let runtimeWorker = null;
  let bootstrapTimer = null;
  if (!captureError) {
    const NativeWorker = host.Worker;
    const channel = crypto.randomUUID();
    host.Worker = new Proxy(NativeWorker, {construct(target, args, newTarget) {
      const [url, options] = args;
      if (!options?.isC3MainWorker || options.name !== 'Runtime') return Reflect.construct(target, args, newTarget);
      const original = new host.URL(String(url), host.location.href).href;
      const auditedEntry = new host.URL('workermain.js', host.location.href).href;
      if (options.type !== 'module' || original !== auditedEntry) {
        fail('未支持的游戏运行时入口，游戏继续使用原入口');
        return Reflect.construct(target, args, newTarget);
      }
      if (runtimeWorker) { fail('发现多个运行时，采集已停止完整性保证'); return Reflect.construct(target, args, newTarget); }
      const entry = new host.URL('opendesk-capture-worker.js', original).href;
      let worker;
      try { worker = Reflect.construct(target, [entry, options], newTarget); }
      catch (_) {
        fail('采集运行时创建失败，游戏继续使用原入口');
        return Reflect.construct(target, args, newTarget);
      }
      runtimeWorker = worker;
      let activeWorker = worker;
      let bootstrapReady = false;
      let terminated = false;
      const queued = [];
      const nativePost = worker.postMessage.bind(worker);
      const nativeTerminate = worker.terminate.bind(worker);
      const clearBootstrapTimer = () => { if (bootstrapTimer !== null) host.clearTimeout(bootstrapTimer); bootstrapTimer = null; };
      const flushQueued = () => {
        for (const messageArgs of queued.splice(0)) {
          if (activeWorker === worker) Reflect.apply(nativePost, worker, messageArgs);
          else Reflect.apply(activeWorker.postMessage, activeWorker, messageArgs);
        }
      };
      const fallback = reason => {
        if (terminated || bootstrapReady || activeWorker !== worker) return;
        clearBootstrapTimer(); fail(`${reason}；游戏继续使用原入口`);
        // The transferred canvas/ports are still owned by the main thread until
        // bootstrap-ready, so fallback can forward the original init intact.
        nativeTerminate();
        activeWorker = Reflect.construct(target, args, newTarget);
        for (const type of ['message', 'error', 'messageerror']) activeWorker.addEventListener(type, event => {
          const forwarded = type === 'error' ? new event.constructor(type, {
            message: event.message, filename: event.filename, lineno: event.lineno, colno: event.colno, error: event.error,
          }) : new event.constructor(type, {data: event.data, ports: event.ports});
          worker.dispatchEvent(forwarded);
        });
        flushQueued();
      };
      worker.postMessage = function(...messageArgs) {
        if (terminated) return Reflect.apply(nativePost, worker, messageArgs);
        if (!bootstrapReady && activeWorker === worker) { queued.push(messageArgs); return; }
        if (activeWorker === worker) return Reflect.apply(nativePost, worker, messageArgs);
        return Reflect.apply(activeWorker.postMessage, activeWorker, messageArgs);
      };
      worker.terminate = function() {
        terminated = true; queued.length = 0; clearBootstrapTimer();
        return activeWorker === worker ? nativeTerminate() : activeWorker.terminate();
      };
      worker.addEventListener('message', event => {
        if (event.data?.captureChannel !== channel) return;
        event.stopImmediatePropagation();
        if (event.data.record?.type === 'bootstrap-ready') {
          if (activeWorker === worker && !terminated) {
            bootstrapReady = true; clearBootstrapTimer();
            try { flushQueued(); } catch (_) { fail('游戏初始化消息移交失败'); }
          }
          return;
        }
        observeMessage(event.data.record);
      });
      worker.addEventListener('error', event => {
        if (!bootstrapReady && activeWorker === worker) { event.stopImmediatePropagation(); fallback('采集入口启动失败'); }
        else fail(`游戏运行时发生错误：${String(event.message || '未知错误').slice(0, 180)}`);
      });
      worker.addEventListener('messageerror', () => fail('采集运行时消息无法解码'));
      bootstrapTimer = host.setTimeout(() => fallback('采集入口握手超时'), 3000);
      try { nativePost({type: 'opendesk-capture-init', schemaVersion: 1, channel, ruleProfile: profile}); }
      catch (_) { fallback('采集入口握手发送失败'); }
      return worker;
    }});
  }
  function observeMessage(message) {
    try { accept(message); }
    catch (_) { dropped++; fail('采集消息处理失败'); }
  }
  const timer = host.setInterval(() => { void flush(); }, 250);
  void flush();
  return {flush, snapshot, stop() {
    stopped = true; host.clearInterval(timer);
    if (bootstrapTimer !== null) host.clearTimeout(bootstrapTimer);
  }};
}
if (typeof window !== 'undefined' && typeof require === 'function') installTowerInjection(window, require);
else if (typeof module === 'object' && module.exports) module.exports = {installTowerInjection};
