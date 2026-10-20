import { describe, expect, it, vi } from 'vitest';
import { isModuleHello, createPackageBroker, packageAppearance } from '../../apps/framework/frontend/src/core/package-broker';
import { getBaseTheme, isConnectMessage, matchesSource, resolveMessageAppearance, SUPPORTED_APPEARANCES } from '../../apps/framework/module-sdk/src/index';
import type { Appearance } from '../../apps/framework/module-sdk/src/index';

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
    expect(fields).toEqual({ theme, appearance });
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
