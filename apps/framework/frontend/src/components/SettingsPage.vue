<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { Check, LoaderCircle, Monitor, Save } from '@lucide/vue';
import { errorMessage, type HostAdapter, type Settings, type Theme } from '../core/host';

const props = defineProps<{ host: HostAdapter; settings: Settings; settingsReady: boolean }>();
const emit = defineEmits<{ saved: [settings: Settings]; previewTheme: [theme: Theme] }>();
const draft = ref<Settings>({ ...props.settings });
const saving = ref(false); const failure = ref(''); const saved = ref(false);
const dirty = computed(() => JSON.stringify(draft.value) !== JSON.stringify(props.settings));
watch(() => props.settings, (value) => { draft.value = { ...value }; });
const themes = [
  { id: 'system', label: '跟随系统', description: '自动适配系统明暗' },
  { id: 'light', label: '浅色', description: '深蓝侧栏，清晰分区' },
  { id: 'dark', label: '深色', description: '蓝灰暗底，柔和对比' },
  { id: 'sakura', label: '樱花浅色', description: '暖灰留白，柔雾莓粉' },
  { id: 'ocean', label: '海盐蓝', description: '清透蓝灰，沉静专注' },
  { id: 'forest', label: '松石绿', description: '浅雾绿底，深青点缀' },
  { id: 'sand', label: '暖砂岩', description: '纸感暖白，棕铜细节' },
  { id: 'graphite', label: '石墨夜', description: '中性深灰，低彩度青绿' },
] satisfies Array<{ id: Theme; label: string; description: string }>;

function selectTheme(theme: Theme) { draft.value.theme = theme; saved.value = false; emit('previewTheme', theme); }
async function save() {
  if (!props.settingsReady) return;
  saving.value = true; failure.value = ''; saved.value = false;
  try { const settings = await props.host.saveSettings({ ...draft.value }); emit('saved', settings); saved.value = true; }
  catch (error) { failure.value = errorMessage(error); }
  finally { saving.value = false; }
}
</script>

<template>
  <div class="page-stack settings-page">
    <div class="page-intro settings-intro"><div><p class="eyebrow">PREFERENCES</p><h2>设置</h2><p>让工作台适应你的使用习惯。</p></div><button class="button button-primary" :disabled="!host.available || !settingsReady || saving || !dirty" @click="save"><LoaderCircle v-if="saving" class="spinning" :size="16" /><Save v-else :size="16" />{{ saving ? '正在保存…' : '保存设置' }}</button></div>
    <div v-if="failure" class="alert alert-error" role="alert"><strong>保存失败</strong><span>{{ failure }}</span></div><div v-if="saved" class="alert alert-success" role="status">设置已保存。</div>
    <section class="panel appearance-panel">
      <div class="panel-heading"><h3>外观</h3><span class="panel-caption">即时预览</span></div>
      <p class="panel-description">选择适合你的工作氛围。预览后点击「保存设置」，下次启动继续使用。</p>
      <div class="theme-options" role="group" aria-label="显示主题">
        <button v-for="theme in themes" :key="theme.id" class="theme-option" :class="{ selected: draft.theme === theme.id }" :aria-label="theme.label" :aria-pressed="draft.theme === theme.id" @click="selectTheme(theme.id)">
          <span class="theme-preview" :class="{ 'theme-preview-system': theme.id === 'system' }" :data-theme-preview="theme.id === 'system' ? 'light' : theme.id" aria-hidden="true">
            <template v-if="theme.id === 'system'"><Monitor :size="23" :stroke-width="1.6" /></template>
            <template v-else><span class="theme-preview-sidebar"><i></i><i></i><i></i></span><span class="theme-preview-content"><span class="theme-preview-panel"><i></i><i></i><b></b></span></span></template>
          </span>
          <span class="theme-option-title">{{ theme.label }}<Check v-if="draft.theme === theme.id" class="theme-check" :size="14" aria-hidden="true" /></span>
          <span class="theme-option-description">{{ theme.description }}</span>
        </button>
      </div>
    </section>
    <section class="panel"><div class="panel-heading"><h3>窗口行为</h3></div><label class="setting-row"><span><strong>关闭窗口时收起到托盘</strong><small>保留应用运行。需要结束应用时，使用托盘菜单中的「退出」。</small></span><input v-model="draft.closeToTray" class="switch" type="checkbox" :disabled="!host.available || !settingsReady || saving" @change="saved = false" /></label></section>
    <div class="settings-footer"><p v-if="!host.available" class="inline-note">预览中的主题调整仅作用于当前页面，桌面设置不会保存。</p><p v-else-if="!settingsReady" class="inline-note">尚未成功读取本机设置。请先重试读取，再进行保存。</p><p v-else class="inline-note">设置保存在本机应用数据目录。</p></div>
  </div>
</template>
