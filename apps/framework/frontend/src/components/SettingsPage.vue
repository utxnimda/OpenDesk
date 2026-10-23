<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { Check, LoaderCircle, Monitor, Save, Sparkles } from '@lucide/vue';
import { errorMessage, type HostAdapter, type Settings, type Theme, type UIStyle } from '../core/host';
import { UI_STYLE_PRESETS, uiStylePreset } from '../core/ui-styles';
import CompanionSettings from './CompanionSettings.vue';
import { clonePersonalization, type Personalization } from '../core/personalization';
import type { CompanionCatalog } from '../companions/contracts';

const props = defineProps<{ host: HostAdapter; settings: Settings; settingsReady: boolean; companions?: CompanionCatalog }>();
const emit = defineEmits<{ saved: [settings: Settings]; previewTheme: [theme: Theme]; previewStyle: [style: UIStyle]; previewPersonalization: [value: Personalization] }>();
function copySettings(value: Settings): Settings { return { ...value, personalization: clonePersonalization(value.personalization) }; }
const draft = ref<Settings>(copySettings(props.settings));
let previousSettings = copySettings(props.settings);
const saving = ref(false); const failure = ref(''); const saved = ref(false);
const locked = computed(() => saving.value);
const section = ref('style');
const sections = [{ id: 'style', label: '风格与配色' }, { id: 'companion', label: '宠物陪伴' }, { id: 'window', label: '窗口行为' }];
const dirty = computed(() => draft.value.theme !== props.settings.theme
  || draft.value.uiStyle !== props.settings.uiStyle
  || draft.value.closeToTray !== props.settings.closeToTray);
function mergeSettings(value: Settings) {
  const current = draft.value;
  const incoming = copySettings(value);
  // Preserve only locally edited appearance fields; pet state always comes from the host.
  if (current.theme !== previousSettings.theme) incoming.theme = current.theme;
  if (current.uiStyle !== previousSettings.uiStyle) incoming.uiStyle = current.uiStyle;
  if (current.closeToTray !== previousSettings.closeToTray) incoming.closeToTray = current.closeToTray;
  previousSettings = copySettings(value);
  draft.value = incoming;
}
watch(() => props.settings, mergeSettings, { deep: true });
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

function selectTheme(theme: Theme) { if (locked.value) return; draft.value.theme = theme; saved.value = false; emit('previewTheme', theme); }
function selectStyle(style: UIStyle) {
  if (locked.value) return;
  draft.value.uiStyle = style;
  saved.value = false;
  emit('previewStyle', style);
}
function useSuggestedPalette() { selectTheme(uiStylePreset(draft.value.uiStyle).theme); }
function personalize(value: Personalization) {
  if (saving.value) return;
  draft.value.personalization = clonePersonalization(value);
  saved.value = false;
  emit('previewPersonalization', clonePersonalization(value));
}
function companionSaved(settings: Settings) {
  mergeSettings(settings);
  // App receives authoritative settings, while its active preview keeps this form's edits.
  emit('saved', settings);
  emit('previewTheme', draft.value.theme);
  emit('previewStyle', draft.value.uiStyle);
}
async function save() {
  if (!props.settingsReady || locked.value) return;
  saving.value = true; failure.value = ''; saved.value = false;
  try { const settings = await props.host.saveAppearance(copySettings(draft.value)); emit('saved', settings); saved.value = true; }
  catch (error) { failure.value = errorMessage(error); }
  finally { saving.value = false; }
}
</script>

<template>
  <div class="page-stack settings-page">
    <div class="page-intro settings-intro"><div><p class="eyebrow">MAKE IT YOURS</p><h2>设置与个性化</h2><p>让界面与陪伴适合你的使用习惯。</p></div><button class="button button-primary" :disabled="!host.available || !settingsReady || locked || !dirty" @click="save"><LoaderCircle v-if="saving" class="spinning" :size="16" /><Save v-else :size="16" />{{ saving ? '正在保存…' : '保存设置' }}</button></div>
    <div v-if="failure" class="alert alert-error" role="alert"><strong>保存失败</strong><span>{{ failure }}</span></div><div v-if="saved" class="alert alert-success" role="status">设置已保存。</div>
    <nav class="settings-sections" aria-label="设置分类"><button v-for="item in sections" :key="item.id" :class="{ active: section === item.id }" :aria-pressed="section === item.id" @click="section = item.id">{{ item.label }}</button></nav>
    <section v-show="section === 'style'" class="panel ui-style-panel">
      <div class="panel-heading"><div class="panel-title"><Sparkles :size="17" /><h3>界面风格</h3></div><span class="panel-caption">{{ UI_STYLE_PRESETS.length }} 种布局 · 即时预览</span></div>
      <p class="panel-description">换一种组织工作台的方式。风格改变布局、层次与控件造型，配色可以单独选择。</p>
      <div class="style-options" role="group" aria-label="界面风格">
        <button v-for="preset in UI_STYLE_PRESETS" :key="preset.id" class="style-option" :class="{ selected: draft.uiStyle === preset.id }" :disabled="locked" :aria-label="`${preset.label}风格`" :aria-pressed="draft.uiStyle === preset.id" @click="selectStyle(preset.id)">
          <span class="style-preview" :data-style-preview="preset.id" aria-hidden="true">
            <span class="mini-rail"><i></i><i></i><i></i></span>
            <span class="mini-sidebar"><b></b><i></i><i></i><i></i><em></em></span>
            <span class="mini-main"><span class="mini-toolbar"><i></i><b></b></span><span class="mini-tabs"><i></i><i></i></span><span class="mini-hero"><i></i><i></i><b></b></span><span class="mini-cards"><i></i><i></i><i></i></span></span>
            <span class="mini-sparkle">✦</span>
          </span>
          <span class="style-option-heading"><strong>{{ preset.label }}</strong><Check v-if="draft.uiStyle === preset.id" :size="15" aria-hidden="true" /><small v-else>{{ preset.caption }}</small></span>
          <span class="style-option-copy">{{ preset.description }}</span>
        </button>
      </div>
      <div class="style-palette-suggestion"><span>也可以搭配「{{ uiStylePreset(draft.uiStyle).label }}」的建议配色。</span><button class="button button-secondary button-small" :disabled="locked" @click="useSuggestedPalette">使用建议配色</button></div>
      <p class="inline-note">新版功能包会同步风格。旧包仍可使用；若内部界面未变化，可在「功能管理」选择已安装的新版。</p>
    </section>
    <section v-show="section === 'style'" class="panel appearance-panel">
      <div class="panel-heading"><h3>配色</h3><span class="panel-caption">与界面风格独立选择</span></div>
      <p class="panel-description">保留你喜欢的颜色。预览后点击「保存设置」，下次启动继续使用。</p>
      <div class="theme-options" role="group" aria-label="显示主题">
        <button v-for="theme in themes" :key="theme.id" class="theme-option" :class="{ selected: draft.theme === theme.id }" :disabled="locked" :aria-label="theme.label" :aria-pressed="draft.theme === theme.id" @click="selectTheme(theme.id)">
          <span class="theme-preview" :class="{ 'theme-preview-system': theme.id === 'system' }" :data-theme-preview="theme.id === 'system' ? 'light' : theme.id" aria-hidden="true">
            <template v-if="theme.id === 'system'"><Monitor :size="23" :stroke-width="1.6" /></template>
            <template v-else><span class="theme-preview-sidebar"><i></i><i></i><i></i></span><span class="theme-preview-content"><span class="theme-preview-panel"><i></i><i></i><b></b></span></span></template>
          </span>
          <span class="theme-option-title">{{ theme.label }}<Check v-if="draft.theme === theme.id" class="theme-check" :size="14" aria-hidden="true" /></span>
          <span class="theme-option-description">{{ theme.description }}</span>
        </button>
      </div>
    </section>
    <CompanionSettings v-if="section === 'companion'" :host="host" :catalog="companions" :model-value="draft.personalization.pet" :disabled="locked || (host.available && !settingsReady)" @update:model-value="personalize({ ...draft.personalization, pet: $event })" @saved="companionSaved" />
    <section v-show="section === 'window'" class="panel"><div class="panel-heading"><h3>窗口行为</h3></div><label class="setting-row"><span><strong>关闭窗口时收起到托盘</strong><small>保留应用运行。需要结束应用时，使用托盘菜单中的「退出」。</small></span><input v-model="draft.closeToTray" class="switch" type="checkbox" :disabled="!host.available || !settingsReady || locked" @change="saved = false" /></label></section>
    <div class="settings-footer"><p v-if="!host.available" class="inline-note">风格与配色只在本次浏览器预览中生效，不会写入桌面设置。</p><p v-else-if="!settingsReady" class="inline-note">尚未成功读取本机设置。请先重试读取，再进行保存。</p><p v-else class="inline-note">未保存的外观在离开设置时恢复。设置保存在本机应用数据目录。</p></div>
  </div>
</template>

<style scoped>
.settings-page .ui-style-panel .style-options { grid-template-columns: repeat(auto-fit, minmax(min(180px, 100%), 1fr)); }
</style>
