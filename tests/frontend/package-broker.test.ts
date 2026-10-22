import { afterEach, describe, expect, it, vi } from 'vitest';
import { isModuleHello, createPackageBroker, packageAppearance } from '../../apps/framework/frontend/src/core/package-broker';
import { createModuleBridge, DEFAULT_SURFACE_EFFECTS, getBaseTheme, isConnectMessage, matchesSource, resolveMessageAppearance, resolveMessageSurfaceEffects, resolveMessageUIStyle, SUPPORTED_APPEARANCES, SUPPORTED_UI_STYLES } from '../../apps/framework/module-sdk/src/index';
import type { Appearance, SurfaceEffects } from '../../apps/framework/module-sdk/src/index';

describe('package transport isolation', () => {
  const source = {}; const origin = 'http://127.0.0.1:31000';
  const hello = { type: 'nimdadesk:hello', protocol: 1, generation: 'generation-b' };
  it('requires the exact iframe window, origin and generation', () => {
    expect(isModuleHello({ source, origin, data: hello }, source, origin, 'generation-b')).toBe(true);
    expect(isModuleHello({ source: {}, origin, data: hello }, source, origin, 'generation-b')).toBe(false);
    expect(isModuleHello({ source, origin: 'http://127.0.0.1:31001', data: hello }, source, origin, 'generation-b')).toBe(false);
    expect(isModuleHello({ source, origin, data: hello }, source, origin, 'old-generation')).toBe(false);
    expect(matchesSource({ source, origin: 'null' }, source, 'null')).toBe(false);
  });
  it('rejects stale and malformed module connect metadata', () => {
    const connect = { type: 'nimdadesk:connect', protocol: 1, theme: 'dark', identity: { moduleId: 'test-lab', version: '1.0.0', digest: 'digest', generation: 'new' } };
    expect(isConnectMessage(connect, 'new')).toBe(true);
    expect(isConnectMessage(connect, 'old')).toBe(false);
    expect(isConnectMessage({ ...connect, protocol: 2 }, 'new')).toBe(false);
  });
  const appearances: Array<[Appearance, 'light' | 'dark']> = [
    ['light', 'light'], ['dark', 'dark'], ['sakura', 'light'], ['ocean', 'light'],
    ['forest', 'light'], ['sand', 'light'], ['graphite', 'dark'],
  ];
  it('covers every supported appearance with an explicit protocol 1 base', () => {
    expect(appearances.map(([appearance]) => appearance)).toEqual([...SUPPORTED_APPEARANCES]);
  });
  it.each(appearances)('transports %s with a legacy-compatible %s base', (appearance, theme) => {
    const identity = { moduleId: 'test-lab', version: '1.0.0', digest: 'digest', generation: 'new' };
    const fields = packageAppearance(appearance);
    expect(getBaseTheme(appearance)).toBe(theme);
    expect(fields).toEqual({ theme, appearance, uiStyle: 'simple', surfaceEffects: DEFAULT_SURFACE_EFFECTS });
    // An installed protocol 1 SDK that only knows theme sees the correct base.
    const legacyFields = { theme: fields.theme };
    expect(isConnectMessage({ type: 'nimdadesk:connect', protocol: 1, identity, ...legacyFields }, 'new')).toBe(true);
    expect(isConnectMessage({ type: 'nimdadesk:connect', protocol: 1, identity, ...fields }, 'new')).toBe(true);
    expect(resolveMessageAppearance(fields.theme, fields.appearance)).toBe(appearance);
    expect(resolveMessageAppearance(legacyFields.theme)).toBe(theme);
  });
  it('accepts old host messages and ignores unknown appearance extensions safely', () => {
    expect(resolveMessageAppearance('dark')).toBe('dark');
    expect(resolveMessageAppearance('light')).toBe('light');
    for (const appearance of ['system', 'future-appearance', '__proto__', null, {}, ['sakura']]) {
      expect(resolveMessageAppearance('dark', appearance)).toBe('dark');
      expect(resolveMessageAppearance('light', appearance)).toBe('light');
    }
    expect(resolveMessageAppearance('dark', 'light')).toBe('light');
    expect(resolveMessageAppearance('light', 'dark')).toBe('dark');
    expect(resolveMessageAppearance('sakura', 'sakura')).toBeNull();
    expect(resolveMessageAppearance(undefined, 'sakura')).toBeNull();
  });
  it.each(SUPPORTED_UI_STYLES)('transports %s styling independently from its compatible color theme', (uiStyle) => {
    for (const [appearance, theme] of appearances) {
      const fields = packageAppearance(appearance, uiStyle);
      expect(fields).toEqual({ theme, appearance, uiStyle, surfaceEffects: DEFAULT_SURFACE_EFFECTS });
      expect(resolveMessageUIStyle(fields.uiStyle)).toBe(uiStyle);
      expect(resolveMessageAppearance(fields.theme, fields.appearance)).toBe(appearance);
    }
  });
  it('falls back to simple for older hosts and unrecognized style fields', () => {
    for (const uiStyle of [undefined, null, 'future-style', '__proto__', {}, ['anime'], true, 1]) {
      expect(resolveMessageUIStyle(uiStyle)).toBe('simple');
    }
  });
  it('forwards only validated surface properties, never host wallpaper or local paths', () => {
    const effects = {
      material: 'glass', transparency: 62, motion: 'subtle', transparentBackground: true,
      wallpaper: 'data:image/png;base64,private-image', assetPath: 'C:/private/wallpaper.png',
    } as SurfaceEffects;
    const fields = packageAppearance('graphite', 'glass', effects);
    expect(fields).toEqual({
      theme: 'dark', appearance: 'graphite', uiStyle: 'glass',
      surfaceEffects: { material: 'glass', transparency: 62, motion: 'subtle', transparentBackground: true },
    });
    expect(JSON.stringify(fields)).not.toContain('private');
    expect(isConnectMessage({ type: 'nimdadesk:connect', protocol: 1, identity: { moduleId: 'test', version: '1.0.0', digest: 'sha', generation: 'g' }, ...fields }, 'g')).toBe(true);
  });
  it('defaults malformed surface fields without admitting arbitrary CSS values', () => {
    for (const value of [undefined, null, false, 78, 'glass', [], ['glass']]) {
      expect(resolveMessageSurfaceEffects(value)).toEqual(DEFAULT_SURFACE_EFFECTS);
    }
    for (const transparency of [-1, 0, 39, 101, Infinity, -Infinity, NaN, '75', '70%;background:url(secret)', null, []]) {
      expect(resolveMessageSurfaceEffects({ material: 'glass', transparency, motion: 'full', transparentBackground: true })).toEqual({ material: 'glass', transparency: 78, motion: 'full', transparentBackground: true });
    }
    expect(resolveMessageSurfaceEffects({ material: 'future', transparency: 80, motion: 'future', transparentBackground: 'true' })).toEqual({ material: 'solid', transparency: 80, motion: 'off', transparentBackground: false });
    for (const transparency of [40, 78.5, 100]) {
      expect(resolveMessageSurfaceEffects({ transparency }).transparency).toBe(transparency);
    }
  });
  it('does not forward calls from an old generation', async () => {
    const call = vi.fn(); const reply = vi.fn();
    const broker = createPackageBroker('new', call, vi.fn());
    await broker.receive({ type: 'call', generation: 'old', id: '1', method: 'test.echo', params: {} }, reply);
    expect(call).not.toHaveBeenCalled(); expect(reply).not.toHaveBeenCalled();
  });
  it('drops an in-flight result when its frame has been destroyed', async () => {
    let finish!: (value: unknown) => void;
    const call = vi.fn(() => new Promise((resolve) => { finish = resolve; })); const reply = vi.fn();
    const broker = createPackageBroker('active', call, vi.fn());
    await broker.receive({ type: 'ready', generation: 'active' }, vi.fn());
    const request = broker.receive({ type: 'call', generation: 'active', id: '1', method: 'test.echo', params: { text: 'old' } }, reply);
    expect(call).toHaveBeenCalledOnce(); broker.dispose(); finish({ text: 'old response' }); await request;
    expect(reply).not.toHaveBeenCalled();
    await broker.receive({ type: 'call', generation: 'active', id: '2', method: 'test.echo', params: {} }, reply);
    expect(call).toHaveBeenCalledOnce();
  });
  it('returns errors without silently converting failures to success', async () => {
    const reply = vi.fn(); const broker = createPackageBroker('g', vi.fn().mockRejectedValue('MODULE_DISABLED'), vi.fn());
    await broker.receive({ type: 'ready', generation: 'g' }, vi.fn());
    await broker.receive({ type: 'call', generation: 'g', id: '1', method: 'test.echo', params: {} }, reply);
    expect(reply).toHaveBeenCalledWith({ type: 'result', generation: 'g', id: '1', ok: false, error: 'MODULE_DISABLED' });
  });
  it('rejects oversized requests before calling a worker', async () => {
    const call = vi.fn(); const reply = vi.fn(); const broker = createPackageBroker('g', call, vi.fn());
    await broker.receive({ type: 'ready', generation: 'g' }, vi.fn());
    await broker.receive({ type: 'call', generation: 'g', id: '1', method: 'test.echo', params: { text: '字'.repeat(30_000) } }, reply);
    expect(call).not.toHaveBeenCalled(); expect(reply.mock.calls[0][0].ok).toBe(false);
  });
  it('does not forward calls until the host accepts the compiled UI identity', async () => {
    const call = vi.fn(); const reply = vi.fn();
    const broker = createPackageBroker('g', call, vi.fn().mockRejectedValue(new Error('UI_VERSION_MISMATCH')));
    await broker.receive({ type: 'ready', generation: 'g', moduleId: 'wrong', version: '9' }, reply);
    expect(reply).toHaveBeenCalledWith({ type: 'host-error', generation: 'g', error: 'UI_VERSION_MISMATCH' });
    await broker.receive({ type: 'call', generation: 'g', id: '1', method: 'test.echo', params: {} }, reply);
    expect(call).not.toHaveBeenCalled();
  });
});

describe('module appearance updates', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  function connectBridge(fields: Record<string, unknown>) {
    vi.useFakeTimers();
    const parent = { postMessage: vi.fn() };
    const listeners = new Map<string, (event: MessageEvent) => void>();
    const dataset: Record<string, string> = {};
    const properties = new Map<string, string>();
    const port = {
      onmessage: null as ((event: { data: unknown }) => void) | null,
      start: vi.fn(), close: vi.fn(), postMessage: vi.fn(),
    };
    vi.stubGlobal('window', {
      parent,
      location: { hash: '#nd-host-origin=http%3A%2F%2Flocalhost%3A1420&nd-generation=current' },
      addEventListener: (type: string, listener: (event: MessageEvent) => void) => listeners.set(type, listener),
      removeEventListener: (type: string) => listeners.delete(type),
    });
    vi.stubGlobal('document', { documentElement: { dataset, style: { setProperty: (name: string, value: string) => properties.set(name, value) } } });
    vi.stubGlobal('__MODULE_ID__', 'test-lab');
    vi.stubGlobal('__MODULE_VERSION__', '1.0.0');
    const bridge = createModuleBridge();
    listeners.get('message')!({
      source: parent, origin: 'http://localhost:1420', ports: [port],
      data: {
        type: 'nimdadesk:connect', protocol: 1,
        identity: { moduleId: 'test-lab', version: '1.0.0', digest: 'digest', generation: 'current' },
        ...fields,
      },
    } as unknown as MessageEvent);
    port.onmessage!({ data: { type: 'accepted', generation: 'current' } });
    return { bridge, dataset, properties, port };
  }

  it('applies handshake and live styles without replacing the module connection', async () => {
    const { bridge, dataset, port } = connectBridge(packageAppearance('graphite', 'cursor'));
    await bridge.ready;
    expect(dataset).toMatchObject({ theme: 'graphite', uiStyle: 'cursor' });
    for (const uiStyle of SUPPORTED_UI_STYLES) {
      port.onmessage!({ data: { type: 'theme', generation: 'current', ...packageAppearance('sakura', uiStyle) } });
      expect(dataset).toMatchObject({ theme: 'sakura', uiStyle });
    }
    expect(port.start).toHaveBeenCalledOnce();
    expect(port.postMessage).toHaveBeenCalledOnce();
    expect(bridge.host.available).toBe(true);
    bridge.dispose();
    expect(port.close).toHaveBeenCalledOnce();
  });

  it('uses simple for legacy updates and ignores invalid, stale or disposed updates', async () => {
    const { bridge, dataset, port } = connectBridge({ theme: 'dark' });
    await bridge.ready;
    expect(dataset).toMatchObject({ theme: 'dark', uiStyle: 'simple' });
    port.onmessage!({ data: { type: 'theme', generation: 'current', theme: 'light', appearance: 'ocean', uiStyle: 'web' } });
    expect(dataset).toMatchObject({ theme: 'ocean', uiStyle: 'web' });
    port.onmessage!({ data: { type: 'theme', generation: 'old', theme: 'dark', uiStyle: 'anime' } });
    port.onmessage!({ data: { type: 'theme', generation: 'current', theme: 'invalid', uiStyle: 'anime' } });
    expect(dataset).toMatchObject({ theme: 'ocean', uiStyle: 'web' });
    port.onmessage!({ data: { type: 'theme', generation: 'current', theme: 'dark', uiStyle: 'unknown' } });
    expect(dataset).toMatchObject({ theme: 'dark', uiStyle: 'simple' });
    port.onmessage!({ data: { type: 'theme', generation: 'current', theme: 'light' } });
    expect(dataset).toMatchObject({ theme: 'light', uiStyle: 'simple' });
    bridge.dispose();
    port.onmessage!({ data: { type: 'theme', generation: 'current', theme: 'dark', uiStyle: 'anime' } });
    expect(dataset).toMatchObject({ theme: 'light', uiStyle: 'simple' });
  });

  it('applies glass on the real bridge and restores opaque defaults for a legacy update', async () => {
    const effects: SurfaceEffects = { material: 'glass', transparency: 61, motion: 'subtle', transparentBackground: true };
    const { bridge, dataset, properties, port } = connectBridge(packageAppearance('graphite', 'glass', effects));
    await bridge.ready;
    expect(dataset).toEqual({ theme: 'graphite', uiStyle: 'glass', material: 'glass', motion: 'subtle', shellBackground: 'true' });
    expect(properties.get('--glass-opacity')).toBe('61%');
    port.onmessage!({ data: { type: 'theme', generation: 'current', ...packageAppearance('ocean', 'web', { ...effects, transparency: 100, motion: 'off', transparentBackground: false }) } });
    expect(dataset).toEqual({ theme: 'ocean', uiStyle: 'web', material: 'glass', motion: 'off', shellBackground: 'false' });
    expect(properties.get('--glass-opacity')).toBe('100%');
    port.onmessage!({ data: { type: 'theme', generation: 'current', theme: 'light' } });
    expect(dataset).toEqual({ theme: 'light', uiStyle: 'simple', material: 'solid', motion: 'off', shellBackground: 'false' });
    expect(properties.get('--glass-opacity')).toBe('78%');
    expect(port.start).toHaveBeenCalledOnce();
    expect(port.postMessage).toHaveBeenCalledOnce();
    bridge.dispose();
  });

  it('does not apply material changes from stale, invalid-theme or disposed messages', async () => {
    const effects: SurfaceEffects = { material: 'glass', transparency: 45, motion: 'full', transparentBackground: true };
    const { bridge, dataset, properties, port } = connectBridge({ theme: 'light' });
    await bridge.ready;
    const before = { ...dataset };
    port.onmessage!({ data: { type: 'theme', generation: 'stale', ...packageAppearance('dark', 'glass', effects) } });
    port.onmessage!({ data: { type: 'theme', generation: 'current', theme: 'invalid', uiStyle: 'glass', surfaceEffects: effects } });
    expect(dataset).toEqual(before);
    expect(properties.get('--glass-opacity')).toBe('78%');
    bridge.dispose();
    port.onmessage!({ data: { type: 'theme', generation: 'current', ...packageAppearance('dark', 'glass', effects) } });
    expect(dataset).toEqual(before);
    expect(properties.get('--glass-opacity')).toBe('78%');
  });
});
