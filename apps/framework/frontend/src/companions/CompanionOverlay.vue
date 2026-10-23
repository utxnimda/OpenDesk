<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { PawPrint } from '@lucide/vue';
import type { PetSettings } from '../core/personalization';
import type { HostAdapter, Settings } from '../core/host';
import CompanionSurface from './CompanionSurface.vue';
import { canDisplayCompanion, clampCompanionPosition, companionBox, findCompanion, type CompanionCatalog, type CompanionPoint } from './contracts';

const props = defineProps<{ host: HostAdapter; catalog: CompanionCatalog; settings: PetSettings; recallKey?: number }>();
const emit = defineEmits<{ dismiss: []; saved: [settings: Settings]; preview: [settings: PetSettings] }>();
const dismissed = ref(false);
const position = ref<CompanionPoint>({ x: 0, y: 0 });
const viewport = ref({ width: 0, height: 0 });
const dragging = ref(false);
const box = computed(() => companionBox(props.settings.size));
const model = computed(() => findCompanion(props.catalog, props.settings.modelId));
const enabled = computed(() => canDisplayCompanion(props.catalog, props.settings, 'in-app'));
const geometry = computed(() => ({ left: position.value.x + 'px', top: position.value.y + 'px', width: box.value.width + 'px', height: box.value.height + 'px' }));
let initialized = false;
let drag: { id: number; origin: CompanionPoint; start: CompanionPoint } | null = null;
function measure() {
  viewport.value = { width: window.innerWidth, height: window.innerHeight };
  if (!initialized) { position.value = { x: viewport.value.width - box.value.width - 18, y: viewport.value.height - box.value.height - 12 }; initialized = true; }
  position.value = clampCompanionPosition(position.value, viewport.value, box.value);
}
function startDrag(event: PointerEvent) { drag = { id: event.pointerId, origin: { ...position.value }, start: { x: event.clientX, y: event.clientY } }; }
function moveDrag(event: PointerEvent) {
  if (!drag || drag.id !== event.pointerId) return;
  const dx = event.clientX - drag.start.x; const dy = event.clientY - drag.start.y;
  if (!dragging.value && Math.hypot(dx, dy) <= 5) return;
  dragging.value = true;
  position.value = clampCompanionPosition({ x: drag.origin.x + dx, y: drag.origin.y + dy }, viewport.value, box.value);
}
function endDrag() { drag = null; dragging.value = false; }
function dismiss() { endDrag(); dismissed.value = true; emit('dismiss'); }
watch([() => props.settings.enabled, () => props.settings.modelId, () => props.settings.mode], () => { dismissed.value = false; void nextTick(measure); });
watch(() => props.recallKey, () => { dismissed.value = false; void nextTick(measure); });
watch(box, () => { void nextTick(measure); });
onMounted(() => { measure(); window.addEventListener('resize', measure); });
onUnmounted(() => { endDrag(); window.removeEventListener('resize', measure); });
</script>

<template>
  <div v-if="enabled" class="companion-overlay" aria-label="应用内宠物">
    <button v-if="dismissed" class="companion-recall" @click="dismissed = false"><PawPrint :size="15" />唤回宠物</button>
    <div v-else-if="model" class="companion-overlay-position" :style="geometry">
      <CompanionSurface :host="host" :model="model" :settings="settings" :dragging="dragging" @saved="emit('saved', $event)" @preview="emit('preview', $event)" @drag-start="startDrag" @drag-move="moveDrag" @drag-end="endDrag" @dismiss="dismiss" />
    </div>
  </div>
</template>
