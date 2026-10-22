<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { ExternalLink, Heart, PawPrint } from '@lucide/vue';
import { errorMessage, type HostAdapter } from '../core/host';
import type { PetSettings } from '../core/personalization';
import { canDisplayCompanion, companionExtraActions, findCompanion, type CompanionCatalog } from '../companions/contracts';

const props = defineProps<{ host: HostAdapter; catalog?: CompanionCatalog; modelValue: PetSettings; disabled: boolean; savedMode: PetSettings }>();
const emit = defineEmits<{ 'update:modelValue': [value: PetSettings] }>();
const error = ref(''); const opening = ref(false); const windowVisible = ref(false);
let disposed = false; let visibilityRevision = 0; let unsubscribe: (() => void) | undefined;
const selected = computed(() => findCompanion(props.catalog, props.modelValue.modelId));
const canShow = computed(() => canDisplayCompanion(props.catalog, props.savedMode, 'desktop'));
const animationDescription = computed(() => {
  const labels = selected.value ? companionExtraActions(selected.value).map((action) => ({ walk: '走动', wave: '挥手', sleep: '休息' })[action]) : [];
  return ['循环动画', ...labels].join('、') + '；暂停时使用静态形象。';
});
function update<K extends keyof PetSettings>(key: K, value: PetSettings[K]) { if (!props.disabled) emit('update:modelValue', { ...props.modelValue, [key]: value }); }
async function show() {
  if (!canShow.value || !props.host.available || opening.value || props.disabled) return;
  opening.value = true; error.value = '';
  try { await props.host.showCompanion(); windowVisible.value = (await props.host.getCompanionState()).visible; }
  catch (failure) { error.value = errorMessage(failure); }
  finally { opening.value = false; }
}
onMounted(async () => {
  if (!props.host.available || !props.catalog?.models.length) return;
  try {
    const release = await props.host.subscribeCompanionVisibilityChanged((visible) => { visibilityRevision++; windowVisible.value = visible; });
    if (disposed) { release(); return; }
    unsubscribe = release;
    const revision = visibilityRevision;
    const state = await props.host.getCompanionState();
    if (!disposed && revision === visibilityRevision) windowVisible.value = state.visible;
  } catch (failure) { if (!disposed) error.value = errorMessage(failure); }
});
onUnmounted(() => { disposed = true; unsubscribe?.(); });
</script>

<template>
  <section class="panel companion-settings">
    <div class="panel-heading"><div class="panel-title"><PawPrint :size="18" /><h3>桌面陪伴</h3></div><span class="panel-caption">离线 · 由 OpenDesk 管理</span></div>
    <div v-if="!catalog?.models.length" class="companion-empty-state" role="status"><PawPrint :size="28" :stroke-width="1.5" /><strong>暂无可用宠物资源，等待添加已授权资源</strong><p>添加可用资源后，可选择桌面悬浮或应用内陪伴。</p></div>
    <template v-else>
    <div class="companion-introduction"><div class="companion-model-preview"><img v-if="selected" :src="selected.assets.still" :alt="selected.label" /><Heart v-else :size="32" /></div><div><p class="eyebrow">YOUR LITTLE COMPANION</p><h3>{{ selected?.label || '尚未提供宠物资源' }}</h3><p>{{ selected?.description || '当前组合中没有可用的宠物模型。' }}</p><label class="setting-row compact"><span><strong>启用宠物</strong></span><input aria-label="启用宠物" class="switch" type="checkbox" :checked="modelValue.enabled" :disabled="disabled || !selected" @change="update('enabled', ($event.target as HTMLInputElement).checked)" /></label></div></div>
    <div class="companion-modes" role="group" aria-label="宠物显示模式"><button :aria-pressed="modelValue.mode === 'desktop'" :class="{ selected: modelValue.mode === 'desktop' }" :disabled="disabled" @click="update('mode', 'desktop')"><strong>桌面悬浮</strong><small>独立透明窗口，跟随主程序退出</small></button><button :aria-pressed="modelValue.mode === 'in-app'" :class="{ selected: modelValue.mode === 'in-app' }" :disabled="disabled" @click="update('mode', 'in-app')"><strong>应用内陪伴</strong><small>停留在工作台里，可拖动位置</small></button></div>
    <div class="personalization-fields"><label><span>宠物角色</span><select aria-label="宠物角色" :value="modelValue.modelId ?? catalog?.defaultId" :disabled="disabled || !catalog?.models.length" @change="update('modelId', ($event.target as HTMLSelectElement).value)"><option v-for="model in catalog?.models ?? []" :key="model.id" :value="model.id">{{ model.label }}</option></select></label></div>
    <div class="personalization-sliders single"><label><span>角色大小 <output>{{ modelValue.size }} px</output></span><input aria-label="角色大小" type="range" min="96" max="256" step="8" :value="modelValue.size" :disabled="disabled" @input="update('size', Number(($event.target as HTMLInputElement).value))" /></label></div>
    <label class="setting-row personalization-toggle"><span><strong>播放角色动画</strong><small>{{ animationDescription }}</small></span><input aria-label="播放角色动画" class="switch" type="checkbox" :checked="modelValue.animated" :disabled="disabled" @change="update('animated', ($event.target as HTMLInputElement).checked)" /></label>
    <label v-if="modelValue.mode === 'desktop'" class="setting-row personalization-toggle"><span><strong>保持在其他窗口上方</strong><small>关闭后可被其他程序覆盖。</small></span><input aria-label="宠物保持置顶" class="switch" type="checkbox" :checked="modelValue.alwaysOnTop" :disabled="disabled" @change="update('alwaysOnTop', ($event.target as HTMLInputElement).checked)" /></label>
    <p v-if="host.available && canShow" class="inline-note" role="status">{{ windowVisible ? '桌面宠物窗口已显示。' : '桌面宠物目前已收起。' }}</p>
    <div v-if="error" class="alert alert-error" role="alert">{{ error }}</div>
    <div class="button-row companion-window-actions"><button class="button button-secondary" :disabled="!host.available || opening || !canShow || disabled" @click="show"><ExternalLink :size="15" />{{ opening ? '正在显示…' : '显示桌面宠物' }}</button><span class="inline-note">{{ !host.available ? '浏览器可以预览应用内模式，桌面悬浮需在桌面应用中使用。' : '保存后应用模式和大小。宠物被暂时收起时，可以在这里重新显示。' }}</span></div>
    <p class="inline-note">拖动角色可移动。应用内模式只在宠物和控件区域接收操作，位置在本次会话保留；桌面模式使用独立透明窗口，其空白矩形目前也会接收鼠标，可拖到不影响操作的位置。主窗口收起到托盘时继续陪伴，退出 OpenDesk 时一起退出。</p>
    </template>
  </section>
</template>
