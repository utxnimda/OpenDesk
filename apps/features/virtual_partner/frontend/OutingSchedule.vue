<script setup lang="ts">
import { computed, ref } from 'vue';
import type { LimitedHost } from '../../../framework/module-sdk/src/index';
import type { OutingScheduleState } from './outingClock';

const props = defineProps<{ host: LimitedHost; schedule: OutingScheduleState; interestsPending?: boolean }>();
const emit = defineEmits<{ update: [OutingScheduleState] }>();
const saving = ref(false);
const error = ref('');
const nextTime = computed(() => props.schedule.nextAt &&
  new Date(props.schedule.nextAt).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }));

async function change(event: Event) {
  const checkbox = event.currentTarget as HTMLInputElement;
  if (saving.value) return;
  if (checkbox.checked && props.interestsPending) {
    checkbox.checked = props.schedule.enabled;
    return;
  }
  saving.value = true;
  error.value = '';
  try {
    emit('update', await props.host.callWorker<OutingScheduleState>('partner.outing.schedule', { enabled: checkbox.checked }));
  } catch (cause) {
    checkbox.checked = props.schedule.enabled;
    error.value = '未能修改自动外出设置。' + (cause instanceof Error ? cause.message : String(cause));
  } finally { saving.value = false; }
}
</script>

<template>
  <section class="vp-outing-schedule" aria-label="自动外出设置">
    <label><input type="checkbox" :checked="schedule.enabled" :disabled="saving || (!schedule.enabled && interestsPending)" @change="change" />允许伙伴自动外出</label>
    <p v-if="interestsPending" class="vp-hint">{{ schedule.enabled ? '自动外出仍使用已保存的兴趣。' : '请先完成兴趣编辑并保存，再开启自动外出。' }}</p>
    <p class="vp-hint">虚拟伙伴页签可见时生效。每次至少间隔 {{ schedule.intervalMinutes }} 分钟，每天最多尝试 {{ schedule.dailyLimit }} 次。有模型连接时，伙伴可以用它拟一条本地留言草稿。</p>
    <p v-if="schedule.enabled" class="vp-hint">今天已尝试 {{ schedule.attemptsToday }}/{{ schedule.dailyLimit }} 次 · 下次最早 {{ nextTime }}</p>
    <p v-if="error" class="vp-alert" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.vp-outing-schedule { display: grid; gap: .35rem; }
.vp-outing-schedule label { display: flex; align-items: center; gap: .55rem; font-weight: 600; }
.vp-outing-schedule input { width: 1rem; height: 1rem; accent-color: var(--accent); }
.vp-outing-schedule p { margin: 0; }
</style>
