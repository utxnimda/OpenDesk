<script setup lang="ts">
import { onUnmounted, ref } from 'vue';
import { DEFAULT_SIDEBAR_WIDTH, keyboardSidebarWidth } from './sidebarResize';

const props = defineProps<{ width: number; min: number; max: number }>();
const emit = defineEmits<{ preview: [width: number]; commit: [width: number]; cancel: []; resizing: [active: boolean] }>();
const divider = ref<HTMLElement | null>(null);
let drag: { pointer: number; startX: number; startWidth: number } | undefined;
function dragWidth(clientX: number) { return drag ? Math.max(props.min, Math.min(props.max, Math.round(drag.startWidth + clientX - drag.startX))) : props.width; }
function begin(event: PointerEvent) {
  if (event.button !== 0 || !event.isPrimary || drag) return;
  event.preventDefault();
  (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
  drag = { pointer: event.pointerId, startX: event.clientX, startWidth: props.width };
  emit('resizing', true);
  divider.value?.focus({ preventScroll: true });
}
function move(event: PointerEvent) { if (drag?.pointer === event.pointerId) emit('preview', dragWidth(event.clientX)); }
function release(event: PointerEvent) {
  if (drag?.pointer !== event.pointerId) return;
  const width = dragWidth(event.clientX);
  const changed = event.clientX !== drag.startX && width !== drag.startWidth;
  drag = undefined;
  if (divider.value?.hasPointerCapture(event.pointerId)) divider.value.releasePointerCapture(event.pointerId);
  if (!changed) emit('cancel');
  emit('resizing', false);
  if (changed) emit('commit', width);
}
function cancel() {
  if (!drag) return;
  const pointer = drag.pointer;
  drag = undefined;
  if (divider.value?.hasPointerCapture(pointer)) divider.value.releasePointerCapture(pointer);
  emit('cancel');
  emit('resizing', false);
}
function key(event: KeyboardEvent) {
  if (drag) { if (event.key === 'Escape') { event.preventDefault(); cancel(); } return; }
  const width = keyboardSidebarWidth(event.key, props.width, props.min, props.max, event.shiftKey);
  if (width !== null) { event.preventDefault(); emit('commit', width); }
}
onUnmounted(cancel);
</script>

<template>
  <div ref="divider" class="sidebar-divider" role="separator" aria-orientation="vertical" aria-label="工作区侧栏宽度" aria-controls="ai-workspace-sidebar" :aria-valuemin="min" :aria-valuemax="max" :aria-valuenow="width" :aria-valuetext="`${width} 像素`" tabindex="0" title="拖动调整宽度；方向键微调，双击复位" @pointerdown="begin" @pointermove="move" @pointerup="release" @pointercancel="cancel" @lostpointercapture="cancel" @dblclick="emit('commit', DEFAULT_SIDEBAR_WIDTH)" @keydown="key"></div>
</template>
