<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue';
import { Check, LoaderCircle, PawPrint, Power } from '@lucide/vue';
import { errorMessage, type HostAdapter, type Settings } from '../core/host';
import type { PetSettings } from '../core/personalization';
import { findCompanion, type CompanionCatalog } from '../companions/contracts';

const props = defineProps<{ host: HostAdapter; catalog?: CompanionCatalog; modelValue: PetSettings; disabled: boolean }>();
const emit = defineEmits<{ 'update:modelValue': [value: PetSettings]; saved: [settings: Settings] }>();
const failure = ref('');
const notice = ref('');
const selecting = ref(false);
const pendingId = ref<string | null>(null);
const locked = computed(() => props.disabled || selecting.value);
const selected = computed(() => props.modelValue.enabled ? findCompanion(props.catalog, props.modelValue.modelId) : undefined);
const unavailable = computed(() => props.modelValue.enabled && !selected.value);
let disposed = false;
onUnmounted(() => { disposed = true; });

async function select(modelId: string | null) {
  if (locked.value) return;
  failure.value = ''; notice.value = '';
  if (modelId !== null && !props.catalog?.models.some((model) => model.id === modelId)) {
    failure.value = '所选宠物资源不可用，请重新选择。';
    return;
  }
  if (!props.host.available) {
    emit('update:modelValue', { ...props.modelValue, enabled: modelId !== null, modelId, mode: 'in-app' });
    notice.value = modelId === null ? '已关闭本次预览中的宠物。' : '已在当前浏览器会话预览，未写入桌面设置。';
    return;
  }
  selecting.value = true; pendingId.value = modelId;
  try {
    // Selecting the same model intentionally reaches the host so it can recall the pet.
    const settings = await props.host.selectCompanion(modelId);
    if (disposed) return;
    emit('saved', settings);
    notice.value = modelId === null ? '已停用宠物。' : '宠物选择已立即生效。';
  } catch (error) {
    if (!disposed) failure.value = errorMessage(error);
  } finally {
    if (!disposed) selecting.value = false;
  }
}
</script>

<template>
  <section class="panel companion-settings" :aria-busy="selecting">
    <div class="panel-heading"><div class="panel-title"><PawPrint :size="18" /><h3>选择宠物</h3></div><span class="panel-caption">{{ host.available ? '选择后立即生效' : '仅本次浏览器预览' }}</span></div>
    <p class="panel-description">选一个伙伴陪伴你。再次点击当前角色可以召回宠物。</p>
    <div v-if="failure" class="alert alert-error" role="alert"><strong>宠物选择失败</strong><span>{{ failure }}</span></div>
    <p v-if="notice" class="inline-note companion-selection-notice" role="status">{{ notice }}</p>
    <p v-if="unavailable" class="inline-note">当前选择的宠物资源不可用，请选择其他角色或停用宠物。</p>
    <div class="companion-options" role="group" aria-label="宠物角色选择">
      <button class="companion-choice companion-choice-none" :class="{ selected: !modelValue.enabled }" :aria-pressed="!modelValue.enabled" :disabled="locked" @click="select(null)">
        <span class="companion-choice-avatar" aria-hidden="true"><Power :size="34" :stroke-width="1.4" /></span>
        <span class="companion-choice-heading"><strong>不启用宠物</strong><LoaderCircle v-if="selecting && pendingId === null" class="spinning" :size="16" aria-hidden="true" /><Check v-else-if="!modelValue.enabled" :size="16" aria-hidden="true" /></span>
        <span class="companion-choice-description">关闭宠物显示，保留其他使用设置。</span>
      </button>
      <button v-for="model in catalog?.models ?? []" :key="model.id" class="companion-choice" :class="{ selected: selected?.id === model.id }" :aria-pressed="selected?.id === model.id" :disabled="locked" @click="select(model.id)">
        <span class="companion-choice-avatar"><img :src="model.assets.still" :alt="model.label" /></span>
        <span class="companion-choice-heading"><strong>{{ model.label }}</strong><LoaderCircle v-if="selecting && pendingId === model.id" class="spinning" :size="16" aria-hidden="true" /><Check v-else-if="selected?.id === model.id" :size="16" aria-hidden="true" /></span>
        <span class="companion-choice-description">{{ model.description }}</span>
      </button>
    </div>
    <div v-if="!catalog?.models.length" class="companion-empty-state" role="status"><PawPrint :size="28" :stroke-width="1.5" /><strong>暂无可用宠物资源，等待添加已授权资源</strong></div>
    <p class="inline-note companion-menu-help">右键宠物调整显示位置、动作、大小、置顶和暂停等；收起后可从托盘显示宠物。</p>
    <p v-if="!host.available" class="inline-note">浏览器只预览应用内宠物，不会写入桌面设置，也不提供系统托盘。</p>
  </section>
</template>

<style scoped>
.companion-options { display: grid; grid-template-columns: repeat(auto-fit,minmax(190px,1fr)); gap: 14px; margin-top: 18px; }
.companion-choice { display: flex; flex-direction: column; gap: 12px; min-width: 0; padding: 16px; text-align: left; border: 1px solid var(--border); border-radius: 14px; color: var(--text); background: var(--surface-soft); }
.companion-choice:hover:not(:disabled) { border-color: var(--accent); }
.companion-choice.selected { border-color: var(--accent); background: var(--accent-soft); box-shadow: 0 0 0 1px color-mix(in srgb,var(--accent) 28%,transparent); }
.companion-choice-avatar { display: grid; place-items: center; width: 100%; height: 136px; border: 1px solid var(--border); border-radius: 10px; color: var(--muted); background: var(--surface); }
.companion-choice-avatar img { display: block; width: 100%; height: 100%; min-height: 0; padding: 7px; object-fit: contain; }
.companion-choice-heading { display: flex; align-items: center; justify-content: space-between; gap: 9px; width: 100%; }
.companion-choice-heading strong { font-size: 14px; font-weight: 600; overflow-wrap: anywhere; }
.companion-choice-heading > svg { flex-shrink: 0; color: var(--accent); }
.companion-choice-description { color: var(--muted); font-size: 11px; line-height: 1.8; overflow-wrap: anywhere; }
.companion-menu-help { margin-top: 20px; }
.companion-selection-notice { margin-top: 12px; }
:root[data-ui-style="glass"] .companion-choice,
:root[data-ui-style="glass"] .companion-choice-avatar { border-radius: 2px; box-shadow: none; border-color: var(--glass-line); background: var(--glass-fill); }
:root[data-ui-style="glass"] .companion-choice.selected { border-color: var(--accent); background: var(--accent-soft); box-shadow: none; }
:root[data-ui-style="mint"] .companion-options { grid-template-columns: repeat(auto-fit,minmax(min(100%,240px),1fr)); gap: 16px; }
:root[data-ui-style="mint"] .companion-choice { display: grid; grid-template-columns: 76px minmax(0,1fr); gap: 5px 14px; align-content: center; padding: 17px; border-radius: 22px; background: var(--surface-soft); }
:root[data-ui-style="mint"] .companion-choice-avatar { grid-row: 1 / 3; width: 76px; height: 86px; border: 0; border-radius: 16px; background: transparent; }
:root[data-ui-style="mint"] .companion-choice-heading { align-self: end; }
:root[data-ui-style="mint"] .companion-choice-description { align-self: start; }
:root[data-ui-style="mint"] .companion-choice.selected { border-color: var(--mint-toggle); background: var(--accent-soft); box-shadow: 0 0 0 3px color-mix(in srgb,var(--mint-toggle) 12%,transparent); }
</style>
