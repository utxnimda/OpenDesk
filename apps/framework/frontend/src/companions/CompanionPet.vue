<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { Footprints, Hand, Moon, Pause, Play, X } from '@lucide/vue';
import { companionExtraActions, companionFrame, companionSize, type CompanionAction, type CompanionModel } from './contracts';

const props = withDefaults(defineProps<{
  model: CompanionModel;
  size: number;
  animated: boolean;
  active?: boolean;
  reducedMotion?: boolean;
  dragging?: boolean;
}>(), { active: true, reducedMotion: false, dragging: false });
const emit = defineEmits<{
  dragStart: [event: PointerEvent];
  dragMove: [event: PointerEvent];
  dragEnd: [event: PointerEvent];
  dismiss: [];
  assetReady: [modelId: string];
}>();

const action = ref<CompanionAction>('idle');
const paused = ref(false);
const resting = ref(false);
const systemReducedMotion = ref(false);
const pageHidden = ref(false);
const bubble = ref('');
const imageFailed = ref(false);
const imageError = '角色图片未能加载，请重新打开宠物或切换角色。';
const allowAnimation = computed(() => props.animated && props.active && !props.reducedMotion && !systemReducedMotion.value && !pageHidden.value && !paused.value);
const extraActions = computed(() => companionExtraActions(props.model));
const frame = computed(() => companionFrame(props.model, action.value, allowAnimation.value));
const source = computed(() => frame.value.source);
const status = computed(() => ({ still: '静静陪伴', sleep: '休息中', walk: '活动一下', wave: '挥手', idle: '陪伴中' })[frame.value.action]);
const dimensions = computed(() => ({ '--pet-size': companionSize(props.size) + 'px', '--pet-width': Math.max(240, companionSize(props.size) + 80) + 'px' }));
const greetings = ['我在这里，慢慢来。', '今天也给自己留一点空间。', '一起把小事做好。'];
let greetingIndex = 0;
let actionTimer: ReturnType<typeof setTimeout> | undefined;
let bubbleTimer: ReturnType<typeof setTimeout> | undefined;
let preference: MediaQueryList | undefined;
let pointer: { id: number; x: number; y: number; moved: boolean } | null = null;
let suppressClickUntil = 0;

function clearTimers() { clearTimeout(actionTimer); clearTimeout(bubbleTimer); actionTimer = undefined; bubbleTimer = undefined; }
function say(text: string) {
  clearTimeout(bubbleTimer);
  bubble.value = text;
  if (props.active && !pageHidden.value) bubbleTimer = setTimeout(() => { bubble.value = ''; }, 3200);
}
function resetAction() { clearTimeout(actionTimer); actionTimer = undefined; action.value = resting.value ? 'sleep' : 'idle'; }
function wave() {
  if (!allowAnimation.value || !extraActions.value.includes('wave')) return;
  resting.value = false;
  action.value = 'wave';
  clearTimeout(actionTimer);
  say(greetings[greetingIndex++ % greetings.length]);
  actionTimer = setTimeout(resetAction, 1800);
}
function walk() {
  if (!allowAnimation.value || !extraActions.value.includes('walk')) return;
  resting.value = false;
  action.value = 'walk';
  clearTimeout(actionTimer);
  say('活动一下，马上回来。');
  actionTimer = setTimeout(resetAction, 3984);
}
function togglePause() { paused.value = !paused.value; resetAction(); say(paused.value ? '暂停动画，安静陪着你。' : '准备好了，继续陪伴。'); }
function toggleRest() {
  if (!extraActions.value.includes('sleep') || (!resting.value && !allowAnimation.value)) return;
  resting.value = !resting.value; resetAction(); say(resting.value ? '休息一下，需要时再叫我。' : '我回来啦。');
}
function onAvatarClick(event: MouseEvent) { if (event.detail > 0 && performance.now() < suppressClickUntil) return; wave(); }
function onPointerDown(event: PointerEvent) {
  if (event.button !== 0) return;
  pointer = { id: event.pointerId, x: event.clientX, y: event.clientY, moved: false };
  (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
  emit('dragStart', event);
}
function onPointerMove(event: PointerEvent) {
  if (!pointer || pointer.id !== event.pointerId) return;
  if (Math.hypot(event.clientX - pointer.x, event.clientY - pointer.y) > 5) pointer.moved = true;
  emit('dragMove', event);
}
function onPointerEnd(event: PointerEvent) {
  if (!pointer || pointer.id !== event.pointerId) return;
  if (pointer.moved) suppressClickUntil = performance.now() + 350;
  pointer = null;
  const target = event.currentTarget as HTMLElement;
  if (target.hasPointerCapture(event.pointerId)) target.releasePointerCapture(event.pointerId);
  emit('dragEnd', event);
}
function updateVisibility() { pageHidden.value = document.hidden; }
function updateMotion() { systemReducedMotion.value = preference?.matches ?? false; }
watch(source, () => { imageFailed.value = false; });
watch(() => props.model.id, () => { clearTimers(); action.value = 'idle'; resting.value = false; bubble.value = ''; });
watch(extraActions, (available) => {
  if (action.value !== 'idle' && !available.includes(action.value)) { clearTimers(); action.value = 'idle'; resting.value = false; bubble.value = ''; }
});
watch([() => props.active, pageHidden, allowAnimation], ([active, hidden]) => {
  if (!active || hidden || !allowAnimation.value) { clearTimers(); action.value = resting.value ? 'sleep' : 'idle'; bubble.value = ''; }
});
onMounted(() => {
  preference = window.matchMedia('(prefers-reduced-motion: reduce)');
  updateMotion(); updateVisibility();
  preference.addEventListener('change', updateMotion);
  document.addEventListener('visibilitychange', updateVisibility);
});
onUnmounted(() => { clearTimers(); pointer = null; preference?.removeEventListener('change', updateMotion); document.removeEventListener('visibilitychange', updateVisibility); });
</script>

<template>
  <div class="companion-pet" :class="{ 'is-dragging': dragging, 'is-resting': resting }" :style="dimensions" :data-action="frame.action">
    <div v-if="bubble" class="companion-bubble" role="status">{{ bubble }}</div>
    <button class="companion-avatar" :aria-label="model.label + '，' + status + (extraActions.includes('wave') && allowAnimation ? '；点击挥手，可拖动' : '；可拖动')" :title="model.label + (extraActions.includes('wave') && allowAnimation ? ' · 点击挥手 / 拖动移动' : ' · 拖动移动')" @pointerdown="onPointerDown" @pointermove="onPointerMove" @pointerup="onPointerEnd" @pointercancel="onPointerEnd" @click="onAvatarClick">
      <img v-if="!imageFailed" :src="source" :alt="model.label" draggable="false" decoding="async" @load="emit('assetReady', model.id)" @error="imageFailed = true" />
      <span v-else class="companion-image-error" role="alert">{{ imageError }}</span>
    </button>
    <div class="companion-toolbar" role="group" :aria-label="model.label + '的互动'">
      <button :aria-label="paused ? '继续宠物动画' : '暂停宠物动画'" :title="paused ? '继续动画' : '暂停动画'" :aria-pressed="paused" :disabled="!animated || !active || reducedMotion || systemReducedMotion || pageHidden" @click="togglePause"><Play v-if="paused" :size="14" /><Pause v-else :size="14" /></button>
      <button v-if="extraActions.includes('wave')" aria-label="宠物挥手" title="挥手" :disabled="!allowAnimation" @click="wave"><Hand :size="14" /></button>
      <button v-if="extraActions.includes('walk')" aria-label="宠物活动一下" title="活动一下" :disabled="!allowAnimation" @click="walk"><Footprints :size="14" /></button>
      <button v-if="extraActions.includes('sleep')" :aria-label="resting ? '唤醒宠物' : '让宠物休息'" :title="resting ? '唤醒' : '休息'" :aria-pressed="resting" :disabled="!resting && !allowAnimation" @click="toggleRest"><Moon :size="14" /></button>
      <span class="companion-control-separator" aria-hidden="true"></span>
      <button aria-label="暂时收起宠物" title="暂时收起" @click="emit('dismiss')"><X :size="14" /></button>
    </div>
  </div>
</template>
