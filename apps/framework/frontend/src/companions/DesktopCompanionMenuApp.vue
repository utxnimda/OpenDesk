<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue';
import { host as desktopHost, errorMessage, type CompanionMenuContext, type HostAdapter, type Settings } from '../core/host';
import { findCompanion, type CompanionCatalog } from './contracts';
import { companionMenuChange, type CompanionMenuChoice } from './menu';
import CompanionMenuPanel from './CompanionMenuPanel.vue';

const props = defineProps<{ catalog: CompanionCatalog; adapter?: HostAdapter }>();
const host = props.adapter ?? desktopHost;
const context = ref<CompanionMenuContext | null>(null);
const settings = ref<Settings | null>(null);
const failure = ref(''); const loading = ref(true); const busy = ref(false);
const model = computed(() => context.value ? findCompanion(props.catalog, context.value.modelId) : undefined);
const systemReducedMotion = ref(false);
const canAnimate = computed(() => Boolean(context.value?.canAnimate) && !systemReducedMotion.value);
const releases: Array<() => void> = [];
let disposed = false; let revision = 0; let settingsRevision = 0; let operation = 0;
let themeMedia: MediaQueryList | undefined; let motionMedia: MediaQueryList | undefined;

function applyTheme() {
  if (!settings.value) return;
  document.documentElement.dataset.theme = settings.value.theme === 'system' ? (themeMedia?.matches ? 'dark' : 'light') : settings.value.theme;
  document.documentElement.dataset.uiStyle = settings.value.uiStyle;
}
function applyMotion() { systemReducedMotion.value = motionMedia?.matches ?? false; }
function matchesContext(value: Settings, current: CompanionMenuContext): boolean {
  const pet = value.personalization.pet;
  return pet.enabled && pet.mode === 'desktop' && (pet.modelId ?? props.catalog.defaultId) === current.modelId;
}
async function close() {
  const current = context.value;
  if (!current) return;
  try {
    await host.closeCompanionMenu(current.generation);
    if (context.value?.generation === current.generation) context.value = null;
  } catch (error) { if (!disposed && context.value?.generation === current.generation) failure.value = errorMessage(error); }
}
async function refreshSettings() {
  const id = ++settingsRevision;
  try {
    const value = await host.getSettings();
    if (disposed || id !== settingsRevision) return;
    settings.value = value; applyTheme();
    if (context.value && !matchesContext(value, context.value)) await close();
  } catch (error) { if (!disposed && id === settingsRevision) failure.value = errorMessage(error); }
}
async function refreshContext() {
  const id = ++revision; const settingId = ++settingsRevision;
  loading.value = true; failure.value = ''; operation++; busy.value = false;
  try {
    const [current, value] = await Promise.all([host.getCompanionMenuContext(), host.getSettings()]);
    if (disposed || id !== revision) return;
    context.value = current;
    if (settingId === settingsRevision) settings.value = value;
    else await refreshSettings();
    if (disposed || id !== revision || context.value?.generation !== current.generation) return;
    applyTheme();
    if (!settings.value || !matchesContext(settings.value, current)) { await close(); return; }
    if (!model.value) throw new Error('当前角色资源不可用，请在应用内重新选择宠物。');
    loading.value = false;
    await nextTick();
    if (!disposed && context.value?.generation === current.generation && id === revision) await host.companionMenuReady(current.generation);
  } catch (error) { if (!disposed && id === revision) failure.value = errorMessage(error); }
  finally { if (!disposed && id === revision) loading.value = false; }
}
async function select(choice: CompanionMenuChoice) {
  const current = context.value; const selected = model.value; const preferences = settings.value;
  if (disposed || busy.value || !current || !selected || !preferences || !matchesContext(preferences, current)) return;
  const token = ++operation;
  busy.value = true; failure.value = '';
  try {
    if (choice === 'hide') { await host.hideCompanion(current.generation); await close(); return; }
    if (choice === 'open-app') { await close(); await host.openCompanionApp(); return; }
    if (choice === 'toggle-animation' && !canAnimate.value) return;
    const change = companionMenuChange(choice, preferences.personalization.pet, selected);
    if (!change) return;
    const saved = await host.updateCompanion(change, current.generation);
    if (disposed || token !== operation || context.value?.generation !== current.generation) return;
    settingsRevision++; settings.value = saved; applyTheme();
    if (!matchesContext(saved, current)) await close();
  } catch (error) { if (!disposed && token === operation && context.value?.generation === current.generation) failure.value = errorMessage(error); }
  finally { if (!disposed && token === operation) busy.value = false; }
}
onMounted(async () => {
  document.documentElement.dataset.surface = 'companion-menu';
  themeMedia = window.matchMedia('(prefers-color-scheme: dark)');
  motionMedia = window.matchMedia('(prefers-reduced-motion: reduce)');
  themeMedia.addEventListener('change', applyTheme); motionMedia.addEventListener('change', applyMotion); applyMotion();
  if (!host.available) { failure.value = '桌面宠物菜单需要由 OpenDesk 打开。'; loading.value = false; return; }
  try {
    for (const subscribe of [() => host.subscribeCompanionMenuChanged(() => { void refreshContext(); }), () => host.subscribeSettingsChanged(() => { void refreshSettings(); })]) {
      const release = await subscribe();
      if (disposed) { release(); return; } releases.push(release);
    }
    await refreshContext();
  } catch (error) { if (!disposed) { failure.value = errorMessage(error); loading.value = false; } }
});
onUnmounted(() => { disposed = true; revision++; settingsRevision++; operation++; releases.forEach((release) => release()); themeMedia?.removeEventListener('change', applyTheme); motionMedia?.removeEventListener('change', applyMotion); });
</script>

<template>
  <main class="desktop-companion-menu" aria-label="宠物菜单" @contextmenu.prevent>
    <CompanionMenuPanel v-if="model && settings && context && !loading" :model="model" :settings="settings.personalization.pet" :can-animate="canAnimate" :busy="busy" :error="failure" @select="select" @close="close" />
    <div v-else-if="loading || failure" class="pet-menu-message" :role="failure ? 'alert' : 'status'">
      <p>{{ failure || '正在打开宠物菜单…' }}</p><button v-if="context" @click="close">关闭</button>
    </div>
  </main>
</template>

<style scoped>
.desktop-companion-menu { width: 100%; height: 100%; min-height: 0; overflow: hidden; }
.pet-menu-message { margin: 8px; padding: 16px; border: 1px solid var(--border); border-radius: 18px; background: var(--surface); color: var(--text); font-size: 12px; line-height: 1.6; }
.pet-menu-message button { border: 0; border-radius: 8px; padding: 6px 12px; background: var(--accent-soft); color: var(--accent); }
</style>
