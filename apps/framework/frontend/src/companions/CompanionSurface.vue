<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue';
import { errorMessage, type HostAdapter, type Settings } from '../core/host';
import type { PetPreferenceChange, PetSettings } from '../core/personalization';
import CompanionPet from './CompanionPet.vue';
import BrowserCompanionMenu from './BrowserCompanionMenu.vue';
import { companionBox, type CompanionModel, type CompanionPoint } from './contracts';
import { applyPetPreference, companionMenuChange, type CompanionMenuChoice } from './menu';
const props = withDefaults(defineProps<{ host: HostAdapter; model: CompanionModel; settings: PetSettings; active?: boolean; dragging?: boolean }>(), { active: true, dragging: false });
const emit = defineEmits<{ saved: [settings: Settings]; preview: [settings: PetSettings]; dismiss: []; assetReady: [modelId: string]; dragStart: [event: PointerEvent]; dragMove: [event: PointerEvent]; dragEnd: [event: PointerEvent] }>();
const busy = ref(false); const failure = ref('');
const browserMenu = ref<{ position: CompanionPoint } | null>(null);
const canAnimate = ref(true);
let disposed = false; let menuRevision = 0;
function closeMenu() {
  menuRevision++; browserMenu.value = null;
}
function showFailure(error: unknown) { if (!disposed) failure.value = errorMessage(error); else console.error('Companion menu cleanup failed:', error); }
async function update(change: PetPreferenceChange) {
  if (busy.value || disposed) return;
  busy.value = true; failure.value = '';
  try {
    if (props.host.available) emit('saved', await props.host.updateCompanion(change));
    else emit('preview', applyPetPreference(props.settings, change));
  } catch (error) { showFailure(error); }
  finally { busy.value = false; }
}
async function select(choice: CompanionMenuChoice, revision: number, modelId: string) {
  if (disposed || busy.value || revision !== menuRevision || modelId !== props.model.id || !props.active) return;
  try {
    if (choice === 'hide') { closeMenu(); emit('dismiss'); return; }
    if (choice === 'open-app') { closeMenu(); await props.host.openCompanionApp(); return; }
    if (choice === 'toggle-animation' && !canAnimate.value) return;
    const change = companionMenuChange(choice, props.settings, props.model);
    if (change) await update(change);
  } catch (error) { showFailure(error); }
}
async function openMenu(request: CompanionPoint & { canAnimate: boolean }) {
  if (disposed || busy.value || !props.active) return;
  try {
    closeMenu(); failure.value = ''; canAnimate.value = request.canAnimate;
    if (props.host.available && props.settings.mode === 'desktop') {
      await props.host.showCompanionMenu(props.model.id, request.canAnimate);
    } else {
      browserMenu.value = { position: request };
    }
  } catch (error) { showFailure(error); }
}
function browserSelect(choice: CompanionMenuChoice) { void select(choice, menuRevision, props.model.id); }
function dismissMenu() { closeMenu(); }
watch(() => JSON.stringify([props.model.id, props.settings.mode, props.settings.enabled, props.active]), dismissMenu);
onUnmounted(() => { disposed = true; dismissMenu(); });
</script>

<template>
  <div class="companion-surface" :aria-busy="busy" :style="{ width: companionBox(settings.size).width + 'px' }">
    <CompanionPet :model="model" :size="settings.size" :animated="settings.animated" :action-mode="settings.actionMode" :active="active" :dragging="dragging" @animated-change="update({ type: 'animated', value: $event })" @menu="openMenu" @asset-ready="emit('assetReady', $event)" @drag-start="emit('dragStart', $event)" @drag-move="emit('dragMove', $event)" @drag-end="emit('dragEnd', $event)" />
    <div v-if="failure && !browserMenu" class="companion-setting-error" role="alert">{{ failure }}<button aria-label="关闭宠物设置错误" @click="failure = ''">×</button></div>
    <BrowserCompanionMenu v-if="browserMenu" :model="model" :settings="settings" :can-animate="canAnimate" :desktop-available="host.available" :busy="busy" :error="failure" :position="browserMenu.position" @select="browserSelect" @close="dismissMenu" />
  </div>
</template>

<style scoped>
.companion-surface { position: relative; max-width: 100%; pointer-events: none; }
.companion-setting-error { position: absolute; bottom: 38px; left: 8px; right: 8px; padding: 8px 24px 8px 10px; border: 1px solid #f0b7b7; border-radius: 8px; background: #fff6f6; color: #8a2222; font-size: 12px; line-height: 1.4; pointer-events: auto; }
.companion-setting-error button { position: absolute; right: 4px; top: 3px; border: 0; background: transparent; color: inherit; cursor: pointer; }
</style>
