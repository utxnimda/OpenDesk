import { onBeforeUnmount, onMounted, ref, shallowRef, watch, type Ref } from 'vue';
import { errorMessage, type PetPortraitCatalog, type PetPortraitsClient } from '../../../framework/module-sdk/src/index';
import { avatarFrameFromRgba, defaultAvatarCrop, exportAvatarCrop } from './avatar';

/** The catalog and default image are view state. Only explicit edits enter the profile draft. */
export function usePetPortraits(client: PetPortraitsClient | undefined, needsDefault: Ref<boolean>) {
  const catalog = shallowRef<PetPortraitCatalog>();
  const catalogError = ref('');
  const fallback = ref('');
  const fallbackError = ref('');
  let revision = 0;
  let unsubscribe: (() => void) | undefined;

  async function refresh() {
    if (!client) return;
    const ticket = ++revision;
    catalog.value = undefined;
    catalogError.value = '';
    try {
      const next = await client.list();
      if (ticket === revision) catalog.value = next;
    } catch (cause) {
      if (ticket === revision) catalogError.value = '无法读取桌宠列表。' + errorMessage(cause);
    }
  }

  watch([catalog, needsDefault], async ([list, needed], _, onCleanup) => {
    let current = true;
    onCleanup(() => { current = false; });
    fallback.value = fallbackError.value = '';
    const pet = list?.pets.find(item => item.id === list.activeId);
    if (!client || !needed || !pet) return;
    for (const id of ['idle', 'still']) {
      if (!pet.sources.some(source => source.id === id)) continue;
      try {
        const raw = await client.frame(pet.id, id, 0);
        if (!current) return;
        fallback.value = exportAvatarCrop(avatarFrameFromRgba(raw), defaultAvatarCrop()).preview;
        if (id === 'still' && fallbackError.value) fallbackError.value = '待机首帧暂不可用，已显示桌宠静态图。';
        return;
      } catch (cause) {
        if (!current) return;
        fallbackError.value = '无法显示桌宠头像。' + errorMessage(cause);
      }
    }
  }, {flush: 'sync'});

  onMounted(() => {
    unsubscribe = client?.onChange(refresh);
    void refresh();
  });
  onBeforeUnmount(() => { revision++; unsubscribe?.(); });
  return {catalog, catalogError, fallback, fallbackError, refresh};
}
