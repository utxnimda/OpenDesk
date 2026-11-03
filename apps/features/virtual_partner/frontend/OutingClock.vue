<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, watch } from 'vue';
import type { LimitedHost } from '../../../framework/module-sdk/src/index';
import type { OutingScheduleState, OutingTick } from './outingClock';

const props = defineProps<{ host: LimitedHost; schedule: OutingScheduleState }>();
const emit = defineEmits<{ update: [OutingScheduleState]; started: []; failure: [string] }>();
const marker = ref<HTMLElement>();
let timer: ReturnType<typeof setTimeout> | undefined;
let observer: IntersectionObserver | undefined;
let visible = false;
let alive = false;
let pending = false;
let retryAt = 0;

function plan() {
  clearTimeout(timer);
  if (!alive || pending || !visible || document.hidden || !props.schedule.enabled) return;
  const due = Math.max(props.schedule.nextAt ?? 0, retryAt);
  timer = setTimeout(tick, Math.min(2_147_483_647, Math.max(0, due - Date.now())));
}
async function tick() {
  if (!alive || !visible || document.hidden || pending || !props.schedule.enabled) return;
  pending = true;
  try {
    const result = await props.host.callWorker<OutingTick>('partner.outing.tick', {});
    if (!alive) return;
    emit('update', result.schedule);
    if (result.startedId) emit('started');
  } catch (cause) {
    if (alive) emit('failure', '自动外出未能启动。' + (cause instanceof Error ? cause.message : String(cause)));
  } finally {
    pending = false;
    retryAt = Date.now() + 60_000;
    plan();
  }
}
onMounted(() => {
  alive = true;
  const page = marker.value?.parentElement;
  if (page) {
    observer = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; plan(); });
    observer.observe(page);
  }
  document.addEventListener('visibilitychange', plan);
});
watch(() => props.schedule.nextAt, plan);
onBeforeUnmount(() => {
  alive = false;
  clearTimeout(timer);
  observer?.disconnect();
  document.removeEventListener('visibilitychange', plan);
});
</script>

<template><span ref="marker" hidden aria-hidden="true"></span></template>
