<script setup lang="ts">
import { computed, ref } from 'vue';
import { ArrowDown, GitCommitHorizontal, History, Layers, Tag } from '@lucide/vue';
import { buildVersion, CHANGE_LABELS, filterReleaseHistory, type BuildVersion } from '../core/release';

const props = defineProps<{ version?: BuildVersion; hostVersion?: string | null }>();
const version = computed(() => props.version ?? buildVersion);
const featureId = ref('');
const historyElement = ref<HTMLElement | null>(null);
const history = computed(() => filterReleaseHistory(version.value.releases, featureId.value));
const featuresById = computed(() => new Map(version.value.features.map((feature) => [feature.id, feature])));
const mismatch = computed(() => props.hostVersion && version.value.appVersion !== 'unknown' && props.hostVersion !== version.value.appVersion);
const gitState = computed(() => version.value.git.dirty === true ? '未提交构建' : version.value.git.dirty === false ? '工作区干净' : '工作区状态未知');

function showFeatureHistory(id: string) {
  featureId.value = id;
  historyElement.value?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}
</script>

<template>
  <div class="page-stack version-page">
    <div class="page-intro"><p class="eyebrow">VERSION & HISTORY</p><h2>版本与更新</h2><p>查看当前应用、各项功能的版本，以及每次迭代的变化。</p></div>

    <section class="panel build-panel" aria-label="当前构建信息">
      <div class="build-title"><div class="app-version"><span>应用版本</span><strong>{{ version.appVersion === 'unknown' ? '版本未知' : `v${version.appVersion}` }}</strong></div><span class="build-state" :class="{ 'is-dirty': version.git.dirty === true, 'is-clean': version.git.dirty === false }">{{ gitState }}</span></div>
      <dl class="build-facts"><div><dt><GitCommitHorizontal :size="14" />代码提交</dt><dd v-if="version.git.commit"><code :title="version.git.commit">{{ version.git.shortCommit || version.git.commit }}</code><span class="full-commit">{{ version.git.commit }}</span></dd><dd v-else>Git 提交未知</dd></div><div><dt><Layers :size="14" />功能记录</dt><dd>{{ version.features.length }} 项功能 · {{ version.releases.length }} 个应用版本</dd></div><div v-if="hostVersion"><dt>桌面宿主版本</dt><dd>v{{ hostVersion }}</dd></div></dl>
      <p v-if="version.git.dirty === true" class="build-note dirty-note">此构建包含未提交的本地修改，提交号不能完整代表当前代码。</p>
      <p v-else-if="version.git.dirty === null || !version.git.commit" class="build-note">构建时未获得完整 Git 信息，无法确认代码来源或工作区状态。</p>
      <p v-else class="build-note">以上信息记录于构建时；工作区干净不代表已发布正式版本。</p>
    </section>

    <div v-if="mismatch" class="alert alert-error" role="alert"><strong>前端与桌面宿主版本不匹配</strong><span>前端 v{{ version.appVersion }}，桌面宿主 v{{ hostVersion }}。请重新构建并启动配套版本。</span></div>

    <section class="panel" aria-label="当前功能版本">
      <div class="panel-heading"><div class="panel-title"><Layers :size="17" /><h3>当前功能版本</h3></div><span class="panel-caption">功能 ID 保持稳定，版本独立递增</span></div>
      <div v-if="version.features.length" class="feature-table-wrap"><table class="feature-table"><thead><tr><th scope="col">功能 / 稳定 ID</th><th scope="col">所属模块</th><th scope="col">功能版本</th><th scope="col">最近更新于</th><th scope="col"><span class="visually-hidden">历史记录</span></th></tr></thead><tbody><tr v-for="feature in version.features" :key="feature.id"><th scope="row"><strong>{{ feature.name }}</strong><code>{{ feature.id }}</code></th><td>{{ feature.module }}</td><td><span class="feature-version">v{{ feature.version }}</span></td><td>应用 v{{ feature.updatedIn }}</td><td><button class="history-link" :aria-label="`查看${feature.name}的迭代记录`" @click="showFeatureHistory(feature.id)">查看迭代<ArrowDown :size="12" /></button></td></tr></tbody></table></div>
      <p v-else class="empty-version-data">此构建未附带功能版本记录。</p>
    </section>

    <section ref="historyElement" class="panel history-panel" aria-label="功能迭代记录">
      <div class="panel-heading"><div class="panel-title"><History :size="17" /><h3>迭代记录</h3></div><label class="history-filter">筛选功能<select v-model="featureId" aria-label="筛选功能迭代记录"><option value="">全部功能</option><option v-for="feature in version.features" :key="feature.id" :value="feature.id">{{ feature.name }} · {{ feature.id }}</option></select></label></div>
      <p class="history-count" aria-live="polite">{{ featureId ? `「${featuresById.get(featureId)?.name ?? featureId}」` : '全部功能' }} · {{ history.length }} 个应用版本</p>
      <ol v-if="history.length" class="release-list"><li v-for="release in history" :key="release.version" class="release-entry"><header class="release-heading"><Tag :size="15" /><h4>v{{ release.version }}</h4><time :datetime="release.date">{{ release.date }}</time><span v-if="release.version === version.appVersion" class="current-release">当前版本记录</span></header><p class="release-summary">{{ release.summary }}</p><ul class="change-list"><li v-for="(change, index) in release.changes" :key="`${change.featureId}-${index}`"><span class="change-type" :class="`change-${change.type}`">{{ CHANGE_LABELS[change.type] }}</span><div><div class="change-feature"><strong>{{ featuresById.get(change.featureId)?.name ?? change.featureId }}</strong><span>v{{ change.version }}</span><code>{{ change.featureId }}</code></div><p>{{ change.description }}</p></div></li></ul></li></ol>
      <p v-else class="empty-version-data">{{ featureId ? '此功能尚无迭代记录。' : '此构建未附带迭代记录。' }}</p>
    </section>
  </div>
</template>

<style scoped>
.build-title { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.app-version > span { display: block; font-size: 11px; color: var(--muted); }
.app-version strong { display: block; font-size: 30px; font-weight: 600; margin-top: 8px; letter-spacing: -.5px; }
.build-state { font-size: 11px; border: 1px solid var(--border); border-radius: 5px; padding: 7px 10px; color: var(--muted); }
.build-state.is-dirty { background: var(--error-bg); color: var(--error-text); border-color: transparent; }
.build-state.is-clean { background: var(--success-bg); color: var(--success-text); border-color: transparent; }
.build-facts { display: flex; flex-wrap: wrap; gap: 24px 40px; margin: 23px 0 0; }
.build-facts dt { display: flex; align-items: center; gap: 5px; color: var(--muted); font-size: 10px; margin-bottom: 9px; }
.build-facts dd { margin: 0; font-size: 12px; }
.full-commit { display: block; font-family: Consolas, monospace; overflow-wrap: anywhere; font-size: 10px; color: var(--muted); margin-top: 7px; }
.build-note { margin-top: 20px; padding-top: 15px; border-top: 1px solid var(--border); color: var(--muted); font-size: 11px; line-height: 1.8; }
.dirty-note { color: var(--error-text); }
.feature-table-wrap { overflow-x: auto; }
.feature-table { width: 100%; border-collapse: collapse; font-size: 11px; text-align: left; }
.feature-table thead th { font-weight: 500; color: var(--muted); padding: 0 12px 12px; border-bottom: 1px solid var(--border); white-space: nowrap; }
.feature-table td, .feature-table tbody th { padding: 15px 12px; border-bottom: 1px solid var(--border); vertical-align: middle; font-weight: 400; }
.feature-table tr:last-child td, .feature-table tr:last-child th { border-bottom: 0; }
.feature-table th:first-child { padding-left: 0; }
.feature-table td:last-child { padding-right: 0; }
.feature-table tbody th strong { display: block; font-size: 12px; font-weight: 550; margin-bottom: 7px; }
.feature-table tbody th code { color: var(--muted); font-size: 10px; }
.feature-version { color: var(--accent); font-family: Consolas, monospace; font-size: 12px; white-space: nowrap; }
.history-link { display: inline-flex; align-items: center; gap: 4px; padding: 5px 0; border: 0; background: transparent; color: var(--accent); white-space: nowrap; font-size: 10px; }
.history-link:hover { text-decoration: underline; }
.history-panel { scroll-margin-top: 20px; }
.history-filter { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 10px; }
.history-filter select { max-width: 310px; padding: 7px 9px; border: 1px solid var(--border); border-radius: 5px; background: var(--surface); color: var(--text); font-size: 11px; }
.history-filter select:focus-visible { outline: 3px solid var(--focus); outline-offset: 3px; }
.history-count { font-size: 10px; color: var(--muted); margin-bottom: 20px; }
.release-list, .change-list { padding: 0; list-style: none; margin: 0; }
.release-entry + .release-entry { margin-top: 27px; padding-top: 27px; border-top: 1px solid var(--border); }
.release-heading { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; color: var(--accent); }
.release-heading h4 { font-size: 16px; color: var(--text); font-weight: 600; }
.release-heading time { color: var(--muted); font-size: 10px; margin-left: 5px; }
.current-release { font-size: 9px; padding: 3px 6px; background: var(--accent-soft); border-radius: 4px; }
.release-summary { margin: 10px 0 18px; font-size: 12px; line-height: 1.8; color: var(--muted); }
.change-list { display: flex; flex-direction: column; gap: 17px; }
.change-list li { display: flex; align-items: start; gap: 12px; }
.change-type { padding: 3px 6px; font-size: 9px; line-height: 1.5; border-radius: 4px; background: var(--surface-soft); color: var(--muted); white-space: nowrap; }
.change-added { background: var(--success-bg); color: var(--success-text); }
.change-fixed, .change-changed { background: var(--accent-soft); color: var(--accent); }
.change-breaking { background: var(--error-bg); color: var(--error-text); }
.change-feature { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; font-size: 11px; line-height: 1.8; }
.change-feature strong { font-weight: 550; }
.change-feature > span, .change-feature code { font-size: 10px; color: var(--muted); }
.change-list p { margin-top: 5px; font-size: 11px; color: var(--muted); line-height: 1.9; }
.empty-version-data { color: var(--muted); font-size: 12px; padding: 8px 0; }
.visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
@media (max-width: 1000px) { .history-panel > .panel-heading { align-items: start; flex-direction: column; } .history-filter { width: 100%; } .history-filter select { min-width: 0; max-width: 100%; flex: 1; } .feature-table { min-width: 560px; } .panel-heading .panel-caption { max-width: 170px; line-height: 1.7; } }
</style>
