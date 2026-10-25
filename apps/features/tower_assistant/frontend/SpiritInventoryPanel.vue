<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import { Crosshair, Gem, Search, X } from '@lucide/vue';
import { formatQi } from './map';
import { filterSpirits, rankSpirits, spiritContribution, spiritForEntity, spiritMapBounds, spiritPedestal, type SpiritLocationFilter } from './spiritInventory';
import SpriteIcon from './SpriteIcon.vue';
import TowerMap from './TowerMap.vue';
import type { Snapshot, SpiritItem, VisualCatalog } from './types';

const props = defineProps<{ snapshot: Snapshot; visuals: VisualCatalog | null; visualsPending?: boolean; visualsFailure?: string }>();
const location = ref<SpiritLocationFilter>('all');
const search = ref('');
const selectedId = ref('');
const platformId = ref('');
const focusSelected = ref(false);
const zoom = ref(1);
const rowElements = new Map<string, HTMLElement>();
const rows = computed(() => rankSpirits(props.snapshot.items));
const shown = computed(() => filterSpirits(rows.value, location.value, search.value));
const rankByItem = computed(() => Object.fromEntries(rows.value.map((row) => [row.item.id, row.rank])));
const warehouseCount = computed(() => props.snapshot.items.filter((item) => item.location === 'warehouse').length);
const platforms = computed(() => props.snapshot.entities.filter((entry) => entry.kind === 'platform'));
const receivingPlatform = computed(() => platforms.value.find((entry) => entry.id === platformId.value) ?? platforms.value[0]);
const selected = computed(() => rows.value.find((row) => row.item.id === selectedId.value));
const selectedPedestal = computed(() => selected.value && spiritPedestal(selected.value.item, props.snapshot.entities));
const mapEntities = computed(() => props.snapshot.entities.filter((entry) => entry.kind === 'pedestal' || entry.kind === 'platform'));
const bounds = computed(() => spiritMapBounds(props.snapshot, focusSelected.value ? selectedPedestal.value : undefined));
const sourceTime = computed(() => new Date(props.snapshot.source.savedAt).toLocaleString('zh-CN', { hour12: false }));
const elements = ['金', '木', '水', '火', '土'];

function number(value?: number) { return value === undefined ? '未知' : formatQi(value); }
function placeLabel(item: SpiritItem) {
  if (item.location === 'warehouse') return '仓库';
  const pedestal = spiritPedestal(item, props.snapshot.entities);
  return pedestal ? `已摆 · (${pedestal.x}, ${pedestal.y})` : '已摆 · 坐标未提供';
}
function contributionLabel(item: SpiritItem) {
  if (item.location === 'warehouse') return '未摆放';
  if (!receivingPlatform.value) return '无渡劫台';
  return number(spiritContribution(item, receivingPlatform.value.id));
}
function selectItem(id: string) {
  selectedId.value = id;
  focusSelected.value = Boolean(selectedPedestal.value);
  zoom.value = 1;
}
async function selectMapEntity(id: string) {
  const item = spiritForEntity(id, props.snapshot);
  if (!item) return;
  selectedId.value = item.id;
  location.value = 'all';
  search.value = '';
  await nextTick();
  rowElements.get(item.id)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}
function setRowElement(id: string, value: unknown) {
  if (value && typeof (value as HTMLElement).scrollIntoView === 'function') rowElements.set(id, value as HTMLElement);
  else rowElements.delete(id);
}
watch(() => `${props.snapshot.source.id}:${props.snapshot.source.sha256}`, () => {
  selectedId.value = ''; platformId.value = ''; focusSelected.value = false; zoom.value = 1;
  location.value = 'all'; search.value = ''; rowElements.clear();
}, { flush: 'sync' });
</script>

<template>
  <section class="spirit-inventory" aria-label="灵物总览">
    <header class="overview-heading">
      <div><h3><Gem :size="19" />灵物总览 <span>{{ rows.length }} 件</span></h3><p>已摆放 {{ rows.length - warehouseCount }} 件 · 仓库 {{ warehouseCount }} 件</p></div>
      <div class="snapshot-label"><strong>{{ snapshot.source.name }}</strong><time :datetime="snapshot.source.savedAt">{{ sourceTime }}</time><span>存档快照 · 非实时数据</span></div>
    </header>
    <p class="ranking-explanation">按基础灵气比较全部灵物，同值并列排名。筛选和搜索保留全局名次；置物台加成与渡劫台贡献分别显示。</p>
    <div class="inventory-workspace">
      <section class="inventory-map" aria-label="已摆灵物地图">
        <header class="section-toolbar"><div><strong>城中已摆灵物</strong><span># 为全局基础灵气名次</span></div><button class="control" :disabled="!focusSelected" @click="focusSelected = false; zoom = 1"><Crosshair :size="13" />显示全图</button></header>
        <div v-if="mapEntities.length" class="map-container">
          <TowerMap :entities="mapEntities" :bounds="bounds" :platform-id="receivingPlatform?.id ?? ''" :items="snapshot.items" :item-ranks="rankByItem" :visuals="visuals" :selected-entity-id="selectedPedestal?.id ?? ''" :zoom="zoom" label="灵物全局排名地图" @select-entity="selectMapEntity" />
        </div>
        <div v-else class="empty-map">此存档没有城中置物台，仍可查看仓库灵物。</div>
        <footer class="map-footer"><label>地图缩放<input v-model.number="zoom" type="range" min="1" max="3" step=".25" /><span>{{ Math.round(zoom * 100) }}%</span></label><span v-if="visualsPending">正在读取图标…</span><span v-else-if="visualsFailure" class="visual-warning" role="status">{{ visualsFailure }}</span></footer>
      </section>

      <section class="ranking-panel" aria-label="全部灵物排行榜">
        <header class="section-toolbar"><div><strong>基础灵气排行</strong><span>显示 {{ shown.length }} / {{ rows.length }} 件</span></div></header>
        <div class="inventory-filters"><label>位置<select v-model="location" aria-label="灵物位置筛选"><option value="all">全部灵物</option><option value="placed">已摆放</option><option value="warehouse">仓库</option></select></label><label class="search"><Search :size="14" /><input v-model="search" aria-label="搜索灵物名称或编号" placeholder="搜索名称或编号" /></label></div>
        <div class="rank-list" aria-label="灵物排名列表">
          <button v-for="row in shown" :key="row.item.id" :ref="(element) => setRowElement(row.item.id, element)" class="rank-row" :class="{ selected: selectedId === row.item.id }" :aria-pressed="selectedId === row.item.id" :aria-label="`第${row.rank}名 ${row.item.name} 基础灵气 ${formatQi(row.item.qi)} ${placeLabel(row.item)}`" @click="selectItem(row.item.id)">
            <span class="rank-number" :class="{ podium: row.rank <= 3 }">#{{ row.rank }}</span>
            <SpriteIcon :icon-key="row.item.iconKey" :visuals="visuals" :label="row.item.name" :size="31" fallback="item" />
            <span class="item-summary"><span class="name-line"><strong>{{ row.item.name }}</strong><b>{{ formatQi(row.item.qi) }}</b></span><span class="item-meta">{{ placeLabel(row.item) }} · {{ row.item.qualityName ?? (row.item.quality === undefined ? '品质未知' : `品质 ${row.item.quality}`) }} · {{ row.item.level === undefined ? '等级未知' : `${row.item.level} 级` }}</span><span class="qi-track" aria-hidden="true"><i :style="{ width: `${row.relativeQi}%` }" /></span></span>
          </button>
          <p v-if="!shown.length" class="empty-list">{{ rows.length ? '没有符合条件的灵物。' : '此存档没有已摆放或仓库灵物。' }}</p>
        </div>
        <p class="list-help">柱条以最高基础灵气为 100%。点击已摆灵物可定位地图，仓库灵物直接显示详情。</p>
      </section>
    </div>

    <section v-if="selected" class="selected-spirit" aria-label="选中灵物详情">
      <header><div class="selected-heading"><SpriteIcon :icon-key="selected.item.iconKey" :visuals="visuals" :label="selected.item.name" :size="42" fallback="item" /><div><h4>{{ selected.item.name }} <span>全局第 {{ selected.rank }} 名</span></h4><p>{{ placeLabel(selected.item) }} · {{ selected.item.id }}</p></div></div><button class="control" aria-label="关闭灵物详情" @click="selectedId = ''; focusSelected = false"><X :size="16" /></button></header>
      <div class="qi-details">
        <dl><dt>基础灵气</dt><dd>{{ formatQi(selected.item.qi) }}</dd></dl>
        <dl><dt>当前置物台灵气<span v-if="selectedPedestal?.tier !== undefined"> · {{ selectedPedestal.tier }} 级台</span></dt><dd>{{ selected.item.location === 'warehouse' ? '未摆放' : number(selected.item.currentQi) }}</dd></dl>
        <dl><dt>对所选渡劫台的贡献</dt><dd>{{ contributionLabel(selected.item) }}</dd></dl>
        <label class="platform-selector">查看贡献的渡劫台<select v-if="platforms.length" v-model="platformId" aria-label="查看灵物贡献的渡劫台"><option value="">{{ platforms[0].name }} ({{ platforms[0].x }}, {{ platforms[0].y }})</option><option v-for="entry in platforms.slice(1)" :key="entry.id" :value="entry.id">{{ entry.name }} ({{ entry.x }}, {{ entry.y }})</option></select><span v-else>此存档没有渡劫台</span></label>
      </div>
      <div class="element-details"><span v-for="element in elements" :key="element">{{ element }} <strong>{{ number(selected.item.attributes?.[element]) }}</strong></span></div>
      <p class="detail-note">基础灵气是灵物自身数值，不等于五行之和。置物台灵气含当前台等级加成；范围外对所选渡劫台贡献为 0。仓库灵物没有当前摆放贡献。</p>
    </section>
    <p v-else class="selection-hint">点击地图或排行中的灵物，查看基础值、台加成、五行与渡劫台贡献。</p>
    <details v-if="snapshot.warnings.length" class="inventory-warnings"><summary>存档说明与限制</summary><ul><li v-for="warning in snapshot.warnings" :key="warning">{{ warning }}</li></ul></details>
  </section>
</template>

<style scoped>
.spirit-inventory { display: grid; gap: 15px; }
.overview-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 18px; padding: 4px 0; }
.overview-heading h3 { display: flex; align-items: center; gap: 9px; font-size: 20px; font-weight: 550; margin: 0; }
.overview-heading h3 > span { color: var(--muted); font-size: 12px; font-weight: 400; }
.overview-heading p { color: var(--muted); font-size: 12px; margin: 9px 0 0; }
.snapshot-label { display: grid; justify-items: end; gap: 4px; font-size: 11px; color: var(--muted); }
.snapshot-label strong { color: var(--text); font-weight: 500; }
.ranking-explanation, .selection-hint { font-size: 12px; line-height: 1.7; color: var(--muted); margin: 0; }
.inventory-workspace { display: grid; grid-template-columns: minmax(0,1.2fr) minmax(300px,1fr); gap: 15px; align-items: start; }
.inventory-map, .ranking-panel, .selected-spirit { min-width: 0; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); overflow: hidden; }
.section-toolbar { display: flex; justify-content: space-between; align-items: center; gap: 9px; padding: 14px; border-bottom: 1px solid var(--border); min-height: 58px; }
.section-toolbar > div { display: grid; gap: 4px; }
.section-toolbar strong { font-size: 12px; font-weight: 550; }
.section-toolbar span { font-size: 10px; color: var(--muted); }
.control { display: inline-flex; align-items: center; justify-content: center; gap: 5px; min-height: 29px; border: 1px solid var(--border); border-radius: 5px; background: var(--surface); color: var(--text); padding: 5px 8px; font-size: 10px; cursor: pointer; }
.control:hover { background: var(--surface-soft); }.control:disabled { opacity: .4; cursor: default; }
.map-container { height: 446px; padding: 10px 8px 0; }.empty-map { height: 446px; display: grid; place-items: center; padding: 25px; font-size: 12px; color: var(--muted); }
.map-footer { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; padding: 10px 14px; border-top: 1px solid var(--border); font-size: 10px; color: var(--muted); }
.map-footer label { display: flex; align-items: center; gap: 8px; }.map-footer input { width: 80px; accent-color: var(--accent); }.visual-warning { color: var(--danger, var(--text)); }
.inventory-filters { display: flex; align-items: end; gap: 10px; padding: 12px; }
.inventory-filters label { display: grid; gap: 5px; color: var(--muted); font-size: 10px; }
select, input { min-height: 32px; color: var(--text); border: 1px solid var(--border); border-radius: 5px; background: var(--surface); padding: 5px 8px; font: inherit; }
.inventory-filters .search { display: flex; align-items: center; position: relative; flex: 1; min-width: 0; }.search svg { position: absolute; left: 9px; pointer-events: none; }.search input { width: 100%; min-width: 0; padding-left: 30px; font-size: 11px; }
.rank-list { height: 365px; overflow: auto; padding: 0 7px 6px; scrollbar-width: thin; }
.rank-row { display: flex; align-items: center; width: 100%; gap: 9px; padding: 10px 7px; border: 1px solid transparent; border-bottom-color: var(--border); border-radius: 5px; background: transparent; color: var(--text); text-align: left; cursor: pointer; }
.rank-row:hover { background: var(--surface-soft); }.rank-row.selected { background: var(--accent-soft); border-color: var(--accent); }
.rank-number { flex: 0 0 34px; text-align: center; font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }.rank-number.podium { color: var(--accent); font-weight: 650; }
.item-summary { display: grid; gap: 5px; flex: 1; min-width: 0; }.name-line { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }.name-line strong { font-size: 12px; font-weight: 500; overflow-wrap: anywhere; }.name-line b { font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; }
.item-meta { font-size: 10px; color: var(--muted); line-height: 1.5; }.qi-track { display: block; height: 4px; border-radius: 3px; background: var(--surface-soft); overflow: hidden; }.qi-track i { display: block; height: 100%; background: var(--accent); border-radius: inherit; opacity: .7; }
.list-help { padding: 10px 14px; margin: 0; font-size: 10px; line-height: 1.6; color: var(--muted); border-top: 1px solid var(--border); }.empty-list { color: var(--muted); text-align: center; padding: 30px 10px; font-size: 12px; }
.selected-spirit { padding: 17px; }.selected-spirit > header { display: flex; justify-content: space-between; gap: 14px; }.selected-heading { display: flex; align-items: center; gap: 12px; }.selected-heading h4 { margin: 0; font-size: 16px; font-weight: 550; }.selected-heading h4 span { display: inline-block; color: var(--accent); font-size: 11px; margin-left: 8px; }.selected-heading p { font-size: 11px; color: var(--muted); margin: 5px 0 0; }
.qi-details { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)) minmax(160px,1fr); gap: 14px; margin: 20px 0; }.qi-details dl { margin: 0; padding-right: 10px; border-right: 1px solid var(--border); }.qi-details dt, .platform-selector { color: var(--muted); font-size: 10px; line-height: 1.6; }.qi-details dd { margin: 7px 0 0; font-size: 21px; font-variant-numeric: tabular-nums; }.platform-selector { display: grid; align-content: start; gap: 6px; }.platform-selector select { width: 100%; font-size: 11px; }
.element-details { display: flex; flex-wrap: wrap; gap: 12px; }.element-details span { min-width: 70px; color: var(--muted); font-size: 11px; }.element-details strong { margin-left: 6px; color: var(--text); font-weight: 500; }.detail-note { color: var(--muted); font-size: 11px; line-height: 1.7; margin: 15px 0 0; }
.inventory-warnings { color: var(--muted); font-size: 11px; line-height: 1.8; }.inventory-warnings summary { cursor: pointer; }.inventory-warnings ul { padding-left: 20px; }
button:focus-visible, input:focus-visible, select:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
@media(max-width: 900px) { .inventory-workspace { grid-template-columns: 1fr; }.qi-details { grid-template-columns: repeat(2,minmax(0,1fr)); }.overview-heading { flex-wrap: wrap; }.snapshot-label { justify-items: start; }.rank-list { height: 370px; } }
@media(max-width: 480px) { .qi-details { grid-template-columns: 1fr; }.qi-details dl { border-right: 0; }.map-container, .empty-map { height: 350px; } }
</style>
