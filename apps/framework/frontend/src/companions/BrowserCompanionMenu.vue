<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import type { PetSettings } from '../core/personalization';
import type { CompanionModel, CompanionPoint } from './contracts';
import type { CompanionMenuChoice } from './menu';
import CompanionMenuPanel from './CompanionMenuPanel.vue';

const props = withDefaults(defineProps<{
  model: CompanionModel;
  settings: PetSettings;
  canAnimate: boolean;
  desktopAvailable?: boolean;
  busy?: boolean;
  error?: string;
  position: CompanionPoint;
}>(), { desktopAvailable: true, busy: false, error: '' });
const emit = defineEmits<{ select: [choice: CompanionMenuChoice]; close: [] }>();
const element = ref<HTMLElement | null>(null);
const location = ref({ left: `${props.position.x}px`, top: `${props.position.y}px` });
let previousFocus: HTMLElement | null = null;
let restoreFocus = true;
let resizeObserver: ResizeObserver | undefined;
let disposed = false;

function fit() {
  const rect = element.value?.getBoundingClientRect();
  if (!rect) return;
  location.value = {
    left: `${Math.max(0, Math.min(props.position.x, window.innerWidth - rect.width))}px`,
    top: `${Math.max(0, Math.min(props.position.y, window.innerHeight - rect.height))}px`,
  };
}
function outside(event: PointerEvent) {
  if (event.target instanceof Node && !element.value?.contains(event.target)) {
    restoreFocus = false;
    emit('close');
  }
}
watch(() => [props.position.x, props.position.y], async () => { await nextTick(); if (!disposed) fit(); });
onMounted(async () => {
  previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  document.addEventListener('pointerdown', outside, true);
  window.addEventListener('resize', fit);
  await nextTick();
  if (disposed) return;
  fit();
  resizeObserver = new ResizeObserver(fit);
  if (element.value) resizeObserver.observe(element.value);
});
onUnmounted(() => {
  disposed = true;
  resizeObserver?.disconnect();
  document.removeEventListener('pointerdown', outside, true);
  window.removeEventListener('resize', fit);
  if (restoreFocus && previousFocus?.isConnected) previousFocus.focus();
});
</script>

<template>
  <Teleport to="body">
    <div ref="element" class="pet-menu-position" :style="location">
      <CompanionMenuPanel :model="model" :settings="settings" :can-animate="canAnimate" :desktop-available="desktopAvailable" :busy="busy" :error="error" @select="emit('select', $event)" @close="emit('close')" />
    </div>
  </Teleport>
</template>

<style scoped>
.pet-menu-position { position: fixed; z-index: 10000; width: 320px; max-width: 100vw; max-height: 100dvh; pointer-events: auto; }
</style>
