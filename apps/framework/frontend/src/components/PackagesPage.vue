<script setup lang="ts">
import { computed, ref } from 'vue';
import { Archive, Check, ChevronDown, Download, Layers, LoaderCircle, Package, RefreshCw } from '@lucide/vue';
import { errorMessage, type HostAdapter } from '../core/host';
import { canOpenPackage, canSelectPackage, safeRepositoryLink, selectedPackage, type InstalledModule, type PackageCatalog, type PackageVersion } from '../core/packages';

const props = defineProps<{ host: HostAdapter; catalog: PackageCatalog; loading: boolean }>();
const emit = defineEmits<{ changed: [catalog: PackageCatalog]; refresh: []; open: [id: string] }>();
const pending = ref(''); const failure = ref(''); const success = ref('');
const imports = computed(() => [...props.catalog.imports].reverse());
const versionsOpen = ref<Record<string, boolean>>({});
const statusLabel = (status: string) => ({ installed: '已安装', duplicate: '重复包，已保留', rejected: '校验未通过' }[status] ?? status);
const stateLabel = (state: string) => ({ loading: '正在加载', running: '运行中', disabled: '已停用', stopped: '未运行', unselected: '未选择版本', switching: '正在切换', busy: '处理中', failed: '启动失败' }[state] ?? state);
const importDate = (milliseconds: number) => Number.isFinite(milliseconds) ? new Date(milliseconds).toLocaleString(undefined, { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }) : '时间未知';
async function perform(key: string, action: () => Promise<PackageCatalog>, message: string) {
  if (pending.value) return;
  pending.value = key; failure.value = ''; success.value = '';
  try { const catalog = await action(); emit('changed', catalog); success.value = message; }
  catch (error) { failure.value = errorMessage(error); }
  finally { pending.value = ''; }
}
async function importFile() {
  await perform('import', () => props.host.importPackage(), '已读取功能包目录。导入结果和原文件保留位置见下方记录。');
}
async function toggle(module: InstalledModule) {
  await perform(module.id, () => props.host.setPackageEnabled(module.id, !module.enabled), module.enabled ? `${module.label}已停用；文件和版本仍保留。` : `${module.label}已启用。`);
}
async function select(module: InstalledModule, version: PackageVersion) {
  if (!canSelectPackage(module, version)) return;
  await perform(module.id, () => props.host.selectPackage(module.id, version.version, version.digest), `${module.label}已选择 v${version.version}。其他功能保持原版本。`);
}
</script>

<template>
  <div class="page-stack packages-page">
    <div class="page-intro packages-intro"><div><p class="eyebrow">MODULE LIBRARY</p><h2>功能管理</h2><p>每个功能独立启用、选择版本。导入新包后，原有版本仍然保留。</p></div><button class="button button-primary" :disabled="!host.available || Boolean(pending) || loading" @click="importFile"><LoaderCircle v-if="pending === 'import'" :size="16" class="spinning" /><Download v-else :size="16" />导入 .ndpkg</button></div>
    <div v-if="failure" class="alert alert-error" role="alert"><strong>操作未完成</strong><span>{{ failure }}</span><span>当前选择以宿主返回的状态为准。可刷新后重试。</span></div>
    <div v-if="success" class="alert alert-success" role="status">{{ success }}</div>
    <div class="section-heading"><div><p class="eyebrow">INSTALLED</p><h3>已安装功能 <span class="count-label">{{ catalog.modules.length }}</span></h3></div><button class="button button-quiet button-small" :disabled="!host.available || loading || Boolean(pending)" @click="$emit('refresh')"><RefreshCw :size="14" />刷新</button></div>
    <div v-if="!catalog.modules.length" class="panel package-empty"><Package :size="30" :stroke-width="1.4" /><h3>{{ loading ? '正在读取功能目录…' : '还没有可显示的功能包' }}</h3><p>{{ host.available ? '选择完整的 .ndpkg 文件导入。校验失败的原文件也会保留，并记录原因。' : '浏览器预览无法读取或导入本机功能包。请在桌面应用中使用。' }}</p></div>
    <article v-for="module in catalog.modules" :key="module.id" class="panel package-card">
      <div class="package-card-head"><div class="package-card-icon"><Package :size="22" :stroke-width="1.5" /></div><div class="package-card-title"><h3>{{ module.label }}</h3><code>{{ module.id }}</code></div><label class="package-enable"><span>{{ module.enabled ? '已启用' : '已停用' }}</span><input type="checkbox" class="switch" :aria-label="`启用${module.label}`" :checked="module.enabled" :disabled="!host.available || Boolean(pending) || (!module.enabled && !selectedPackage(module)?.compatible)" @change="toggle(module)" /></label></div>
      <div class="package-current"><span>当前选择</span><strong>{{ module.selectedVersion ? `v${module.selectedVersion}` : '尚未选择版本' }}</strong><span class="outline-tag">{{ selectedPackage(module)?.source === 'bundled' ? '随应用提供' : selectedPackage(module) ? '本机导入' : '' }}</span><span class="package-state">{{ stateLabel(module.state) }}</span></div>
      <p v-if="module.error" class="package-error" role="status">{{ module.error }}</p>
      <div v-if="module.lastError" class="alert alert-error" role="status"><strong>最近一次加载失败</strong><span>{{ module.lastError }}</span><span>这是保留的失败记录；当前生效版本以上方「当前选择」为准。</span></div>
      <div class="package-card-actions"><button class="button button-secondary button-small" @click="versionsOpen[module.id] = !versionsOpen[module.id]"><Layers :size="14" />{{ module.versions.length }} 个版本<ChevronDown :size="13" :class="{ flipped: versionsOpen[module.id] }" /></button><button class="button button-primary button-small" :disabled="!canOpenPackage(module) || Boolean(pending)" @click="$emit('open', module.id)">打开功能</button></div>
      <div v-if="versionsOpen[module.id]" class="package-versions"><div v-for="version in module.versions" :key="version.digest" class="package-version-row"><div><strong>v{{ version.version }}</strong><span class="version-source">{{ version.source === 'bundled' ? '随应用提供' : '导入包' }}</span><code :title="version.digest">{{ version.digest.slice(0, 16) }}…</code><div class="package-provenance"><span v-if="version.sourceCommit" :title="version.sourceCommit">Git {{ version.sourceCommit.slice(0, 10) }}</span><span v-else>Git 来源未提供</span><span v-if="version.sourceDirty" class="source-dirty">含未提交修改</span><a v-if="safeRepositoryLink(version.sourceRepository)" :href="safeRepositoryLink(version.sourceRepository)" target="_blank" rel="noopener noreferrer">来源仓库 ↗</a></div><p v-if="version.error" class="package-error">{{ version.error }}</p></div><span v-if="module.selectedDigest === version.digest" class="package-selected"><Check :size="14" />当前选择</span><button v-else class="button button-secondary button-small" :disabled="!host.available || Boolean(pending) || !canSelectPackage(module, version)" @click="select(module, version)">{{ version.compatible ? '使用此版本' : '不兼容' }}</button></div></div>
    </article>
    <section class="panel import-history"><div class="panel-heading"><div class="panel-title"><Archive :size="18" /><h3>导入记录</h3></div><span class="panel-caption">{{ imports.length }} 条 · 原文件保留</span></div><p v-if="!imports.length" class="panel-description">尚无导入记录。失败和重复导入会保留原因，不覆盖已安装版本。</p><details v-for="entry in imports" :key="entry.id" class="import-entry" :open="entry.status === 'rejected'"><summary><span class="import-status" :class="entry.status">{{ statusLabel(entry.status) }}</span><strong>{{ entry.fileName }}</strong><time>{{ importDate(entry.createdAt) }}</time></summary><div class="import-details"><p v-if="entry.moduleId">{{ entry.moduleId }} · {{ entry.version ? `v${entry.version}` : '版本未知' }}</p><p v-if="entry.error" class="package-error">{{ entry.error }}</p><label>原文件保留位置<code>{{ entry.storedPath }}</code></label><label v-if="entry.digest">包摘要<code>{{ entry.digest }}</code></label></div></details></section>
  </div>
</template>
