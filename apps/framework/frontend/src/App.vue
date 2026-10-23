<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { Activity, ArrowUpRight, Blocks, Boxes, ChevronRight, CircleHelp, Grid2X2, History, Monitor, Package, Palette, Settings2, Sparkles } from '@lucide/vue';
import WorkspacePage from './components/WorkspacePage.vue';
import DiagnosticsPage from './components/DiagnosticsPage.vue';
import SettingsPage from './components/SettingsPage.vue';
import PackageFrame from './components/PackageFrame.vue';
import PackagesPage from './components/PackagesPage.vue';
import VersionPage from './components/VersionPage.vue';
import { buildVersion } from './core/release';
import { DEFAULT_SETTINGS, errorMessage, host as desktopHost, type HostAdapter, type HostInfo, type Settings, type Theme, type UIStyle } from './core/host';
import { EMPTY_CATALOG, canOpenPackage, packageKey, type PackageCatalog } from './core/packages';
import { uiStylePreset } from './core/ui-styles';
import CompanionOverlay from './companions/CompanionOverlay.vue';
import { canDisplayCompanion, type CompanionCatalog } from './companions/contracts';
import { clonePersonalization, type Personalization } from './core/personalization';
import { surfaceEffects } from './core/appearance-state';

const props = defineProps<{ adapter?: HostAdapter; companions?: CompanionCatalog }>();
const host = props.adapter ?? desktopHost;
const currentPage = ref('workspace');
const info = ref<HostInfo | null>(null);
const settings = ref<Settings>({ ...DEFAULT_SETTINGS, personalization: clonePersonalization(DEFAULT_SETTINGS.personalization) });
const theme = ref<Theme>('system');
const uiStyle = ref<UIStyle>(DEFAULT_SETTINGS.uiStyle);
const personalization = ref(clonePersonalization(DEFAULT_SETTINGS.personalization));
const companionError = ref('');
const companionRecall = ref(0);
const resolvedSurfaceEffects = computed(() => surfaceEffects(uiStyle.value));
let companionDesired = '';
let companionQueue = Promise.resolve();
const content = ref<HTMLElement | null>(null);
const activeStyle = computed(() => uiStylePreset(uiStyle.value));
const initializing = ref(host.available);
const infoError = ref(''); const settingsError = ref(''); const packageError = ref('');
const runtimeError = ref('');
const settingsReady = ref(false);
const hostError = computed(() => [infoError.value, settingsError.value].filter(Boolean).join('；'));
const catalog = ref<PackageCatalog>(EMPTY_CATALOG);
const catalogLoading = ref(false);
const visited = ref(new Set<string>());
const modules = computed(() => catalog.value.modules.filter(canOpenPackage));
const mountedModules = computed(() => modules.value.filter((module) => visited.value.has(module.id)));
const resolvedTheme = ref<Exclude<Theme, 'system'>>('light');
const unsubscribe: Array<() => void> = [];
let unmounted = false;
let settingsRequest = 0;
const systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
const selectedModule = computed(() => modules.value.find((module) => module.id === currentPage.value));
const pageLabel = computed(() => ({ workspace: '工作台', packages: '功能管理', diagnostics: '运行诊断', settings: '设置', version: '版本与更新' }[currentPage.value] ?? selectedModule.value?.label ?? '工作台'));
const builtinNavigation = [{ id: 'workspace', label: '工作台', icon: Grid2X2 }, { id: 'packages', label: '功能管理', icon: Package }, { id: 'diagnostics', label: '运行诊断', icon: Activity }, { id: 'version', label: '版本与更新', icon: History }];
const workspaceTabs = computed(() => {
  const tabs = [{ id: 'workspace', label: '工作台' }, ...mountedModules.value.map(({ id, label }) => ({ id, label }))];
  if (!tabs.some(({ id }) => id === currentPage.value)) tabs.push({ id: currentPage.value, label: pageLabel.value });
  return tabs;
});

function applyTheme() { resolvedTheme.value = theme.value === 'system' ? (systemTheme.matches ? 'dark' : 'light') : theme.value; document.documentElement.dataset.theme = resolvedTheme.value; }
watch(theme, applyTheme, { immediate: true });
watch(uiStyle, (value) => { document.documentElement.dataset.uiStyle = value; }, { immediate: true });
systemTheme.addEventListener('change', applyTheme);
onUnmounted(() => {
  unmounted = true;
  settingsRequest++;
  systemTheme.removeEventListener('change', applyTheme);
  unsubscribe.forEach((dispose) => dispose());
});

watch(resolvedSurfaceEffects, (effects) => {
  const root = document.documentElement;
  root.dataset.material = effects.material;
  root.dataset.motion = effects.motion;
  root.style.setProperty('--glass-opacity', effects.transparency + '%');
}, { immediate: true });

function reconcileCompanion(value: Personalization['pet']) {
  if (!host.available) return;
  const shouldShow = canDisplayCompanion(props.companions, value, 'desktop');
  const desired = JSON.stringify({ pet: value, shouldShow });
  if (desired === companionDesired) return;
  companionDesired = desired;
  companionQueue = companionQueue.then(async () => {
    if (unmounted || desired !== companionDesired) return;
    companionError.value = '';
    if (shouldShow) await host.showCompanion();
    else await host.hideCompanion();
  }).catch((error) => {
    if (!unmounted && desired === companionDesired) { companionDesired = ''; companionError.value = '宠物窗口操作失败：' + errorMessage(error); }
  });
}
watch(() => props.companions, () => { reconcileCompanion(settings.value.personalization.pet); }, { deep: true });

async function refreshInfo() {
  if (!host.available) return;
  infoError.value = '';
  try { info.value = await host.getInfo(); } catch (error) { infoError.value = errorMessage(error); }
}

function updateSettings(value: Settings) {
  // Native save broadcasts may arrive after its response. An identical saved
  // value must not cancel a new preview made while that notification was in flight.
  if (JSON.stringify(settings.value) !== JSON.stringify(value)) {
    const previous = settings.value;
    const preserveTheme = currentPage.value === 'settings' && theme.value !== previous.theme && value.theme === previous.theme;
    const preserveStyle = currentPage.value === 'settings' && uiStyle.value !== previous.uiStyle && value.uiStyle === previous.uiStyle;
    settings.value = { ...value, personalization: clonePersonalization(value.personalization) };
    if (!preserveTheme) theme.value = value.theme;
    if (!preserveStyle) uiStyle.value = value.uiStyle;
    personalization.value = clonePersonalization(value.personalization);
  }
  // Native preference commands own companion window effects. Replaying them
  // from an asynchronous settings mirror can hide a newer menu or window.
}
function previewTheme(value: Theme) {
  theme.value = value;
  if (!host.available) settings.value = { ...settings.value, theme: value };
}
function previewStyle(value: UIStyle) {
  uiStyle.value = value;
  if (!host.available) settings.value = { ...settings.value, uiStyle: value };
}
function previewPersonalization(value: Personalization) {
  personalization.value = clonePersonalization(value);
  if (!host.available) {
    settings.value = { ...settings.value, personalization: clonePersonalization(value) };
    if (value.pet.enabled) companionRecall.value++;
  }
}
function updateCatalog(value: PackageCatalog) {
  if (value.hostApi !== 1) { packageError.value = '功能目录的宿主接口版本不匹配。'; return; }
  const available = new Set(value.modules.filter(canOpenPackage).map((module) => module.id));
  for (const id of visited.value) if (!available.has(id)) visited.value.delete(id);
  catalog.value = value;
  if (!['workspace', 'packages', 'diagnostics', 'settings', 'version'].includes(currentPage.value) && !modules.value.some((module) => module.id === currentPage.value)) currentPage.value = 'packages';
}
async function refreshCatalog() {
  if (!host.available) return;
  catalogLoading.value = true; packageError.value = '';
  try { updateCatalog(await host.listPackages()); }
  catch (error) { packageError.value = errorMessage(error); }
  finally { catalogLoading.value = false; }
}
function navigate(page: string) {
  if (currentPage.value === 'settings' && page !== 'settings' && host.available) {
    theme.value = settings.value.theme;
    uiStyle.value = settings.value.uiStyle;
    personalization.value = clonePersonalization(settings.value.personalization);
  }
  currentPage.value = page;
  if (modules.value.some((module) => module.id === page)) visited.value.add(page);
  void nextTick(() => { content.value?.scrollTo({ top: 0, behavior: 'instant' }); });
}

async function loadDesktopState() {
  if (!host.available) return;
  const request = ++settingsRequest;
  initializing.value = true;
  infoError.value = ''; settingsError.value = '';
  const results = await Promise.allSettled([host.getInfo(), host.getSettings()]);
  if (results[0].status === 'fulfilled') info.value = results[0].value;
  else infoError.value = errorMessage(results[0].reason);
  if (!unmounted && request === settingsRequest) {
    if (results[1].status === 'fulfilled') { updateSettings(results[1].value); settingsReady.value = true; reconcileCompanion(results[1].value.personalization.pet); }
    else { settingsError.value = errorMessage(results[1].reason); settingsReady.value = false; }
  }
  initializing.value = false;
}
onMounted(async () => {
  if (host.available) {
    try {
      const dispose = await host.subscribeCompanionRecall(() => { companionRecall.value++; });
      if (unmounted) dispose(); else unsubscribe.push(dispose);
    } catch (error) { companionError.value = '宠物召回订阅失败：' + errorMessage(error); }
    try {
      const dispose = await host.subscribePackagesChanged(() => { void refreshCatalog(); });
      if (unmounted) dispose(); else unsubscribe.push(dispose);
    } catch (error) { packageError.value = `功能状态订阅失败：${errorMessage(error)}`; }
  }
  await loadDesktopState();
  if (host.available) {
    try {
      const dispose = await host.subscribeSettingsChanged(() => {
        const request = ++settingsRequest;
        void host.getSettings().then((value) => { if (!unmounted && request === settingsRequest) updateSettings(value); }).catch((error) => { if (!unmounted && request === settingsRequest) settingsError.value = errorMessage(error); });
      });
      if (unmounted) dispose(); else unsubscribe.push(dispose);
    } catch (error) { settingsError.value = '设置订阅失败：' + errorMessage(error); }
  }
  await refreshCatalog();
  if (info.value?.moduleSmoke) {
    try {
      const dispose = await host.subscribeSmokeNavigate((id) => { void refreshCatalog().then(() => { if (modules.value.some((module) => module.id === id)) navigate(id); }); });
      if (unmounted) dispose(); else unsubscribe.push(dispose);
    } catch (error) { packageError.value = `模块验证订阅失败：${errorMessage(error)}`; }
    if (modules.value.some((module) => module.id === 'test-lab')) navigate('test-lab');
  }
  if (host.available) {
    try { await host.frontendReady(); }
    catch (error) { infoError.value = `前端就绪通知失败：${errorMessage(error)}`; }
  }
});
</script>

<template>
  <div class="app-shell" :data-ui-style="uiStyle">
    <header v-if="uiStyle === 'glass'" class="glass-header">
      <button class="glass-brand" aria-label="返回工作台" @click="navigate('workspace')"><Boxes :size="20" :stroke-width="1.5" /><strong>OpenDesk</strong></button>
      <nav class="glass-tabs" aria-label="主导航"><button v-for="item in builtinNavigation" :key="item.id" :class="{ active: currentPage === item.id || (item.id === 'workspace' && Boolean(selectedModule)) }" :aria-current="currentPage === item.id ? 'page' : undefined" @click="navigate(item.id)">{{ item.label }}</button><button :class="{ active: currentPage === 'settings' }" :aria-current="currentPage === 'settings' ? 'page' : undefined" @click="navigate('settings')">设置</button></nav>
      <button class="glass-appearance" aria-label="切换界面风格" @click="navigate('settings')"><Palette :size="16" /><span>{{ activeStyle.label }}</span></button>
    </header>
    <nav v-if="uiStyle === 'cursor'" class="activity-rail" aria-label="快捷导航">
      <span class="activity-brand" aria-hidden="true"><Boxes :size="23" /></span>
      <button v-for="item in builtinNavigation" :key="item.id" :aria-label="item.label" :title="item.label" :aria-current="currentPage === item.id ? 'page' : undefined" :class="{ active: currentPage === item.id }" @click="navigate(item.id)"><component :is="item.icon" :size="21" :stroke-width="1.6" /></button>
      <button class="activity-settings" aria-label="设置" title="设置" :class="{ active: currentPage === 'settings' }" @click="navigate('settings')"><Settings2 :size="21" /></button>
    </nav>
    <aside class="sidebar">
      <div class="brand"><span class="brand-mark"><Boxes :size="23" :stroke-width="1.7" /></span><div><strong>OpenDesk</strong><span>{{ uiStyle === 'mint' ? 'MINT WORKSPACE' : 'LOCAL WORKSPACE' }}</span></div></div>
      <div class="sidebar-rule"></div>
      <div class="sidebar-scroll">
      <div class="navigation-label">{{ uiStyle === 'cursor' ? '工作区 · EXPLORER' : uiStyle === 'glass' ? '工作区导航' : '工作空间' }}</div>
      <nav v-if="uiStyle === 'glass'" class="navigation" aria-label="工作区导航"><button class="nav-item" :class="{ active: currentPage === 'workspace' }" :aria-current="currentPage === 'workspace' ? 'page' : undefined" @click="navigate('workspace')"><Grid2X2 :size="17" :stroke-width="1.5" /><span>总览</span></button></nav>
      <nav v-else class="navigation" aria-label="主导航"><button v-for="item in builtinNavigation" :key="item.id" class="nav-item" :class="{ active: currentPage === item.id }" :aria-current="currentPage === item.id ? 'page' : undefined" @click="navigate(item.id)"><component :is="item.icon" :size="18" :stroke-width="1.65" /><span>{{ item.label }}</span><ChevronRight v-if="currentPage === item.id" :size="14" class="nav-arrow" /></button></nav>
      <template v-if="modules.length"><div class="navigation-label module-nav-label">功能模块</div><nav class="navigation" aria-label="功能模块"><button v-for="module in modules" :key="module.id" class="nav-item" :class="{ active: currentPage === module.id }" :aria-current="currentPage === module.id ? 'page' : undefined" @click="navigate(module.id)"><Blocks :size="18" :stroke-width="1.65" /><span>{{ module.label }}</span></button></nav></template>
      </div>
      <div class="sidebar-bottom"><div class="sidebar-note"><div class="note-icon"><CircleHelp :size="16" /></div><p>一个框架，<br />容纳你的下一个想法。</p><ArrowUpRight :size="15" /></div><button class="nav-item" :class="{ active: currentPage === 'settings' }" :aria-current="currentPage === 'settings' ? 'page' : undefined" @click="navigate('settings')"><Settings2 :size="18" :stroke-width="1.65" /><span>设置</span></button><button class="sidebar-version" :class="{ 'has-changes': buildVersion.git.dirty === true }" aria-label="查看应用版本与更新记录" @click="navigate('version')"><span class="version-dot"></span><span>{{ buildVersion.git.dirty === true ? '未提交构建' : '本地运行' }}</span><span class="version-number">{{ buildVersion.appVersion === 'unknown' ? '版本未知' : `v${buildVersion.appVersion}` }}</span></button></div>
    </aside>
    <div class="main-shell">
      <header class="topbar">
        <div class="breadcrumb"><Sparkles v-if="uiStyle === 'anime'" :size="17" /><span>OpenDesk</span><ChevronRight :size="13" /><strong>{{ pageLabel }}</strong><span v-if="selectedModule" class="current-module-version">v{{ selectedModule.selectedVersion }}</span></div>
        <div class="topbar-actions"><button class="style-switch" aria-label="切换界面风格" @click="navigate('settings')"><Palette :size="15" /><span>{{ activeStyle.label }}</span></button><span class="environment-pill"><Monitor :size="13" />{{ host.available ? '桌面环境' : '浏览器预览' }}</span></div>
      </header>
      <nav v-if="uiStyle === 'cursor'" class="workbench-tabs" aria-label="工作区标签"><button v-for="tab in workspaceTabs" :key="tab.id" :class="{ active: currentPage === tab.id }" :aria-current="currentPage === tab.id ? 'page' : undefined" @click="navigate(tab.id)"><Grid2X2 v-if="tab.id === 'workspace'" :size="13" /><Blocks v-else :size="13" />{{ tab.label }}</button></nav>
      <main ref="content" class="main-content" :class="{ 'is-module-view': Boolean(selectedModule) }" :aria-label="pageLabel">
        <div v-if="companionError" class="alert alert-error" role="alert"><strong>宠物状态</strong><span>{{ companionError }}</span><button class="button button-quiet button-small" @click="navigate('settings')">打开设置</button></div>
        <div v-if="!host.available" class="preview-banner"><Monitor :size="15" /><span>浏览器预览，桌面能力不可用</span><span class="preview-detail">可预览界面与主题</span></div>
        <div v-if="initializing" class="alert" role="status">正在读取桌面环境与设置…</div>
        <div v-if="hostError" class="alert alert-error" role="alert"><strong>桌面状态读取失败</strong><span>{{ hostError }}</span><button class="button button-quiet button-small" :disabled="initializing" @click="loadDesktopState">重新读取状态与设置</button></div>
        <div v-if="packageError" class="alert alert-error" role="alert"><strong>功能目录或实例操作失败</strong><span>{{ packageError }}</span><button class="button button-quiet button-small" :disabled="catalogLoading" @click="refreshCatalog">重新读取功能目录</button></div>
        <div v-if="runtimeError" class="alert alert-error" role="alert"><strong>功能运行记录</strong><span>{{ runtimeError }}</span><div class="button-row"><button class="button button-quiet button-small" @click="navigate('packages')">查看功能管理</button><button class="button button-quiet button-small" @click="runtimeError = ''">知道了</button></div></div>
        <WorkspacePage v-if="currentPage === 'workspace'" :modules="modules" :desktop="host.available" :ui-style="uiStyle" :loading="catalogLoading" @navigate="navigate" />
        <DiagnosticsPage v-else-if="currentPage === 'diagnostics'" :host="host" :info="info" @refresh="refreshInfo" />
        <SettingsPage v-else-if="currentPage === 'settings'" :host="host" :settings="settings" :settings-ready="settingsReady" :companions="companions" @saved="updateSettings" @preview-theme="previewTheme" @preview-style="previewStyle" @preview-personalization="previewPersonalization" />
        <VersionPage v-else-if="currentPage === 'version'" :host-version="info?.appVersion" />
        <PackagesPage v-else-if="currentPage === 'packages'" :host="host" :catalog="catalog" :loading="catalogLoading" @changed="updateCatalog" @refresh="refreshCatalog" @open="navigate" />
        <PackageFrame v-for="module in mountedModules" v-show="currentPage === module.id" :key="packageKey(module)" :module="module" :host="host" :theme="resolvedTheme" :ui-style="uiStyle" :surface-effects="resolvedSurfaceEffects" @failure="runtimeError = $event" />
        <footer v-if="!selectedModule" class="main-footer"><span>OPENDESK / PERSONAL TOOLKIT</span><span>清晰的边界 · 按需的能力</span></footer>
      </main>
      <footer v-if="uiStyle === 'cursor' || uiStyle === 'glass'" class="workbench-statusbar"><span><span class="version-dot"></span>{{ host.available ? '桌面工作区' : '界面预览' }}</span><span>{{ pageLabel }}</span><span>OpenDesk v{{ buildVersion.appVersion }}</span></footer>
    </div>
  </div>
  <CompanionOverlay v-if="companions" :host="host" :catalog="companions" :settings="personalization.pet" :recall-key="companionRecall" @saved="updateSettings" @preview="previewPersonalization({ pet: $event })" />
</template>

<style scoped>
button.sidebar-version { width: 100%; text-align: left; background: transparent; border-right: 0; border-bottom: 0; border-left: 0; }
button.sidebar-version:hover { color: var(--sidebar-strong-hover, #dbe8fb); }
button.sidebar-version .version-number { color: var(--sidebar-version-number, #9baec8); }
button.sidebar-version.has-changes .version-dot { background: var(--sidebar-dirty, #dfad6f); }
button.sidebar-version.has-changes > span:nth-child(2) { color: var(--sidebar-dirty, #dfad6f); }
</style>
