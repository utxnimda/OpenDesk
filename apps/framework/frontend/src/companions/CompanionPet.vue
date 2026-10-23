<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue';
import { MoreHorizontal, Pause, Play } from '@lucide/vue';
import { companionActionDuration, companionActionMedia, companionExtraActions, companionFrame, companionPrimaryAction, companionSelectedAction, companionSize, type CompanionAction, type CompanionMenuRequest, type CompanionModel, type CompanionTransientAction } from './contracts';
import { createCompanionPlayback, type CompanionPlaybackFrame } from './playback';

const props = withDefaults(defineProps<{
  model: CompanionModel;
  size: number;
  animated: boolean;
  actionMode?: CompanionAction;
  active?: boolean;
  reducedMotion?: boolean;
  dragging?: boolean;
}>(), { actionMode: 'idle', active: true, reducedMotion: false, dragging: false });
const emit = defineEmits<{
  dragStart: [event: PointerEvent];
  dragMove: [event: PointerEvent];
  dragEnd: [event: PointerEvent];
  dismiss: [];
  assetReady: [modelId: string];
  animatedChange: [animated: boolean];
  menu: [request: CompanionMenuRequest];
}>();

const systemReducedMotion = ref(false);
const pageHidden = ref(false);
const bubble = ref('');
const canAnimate = computed(() => props.active && !props.reducedMotion && !systemReducedMotion.value && !pageHidden.value);
const allowAnimation = computed(() => props.animated && canAnimate.value);
const extraActions = computed(() => companionExtraActions(props.model));
const primaryAction = computed(() => companionPrimaryAction(props.model));
const selectedAction = computed(() => companionSelectedAction(props.model, props.actionMode));
const labels = { still: '静静陪伴', sleep: '休息中', walk: '活动一下', wave: '挥手', thinking: '托腮思考', idle: '陪伴中' };
const video = ref<HTMLVideoElement | null>(null);
const avatar = ref<HTMLButtonElement | null>(null);
const frame = shallowRef<CompanionPlaybackFrame>({ generation: 0, modelId: props.model.id, ...companionFrame(props.model, 'idle', allowAnimation.value), media: 'image', phase: 'loading' });
function releaseVideo() { const current = video.value; if (current) { current.pause(); current.removeAttribute('src'); current.load(); } }
const playback = createCompanionPlayback((value) => {
  if (value.generation !== frame.value.generation) releaseVideo();
  frame.value = value;
}, showSelectedMode);
const source = computed(() => frame.value.source);
const status = computed(() => frame.value.phase === 'failed' ? '资源加载失败' : frame.value.phase !== 'playing' && frame.value.action !== 'idle' && frame.value.action !== 'still' ? '正在准备动作' : labels[frame.value.action]);
const interactionHint = computed(() => primaryAction.value && allowAnimation.value ? `点击${labels[primaryAction.value]}，可拖动` : '可拖动');
const dimensions = computed(() => ({ '--pet-size': companionSize(props.size) + 'px', '--pet-width': Math.max(240, companionSize(props.size) + 80) + 'px' }));
const greetings = ['我在这里，慢慢来。', '今天也给自己留一点空间。', '一起把小事做好。'];
let greetingIndex = 0;
let bubbleTimer: ReturnType<typeof setTimeout> | undefined;
let preference: MediaQueryList | undefined;
let pointer: { id: number; x: number; y: number; moved: boolean } | null = null;
let suppressClickUntil = 0;
let oneShotGeneration: number | undefined;

function clearBubble() { clearTimeout(bubbleTimer); bubbleTimer = undefined; bubble.value = ''; }
function say(text: string) {
  clearTimeout(bubbleTimer);
  bubble.value = text;
  if (props.active && !pageHidden.value) bubbleTimer = setTimeout(() => { bubble.value = ''; }, 3200);
}
function showSelectedMode() {
  oneShotGeneration = undefined;
  clearBubble();
  const mode = selectedAction.value;
  if (!allowAnimation.value || mode === 'idle' || mode === 'sleep') {
    playback.show({ modelId: props.model.id, ...companionFrame(props.model, mode, allowAnimation.value) });
  } else {
    const durationMs = companionActionDuration(props.model, mode);
    if (durationMs !== undefined) playback.play({ modelId: props.model.id, action: mode, source: props.model.assets[mode]!, idle: props.model.assets.idle,
      media: companionActionMedia(props.model, mode), durationMs });
  }
}
function playAction(action: CompanionTransientAction) {
  const durationMs = companionActionDuration(props.model, action);
  if (!allowAnimation.value || !extraActions.value.includes(action) || durationMs === undefined) return;
  clearBubble();
  playback.play({ modelId: props.model.id, action, source: props.model.assets[action]!, idle: props.model.assets.idle,
    media: companionActionMedia(props.model, action), durationMs });
  oneShotGeneration = playback.current?.generation;
}
function togglePause() { if (canAnimate.value) emit('animatedChange', !props.animated); }
function requestMenu(point?: { x: number; y: number }, anchor: HTMLElement | null = avatar.value) {
  const rect = anchor?.getBoundingClientRect();
  emit('menu', { x: point?.x ?? rect?.left ?? 0, y: point?.y ?? rect?.bottom ?? 0, canAnimate: canAnimate.value });
}
function onContextMenu(event: MouseEvent) { requestMenu(event.clientX || event.clientY ? { x: event.clientX, y: event.clientY } : undefined); }
function onMenuButton(event: MouseEvent) { requestMenu(undefined, event.currentTarget as HTMLElement); }
function onMenuKey(event: KeyboardEvent) {
  if (event.key !== 'ContextMenu' && !(event.key === 'F10' && event.shiftKey)) return;
  event.preventDefault(); event.stopPropagation(); requestMenu();
}
function onAvatarClick(event: MouseEvent) { if (event.button !== 0 || (event.detail > 0 && performance.now() < suppressClickUntil)) return; if (primaryAction.value) playAction(primaryAction.value); }
function eventIdentity(event: Event) {
  const target = event.currentTarget as HTMLImageElement | HTMLVideoElement;
  return { target, generation: Number(target.dataset.playback), source: target.dataset.source ?? '' };
}
async function onMediaLoaded(event: Event) {
  const current = eventIdentity(event);
  if (!playback.loaded(current.generation, current.source)) return;
  if (frame.value.media === 'video') {
    try {
      const element = current.target as HTMLVideoElement;
      element.currentTime = 0;
      await element.play();
      if (!playback.started(current.generation, current.source)) return;
    } catch {
      playback.failed(current.generation, current.source, '动作视频无法播放，请重新点击动作或切换角色。');
      return;
    }
  }
  emit('assetReady', frame.value.modelId);
  if (oneShotGeneration === current.generation) {
    if (frame.value.action === 'wave') say(greetings[greetingIndex++ % greetings.length]);
    else if (frame.value.action === 'walk') say('活动一下，马上回来。');
  }
}
function onMediaError(event: Event) { const current = eventIdentity(event); playback.failed(current.generation, current.source); }
function onVideoEnded(event: Event) { const current = eventIdentity(event); playback.ended(current.generation, current.source); }
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
watch(() => props.model, () => { playback.cancel(); showSelectedMode(); }, { deep: true, flush: 'sync' });
watch([allowAnimation, selectedAction], showSelectedMode, { immediate: true, flush: 'sync' });
onMounted(() => {
  preference = window.matchMedia('(prefers-reduced-motion: reduce)');
  updateMotion(); updateVisibility();
  preference.addEventListener('change', updateMotion);
  document.addEventListener('visibilitychange', updateVisibility);
});
onBeforeUnmount(() => { playback.dispose(); releaseVideo(); clearBubble(); pointer = null; preference?.removeEventListener('change', updateMotion); document.removeEventListener('visibilitychange', updateVisibility); });
</script>

<template>
  <div class="companion-pet" :class="{ 'is-dragging': dragging, 'is-resting': selectedAction === 'sleep' }" :style="dimensions" :data-action="frame.action" :data-playback-phase="frame.phase" @contextmenu.prevent.stop="onContextMenu" @keydown="onMenuKey">
    <div v-if="bubble" class="companion-bubble" role="status">{{ bubble }}</div>
    <button ref="avatar" class="companion-avatar" :aria-label="model.label + '，' + status + '；' + interactionHint" :title="model.label + ' · ' + interactionHint" @pointerdown="onPointerDown" @pointermove="onPointerMove" @pointerup="onPointerEnd" @pointercancel="onPointerEnd" @click="onAvatarClick">
      <span v-if="frame.phase === 'failed'" class="companion-image-error" role="alert">{{ frame.error }}</span>
      <video v-else-if="frame.media === 'video'" :key="frame.generation" ref="video" :src="source" :aria-label="model.label + ' · ' + labels[frame.action]" :data-playback="frame.generation" :data-source="source" muted playsinline preload="auto" @loadeddata="onMediaLoaded" @ended="onVideoEnded" @error="onMediaError" />
      <img v-else :key="frame.generation" :src="source" :alt="model.label" :data-playback="frame.generation" :data-source="source" draggable="false" decoding="async" @load="onMediaLoaded" @error="onMediaError" />
    </button>
    <div class="companion-toolbar" role="group" :aria-label="model.label + '的互动'">
      <button :aria-label="animated ? '暂停宠物动画' : '继续宠物动画'" :title="animated ? '暂停动画' : '播放动画'" :aria-pressed="!animated" :disabled="!canAnimate" @click="togglePause"><Pause v-if="animated" :size="14" /><Play v-else :size="14" /></button>
      <button aria-label="宠物菜单" title="更多" @click="onMenuButton"><MoreHorizontal :size="14" /></button>
    </div>
  </div>
</template>

<style scoped>
.companion-avatar video { width: var(--pet-size); height: var(--pet-size); min-width: 0; min-height: 0; display: block; object-fit: contain; object-position: center bottom; pointer-events: none; }
</style>
