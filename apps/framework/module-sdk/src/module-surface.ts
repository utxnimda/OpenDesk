/** Bounded, host-owned secondary view. No URLs, HTML, shell or window handles. */
export interface ModuleSurfaceOptions {
  view: string;
  title: string;
  width: number;
  height: number;
  alwaysOnTop: boolean;
  context: Record<string, unknown>;
}
export interface ModuleSurfaceContext { view: string; data: Record<string, unknown> }
export interface ModuleSurfaceClient {
  readonly context: ModuleSurfaceContext | null;
  open(options: ModuleSurfaceOptions): Promise<void>;
  close(): Promise<void>;
  setAlwaysOnTop(enabled: boolean): Promise<void>;
  startDrag(): Promise<void>;
}
export type SurfaceOperation =
  | { action: 'open'; params: ModuleSurfaceOptions }
  | { action: 'close' | 'start-drag'; params: Record<string, never> }
  | { action: 'always-on-top'; params: { enabled: boolean } };

function record(value: unknown): value is Record<string, unknown> { return typeof value === 'object' && value !== null && !Array.isArray(value); }
function keys(value: Record<string, unknown>, expected: string[]) { return Object.keys(value).length === expected.length && expected.every(key => Object.hasOwn(value, key)); }
export function isSurfaceContext(value: unknown): value is ModuleSurfaceContext {
  return record(value) && keys(value, ['view', 'data']) && typeof value.view === 'string' && /^[a-z][a-z0-9-]{0,47}$/.test(value.view)
    && record(value.data) && new TextEncoder().encode(JSON.stringify(value.data)).byteLength <= 8192;
}
export function parseSurfaceOperation(action: unknown, value: unknown): SurfaceOperation {
  if (!record(value)) throw new Error('无效的独立窗口参数。');
  if ((action === 'close' || action === 'start-drag') && keys(value, [])) return { action, params: {} };
  if (action === 'always-on-top' && keys(value, ['enabled']) && typeof value.enabled === 'boolean') return { action, params: { enabled: value.enabled } };
  if (action === 'open' && keys(value, ['view', 'title', 'width', 'height', 'alwaysOnTop', 'context'])
    && typeof value.title === 'string' && value.title.trim() && [...value.title].length <= 80 && !/[\u0000-\u001f\u007f]/.test(value.title)
    && isSurfaceContext({ view: value.view, data: value.context })
    && typeof value.width === 'number' && Number.isInteger(value.width) && value.width >= 320 && value.width <= 1000
    && typeof value.height === 'number' && Number.isInteger(value.height) && value.height >= 240 && value.height <= 1400
    && typeof value.alwaysOnTop === 'boolean') return { action, params: value as unknown as ModuleSurfaceOptions };
  throw new Error('无效的独立窗口操作或参数。');
}
