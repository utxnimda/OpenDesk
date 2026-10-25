import { afterEach, describe, expect, it, vi } from 'vitest';
import { parseSurfaceOperation } from '../../apps/framework/module-sdk/src/module-surface';
import { createModuleBridge } from '../../apps/framework/module-sdk/src/index';
import { createPackageBroker } from '../../apps/framework/frontend/src/core/package-broker';
import { createHostAdapter, type InvokeBridge } from '../../apps/framework/frontend/src/core/host';

const options = { view: 'ranking', title: '排行', width: 440, height: 700, alwaysOnTop: true, context: { selected: 'one' } };
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });
describe('module secondary surface boundary', () => {
  it('accepts a bounded view and rejects URLs, arbitrary keys and oversized context', () => {
    expect(parseSurfaceOperation('open', options)).toEqual({ action: 'open', params: options });
    for (const value of [{ ...options, view: 'https://example.com' }, { ...options, url: 'file:///x' }, { ...options, title: 'x\n' },
      { ...options, width: Infinity }, { ...options, width: 319 }, { ...options, height: 1401 }, { ...options, context: [] },
      { ...options, context: { data: '汉'.repeat(3000) } }]) expect(() => parseSurfaceOperation('open', value)).toThrow();
    expect(() => parseSurfaceOperation('close', { token: 'other' })).toThrow();
    expect(() => parseSurfaceOperation('always-on-top', { enabled: 'true' })).toThrow();
  });
  it('binds surface requests to ready generation and never sends them to the worker', async () => {
    const worker = vi.fn(); const surface = vi.fn().mockResolvedValue(undefined); const reply = vi.fn();
    const broker = createPackageBroker('g', worker, vi.fn(), surface);
    const message = { type: 'surface', generation: 'g', id: '1', action: 'open', params: options };
    await broker.receive(message, reply);
    expect(reply.mock.lastCall?.[0].ok).toBe(false); expect(surface).not.toHaveBeenCalled();
    await broker.receive({ type: 'ready', generation: 'g' }, reply);
    await broker.receive({ ...message, generation: 'old' }, reply);
    expect(surface).not.toHaveBeenCalled();
    await broker.receive({ ...message, params: { ...options, token: 'unrelated' } }, reply);
    expect(surface).not.toHaveBeenCalled();
    await broker.receive(message, reply);
    expect(surface).toHaveBeenCalledExactlyOnceWith({ action: 'open', params: options });
    expect(worker).not.toHaveBeenCalled(); expect(reply.mock.lastCall?.[0].ok).toBe(true);
    broker.dispose(); await broker.receive({ ...message, id: '2' }, reply);
    expect(surface).toHaveBeenCalledOnce();
  });
  it('preserves errors and drops a late window result after frame disposal', async () => {
    let finish!: () => void;
    const reply = vi.fn(); const surface = vi.fn().mockRejectedValueOnce(new Error('owner revoked')).mockImplementationOnce(() => new Promise<void>(resolve => { finish = resolve; }));
    const broker = createPackageBroker('g', vi.fn(), vi.fn(), surface);
    await broker.receive({ type: 'ready', generation: 'g' }, reply);
    const message = { type: 'surface', generation: 'g', id: '1', action: 'close', params: {} };
    await broker.receive(message, reply); expect(reply.mock.lastCall?.[0].error).toBe('owner revoked');
    const pending = broker.receive({ ...message, id: '2' }, reply);
    const count = reply.mock.calls.length; broker.dispose(); finish(); await pending;
    expect(reply).toHaveBeenCalledTimes(count);
  });
  it('uses named native operations with the owned generation', async () => {
    const invoke = vi.fn().mockResolvedValue(undefined); const host = createHostAdapter(invoke as InvokeBridge);
    await host.openPackageSurface('owned', options); await host.getPackageSurfaceContext();
    await host.callPackageSurface('view-2', 'test.status'); await host.startPackageSurfaceDrag('view-2');
    await host.setPackageSurfaceAlwaysOnTop('view-2', false); await host.closePackageSurface('view-2');
    expect(invoke.mock.calls).toEqual([
      ['module_surface_open', { token: 'owned', options }], ['module_surface_context', undefined],
      ['module_surface_call', { generation: 'view-2', method: 'test.status', params: {} }],
      ['module_surface_start_drag', { generation: 'view-2' }],
      ['module_surface_set_always_on_top', { generation: 'view-2', enabled: false }],
      ['module_surface_close', { generation: 'view-2' }],
    ]);
  });
  function connect(moduleSurface?: unknown) {
    const parent = { postMessage: vi.fn() }; const listeners = new Map<string, (event: MessageEvent) => void>();
    const port = { onmessage: null as ((event: { data: unknown }) => void) | null, start: vi.fn(), close: vi.fn(), postMessage: vi.fn() };
    vi.stubGlobal('window', { parent, location: { hash: '#nd-host-origin=http%3A%2F%2Flocalhost%3A1420&nd-generation=g' },
      addEventListener: (type: string, fn: (event: MessageEvent) => void) => listeners.set(type, fn), removeEventListener: (type: string) => listeners.delete(type) });
    vi.stubGlobal('document', { documentElement: { dataset: {}, style: { setProperty: vi.fn() } } });
    vi.stubGlobal('__MODULE_ID__', 'test'); vi.stubGlobal('__MODULE_VERSION__', '1.0.0');
    const bridge = createModuleBridge();
    listeners.get('message')!({ source: parent, origin: 'http://localhost:1420', ports: [port], data: {
      type: 'nimdadesk:connect', protocol: 1, theme: 'dark', identity: { moduleId: 'test', version: '1.0.0', digest: 'd', generation: 'g' }, moduleSurface,
    } } as unknown as MessageEvent);
    port.onmessage!({ data: { type: 'accepted', generation: 'g' } });
    return { bridge, port };
  }
  it('keeps older hosts compatible and exposes only an accepted host surface contract', async () => {
    const old = connect(); await old.bridge.ready; expect(old.bridge.host.surface).toBeUndefined(); old.bridge.dispose();
    const { bridge, port } = connect({ context: { view: 'ranking', data: { selected: 'one' } } }); await bridge.ready;
    expect(bridge.host.surface?.context).toEqual({ view: 'ranking', data: { selected: 'one' } });
    const request = bridge.host.surface!.setAlwaysOnTop(false); await Promise.resolve();
    expect(port.postMessage.mock.lastCall?.[0]).toMatchObject({ type: 'surface', generation: 'g', action: 'always-on-top', params: { enabled: false } });
    const id = port.postMessage.mock.lastCall![0].id;
    port.onmessage!({ data: { type: 'result', generation: 'g', id, ok: false, error: 'stale surface' } });
    await expect(request).rejects.toThrow('stale surface'); bridge.dispose();
    await expect(bridge.host.surface!.close()).rejects.toThrow('关闭');
  });
});
