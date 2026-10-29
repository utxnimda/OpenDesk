/** Explicit, origin-bound protocol between this Web App and its verified module wrapper. */
import { applyAppearance, parseAppearanceMessage, readAppearanceFragment } from '../shared/appearance';
export interface HostTool { name: string; title: string; description: string; moduleId: string; version: string;
  digest: string; generation: string; inputSchema: Record<string, unknown>; outputSchema?: Record<string, unknown>; readOnly: true }
const fragment = new URLSearchParams(location.hash.slice(1));
const parentOrigin = fragment.get('hostOrigin') ?? '';
const nonce = fragment.get('bridgeNonce') ?? '';
const channel = 'opendesk.ai.host-tools.v1';
applyAppearance(document.documentElement, readAppearanceFragment(fragment));
let sequence = 0;
const pending = new Map<string, { resolve(v: unknown): void; reject(e: Error): void; timer: ReturnType<typeof setTimeout> }>();
export function available(): boolean { return window.parent !== window && fragment.get('hostTools') === '1' && /^http:\/\/127\.0\.0\.1:\d+$/.test(parentOrigin) && !!nonce; }
window.addEventListener('message', event => {
  const d = event.data;
  if (event.source !== window.parent || event.origin !== parentOrigin || !d || d.channel !== channel || d.nonce !== nonce) return;
  if (d.type === 'appearance') {
    const appearance = parseAppearanceMessage(d);
    if (appearance) applyAppearance(document.documentElement, appearance);
    return;
  }
  const item = pending.get(d.id); if (!item) return;
  pending.delete(d.id); clearTimeout(item.timer);
  if (d.ok === true) item.resolve(d.result); else item.reject(new Error(typeof d.error === 'string' ? d.error : '工具调用失败。'));
});
function request(action: string, params: unknown): Promise<unknown> {
  if (!available()) return Promise.reject(new Error('请在支持插件工具的 OpenDesk 中打开 AI 工作台。'));
  const id = nonce + ':' + (++sequence);
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('工具连接超时；未自动重试。')); }, 185000);
    pending.set(id, { resolve, reject, timer });
    window.parent.postMessage({ channel, nonce, id, action, params }, parentOrigin);
  });
}
function identity(t: HostTool) { return { name:t.name,moduleId:t.moduleId,version:t.version,digest:t.digest,generation:t.generation }; }
export async function listTools(): Promise<HostTool[]> { return await request('list', {}) as HostTool[]; }
export async function grantTools(tools: HostTool[]): Promise<void> { await request('grant', { tools: tools.map(identity) }); }
export function callTool(tool: HostTool, args: Record<string, unknown>): Promise<unknown> { return request('call', { tool: identity(tool), arguments: args }); }
