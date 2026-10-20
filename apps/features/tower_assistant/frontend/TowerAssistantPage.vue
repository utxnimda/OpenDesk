<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { ArrowRight, Check, ChevronDown, Download, Expand, FolderOpen, Layers, Map, Package, RefreshCw, ShieldCheck, Sparkles, Users } from '@lucide/vue';
import { errorMessage, type ModuleServices } from '../../../framework/module-sdk/src/index';
import { createTowerClient } from './client';
import { collectIconKeys, comparisonSvg, ENTITY_STYLE, entitiesAtPoint, focusBounds, formatQi, stepDescription } from './map';
import TowerMap from './TowerMap.vue';
import EntityDetails from './EntityDetails.vue';
import type { Analysis, Inspection, MigrationStep, Point, VisualCatalog } from './types';

const props = defineProps<{ services: ModuleServices }>();
const client = createTowerClient(props.services.host);
const gameDir = ref('');
const saveId = ref('');
const platformId = ref('');
const inspection = ref<Inspection | null>(null);
const analysis = ref<Analysis | null>(null);
const pending = ref('');
const failure = ref('');
const sourceOpen = ref(false);
const selectedStep = ref('');
const selectedEntityId = ref('');
const selectedSide = ref<'before' | 'after'>('before');
const selectedPoint = ref<Point | null>(null);
const visuals = ref<VisualCatalog | null>(null);
const visualsPending = ref(false);
const visualsFailure = ref('');
let visualsRequest = 0;
const scope = ref<'focus' | 'city'>('focus');
const view = ref<'compare' | 'overlay'>('compare');
const zoom = ref(1);
const itemLocation = ref<'all' | 'warehouse' | 'placed'>('warehouse');
const itemSearch = ref('');
const mapSection = ref<HTMLElement | null>(null);
const entityDetails = ref<HTMLElement | null>(null);
const busy = computed(() => Boolean(pending.value));
const snapshot = computed(() => !saveId.value || inspection.value?.snapshot.source.id === saveId.value ? inspection.value?.snapshot : undefined);
const entitiesBefore = computed(() => analysis.value?.entitiesBefore ?? snapshot.value?.entities ?? []);
const entitiesAfter = computed(() => analysis.value?.entitiesAfter ?? entitiesBefore.value);
const platforms = computed(() => entitiesBefore.value.filter((entity) => entity.kind === 'platform'));
const chosenPlatform = computed(() => platforms.value.find((entity) => entity.id === (analysis.value?.platformId || platformId.value)) ?? platforms.value[0]);
const source = computed(() => analysis.value?.source ?? snapshot.value?.source);
const steps = computed(() => analysis.value?.steps ?? []);
const currentStep = computed(() => {
  const step = steps.value.find((entry) => entry.id === selectedStep.value);
  if (!step) return undefined;
  return { ...step, from: step.from ?? entitiesBefore.value.find((entity) => entity.id === step.entityId), to: step.to ?? entitiesAfter.value.find((entity) => entity.id === step.entityId) };
});
const worldBounds = computed(() => analysis.value?.bounds ?? snapshot.value?.bounds);
const mapBounds = computed(() => !worldBounds.value ? null : scope.value === 'city' ? worldBounds.value : focusBounds(worldBounds.value, chosenPlatform.value, currentStep.value));
const items = computed(() => analysis.value?.items ?? snapshot.value?.items ?? []);
const warehouseCount = computed(() => items.value.filter((item) => item.location === 'warehouse').length);
const shownItems = computed(() => items.value.filter((item) => (itemLocation.value === 'all' || item.location === itemLocation.value) && item.name.includes(itemSearch.value.trim())).sort((a, b) => b.qi - a.qi || a.name.localeCompare(b.name)));
const warnings = computed(() => analysis.value?.warnings ?? snapshot.value?.warnings ?? []);
const resultReady = computed(() => analysis.value?.status === 'ready');
const hasMap = computed(() => mapBounds.value && entitiesBefore.value.length > 0);
const selectedBefore = computed(() => entitiesBefore.value.find((entity) => entity.id === selectedEntityId.value));
const selectedAfter = computed(() => entitiesAfter.value.find((entity) => entity.id === selectedEntityId.value));
const selectedEntity = computed(() => (selectedSide.value === 'after' ? selectedAfter.value : selectedBefore.value) ?? selectedBefore.value ?? selectedAfter.value);
const neighbours = computed(() => selectedEntity.value && selectedPoint.value ? entitiesAtPoint(selectedSide.value === 'after' ? entitiesAfter.value : entitiesBefore.value, selectedPoint.value) : []);

function dateLabel(value?: string): string {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function stepLabel(step: MigrationStep): string {
  return stepDescription(step, (step.operation === 'unequip' ? entitiesBefore.value : entitiesAfter.value).find((entity) => entity.id === step.entityId));
}

watch(gameDir, () => { inspection.value = null; analysis.value = null; saveId.value = ''; platformId.value = ''; selectedStep.value = ''; selectedEntityId.value = ''; visuals.value = null; visualsFailure.value = ''; visualsPending.value = false; visualsRequest++; }, { flush: 'sync' });
watch(saveId, () => { analysis.value = null; platformId.value = ''; selectedStep.value = ''; selectedEntityId.value = ''; }, { flush: 'sync' });
watch(platformId, () => { analysis.value = null; selectedStep.value = ''; selectedEntityId.value = ''; }, { flush: 'sync' });
watch(selectedEntityId, (value) => { if (!value) selectedPoint.value = null; }, { flush: 'sync' });

async function refreshVisuals(directory: string) {
  const keys = collectIconKeys([...entitiesBefore.value, ...entitiesAfter.value], items.value).filter((key) => !visuals.value?.sprites[key]);
  if (!keys.length) return;
  const request = ++visualsRequest;
  visualsPending.value = true;
  visualsFailure.value = '';
  try {
    const result = await client.visuals(directory, keys);
    if (request !== visualsRequest) return;
    visuals.value = {
      schemaVersion: 1,
      sheets: { ...visuals.value?.sheets, ...result.sheets },
      sprites: { ...visuals.value?.sprites, ...result.sprites },
      warnings: result.warnings,
    };
    if (result.warnings?.length) visualsFailure.value = result.warnings.join('；');
  } catch (error) {
    if (request === visualsRequest) visualsFailure.value = `图标读取失败，仍可查看名称与详情：${errorMessage(error)}`;
  } finally {
    if (request === visualsRequest) visualsPending.value = false;
  }
}

async function readSnapshot() {
  if (busy.value || !props.services.host.available) return;
  pending.value = '读取存档'; failure.value = ''; analysis.value = null; selectedStep.value = ''; selectedEntityId.value = '';
  const requestedSave = saveId.value;
  try {
    const result = await client.inspect(gameDir.value, requestedSave || undefined);
    gameDir.value = result.gameDir;
    saveId.value = requestedSave;
    inspection.value = result;
    platformId.value = '';
    sourceOpen.value = result.snapshot.entities.filter((entity) => entity.kind === 'platform').length > 1;
    void refreshVisuals(result.gameDir);
  } catch (error) { failure.value = errorMessage(error); sourceOpen.value = true; }
  finally { pending.value = ''; }
}

async function generate() {
  if (busy.value || !props.services.host.available) return;
  pending.value = '分析最高灵气与最少搬动'; failure.value = ''; analysis.value = null; selectedStep.value = ''; selectedEntityId.value = '';
  try {
    analysis.value = await client.analyze(gameDir.value, saveId.value || undefined, platformId.value || undefined);
    zoom.value = 1;
    sourceOpen.value = false;
    void refreshVisuals(gameDir.value);
  } catch (error) { failure.value = errorMessage(error); sourceOpen.value = true; }
  finally { pending.value = ''; }
}

onMounted(async () => {
  if (!props.services.host.available) return;
  pending.value = '查找游戏目录';
  try {
    const result = await client.discover();
    if (result.gameDirs[0]) gameDir.value = result.gameDirs[0];
    else sourceOpen.value = true;
  } catch (error) { failure.value = errorMessage(error); sourceOpen.value = true; }
  finally { pending.value = ''; }
  if (gameDir.value) await readSnapshot();
});

function selectStep(stepId: string) {
  selectedStep.value = selectedStep.value === stepId ? '' : stepId;
  scope.value = 'focus';
  zoom.value = 1;
}

function selectEntity(entityId: string, side: 'before' | 'after', keepPoint = false) {
  const entity = (side === 'after' ? entitiesAfter.value : entitiesBefore.value).find((entry) => entry.id === entityId);
  if (!entity) return;
  if (!keepPoint || !selectedPoint.value || selectedSide.value !== side) selectedPoint.value = { x: entity.x, y: entity.y };
  selectedEntityId.value = entityId;
  selectedSide.value = side;
}

async function fullscreen() {
  try {
    if (document.fullscreenElement === mapSection.value) await document.exitFullscreen();
    else await mapSection.value?.requestFullscreen();
  } catch (error) { failure.value = `无法展开地图：${errorMessage(error)}`; }
}

function exportComparison() {
  if (!analysis.value || !resultReady.value) return;
  const blob = new Blob([comparisonSvg(analysis.value, visuals.value)], { type: 'image/svg+xml;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `渡劫迁移对比-${new Date().toISOString().slice(0, 10)}.svg`;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
</script>

<template>
  <div class="tower-assistant">
    <header class="tool-heading">
      <div><p class="tool-overline">塔防山贼助手 <span>/</span> 布局工具</p><h2>渡劫布局分析</h2><p class="tool-description">先达到可用灵物的最高灵气，再尽量少搬人物和建筑。</p></div>
      <button class="button button-primary analyze-button" :disabled="busy || !services.host.available || !gameDir.trim() || (platforms.length > 1 && !platformId)" @click="generate"><RefreshCw v-if="busy" :size="15" class="spinning" /><Sparkles v-else :size="15" />{{ pending || '生成迁移方案' }}</button>
    </header>

    <div v-if="!services.host.available" class="alert browser-note" role="status"><strong>浏览器预览无法读取本地游戏存档</strong><span>请从 NimdaDesk 桌面应用打开此工具。读取和分析只在本机进行。</span></div>
    <div v-if="failure" class="alert alert-error" role="alert">{{ failure }}</div>

    <section class="source-panel">
      <button class="source-summary" :aria-expanded="sourceOpen" @click="sourceOpen = !sourceOpen"><FolderOpen :size="15" /><span v-if="source"><strong>{{ source.name }}</strong><span class="source-date">{{ dateLabel(source.savedAt) }}</span></span><span v-else>选择游戏目录 · 默认读取最近存档</span><span class="read-only"><ShieldCheck :size="13" />只读分析</span><ChevronDown :size="15" :class="{ flipped: sourceOpen }" /></button>
      <div v-if="sourceOpen" class="source-fields">
        <label class="directory-field">游戏安装目录<input v-model="gameDir" type="text" :disabled="busy || !services.host.available" placeholder="包含 game.exe 和 package.nw 的目录" spellcheck="false" @keydown.enter="readSnapshot" /></label>
        <button class="button button-secondary" :disabled="busy || !services.host.available || !gameDir.trim()" @click="readSnapshot"><RefreshCw :size="13" />读取存档</button>
        <label v-if="inspection" class="save-field">分析存档<select v-model="saveId" :disabled="busy" @change="readSnapshot"><option value="">最近存档（每次分析重新读取）</option><option v-for="save in inspection.saves" :key="save.id" :value="save.id">{{ save.name }} · {{ dateLabel(save.savedAt) }}</option></select></label>
        <label v-if="platforms.length > 1" class="platform-field">渡劫台<select v-model="platformId" :disabled="busy"><option value="">请选择渡劫台</option><option v-for="platform in platforms" :key="platform.id" :value="platform.id">{{ platform.name }} ({{ platform.x }}, {{ platform.y }})</option></select></label>
        <p class="source-help">不修改存档、不移动游戏中的对象。更改游戏摆放或仓库后，请先在游戏中存档，再重新生成方案。</p>
      </div>
    </section>

    <section v-if="analysis" class="result-summary" aria-label="分析结果">
      <div class="qi-summary"><span>渡劫台灵气</span><div><strong>{{ formatQi(analysis.metrics.currentQi) }}</strong><ArrowRight :size="18" /><strong class="target-qi">{{ formatQi(analysis.metrics.targetQi) }}</strong><em v-if="analysis.metrics.gain > 0">+{{ formatQi(analysis.metrics.gain) }}</em></div></div>
      <div class="move-stat"><Layers :size="16" /><strong>{{ analysis.metrics.movedBuildings }}</strong><span>建筑搬动</span></div>
      <div class="move-stat"><Map :size="16" /><strong>{{ analysis.metrics.movedFacilities }}</strong><span>设施搬动</span></div>
      <div class="move-stat"><Users :size="16" /><strong>{{ analysis.metrics.movedPeople }}</strong><span>人物搬动</span></div>
      <div class="move-stat"><Package :size="16" /><strong>{{ analysis.metrics.itemChanges }}</strong><span>灵物更换</span></div>
    </section>
    <p v-if="resultReady && analysis" class="proof-summary"><Check :size="12" /><span>{{ analysis.optimality.qi === 'exact' ? '最高灵气已验证' : '当前已找到的灵气方案' }}</span><span>·</span><span>{{ analysis.optimality.movement === 'exact' ? '搬动最少已验证' : '搬动数仍有优化余地' }}</span></p>
    <div v-if="analysis?.status === 'blocked'" class="alert alert-error" role="status"><strong>当前条件下无法生成合法迁移方案</strong><span>请查看下方分析说明，调整后重新存档分析。</span></div>

    <section ref="mapSection" class="map-section" @keydown.esc="selectedEntityId = ''">
      <div class="map-toolbar">
        <div class="map-title"><Map :size="16" /><h3>{{ analysis ? '摆放迁移对照' : '当前城中摆放' }}</h3><button v-if="selectedEntity" class="selected-object-link" :aria-label="`查看${selectedEntity.name}的对象详情`" @click="entityDetails?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })">{{ selectedEntity.name }} · 详情 <ArrowRight :size="12" /></button></div>
        <div v-if="hasMap" class="map-controls">
          <div class="segmented"><button :class="{ active: scope === 'focus' }" @click="scope = 'focus'; selectedStep = ''; zoom = 1">渡劫区域</button><button :class="{ active: scope === 'city' }" @click="scope = 'city'; zoom = 1">全城</button></div>
          <div v-if="resultReady" class="segmented"><button :class="{ active: view === 'compare' }" @click="view = 'compare'">前后对照</button><button :class="{ active: view === 'overlay' }" @click="view = 'overlay'">迁移箭头</button></div>
          <button class="icon-button" title="展开地图" aria-label="展开地图" @click="fullscreen"><Expand :size="15" /></button>
          <button v-if="resultReady" class="icon-button" title="导出全城对比图与迁移步骤（SVG）" aria-label="导出全城对比图与迁移步骤" @click="exportComparison"><Download :size="15" /></button>
        </div>
      </div>
      <div v-if="hasMap && mapBounds" class="map-panes" :class="{ single: !resultReady || view === 'overlay' }">
        <div v-if="!resultReady || view === 'compare'" class="map-pane"><div class="pane-heading"><span class="pane-dot current-dot" /><strong>当前摆放</strong><span>{{ analysis ? '搬动前' : '存档快照' }}</span></div><TowerMap :entities="entitiesBefore" :bounds="mapBounds" :platform-id="chosenPlatform?.id ?? ''" :steps="steps" :items="items" :visuals="visuals" :selected-step="selectedStep" :selected-entity-id="selectedEntityId" :zoom="zoom" label="当前摆放地图" @select="selectStep" @select-entity="selectEntity($event, 'before')" /></div>
        <div v-if="resultReady" class="map-pane"><div class="pane-heading"><span class="pane-dot target-dot" /><strong>建议摆放</strong><span>{{ view === 'overlay' ? '箭头从原位置指向新位置' : '编号与左图、步骤对应' }}</span></div><TowerMap :entities="entitiesAfter" :bounds="mapBounds" :platform-id="chosenPlatform?.id ?? ''" :steps="steps" :items="items" :visuals="visuals" after :arrows="view === 'overlay' || Boolean(selectedStep)" :selected-step="selectedStep" :selected-entity-id="selectedEntityId" :zoom="zoom" label="建议摆放地图" @select="selectStep" @select-entity="selectEntity($event, 'after')" /></div>
      </div>
      <div v-else class="empty-map"><div class="empty-map-icon"><Map :size="35" stroke-width="1.2" /></div><h3>{{ busy ? pending : '从你的存档开始' }}</h3><p>读取城中摆放和仓库灵物，生成一张可照着搬的对比图。</p><span>最高灵气优先 <span class="empty-separator">·</span> 少搬人物与建筑 <span class="empty-separator">·</span> 编号迁移步骤</span></div>
      <footer v-if="hasMap" class="map-footer"><div class="map-legend"><span v-for="(style, kind) in ENTITY_STYLE" :key="kind"><i :style="{ background: style.fill, borderColor: style.stroke, borderRadius: kind === 'person' ? '50%' : '2px' }" />{{ style.label }}</span><span><i class="changed-legend" />有调整</span></div><label class="zoom-control">缩放<input v-model.number="zoom" type="range" min="1" max="3" step=".25" /><span>{{ Math.round(zoom * 100) }}%</span></label></footer>
      <div v-if="hasMap" class="map-detail-status"><p v-if="selectedEntity"><strong>已选 {{ selectedEntity.name }}</strong><button @click="entityDetails?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })">查看下方详情</button></p><p v-else>点击任意人物、灵台或建筑查看详情；同格对象可在详情中切换。</p><span v-if="visualsPending">正在读取游戏图标…</span><span v-else-if="visualsFailure" class="visuals-warning" role="status">{{ visualsFailure }}</span></div>
      <div v-if="selectedEntity" ref="entityDetails" class="entity-detail-container"><EntityDetails :before="selectedBefore" :after="selectedAfter" :items="items" :visuals="visuals" :has-plan="resultReady" :neighbours="neighbours" :selected-side="selectedSide" @close="selectedEntityId = ''" @select="selectEntity($event, selectedSide, true)" /></div>
    </section>

    <div v-if="source" class="details-grid">
      <section class="steps-section detail-panel">
        <div class="detail-heading"><div><h3>迁移步骤 <span v-if="analysis">{{ steps.length }}</span></h3><p>编号与地图对应，按顺序在游戏内操作。</p></div><button v-if="selectedStep" class="button button-quiet button-small" @click="selectedStep = ''">清除选择</button></div>
        <ol v-if="steps.length" class="step-list"><li v-for="(step, index) in steps" :key="step.id"><button class="step-button" :class="{ selected: selectedStep === step.id }" :aria-pressed="selectedStep === step.id" @click="selectStep(step.id)"><span class="step-number">{{ index + 1 }}</span><span class="step-copy"><strong>{{ stepLabel(step) }}</strong><span>{{ step.reason }}</span></span><span class="step-kind">{{ step.kind === 'move' ? '搬动' : '换灵物' }}</span></button></li></ol>
        <div v-else class="empty-steps"><Check v-if="resultReady" :size="22" /><Layers v-else :size="22" /><p>{{ resultReady ? '当前布局已经达到本次方案，无需调整。' : analysis?.status === 'blocked' ? '没有生成可执行步骤。' : '生成方案后，这里会列出需要移动的人物、建筑和灵物。' }}</p></div>
        <details v-if="analysis || warnings.length" class="analysis-notes" :open="analysis?.status === 'blocked'"><summary>分析说明与限制 <span v-if="warnings.length">{{ warnings.length }} 条提醒</span></summary><div v-if="analysis" class="optimality-note"><span class="optimality-tag">{{ analysis.optimality.qi === 'exact' ? '灵气最优已验证' : '灵气候选方案' }}</span><span class="optimality-tag">{{ analysis.optimality.movement === 'exact' ? '搬动最少已验证' : '搬动数未证明全局最少' }}</span><p>{{ analysis.optimality.explanation }}</p></div><ul v-if="warnings.length"><li v-for="(warning, index) in warnings" :key="index">{{ warning }}</li></ul></details>
      </section>
      <section class="inventory-section detail-panel"><div class="detail-heading"><div><h3>灵物清单 <span>{{ items.length }}</span></h3><p>仓库 {{ warehouseCount }} 件 · 已放置 {{ items.length - warehouseCount }} 件</p></div><Package :size="19" /></div><div class="inventory-filters"><select v-model="itemLocation" aria-label="灵物位置筛选"><option value="warehouse">仓库灵物</option><option value="placed">已放置灵物</option><option value="all">全部灵物</option></select><input v-model="itemSearch" placeholder="搜索灵物名称" aria-label="搜索灵物名称" /></div><div class="item-table-wrap"><table class="item-table"><thead><tr><th>灵物</th><th>位置</th><th>基础灵气</th></tr></thead><tbody><tr v-for="item in shownItems" :key="item.id"><td>{{ item.name }}<small>{{ item.id }}</small></td><td>{{ item.location === 'warehouse' ? '仓库' : '已放置' }}</td><td>{{ formatQi(item.qi) }}</td></tr></tbody></table><p v-if="!shownItems.length" class="no-items">没有符合条件的灵物。</p></div><p class="inventory-footnote">按基础灵气排序。实际对渡劫台的贡献由后台结合灵台等级与距离计算。</p></section>
    </div>
  </div>
</template>

<style scoped>
.tower-assistant { display: flex; flex-direction: column; gap: 15px; padding-bottom: 18px; }
.tool-heading { display: flex; justify-content: space-between; align-items: center; gap: 18px; padding: 2px 0 4px; }
.tool-overline { color: var(--muted); font-size: 10px; letter-spacing: .5px; }
.tool-overline span { margin: 0 8px; opacity: .45; }
.tool-heading h2 { font-size: 24px; font-weight: 550; margin-top: 8px; letter-spacing: .5px; }
.tool-description { color: var(--muted); font-size: 11px; margin-top: 9px; line-height: 1.7; }
.analyze-button { min-width: 139px; }
.tower-assistant > .alert { margin: 0; }
.source-panel { border: 1px solid var(--border); border-radius: 8px; background: var(--surface); }
.source-summary { display: flex; align-items: center; gap: 9px; color: var(--muted); background: transparent; border: 0; width: 100%; padding: 12px 15px; text-align: left; font-size: 11px; }
.source-summary strong { color: var(--text); font-weight: 550; }
.source-summary > svg:last-child { transition: transform .16s ease; }
.source-date { margin-left: 12px; }
.flipped { transform: rotate(180deg); }
.read-only { display: flex; align-items: center; gap: 5px; margin-left: auto; white-space: nowrap; color: var(--success-text); font-size: 10px; }
.source-fields { display: flex; flex-wrap: wrap; gap: 13px; padding: 14px 15px; border-top: 1px solid var(--border); align-items: end; }
.source-fields label { display: flex; flex-direction: column; gap: 7px; color: var(--muted); font-size: 10px; }
.directory-field { flex: 1; min-width: 260px; }
.save-field { flex: 1; min-width: 260px; }
.platform-field { min-width: 180px; }
input:not([type=range]), select { min-height: 36px; color: var(--text); border: 1px solid var(--border); border-radius: 5px; background: var(--surface-soft); padding: 8px 10px; font-size: 11px; min-width: 0; }
.source-help { color: var(--muted); font-size: 10px; line-height: 1.7; flex-basis: 100%; }
.result-summary { display: flex; align-items: center; gap: 24px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); padding: 15px 20px; }
.qi-summary { flex: 1; }
.qi-summary > span { color: var(--muted); font-size: 10px; }
.qi-summary > div { display: flex; align-items: center; gap: 12px; margin-top: 7px; }
.qi-summary strong { font-size: 23px; font-weight: 550; font-variant-numeric: tabular-nums; }
.qi-summary svg { color: var(--muted); opacity: .7; }
.qi-summary .target-qi { color: var(--success-text); }
.qi-summary em { font-size: 10px; background: var(--success-bg); color: var(--success-text); border-radius: 4px; padding: 4px 7px; font-style: normal; }
.move-stat { display: grid; grid-template-columns: 17px auto; gap: 7px 8px; align-items: center; color: var(--muted); }
.move-stat strong { color: var(--text); font-size: 18px; font-weight: 550; }
.move-stat span { grid-column: 1/-1; font-size: 10px; }
.proof-summary { display: flex; align-items: center; gap: 7px; color: var(--muted); font-size: 10px; margin-top: -5px; }
.proof-summary svg { color: var(--success-text); }
.map-section { border: 1px solid var(--border); border-radius: 9px; overflow: hidden; background: var(--surface); }
.map-toolbar { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 12px; padding: 12px 15px; border-bottom: 1px solid var(--border); }
.map-title, .map-controls { display: flex; align-items: center; gap: 10px; }
.map-title { color: var(--accent); }
.map-title h3 { color: var(--text); font-size: 12px; font-weight: 550; }
.selected-object-link { display: flex; align-items: center; gap: 5px; max-width: 230px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; padding: 5px 8px; border: 1px solid var(--border); border-radius: 5px; color: var(--accent); background: var(--accent-soft); font-size: 10px; }
.map-controls { gap: 8px; }
.segmented { display: inline-flex; align-items: center; padding: 3px; background: var(--surface-soft); border: 1px solid var(--border); border-radius: 5px; }
.segmented button { padding: 5px 9px; background: none; border: none; color: var(--muted); border-radius: 3px; font-size: 10px; }
.segmented .active { color: var(--accent); background: var(--accent-soft); }
.icon-button { display: grid; place-items: center; width: 29px; height: 29px; color: var(--muted); border: 1px solid var(--border); background: var(--surface); border-radius: 5px; }
.icon-button:hover { color: var(--accent); background: var(--surface-soft); }
.map-panes { display: grid; grid-template-columns: 1fr 1fr; min-height: 400px; height: clamp(420px, 61vh, 740px); }
.map-panes.single { grid-template-columns: 1fr; }
.map-pane { display: flex; flex-direction: column; min-width: 0; min-height: 0; overflow: hidden; }
.map-pane + .map-pane { border-left: 1px solid var(--border); }
.map-pane :deep(.tower-map) { flex: 1; min-height: 0; }
.pane-heading { padding: 12px 16px 7px; display: flex; align-items: center; gap: 7px; font-size: 10px; }
.pane-heading strong { font-weight: 550; }
.pane-heading > span:last-child { margin-left: auto; color: var(--muted); font-size: 9px; }
.pane-dot { width: 5px; height: 5px; border-radius: 50%; }
.current-dot { background: #8e9fb7; }
.target-dot { background: #5d9b83; }
.empty-map { min-height: 375px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px; text-align: center; padding: 30px; }
.empty-map-icon { border: 1px solid var(--border); border-radius: 17px; padding: 22px; color: var(--accent); background: var(--surface-soft); margin-bottom: 8px; }
.empty-map h3 { font-size: 17px; font-weight: 500; }
.empty-map p { color: var(--muted); font-size: 11px; line-height: 1.8; }
.empty-map > span { color: var(--muted); font-size: 9px; margin-top: 10px; }
.empty-separator { margin: 0 12px; opacity: .5; }
.map-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; border-top: 1px solid var(--border); padding: 11px 15px; }
.map-detail-status { display: flex; align-items: center; justify-content: space-between; gap: 15px; border-top: 1px solid var(--border); padding: 9px 15px; color: var(--muted); font-size: 10px; line-height: 1.7; }
.map-detail-status strong { color: var(--text); font-weight: 500; }
.map-detail-status button { padding: 0; margin-left: 12px; background: transparent; border: 0; color: var(--accent); font-size: 10px; text-decoration: underline; text-underline-offset: 3px; }
.map-detail-status .visuals-warning { color: var(--error-text); max-width: 55%; }
.entity-detail-container { flex: 0 0 auto; }
.map-legend { display: flex; flex-wrap: wrap; gap: 10px; }
.map-legend span { display: inline-flex; align-items: center; gap: 5px; color: var(--muted); font-size: 9px; }
.map-legend i { display: inline-block; width: 8px; height: 8px; border: 1px solid; }
.changed-legend { background: none; border-color: #c35c44 !important; border-width: 2px !important; }
.zoom-control { display: flex; align-items: center; gap: 8px; font-size: 9px; color: var(--muted); white-space: nowrap; }
.zoom-control input { width: 80px; accent-color: var(--accent); height: 12px; }
.zoom-control > span { width: 29px; }
.map-section:fullscreen { border-radius: 0; display: flex; flex-direction: column; height: 100vh; padding: 14px; background: var(--bg); }
.map-section:fullscreen .map-panes { flex: 1; height: auto; }
.map-section:fullscreen .map-toolbar { padding: 8px 15px 16px; }
.map-section:fullscreen .entity-detail-container { max-height: 31vh; overflow-y: auto; }
.details-grid { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(290px, 1fr); gap: 15px; align-items: start; }
.detail-panel { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
.detail-heading { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 18px 18px 15px; color: var(--muted); }
.detail-heading h3 { font-size: 13px; font-weight: 550; color: var(--text); }
.detail-heading h3 span { color: var(--muted); margin-left: 6px; font-size: 10px; font-weight: 400; }
.detail-heading p { margin-top: 7px; font-size: 10px; line-height: 1.6; }
.step-list { margin: 0; padding: 0 8px 10px; list-style: none; max-height: 530px; overflow-y: auto; scrollbar-width: thin; }
.step-button { display: flex; align-items: start; width: 100%; gap: 10px; padding: 12px 10px; text-align: left; background: transparent; border: 1px solid transparent; border-radius: 5px; }
.step-button:hover { background: var(--surface-soft); }
.step-button.selected { background: var(--accent-soft); border-color: var(--border); }
.step-number { width: 22px; height: 22px; flex: 0 0 22px; display: grid; place-items: center; color: #a0513d; background: #b7725712; border: 1px solid #b7725730; border-radius: 50%; font-size: 10px; }
.step-copy { display: flex; flex-direction: column; gap: 6px; flex: 1; min-width: 0; }
.step-copy strong { color: var(--text); font-size: 11px; font-weight: 500; line-height: 1.7; }
.step-copy > span { color: var(--muted); font-size: 10px; line-height: 1.7; }
.step-kind { font-size: 9px; color: var(--muted); white-space: nowrap; padding-top: 4px; }
.empty-steps { color: var(--muted); display: flex; align-items: center; gap: 12px; padding: 30px 20px; font-size: 11px; line-height: 1.8; }
.empty-steps svg { flex-shrink: 0; }
.analysis-notes { padding: 14px 18px; border-top: 1px solid var(--border); font-size: 10px; color: var(--muted); line-height: 1.8; }
.analysis-notes summary { cursor: pointer; color: var(--text); }
.analysis-notes summary span { float: right; color: var(--muted); }
.analysis-notes ul { padding-left: 17px; margin: 12px 0 0; }
.analysis-notes li + li { margin-top: 8px; }
.optimality-note { margin-top: 12px; }
.optimality-note p { margin-top: 8px; }
.optimality-tag { display: inline-block; border: 1px solid var(--border); border-radius: 4px; padding: 2px 5px; font-size: 9px; margin: 0 4px 4px 0; }
.inventory-filters { display: flex; gap: 8px; padding: 0 18px 14px; }
.inventory-filters select { width: 110px; min-height: 32px; font-size: 10px; }
.inventory-filters input { width: 0; flex: 1; min-height: 32px; font-size: 10px; }
.item-table-wrap { max-height: 370px; overflow: auto; scrollbar-width: thin; }
.item-table { width: 100%; border-collapse: collapse; font-size: 10px; }
.item-table th { text-align: left; background: var(--surface-soft); padding: 10px 18px; color: var(--muted); font-weight: 450; white-space: nowrap; position: sticky; top: 0; }
.item-table td { padding: 11px 18px; border-top: 1px solid var(--border); color: var(--text); }
.item-table td:first-child { line-height: 1.7; }
.item-table td small { display: block; font-size: 8px; color: var(--muted); opacity: .7; }
.item-table td:nth-child(2) { color: var(--muted); white-space: nowrap; }
.item-table td:last-child, .item-table th:last-child { text-align: right; font-variant-numeric: tabular-nums; }
.inventory-footnote { padding: 12px 18px; border-top: 1px solid var(--border); color: var(--muted); font-size: 9px; line-height: 1.8; }
.no-items { padding: 25px 18px; color: var(--muted); font-size: 11px; }
@media (max-width: 1180px) { .map-toolbar { flex-direction: column; align-items: start; } .map-controls { width: 100%; } .map-controls > .icon-button:first-of-type { margin-left: auto; } .result-summary { gap: 16px; padding: 14px; } .qi-summary strong { font-size: 20px; } .qi-summary > div { gap: 8px; } .details-grid { grid-template-columns: 1fr; } }
@media (max-width: 900px) { .tool-description { max-width: 270px; } .tool-heading h2 { font-size: 22px; } .result-summary { flex-wrap: wrap; } .qi-summary { flex-basis: 100%; } .move-stat { display: flex; flex: 1; gap: 6px; } .move-stat strong { font-size: 16px; } .pane-heading > span:last-child { display: none; } .map-panes { min-height: 340px; height: 53vh; } .map-footer { flex-wrap: wrap; } .source-date { display: block; margin: 3px 0 0; font-size: 9px; } .segmented button { padding: 5px 7px; } }
</style>
