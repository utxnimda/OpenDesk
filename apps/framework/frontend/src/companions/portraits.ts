import type { PetPortraitCatalog, PetPortraitFrame, PetPortraitOperation } from '../../../module-sdk/src/pet-portraits';
import type { PetSettings } from '../core/personalization';
import { companionExtraActions, findCompanion, type CompanionAction, type CompanionCatalog, type CompanionModel } from './contracts';
import { actionNames } from './menu';
import { openPortraitMedia, type PortraitAsset, type PortraitReader } from './portrait-media';

function sources(model: CompanionModel): Array<{ id: string; label: string; asset: PortraitAsset }> {
  const result: Array<{ id: string; label: string; asset: PortraitAsset }> = [];
  const actions: CompanionAction[] = ['idle', ...companionExtraActions(model)];
  for (const id of actions) {
    const url = model.assets[id]!;
    if (id !== 'idle' && id !== 'sleep' && model.actionMediaTypes?.[id] === 'video') {
      const timing = model.videoFrames?.[id];
      if (timing) result.push({ id, label: actionNames[id], asset: { url, kind: 'video', ...timing } });
    } else if (typeof ImageDecoder !== 'undefined') {
      result.push({ id, label: actionNames[id], asset: { url, kind: 'animation' } });
    }
  }
  result.push({ id: 'still', label: '静态形象', asset: { url: model.assets.still, kind: 'still' } });
  return result;
}

/** One bounded decoder shared by opted-in module frames. Only the composition catalog supplies URLs. */
export function createPetPortraitProvider(catalog: CompanionCatalog, savedPet: () => PetSettings | null, open = openPortraitMedia) {
  const lifetime = new AbortController();
  let queue: Promise<void> = Promise.resolve();
  let pending = 0;
  let cache: { url: string; reader: PortraitReader; last?: PetPortraitFrame } | undefined;
  let idleTimer: ReturnType<typeof setTimeout> | undefined;
  function release() {
    clearTimeout(idleTimer);
    cache?.reader.close(); cache = undefined;
  }
  function activeId() {
    const pet = savedPet();
    return pet?.enabled ? findCompanion(catalog, pet.modelId)?.id ?? null : null;
  }
  function list(): PetPortraitCatalog {
    if (!savedPet()) throw new Error('宿主尚未读取已保存的桌宠设置，请在设置恢复后重试。');
    return { activeId: activeId(), pets: catalog.models.map(model => ({
      id: model.id, label: model.label, sources: sources(model).map(({ id, label }) => ({ id, label })),
    })) };
  }
  async function read(petId: string, sourceId: string, index: number, signal: AbortSignal) {
    signal.throwIfAborted();
    const model = catalog.models.find(item => item.id === petId);
    const asset = model && sources(model).find(item => item.id === sourceId)?.asset;
    if (!asset) throw new Error('桌宠或所选动作当前不可用，请重新选择。');
    clearTimeout(idleTimer);
    try {
      if (cache?.url !== asset.url) {
        release();
        cache = { url: asset.url, reader: await open(asset, signal) };
      }
      signal.throwIfAborted();
      if (index < 0 || !Number.isInteger(index) || index >= cache.reader.count) throw new RangeError('帧号超出桌宠动作的实际帧数。');
      if (cache.last?.index !== index) cache.last = await cache.reader.read(index, signal);
      return cache.last;
    } catch (error) {
      if (!(error instanceof RangeError)) release();
      if (signal.aborted) throw new Error('桌宠头像读取已中止或超时，请重新选帧。');
      if (error instanceof DOMException || error instanceof TypeError) throw new Error('桌宠媒体解码失败，请选择静态形象或重新选帧。');
      throw error;
    } finally {
      if (cache) idleTimer = setTimeout(release, 30_000);
    }
  }
  return {
    get activeId() { return activeId(); },
    async request(operation: PetPortraitOperation, caller: AbortSignal) {
      if (lifetime.signal.aborted || caller.aborted) throw new Error('桌宠头像连接已关闭。');
      if (operation.action === 'list') return list();
      if (pending >= 4) throw new Error('桌宠头像正在解码，请稍后重试。');
      const { petId, sourceId, index } = operation.params;
      const signal = AbortSignal.any([caller, lifetime.signal, AbortSignal.timeout(20_000)]);
      pending++;
      const work = queue.then(() => read(petId, sourceId, index, signal));
      queue = work.then(() => {}, () => {});
      try { return await work; } finally { pending--; }
    },
    dispose() { lifetime.abort(); release(); },
  };
}

export type PetPortraitProvider = ReturnType<typeof createPetPortraitProvider>;
