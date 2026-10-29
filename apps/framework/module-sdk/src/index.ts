/** Browser-only module SDK. It deliberately has no Tauri or host-process imports. */
import { parseToolOperation, toolIdentity, type ModuleToolsClient, type HostTool } from './module-tools';
export type { HostTool, HostToolIdentity, ModuleToolsClient } from './module-tools';
import { isSurfaceContext, parseSurfaceOperation, type ModuleSurfaceClient, type ModuleSurfaceContext } from './module-surface';
export type { ModuleSurfaceClient, ModuleSurfaceContext, ModuleSurfaceOptions } from './module-surface';
export const BRIDGE_PROTOCOL = 1;
// Protocol 1 keeps its legacy nimdadesk:* messages and nd-* fragment keys so
// already installed module packages can still connect after the OpenDesk rename.
export const BROWSER_UNAVAILABLE = '此功能需要由 OpenDesk 打开，独立浏览器不能调用桌面能力。';
/** Resolved appearances; the host resolves its system preference before transport. */
export const SUPPORTED_APPEARANCES = ['light', 'dark', 'sakura', 'ocean', 'forest', 'sand', 'graphite'] as const;
export type Appearance = typeof SUPPORTED_APPEARANCES[number];
/** Layout and surface styling are independent from the user's color appearance. */
export const SUPPORTED_UI_STYLES = ['simple', 'codex', 'cursor', 'web', 'anime', 'glass', 'mint'] as const;
export type UIStyle = typeof SUPPORTED_UI_STYLES[number];

/** A bounded visual description only. Wallpaper bytes and local paths stay in the host. */
export interface SurfaceEffects {
  material: 'solid' | 'glass';
  transparency: number;
  motion: 'off' | 'subtle' | 'full';
  transparentBackground: boolean;
}
export const DEFAULT_SURFACE_EFFECTS: Readonly<SurfaceEffects> = Object.freeze({
  material: 'solid', transparency: 78, motion: 'off', transparentBackground: false,
});

/** Protocol 1 base for installed SDKs that do not know a newer appearance. */
export function getBaseTheme(appearance: Appearance): 'light' | 'dark' {
  return appearance === 'dark' || appearance === 'graphite' ? 'dark' : 'light';
}

export interface ModuleIdentity {
  moduleId: string;
  version: string;
  digest: string;
  generation: string;
}
export interface LimitedHost {
  readonly available: boolean;
  /** Undefined on older hosts; context is null in the owning main view. */
  readonly surface?: ModuleSurfaceClient;
  readonly tools?: ModuleToolsClient;
  callWorker<T>(method: string, params?: Record<string, unknown>): Promise<T>;
}
export interface ModuleServices { host: LimitedHost }
export interface ConnectMessage {
  type: 'nimdadesk:connect'; protocol: 1; identity: ModuleIdentity; theme: 'light' | 'dark';
  /** Optional extension: unrecognized values fall back to the protocol 1 base. */
  appearance?: unknown;
  /** Optional extension: older hosts and unknown styles use the simple layout. */
  uiStyle?: unknown;
  /** Optional extension: older hosts retain an opaque, motion-free module surface. */
  surfaceEffects?: unknown;
  moduleSurface?: unknown;
  moduleTools?: unknown;
}
export interface ModuleBridge {
  host: LimitedHost;
  ready: Promise<ModuleIdentity>;
  dispose(): void;
}

export function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return typeof error === 'string' ? error : JSON.stringify(error) || '未知错误';
}
export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
export function matchesSource(event: { origin: string; source: unknown }, source: unknown, origin: string): boolean {
  return source !== null && event.source === source && event.origin === origin && origin !== 'null';
}
export function isConnectMessage(value: unknown, generation: string): value is ConnectMessage {
  if (!isRecord(value) || value.type !== 'nimdadesk:connect' || value.protocol !== BRIDGE_PROTOCOL || !isRecord(value.identity)) return false;
  const identity = value.identity;
  return identity.generation === generation && ['moduleId', 'version', 'digest', 'generation'].every((key) => typeof identity[key] === 'string' && identity[key] !== '')
    && (value.theme === 'light' || value.theme === 'dark');
}

/** Both handshake and later theme events use the same safe, compatible parser. */
export function resolveMessageAppearance(theme: unknown, appearance?: unknown): Appearance | null {
  if (theme !== 'light' && theme !== 'dark') return null;
  return SUPPORTED_APPEARANCES.find((value) => value === appearance) ?? theme;
}

export function resolveMessageUIStyle(uiStyle?: unknown): UIStyle {
  return SUPPORTED_UI_STYLES.find((value) => value === uiStyle) ?? 'simple';
}

export function resolveMessageSurfaceEffects(value?: unknown): SurfaceEffects {
  const fields = isRecord(value) ? value : {};
  return {
    material: fields.material === 'glass' ? 'glass' : 'solid',
    transparency: typeof fields.transparency === 'number' && Number.isFinite(fields.transparency)
      && fields.transparency >= 40 && fields.transparency <= 100 ? fields.transparency : 78,
    motion: fields.motion === 'subtle' || fields.motion === 'full' ? fields.motion : 'off',
    transparentBackground: fields.transparentBackground === true,
  };
}

function applySurfaceEffects(value?: unknown) {
  const effects = resolveMessageSurfaceEffects(value);
  const root = document.documentElement;
  root.dataset.material = effects.material;
  root.dataset.motion = effects.motion;
  root.dataset.shellBackground = String(effects.transparentBackground);
  root.style.setProperty('--glass-opacity', `${effects.transparency}%`);
}

export function createModuleBridge(): ModuleBridge {
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const parentOrigin = fragment.get('nd-host-origin') ?? '';
  const generation = fragment.get('nd-generation') ?? '';
  let active = true;
  let accepted = false;
  let tools: ModuleToolsClient | undefined;
  let port: MessagePort | null = null;
  let sequence = 0;
  let surface: ModuleSurfaceClient | undefined;
  let resolveReady!: (identity: ModuleIdentity) => void;
  let rejectReady!: (reason: Error) => void;
  const pending = new Map<string, { resolve(value: unknown): void; reject(reason: Error): void; timer: ReturnType<typeof setTimeout> }>();
  const ready = new Promise<ModuleIdentity>((resolve, reject) => { resolveReady = resolve; rejectReady = reject; });
  const handshakeTimer = setTimeout(() => rejectReady(new Error('模块与宿主连接超时，请重新打开此功能。')), 15_000);

  function receive(event: MessageEvent) {
    if (!active || port || !matchesSource(event, window.parent, parentOrigin) || !isConnectMessage(event.data, generation) || event.ports.length !== 1) return;
    port = event.ports[0];
    if (event.data.moduleTools === 1) tools = {
      list: () => toolRequest('list', {}) as Promise<HostTool[]>,
      grant: async items => { await toolRequest('grant', { tools: items.map(toolIdentity) }); },
      call: (tool, args) => toolRequest('call', { tool: toolIdentity(tool), arguments: args }),
    };
    const identity = event.data.identity;
    document.documentElement.dataset.theme = resolveMessageAppearance(event.data.theme, event.data.appearance)!;
    document.documentElement.dataset.uiStyle = resolveMessageUIStyle(event.data.uiStyle);
    applySurfaceEffects(event.data.surfaceEffects);
    if (isRecord(event.data.moduleSurface) && (event.data.moduleSurface.context === null || isSurfaceContext(event.data.moduleSurface.context))) {
      const context = event.data.moduleSurface.context as ModuleSurfaceContext | null;
      surface = {
        context,
        open: (options) => surfaceRequest('open', options),
        close: () => surfaceRequest('close', {}),
        setAlwaysOnTop: (enabled) => surfaceRequest('always-on-top', { enabled }),
        startDrag: () => surfaceRequest('start-drag', {}),
      };
    }
    port.onmessage = (message: MessageEvent) => {
      const data: unknown = message.data;
      if (!active || !isRecord(data) || data.generation !== generation) return;
      if (data.type === 'accepted') { accepted = true; clearTimeout(handshakeTimer); resolveReady(identity); return; }
      if (data.type === 'host-error') { clearTimeout(handshakeTimer); rejectReady(new Error(typeof data.error === 'string' ? data.error : '宿主未接受此模块。')); return; }
      if (data.type === 'theme') {
        const appearance = resolveMessageAppearance(data.theme, data.appearance);
        if (appearance) {
          document.documentElement.dataset.theme = appearance;
          document.documentElement.dataset.uiStyle = resolveMessageUIStyle(data.uiStyle);
          applySurfaceEffects(data.surfaceEffects);
        }
        return;
      }
      if (data.type !== 'result' || typeof data.id !== 'string') return;
      const request = pending.get(data.id);
      if (!request) return;
      pending.delete(data.id); clearTimeout(request.timer);
      if (data.ok === true) request.resolve(data.result);
      else request.reject(new Error(typeof data.error === 'string' ? data.error : '模块请求失败。'));
    };
    port.start();
    port.postMessage({ type: 'ready', generation, moduleId: __MODULE_ID__, version: __MODULE_VERSION__, digest: identity.digest });
    window.removeEventListener('message', receive);
  }
  window.addEventListener('message', receive);
  if (window.parent !== window && parentOrigin && parentOrigin !== 'null' && generation) {
    window.parent.postMessage({ type: 'nimdadesk:hello', protocol: BRIDGE_PROTOCOL, generation }, parentOrigin);
  } else {
    clearTimeout(handshakeTimer); rejectReady(new Error(BROWSER_UNAVAILABLE));
  }

  async function surfaceRequest(action: string, params: unknown): Promise<void> {
    const operation = parseSurfaceOperation(action, params);
    await ready;
    if (!active || !port || !surface) throw new Error('当前宿主不支持独立窗口，或模块实例已关闭。');
    const id = `${generation}:${++sequence}`;
    return new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => { pending.delete(id); reject(new Error('独立窗口请求超时；没有自动重试。')); }, 15_000);
      pending.set(id, { resolve: () => resolve(), reject, timer });
      try { port!.postMessage({ type: 'surface', generation, id, ...operation }); }
      catch (error) { pending.delete(id); clearTimeout(timer); reject(new Error(errorMessage(error))); }
    });
  }
  async function toolRequest(action: string, params: unknown): Promise<unknown> {
    const operation = parseToolOperation(action, params);
    await ready;
    if (!active || !port || !tools) throw new Error('当前宿主尚未提供插件工具接口，请升级 OpenDesk。');
    const id = generation + ':' + (++sequence);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { pending.delete(id); reject(new Error('工具请求超时；没有自动重试。')); }, 180000);
      pending.set(id, { resolve, reject, timer });
      try { port!.postMessage({ type: 'tools', generation, id, ...operation }); }
      catch (e) { pending.delete(id); clearTimeout(timer); reject(e); }
    });
  }
  const host: LimitedHost = {
    get available() { return active && accepted && port !== null; },
    get surface() { return surface; },
    get tools() { return tools; },
    async callWorker<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
      await ready;
      if (!active || !port) throw new Error('模块实例已关闭，不能继续调用。');
      if (!/^[a-z][a-z0-9_.-]{0,127}$/.test(method) || !isRecord(params)) throw new Error('无效的模块请求。');
      if (new TextEncoder().encode(JSON.stringify(params)).byteLength > 65_536) throw new Error('模块请求超过大小限制。');
      const id = `${generation}:${++sequence}`;
      return new Promise<T>((resolve, reject) => {
        const timer = setTimeout(() => { pending.delete(id); reject(new Error('模块请求超时；没有自动重试。')); }, 65_000);
        pending.set(id, { resolve: (value) => resolve(value as T), reject, timer });
        try { port!.postMessage({ type: 'call', generation, id, method, params }); }
        catch (error) { pending.delete(id); clearTimeout(timer); reject(new Error(errorMessage(error))); }
      });
    },
  };
  function dispose() {
    if (!active) return;
    active = false; clearTimeout(handshakeTimer); window.removeEventListener('message', receive);
    rejectReady(new Error('模块实例已关闭。'));
    for (const request of pending.values()) { clearTimeout(request.timer); request.reject(new Error('模块实例已关闭。')); }
    pending.clear(); port?.close(); port = null;
  }
  window.addEventListener('pagehide', dispose, { once: true });
  return { host, ready, dispose };
}
