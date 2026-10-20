<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { Activity, ArrowUpRight, Blocks, Boxes, ChevronRight, CircleHelp, Grid2X2, History, Monitor, Package, Settings2 } from '@lucide/vue';
import WorkspacePage from './components/WorkspacePage.vue';
import DiagnosticsPage from './components/DiagnosticsPage.vue';
import SettingsPage from './components/SettingsPage.vue';
import PackageFrame from './components/PackageFrame.vue';
import PackagesPage from './components/PackagesPage.vue';
import VersionPage from './components/VersionPage.vue';
import { buildVersion } from './core/release';
import { DEFAULT_SETTINGS, errorMessage, host, type HostInfo, type Settings, type Theme } from './core/host';
import { EMPTY_CATALOG, canOpenPackage, packageKey, type PackageCatalog } from './core/packages';

const currentPage = ref('workspace');
const info = ref<HostInfo | null>(null);
const settings = ref<Settings>({ ...DEFAULT_SETTINGS });
const theme = ref<Theme>('system');
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
const systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
const selectedModule = computed(() => modules.value.find((module) => module.id === currentPage.value));
const pageLabel = computed(() => ({ workspace: '工作台', packages: '功能管理', diagnostics: '运行诊断', settings: '设置', version: '版本与更新' }[currentPage.value] ?? selectedModule.value?.label ?? '工作台'));
const builtinNavigation = [{ id: 'workspace', label: '工作台', icon: Grid2X2 }, { id: 'packages', label: '功能管理', icon: Package }, { id: 'diagnostics', label: '运行诊断', icon: Activity }, { id: 'version', label: '版本与更新', icon: History }];

function applyTheme() { resolvedTheme.value = theme.value === 'system' ? (systemTheme.matches ? 'dark' : 'light') : theme.value; document.documentElement.dataset.theme = resolvedTheme.value; }
watch(theme, applyTheme, { immediate: true });
systemTheme.addEventListener('change', applyTheme);
onUnmounted(() => { unmounted = true; systemTheme.removeEventListener('change', applyTheme); unsubscribe.forEach((dispose) => dispose()); });

async function refreshInfo() {
  if (!host.available) return;
  infoError.value = '';
  try { info.value = await host.getInfo(); } catch (error) { infoError.value = errorMessage(error); }
}

function updateSettings(value: Settings) { settings.value = value; theme.value = value.theme; }
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
  if (currentPage.value === 'settings') theme.value = settings.value.theme;
  currentPage.value = page;
  if (modules.value.some((module) => module.id === page)) visited.value.add(page);
  window.scrollTo({ top: 0, behavior: 'instant' });
}

async function loadDesktopState() {
  if (!host.available) return;
  initializing.value = true;
  infoError.value = ''; settingsError.value = '';
  const results = await Promise.allSettled([host.getInfo(), host.getSettings()]);
  if (results[0].status === 'fulfilled') info.value = results[0].value;
  else infoError.value = errorMessage(results[0].reason);
  if (results[1].status === 'fulfilled') { updateSettings(results[1].value); settingsReady.value = true; }
  else { settingsError.value = errorMessage(results[1].reason); settingsReady.value = false; }
  initializing.value = false;
}
onMounted(async () => {
  if (host.available) {
    try {
      const dispose = await host.subscribePackagesChanged(() => { void refreshCatalog(); });
      if (unmounted) dispose(); else unsubscribe.push(dispose);
    } catch (error) { packageError.value = `功能状态订阅失败：${errorMessage(error)}`; }
  }
  await loadDesktopState();
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
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand"><span class="brand-mark"><Boxes :size="23" :stroke-width="1.7" /></span><div><strong>OpenDesk</strong><span>LOCAL WORKSPACE</span></div></div>
      <div class="sidebar-rule"></div>
      <div class="navigation-label">工作空间</div>
      <nav class="navigation" aria-label="主导航"><button v-for="item in builtinNavigation" :key="item.id" class="nav-item" :class="{ active: currentPage === item.id }" :aria-current="currentPage === item.id ? 'page' : undefined" @click="navigate(item.id)"><component :is="item.icon" :size="18" :stroke-width="1.65" /><span>{{ item.label }}</span><ChevronRight v-if="currentPage === item.id" :size="14" class="nav-arrow" /></button></nav>
      <template v-if="modules.length"><div class="navigation-label module-nav-label">功能模块</div><nav class="navigation" aria-label="功能模块"><button v-for="module in modules" :key="module.id" class="nav-item" :class="{ active: currentPage === module.id }" :aria-current="currentPage === module.id ? 'page' : undefined" @click="navigate(module.id)"><Blocks :size="18" :stroke-width="1.65" /><span>{{ module.label }}</span></button></nav></template>
      <div class="sidebar-bottom"><div class="sidebar-note"><div class="note-icon"><CircleHelp :size="16" /></div><p>一个框架，<br />容纳你的下一个想法。</p><ArrowUpRight :size="15" /></div><button class="nav-item" :class="{ active: currentPage === 'settings' }" :aria-current="currentPage === 'settings' ? 'page' : undefined" @click="navigate('settings')"><Settings2 :size="18" :stroke-width="1.65" /><span>设置</span></button><button class="sidebar-version" :class="{ 'has-changes': buildVersion.git.dirty === true }" aria-label="查看应用版本与更新记录" @click="navigate('version')"><span class="version-dot"></span><span>{{ buildVersion.git.dirty === true ? '未提交构建' : '本地运行' }}</span><span class="version-number">{{ buildVersion.appVersion === 'unknown' ? '版本未知' : `v${buildVersion.appVersion}` }}</span></button></div>
    </aside>
    <div class="main-shell">
      <header class="topbar"><div class="breadcrumb"><span>OpenDesk</span><ChevronRight :size="13" /><strong>{{ pageLabel }}</strong></div><span class="environment-pill"><Monitor :size="13" />{{ host.available ? '桌面环境' : '浏览器预览' }}</span></header>
      <main class="main-content">
        <div v-if="!host.available" class="preview-banner"><Monitor :size="15" /><span>浏览器预览，桌面能力不可用</span><span class="preview-detail">可预览界面与主题</span></div>
        <div v-if="initializing" class="alert" role="status">正在读取桌面环境与设置…</div>
        <div v-if="hostError" class="alert alert-error" role="alert"><strong>桌面状态读取失败</strong><span>{{ hostError }}</span><button class="button button-quiet button-small" :disabled="initializing" @click="loadDesktopState">重新读取状态与设置</button></div>
        <div v-if="packageError" class="alert alert-error" role="alert"><strong>功能目录或实例操作失败</strong><span>{{ packageError }}</span><button class="button button-quiet button-small" :disabled="catalogLoading" @click="refreshCatalog">重新读取功能目录</button></div>
        <div v-if="runtimeError" class="alert alert-error" role="alert"><strong>功能运行记录</strong><span>{{ runtimeError }}</span><div class="button-row"><button class="button button-quiet button-small" @click="navigate('packages')">查看功能管理</button><button class="button button-quiet button-small" @click="runtimeError = ''">知道了</button></div></div>
        <WorkspacePage v-if="currentPage === 'workspace'" :module-count="modules.length" :desktop="host.available" @navigate="navigate" />
        <DiagnosticsPage v-else-if="currentPage === 'diagnostics'" :host="host" :info="info" @refresh="refreshInfo" />
        <SettingsPage v-else-if="currentPage === 'settings'" :host="host" :settings="settings" :settings-ready="settingsReady" @saved="updateSettings" @preview-theme="theme = $event" />
        <VersionPage v-else-if="currentPage === 'version'" :host-version="info?.appVersion" />
        <PackagesPage v-else-if="currentPage === 'packages'" :host="host" :catalog="catalog" :loading="catalogLoading" @changed="updateCatalog" @refresh="refreshCatalog" @open="navigate" />
        <PackageFrame v-for="module in mountedModules" v-show="currentPage === module.id" :key="packageKey(module)" :module="module" :host="host" :theme="resolvedTheme" @failure="runtimeError = $event" />
        <footer class="main-footer"><span>OPENDESK / PERSONAL TOOLKIT</span><span>清晰的边界 · 按需的能力</span></footer>
      </main>
    </div>
  </div>
</template>

<style scoped>
button.sidebar-version { width: 100%; text-align: left; background: transparent; border-right: 0; border-bottom: 0; border-left: 0; }
button.sidebar-version:hover { color: var(--sidebar-strong-hover, #dbe8fb); }
button.sidebar-version .version-number { color: var(--sidebar-version-number, #9baec8); }
button.sidebar-version.has-changes .version-dot { background: var(--sidebar-dirty, #dfad6f); }
button.sidebar-version.has-changes > span:nth-child(2) { color: var(--sidebar-dirty, #dfad6f); }
</style>
