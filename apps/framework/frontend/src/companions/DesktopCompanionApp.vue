<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { errorMessage, host as desktopHost, type HostAdapter, type Settings } from '../core/host';
import CompanionPet from './CompanionPet.vue';
import { canDisplayCompanion, findCompanion, type CompanionCatalog } from './contracts';

const props = defineProps<{ catalog: CompanionCatalog; adapter?: HostAdapter }>();
const host = props.adapter ?? desktopHost;
const settings = ref<Settings | null>(null);
const failure = ref('');
const loading = ref(true);
const visible = ref(false);
const dragging = ref(false);
const model = computed(() => settings.value ? findCompanion(props.catalog, settings.value.personalization.pet.modelId) : undefined);
const pet = computed(() => settings.value?.personalization.pet);
const disposers: Array<() => void> = [];
let disposed = false;
let request = 0;
let visibilityRevision = 0;
let readyModel: string | null = null;
let pendingReadyModel: string | null = null;
let connecting = false;
let drag: { x: number; y: number; requested: boolean } | null = null;
const systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
function applyTheme() {
  if (!settings.value) return;
  document.documentElement.dataset.theme = settings.value.theme === 'system' ? (systemTheme.matches ? 'dark' : 'light') : settings.value.theme;
  document.documentElement.dataset.uiStyle = settings.value.uiStyle;
}
async function refresh() {
  const revision = ++request;
  try {
    const value = await host.getSettings();
    if (!disposed && revision === request) {
      settings.value = value; failure.value = ''; applyTheme();
      if (!canDisplayCompanion(props.catalog, value.personalization.pet, 'desktop')) await dismiss();
    }
  }
  catch (error) { if (!disposed && revision === request) failure.value = errorMessage(error); }
  finally { if (!disposed && revision === request) loading.value = false; }
}
async function dismiss() { try { await host.hideCompanion(); visible.value = false; } catch (error) { failure.value = errorMessage(error); } }
function startDrag(event: PointerEvent) { drag = { x: event.clientX, y: event.clientY, requested: false }; }
async function moveDrag(event: PointerEvent) {
  if (!drag || drag.requested || Math.hypot(event.clientX - drag.x, event.clientY - drag.y) <= 5) return;
  drag.requested = true; dragging.value = true;
  try { await host.startCompanionDrag(); }
  catch (error) { if (!disposed) failure.value = '无法移动宠物：' + errorMessage(error); }
  finally { dragging.value = false; drag = null; }
}
function endDrag() { drag = null; dragging.value = false; }
async function reportAssetReady(modelId: string) {
  if (disposed || model.value?.id !== modelId || readyModel === modelId || pendingReadyModel === modelId) return;
  pendingReadyModel = modelId;
  await nextTick();
  if (disposed || model.value?.id !== modelId) { pendingReadyModel = null; return; }
  try {
    await host.companionReady(modelId);
    if (!disposed && model.value?.id === modelId) readyModel = modelId;
  } catch (error) { if (!disposed && model.value?.id === modelId) failure.value = '宠物就绪通知失败：' + errorMessage(error); }
  finally { if (pendingReadyModel === modelId) pendingReadyModel = null; }
}
watch(() => model.value?.id, () => { readyModel = null; });
async function connect() {
  if (connecting || disposed) return;
  if (!host.available) { loading.value = false; failure.value = '独立桌面宠物需要在 OpenDesk 桌面应用中打开。'; return; }
  connecting = true; loading.value = true; failure.value = '';
  disposers.splice(0).forEach((dispose) => dispose());
  try {
    const changed = await host.subscribeSettingsChanged(() => { void refresh(); });
    if (disposed) { changed(); return; } disposers.push(changed);
    const visibilityChanged = await host.subscribeCompanionVisibilityChanged((value) => {
      visibilityRevision++; visible.value = value;
      if (!value) endDrag();
      else if (!loading.value && settings.value && !canDisplayCompanion(props.catalog, settings.value.personalization.pet, 'desktop')) void dismiss();
    });
    if (disposed) { visibilityChanged(); return; } disposers.push(visibilityChanged);
    const revision = visibilityRevision;
    const state = await host.getCompanionState();
    if (!disposed && revision === visibilityRevision) visible.value = state.visible;
    await refresh();
  } catch (error) { if (!disposed) { failure.value = errorMessage(error); loading.value = false; } }
  finally { connecting = false; }
}
onMounted(() => {
  document.documentElement.dataset.surface = 'companion';
  systemTheme.addEventListener('change', applyTheme);
  void connect();
});
onUnmounted(() => { disposed = true; request++; endDrag(); systemTheme.removeEventListener('change', applyTheme); disposers.forEach((dispose) => dispose()); });
</script>

<template>
  <main v-if="catalog.models.length" class="desktop-companion" aria-label="OpenDesk 桌面宠物">
    <div v-if="failure" class="desktop-companion-message" role="alert"><strong>宠物暂时不可用</strong><p>{{ failure }}</p><div><button :disabled="loading" @click="connect">重试连接</button><button v-if="host.available" @click="dismiss">暂时收起</button></div></div>
    <div v-else-if="loading" class="desktop-companion-message" role="status">正在打开宠物…</div>
    <CompanionPet v-else-if="model && pet && pet.enabled && pet.mode === 'desktop'" :model="model" :size="pet.size" :animated="pet.animated" :active="visible" :dragging="dragging" @drag-start="startDrag" @drag-move="moveDrag" @drag-end="endDrag" @dismiss="dismiss" @asset-ready="reportAssetReady" />
  </main>
</template>
