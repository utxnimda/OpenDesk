import { createModuleBridge, errorMessage } from '@opendesk/module-sdk';
import { parseToolOperation } from '../../../../../apps/framework/module-sdk/src/module-tools';
import { observeHostAppearance, readHostAppearance, writeAppearanceFragment } from '../../../shared/appearance';
import './style.css';
const root = document.querySelector<HTMLDivElement>('#app')!;
const bridge = createModuleBridge();
const channel = 'opendesk.ai.host-tools.v1';
const nonce = crypto.randomUUID();
let frame: HTMLIFrameElement | null = null;
let origin = '';
let disposed = false;
const syncAppearance = () => frame?.contentWindow?.postMessage({
  channel, nonce, type: 'appearance', appearanceVersion: 1, ...readHostAppearance(document.documentElement),
}, origin);
let appearanceObserver: MutationObserver | null = null;
const pending = new Set<string>();
async function receive(event: MessageEvent) {
  const data = event.data;
  if (disposed || event.source !== frame?.contentWindow || event.origin !== origin || !data || data.channel !== channel
      || data.nonce !== nonce || typeof data.id !== 'string' || data.id.length > 100 || pending.has(data.id) || pending.size >= 8) return;
  pending.add(data.id);
  try {
    if (!bridge.host.tools) throw new Error('此功能需要支持插件工具的新版 OpenDesk；API 聊天和 Codex 仍可使用。');
    const op = parseToolOperation(data.action, data.params);
    const result = op.action === 'list' ? await bridge.host.tools.list() : op.action === 'grant'
      ? await bridge.host.tools.grant(op.params.tools) : await bridge.host.tools.call(op.params.tool, op.params.arguments);
    if (!disposed) frame?.contentWindow?.postMessage({ channel, nonce, id: data.id, ok: true, result }, origin);
  } catch (cause) {
    if (!disposed) frame?.contentWindow?.postMessage({ channel, nonce, id: data.id, ok: false, error: errorMessage(cause) }, origin);
  } finally { pending.delete(data.id); }
}
void bridge.ready.then(async () => {
  const status = await bridge.host.callWorker<{ url: string }>('ai.status', {});
  const url = new URL(status.url);
  if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !url.port || url.username || url.password) throw new Error('AI 服务地址无效。');
  origin = url.origin;
  const fragment = new URLSearchParams(url.hash.slice(1));
  fragment.set('hostOrigin', window.location.origin); fragment.set('bridgeNonce', nonce);
  writeAppearanceFragment(fragment, readHostAppearance(document.documentElement));
  fragment.set('hostTools', bridge.host.tools ? '1' : '0'); url.hash = fragment.toString();
  frame = document.createElement('iframe'); frame.title = 'AI 工作台'; frame.referrerPolicy = 'no-referrer';
  frame.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-forms allow-downloads allow-popups allow-popups-to-escape-sandbox'); frame.src = url.href;
  // Resend after every iframe load as an update may have occurred before its listener existed.
  frame.addEventListener('load', syncAppearance);
  root.replaceChildren(frame);
  appearanceObserver = observeHostAppearance(document.documentElement, syncAppearance);
  window.addEventListener('message', receive);
}).catch(cause => { root.textContent = 'AI 工作台启动失败：' + errorMessage(cause); });
window.addEventListener('pagehide', () => { disposed = true; appearanceObserver?.disconnect(); window.removeEventListener('message', receive); bridge.dispose(); }, { once: true });
