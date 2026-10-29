/** Optional host capability: consented read-only calls to another active module. */
export interface HostToolIdentity { name: string; moduleId: string; version: string; digest: string; generation: string }
export interface HostTool extends HostToolIdentity {
  title: string; description: string; inputSchema: Record<string, unknown>;
  readOnly: true; contractVersion: 1;
}
export type ToolOperation = { action: 'list'; params: Record<string, never> }
  | { action: 'grant'; params: { tools: HostToolIdentity[] } }
  | { action: 'call'; params: { tool: HostToolIdentity; arguments: Record<string, never> } };
export interface ModuleToolsClient {
  list(): Promise<HostTool[]>;
  grant(tools: HostToolIdentity[]): Promise<void>;
  call(tool: HostToolIdentity, args: Record<string, unknown>): Promise<unknown>;
}
export function toolIdentity(value: unknown): HostToolIdentity {
  if (!value || typeof value !== 'object') throw new Error('工具身份无效。');
  const v = value as Record<string, unknown>;
  const keys = ['name', 'moduleId', 'version', 'digest', 'generation'] as const;
  if (keys.some(k => typeof v[k] !== 'string' || !v[k] || (v[k] as string).length > 128)) throw new Error('工具身份不完整。');
  return Object.fromEntries(keys.map(k => [k, v[k]])) as unknown as HostToolIdentity;
}
export function parseToolOperation(action: unknown, params: unknown): ToolOperation {
  if (!params || typeof params !== 'object' || Array.isArray(params) || JSON.stringify(params).length > 16384) throw new Error('工具请求格式无效。');
  const p = params as Record<string, unknown>;
  if (action === 'list' && Object.keys(p).length === 0) return { action, params: {} };
  if (action === 'grant' && Object.keys(p).join() === 'tools' && Array.isArray(p.tools) && p.tools.length > 0 && p.tools.length <= 16) {
    return { action, params: { tools: p.tools.map(toolIdentity) } };
  }
  if (action === 'call' && Object.keys(p).sort().join() === 'arguments,tool' && p.arguments && typeof p.arguments === 'object' && !Array.isArray(p.arguments) && Object.keys(p.arguments).length === 0) {
    return { action, params: { tool: toolIdentity(p.tool), arguments: {} } };
  }
  throw new Error('暂仅支持已授权的无参数只读工具。');
}
