import { afterEach, describe, expect, it, vi } from 'vitest';
import { createPetPortraitProvider } from '../../apps/framework/frontend/src/companions/portraits';
import { openPortraitMedia } from '../../apps/framework/frontend/src/companions/portrait-media';
import type { CompanionCatalog } from '../../apps/framework/frontend/src/companions/contracts';
import { DEFAULT_PERSONALIZATION, type PetSettings } from '../../apps/framework/frontend/src/core/personalization';
import { createPackageBroker } from '../../apps/framework/frontend/src/core/package-broker';
import { parsePetPortraitOperation } from '../../apps/framework/module-sdk/src/pet-portraits';

const catalog: CompanionCatalog = { defaultId: 'demo', models: [
  { id: 'demo', label: '测试角色', description: '不通过接口传递', assets: { idle: '/private/a.png', still: '/private/b.png', wave: '/private/a.png' } },
] };
const list = parsePetPortraitOperation('list', {});
const frame = (index: number, sourceId = 'idle') => parsePetPortraitOperation('frame', { petId: 'demo', sourceId, index });
const signal = new AbortController().signal;
const saved = { ...DEFAULT_PERSONALIZATION.pet, enabled: true, modelId: null };

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('registered desktop pet portraits', () => {
  it('exposes only catalog labels and IDs and follows the saved selection, including disabled, missing and unread settings', async () => {
    vi.stubGlobal('ImageDecoder', class {});
    let pet: PetSettings | null = saved;
    const service = createPetPortraitProvider(catalog, () => pet);
    const pets = [{ id: 'demo', label: '测试角色', sources: [{ id: 'idle', label: '默认循环' }, { id: 'still', label: '静态形象' }] }];
    await expect(service.request(list, signal)).resolves.toEqual({ activeId: 'demo', pets });
    pet = { ...saved, enabled: false };
    await expect(service.request(list, signal)).resolves.toEqual({ activeId: null, pets });
    pet = { ...saved, modelId: 'missing' };
    await expect(service.request(list, signal)).resolves.toEqual({ activeId: null, pets });
    pet = null;
    await expect(service.request(list, signal)).rejects.toThrow('已保存');
    service.dispose();
  });

  it('retains an explicit first still frame when the runtime lacks an animation decoder', async () => {
    vi.stubGlobal('ImageDecoder', undefined);
    const reader = { read: vi.fn(async () => ({ index: 0, frameCount: 1, width: 1, height: 1, rgba: new Uint8ClampedArray(4) })), close: vi.fn(), count: 1 };
    const open = vi.fn(async () => reader);
    const service = createPetPortraitProvider(catalog, () => saved, open);
    const metadata = await service.request(list, signal);
    expect(metadata).toMatchObject({ pets: [{ sources: [{ id: 'still', label: '静态形象' }] }] });
    await expect(service.request(frame(0), signal)).rejects.toThrow('不可用');
    await expect(service.request(frame(1, 'still'), signal)).rejects.toThrow('实际帧数');
    expect(reader.read).not.toHaveBeenCalled();
    const first = await service.request(frame(0, 'still'), signal);
    expect(first).toMatchObject({ index: 0, frameCount: 1 });
    await service.request(frame(0, 'still'), signal);
    expect(reader.read).toHaveBeenCalledOnce();
    expect(open.mock.calls).toEqual([[{ url: '/private/b.png', kind: 'still' }, expect.any(AbortSignal)]]);
    service.dispose();
    expect(reader.close).toHaveBeenCalledOnce();
    await expect(service.request(frame(0, 'still'), signal)).rejects.toThrow('关闭');
  });

  it('rejects URLs, paths, extra parameters and non-integer or excessive indices before access', async () => {
    for (const params of [
      { petId: '../demo', sourceId: 'idle', index: 0 }, { petId: 'demo', sourceId: 'https://host/pet', index: 0 },
      { petId: 'demo', sourceId: 'still', index: 0, url: 'file:///private' },
      ...[-1, 0.5, NaN, 2048].map(index => ({ petId: 'demo', sourceId: 'idle', index })),
    ]) expect(() => parsePetPortraitOperation('frame', params)).toThrow();
    expect(() => parsePetPortraitOperation('list', { settings: true })).toThrow();
    vi.stubGlobal('document', { baseURI: 'http://tauri.localhost/' });
    vi.stubGlobal('window', { location: { origin: 'http://tauri.localhost' } });
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    for (const url of ['file:///private/photo.png', 'https://remote/photo.png', 'data:image/png,AAA', 'http://someone@tauri.localhost/private.png']) {
      await expect(openPortraitMedia({ url, kind: 'animation' }, signal)).rejects.toThrow('同源');
    }
    expect(fetch).not.toHaveBeenCalled();
  });

  it('bounds concurrent reads and releases the single cached decoder after idle', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('ImageDecoder', class {});
    let resume!: () => void;
    const stalled = new Promise<void>(resolve => { resume = resolve; });
    const reader = { count: 4, close: vi.fn(), read: vi.fn(async (index: number) => {
      await stalled;
      return { width: 1, height: 1, index, frameCount: 4, rgba: new Uint8ClampedArray(4) };
    }) };
    const open = vi.fn(async () => reader);
    const provider = createPetPortraitProvider(catalog, () => saved, open);
    const reads = [0, 1, 2, 3].map(index => provider.request(frame(index), signal));
    await expect(provider.request(frame(1), signal)).rejects.toThrow('正在解码');
    expect(reader.read).toHaveBeenCalledTimes(1);
    resume();
    await Promise.all(reads);
    expect(open).toHaveBeenCalledOnce();
    expect(reader.read.mock.calls.map(([index]) => index)).toEqual([0, 1, 2, 3]);
    vi.advanceTimersByTime(30_000);
    expect(reader.close).toHaveBeenCalledOnce();
    provider.dispose();
    expect(reader.close).toHaveBeenCalledOnce();
  });

  it('requires an opted-in, ready generation and aborts outstanding work when that frame closes', async () => {
    const message = { type: 'pet-portraits', action: 'list', params: {}, generation: 'current', id: '1' };
    const reply = vi.fn();
    const noCapability = createPackageBroker('current', vi.fn(), vi.fn());
    await noCapability.receive({ type: 'ready', generation: 'current' }, vi.fn());
    await noCapability.receive(message, reply);
    expect(reply.mock.calls.at(-1)![0]).toMatchObject({ ok: false, error: expect.stringContaining('未连接') });
    let lifetime!: AbortSignal, finish!: () => void;
    const request = vi.fn((_operation, signal: AbortSignal) => {
      lifetime = signal;
      return new Promise<void>(resolve => { finish = resolve; });
    });
    const broker = createPackageBroker('current', vi.fn(), vi.fn(), undefined, undefined, request);
    await broker.receive(message, reply);
    expect(reply.mock.calls.at(-1)![0]).toMatchObject({ ok: false, error: expect.stringContaining('MODULE_NOT_READY') });
    await broker.receive({ type: 'ready', generation: 'current' }, vi.fn());
    await broker.receive({ ...message, generation: 'old' }, reply);
    expect(request).not.toHaveBeenCalled();
    const read = broker.receive(message, reply);
    expect(request).toHaveBeenCalledOnce();
    reply.mockClear(); broker.dispose(); finish(); await read;
    expect(lifetime.aborted).toBe(true);
    expect(reply).not.toHaveBeenCalled();
  });
});
