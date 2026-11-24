/** Optional host capability: consented cross-module tool mediation (v1 read-only + v2 bounded schema & module wake). */
export type HostToolAccess = 'read' | 'ui-navigate' | 'write';

export interface HostToolIdentity { name: string; moduleId: string; version: string; digest: string; generation: string }
export interface HostTool extends HostToolIdentity {
  title: string; description: string; inputSchema: Record<string, unknown>;
  readOnly: boolean; contractVersion: 1 | 2;
  access?: HostToolAccess;
}
export interface HostModuleSummary {
  moduleId: string;
  label: string;
  version: string;
  enabled: boolean;
  opened: boolean;
  ready: boolean;
  generation: string | null;
  supportsTools: boolean;
}
export interface HostOpenModuleResult {
  moduleId: string;
  label: string;
  version: string;
  opened: boolean;
  ready: boolean;
  generation: string;
  tools: HostTool[];
}
export type ToolOperation =
  | { action: 'list'; params: Record<string, never> }
  | { action: 'list-modules'; params: Record<string, never> }
  | { action: 'open-module'; params: { moduleId: string; focus: boolean } }
  | { action: 'grant'; params: { tools: HostToolIdentity[] } }
  | { action: 'call'; params: { tool: HostToolIdentity; arguments: Record<string, unknown> } };

export interface ModuleToolsClient {
  list(): Promise<HostTool[]>;
  grant(tools: HostToolIdentity[]): Promise<void>;
  call(tool: HostToolIdentity, args: Record<string, unknown>): Promise<unknown>;
  /** Present when host advertises moduleToolsV2: 1. */
  listModules?(): Promise<HostModuleSummary[]>;
  /** Present when host advertises moduleToolsV2: 1. */
  openModule?(moduleId: string, options?: { focus?: boolean }): Promise<HostOpenModuleResult>;
}

const FORBIDDEN_ARG_NAMES = new Set([
  'command', 'cmd', 'shell', 'script', 'exec', 'eval', 'args', 'argv', 'code', 'sh', 'bash', 'powershell', 'pwsh',
]);
const MODULE_ID_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;
const ARG_KEY_RE = /^[A-Za-z0-9_-]{1,48}$/;

function hasControlChars(text: string): boolean {
  for (let i = 0; i < text.length; i += 1) {
    const code = text.charCodeAt(i);
    if (code < 32 || code === 127) return true;
  }
  return false;
}

function hasPathTraversal(text: string): boolean {
  return text.split(/[\\/]/).some(segment => segment === '..');
}

function validateBoundedArguments(raw: unknown): Record<string, unknown> {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new Error('工具参数必须是对象。');
  }
  const entries = Object.entries(raw as Record<string, unknown>);
  if (entries.length > 12) throw new Error('工具参数字段数量超出上限。');
  const out: Record<string, unknown> = {};
  for (const [key, value] of entries) {
    if (!ARG_KEY_RE.test(key) || FORBIDDEN_ARG_NAMES.has(key.toLowerCase())) {
      throw new Error(`不允许的工具参数名称：${key}`);
    }
    if (typeof value === 'string') {
      if (value.length > 500 || hasControlChars(value) || hasPathTraversal(value)) {
        throw new Error(`工具参数 ${key} 超出安全边界。`);
      }
      out[key] = value;
    } else if (typeof value === 'number') {
      if (!Number.isFinite(value)) throw new Error(`工具参数 ${key} 必须是有限数值。`);
      out[key] = value;
    } else if (typeof value === 'boolean') {
      out[key] = value;
    } else if (Array.isArray(value)) {
      if (value.length > 32) throw new Error(`工具参数 ${key} 数组长度超出上限。`);
      const cleanArr: string[] = [];
      for (const item of value) {
        if (typeof item !== 'string' || item.length > 500 || hasControlChars(item) || hasPathTraversal(item)) {
          throw new Error(`工具参数 ${key} 仅允许安全短字符串数组。`);
        }
        cleanArr.push(item);
      }
      out[key] = cleanArr;
    } else {
      throw new Error(`工具参数 ${key} 不支持嵌套对象或空值。`);
    }
  }
  return out;
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
  if (action === 'list-modules' && Object.keys(p).length === 0) return { action, params: {} };
  if (action === 'open-module') {
    const keys = Object.keys(p);
    if (keys.some(k => k !== 'moduleId' && k !== 'focus') || typeof p.moduleId !== 'string' || !MODULE_ID_RE.test(p.moduleId)) {
      throw new Error('打开模块参数无效。');
    }
    const focus = p.focus === undefined ? false : p.focus;
    if (typeof focus !== 'boolean') throw new Error('focus 必须是布尔值。');
    return { action, params: { moduleId: p.moduleId, focus } };
  }
  if (action === 'grant' && Object.keys(p).join() === 'tools' && Array.isArray(p.tools) && p.tools.length > 0 && p.tools.length <= 16) {
    return { action, params: { tools: p.tools.map(toolIdentity) } };
  }
  if (action === 'call' && Object.keys(p).sort().join() === 'arguments,tool') {
    return { action, params: { tool: toolIdentity(p.tool), arguments: validateBoundedArguments(p.arguments) } };
  }
  throw new Error('不支持的工具请求或参数格式无效。');
}
