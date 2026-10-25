// OpenDesk fixed same-directory worker entry. Installed at package root as
// opendesk-capture-worker.js, alongside the audited original workermain.js.
// Keeping a real file URL preserves fetch, FontFace, WASM and image URL bases.
export async function startCaptureWorker(scope, loadModule, timeoutMs = 1500) {
  let config = null;
  let resolveConfig;
  const pending = [];
  const configured = new Promise(resolve => { resolveConfig = resolve; });
  const describe = error => String(error?.message || error).replace(/[\x00-\x1f]/g, ' ').slice(0, 180);
  const publish = record => {
    if (!config) return;
    try { scope.postMessage({captureChannel: config.channel, record}); }
    catch (_) { scope.console.error('OpenDesk worker could not report capture status'); }
  };
  const gate = event => {
    event.stopImmediatePropagation();
    if (event.data?.type !== 'opendesk-capture-init') { pending.push(event); return; }
    const data = event.data;
    if (config || data.schemaVersion !== 1 || typeof data.channel !== 'string' ||
        !/^[0-9a-f-]{36}$/.test(data.channel) || !['audited-2026-10-25', 'audited-2026-10-25-r2'].includes(data.ruleProfile)) {
      publish({type: 'error', error: '采集入口握手无效；游戏继续使用原入口'}); return;
    }
    config = {channel: data.channel, profile: data.ruleProfile};
    resolveConfig(config);
  };
  scope.addEventListener('message', gate);
  const timer = scope.setTimeout(() => resolveConfig(null), timeoutMs);
  await configured;
  scope.clearTimeout(timer);
  scope.addEventListener('unhandledrejection', event => publish({type: 'error',
    error: `游戏运行时初始化失败：${describe(event.reason)}`}));
  if (config) {
    try {
      const observer = await loadModule('./opendesk-capture/observer.js');
      observer.installTowerCapture(scope, publish, config.profile);
    } catch (error) {
      publish({type: 'error', error: `采集观察器加载失败，游戏继续使用原入口：${describe(error)}`});
    }
  }
  try { await loadModule('./workermain.js'); }
  catch (error) {
    publish({type: 'error', error: `原游戏入口导入失败：${describe(error)}`});
    throw error;
  }
  scope.removeEventListener('message', gate);
  // No control frame is ever forwarded to the game. Preserve the native event
  // constructor: NW's Node MessageEvent is not the DOM event constructor.
  for (const event of pending) scope.dispatchEvent(new event.constructor('message', {
    data: event.data, ports: event.ports, origin: event.origin, lastEventId: event.lastEventId,
  }));
  publish({type: 'bootstrap-ready'});
}
if (typeof WorkerGlobalScope !== 'undefined' && self instanceof WorkerGlobalScope) {
  void startCaptureWorker(self, specifier => {
    if (specifier === './opendesk-capture/observer.js') return import('./opendesk-capture/observer.js');
    if (specifier === './workermain.js') return import('./workermain.js');
    throw new Error('Unsupported capture worker module');
  });
}
