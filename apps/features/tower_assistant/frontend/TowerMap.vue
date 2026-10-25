<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, useId } from 'vue';
import { coordinates, coreCells, ENTITY_STYLE, entitiesAtPoint, entityCenter, entitySecondaryLabel, entityShortLabel, groupedStepMarkers, itemForEntity, mapGeometry, visibleEntities } from './map';
import type { Bounds, Entity, MigrationStep, SpiritItem, VisualCatalog } from './types';
import SpriteIcon from './SpriteIcon.vue';

const props = withDefaults(defineProps<{
  entities: Entity[];
  bounds: Bounds;
  platformId: string;
  steps?: MigrationStep[];
  items?: SpiritItem[];
  itemRanks?: Record<string, number>;
  after?: boolean;
  arrows?: boolean;
  selectedStep?: string;
  selectedEntityId?: string;
  visuals?: VisualCatalog | null;
  zoom?: number;
  label: string;
}>(), { steps: () => [], items: () => [], after: false, arrows: false, selectedStep: '', selectedEntityId: '', visuals: null, zoom: 1 });
const emit = defineEmits<{ select: [stepId: string]; selectEntity: [entityId: string] }>();
const uid = useId().replace(/[^a-zA-Z0-9-]/g, '');
const hovered = ref<Entity | null>(null);
const scrollElement = ref<HTMLElement | null>(null);
const viewport = ref({ width: 0, height: 0 });
let resizeObserver: ResizeObserver | undefined;
const geometry = computed(() => mapGeometry(props.bounds));
const renderSize = computed(() => {
  const fit = Math.min((viewport.value.width || geometry.value.width) / geometry.value.width,
    (viewport.value.height || geometry.value.height) / geometry.value.height);
  return { width: geometry.value.width * fit * props.zoom, height: geometry.value.height * fit * props.zoom };
});
onMounted(() => {
  if (!scrollElement.value) return;
  const measure = () => {
    if (!scrollElement.value) return;
    const width = scrollElement.value.clientWidth;
    const height = scrollElement.value.clientHeight;
    if (viewport.value.width !== width || viewport.value.height !== height) viewport.value = { width, height };
  };
  resizeObserver = new ResizeObserver(measure);
  resizeObserver.observe(scrollElement.value);
  measure();
});
onUnmounted(() => resizeObserver?.disconnect());
const entities = computed(() => visibleEntities(props.entities, props.bounds));
const platform = computed(() => props.entities.find((entity) => entity.id === props.platformId));
const gridWidth = computed(() => geometry.value.width - geometry.value.padding * 2);
const gridHeight = computed(() => geometry.value.height - geometry.value.padding * 2);
const columns = computed(() => Array.from({ length: Math.floor(props.bounds.maxX - props.bounds.minX) + 1 }, (_, index) => props.bounds.minX + index));
const rows = computed(() => Array.from({ length: Math.floor(props.bounds.maxY - props.bounds.minY) + 1 }, (_, index) => props.bounds.minY + index));
const moves = computed(() => props.steps.filter((step) => step.kind === 'move' && step.from && step.to));
const markers = computed(() => groupedStepMarkers(props.steps, props.entities, props.after, props.selectedStep));
const stepEntityId = computed(() => props.steps.find((step) => step.id === props.selectedStep)?.entityId);
const cells = computed(() => coreCells(platform.value));
const itemName = (entity: Entity) => entity.kind === 'pedestal' ? props.items.find((item) => item.id === entity.itemId)?.name ?? '空台' : '';
const isChanged = (entity: Entity) => props.steps.some((step) => step.entityId === entity.id);
const overlapsAnother = (entity: Entity) => entitiesAtPoint(props.entities, entity).some((other) => other.id !== entity.id);
const containsPerson = (entity: Entity) => props.entities.some((person) => person.kind === 'person' && entitiesAtPoint([entity], person).length > 0);
const entityTitle = (entity: Entity) => `${entity.name} · ${coordinates(entity)}${entity.tier !== undefined ? ` · ${entity.tier} 级` : ''}${itemName(entity) ? ` · ${itemName(entity)}` : ''}${entity.kind === 'pedestal' && itemForEntity(entity, props.items) ? ` · 基础灵气 ${itemForEntity(entity, props.items)!.qi}` : ''}${entity.itemId && props.itemRanks?.[entity.itemId] ? ` · 全局第 ${props.itemRanks[entity.itemId]} 名` : ''} · 点击查看详情`;
function selectEntity(entity: Entity) {
  hovered.value = entity;
  emit('selectEntity', entity.id);
}
</script>

<template>
  <div class="tower-map">
    <div ref="scrollElement" class="map-scroll" tabindex="0" :aria-label="`${label}，可滚动查看放大后的地图`">
      <svg :viewBox="`0 0 ${geometry.width} ${geometry.height}`" :style="{ width: `${renderSize.width}px`, height: `${renderSize.height}px` }" role="group" :aria-label="label">
        <title>{{ label }}。{{ itemRanks ? '# 为全部灵物的基础灵气名次，点击对象在排行榜中选中。' : '点击对象查看详情，点击编号定位迁移步骤；' }}支持 Tab、Enter 和空格键。</title>
        <defs>
          <pattern :id="`${uid}-grid`" :width="geometry.cell" :height="geometry.cell" patternUnits="userSpaceOnUse" :x="geometry.padding" :y="geometry.padding">
            <path :d="`M ${geometry.cell} 0 L 0 0 0 ${geometry.cell}`" fill="none" class="grid-line" />
          </pattern>
          <marker :id="`${uid}-arrow`" markerWidth="7" markerHeight="7" refX="6" refY="3" orient="auto">
            <path d="M0,0 L6,3 L0,6" fill="none" stroke="#c35c44" stroke-width="1.5" />
          </marker>
          <clipPath :id="`${uid}-clip`"><rect :x="geometry.padding" :y="geometry.padding" :width="gridWidth" :height="gridHeight" /></clipPath>
        </defs>
        <rect :x="geometry.padding" :y="geometry.padding" :width="gridWidth" :height="gridHeight" class="plot-background" />
        <g :clip-path="`url(#${uid}-clip)`">
          <rect v-for="point in cells" :key="`${point.x}:${point.y}`" :x="geometry.x(point.x) - geometry.cell / 2" :y="geometry.y(point.y) - geometry.cell / 2" :width="geometry.cell" :height="geometry.cell" fill="#d5b45b35" stroke="#c8a451" stroke-width=".5" />
          <rect :x="geometry.padding" :y="geometry.padding" :width="gridWidth" :height="gridHeight" :fill="`url(#${uid}-grid)`" />
          <g v-for="entity in entities" :key="entity.id" class="map-entity" :class="{ 'selected-entity': selectedEntityId === entity.id, 'step-entity': stepEntityId === entity.id, 'is-person': entity.kind === 'person' }" role="button" tabindex="0" :aria-label="entityTitle(entity)" :aria-pressed="selectedEntityId === entity.id" @mouseenter="hovered = entity" @focus="hovered = entity" @click="selectEntity(entity)" @keydown.enter.prevent="selectEntity(entity)" @keydown.space.prevent="selectEntity(entity)">
            <title>{{ entityTitle(entity) }}</title>
            <template v-if="entity.kind === 'person'">
              <rect v-if="overlapsAnother(entity)" class="entity-shape person-background" :x="geometry.x(entity.x) - geometry.cell * .47" :y="geometry.y(entity.y) - geometry.cell * .47" :width="geometry.cell * .94" :height="geometry.cell * .37" rx="2" :fill="ENTITY_STYLE.person.fill" :stroke="isChanged(entity) ? '#c35c44' : ENTITY_STYLE.person.stroke" />
              <circle v-else class="entity-shape person-background" :cx="geometry.x(entity.x)" :cy="geometry.y(entity.y) - 2" :r="geometry.cell * .43" :fill="ENTITY_STYLE.person.fill" :stroke="isChanged(entity) ? '#c35c44' : ENTITY_STYLE.person.stroke" :stroke-width="isChanged(entity) ? 2 : 1" />
              <SpriteIcon :x="geometry.x(entity.x) - geometry.cell * (overlapsAnother(entity) ? .43 : .28)" :y="geometry.y(entity.y) - geometry.cell * .44" :size="geometry.cell * (overlapsAnother(entity) ? .32 : .56)" :icon-key="entity.iconKey" :visuals="visuals" :label="entity.name" fallback="person" aria-hidden="true" class="map-sprite" />
              <text :x="geometry.x(entity.x) + geometry.cell * (overlapsAnother(entity) ? .16 : 0)" :y="geometry.y(entity.y) + geometry.cell * (overlapsAnother(entity) ? -.19 : .36)" text-anchor="middle" class="person-label">{{ entityShortLabel(entity, items).slice(0, overlapsAnother(entity) ? 2 : 4) }}</text>
            </template>
            <template v-else>
              <rect class="entity-shape" :x="geometry.x(entityCenter(entity).x) - entity.width * geometry.cell / 2 + 1" :y="geometry.y(entityCenter(entity).y) - entity.height * geometry.cell / 2 + 1" :width="entity.width * geometry.cell - 2" :height="entity.height * geometry.cell - 2" rx="3" :fill="ENTITY_STYLE[entity.kind].fill" :stroke="isChanged(entity) ? '#c35c44' : ENTITY_STYLE[entity.kind].stroke" :stroke-width="isChanged(entity) ? 2.5 : 1" />
              <template v-if="entity.kind === 'pedestal' && itemForEntity(entity, items)">
                <SpriteIcon :x="geometry.x(entity.x) - 13" :y="geometry.y(entity.y) + (containsPerson(entity) ? 0 : -9)" :size="containsPerson(entity) ? 7 : 9" :icon-key="itemForEntity(entity, items)?.iconKey" :visuals="visuals" :label="itemName(entity)" fallback="item" aria-hidden="true" class="map-sprite" />
                <text :x="geometry.x(entity.x) + 4" :y="geometry.y(entity.y) + (containsPerson(entity) ? 5 : -1)" text-anchor="middle" class="entity-label pedestal-label">{{ entityShortLabel(entity, items).slice(0, 3) }}</text>
                <text :x="geometry.x(entity.x)" :y="geometry.y(entity.y) + (containsPerson(entity) ? 13 : 10)" text-anchor="middle" class="qi-label">{{ entitySecondaryLabel(entity, items) }}</text>
                <text v-if="entity.itemId && itemRanks?.[entity.itemId]" :x="geometry.x(entity.x)" :y="geometry.y(entity.y) - 11" text-anchor="middle" class="rank-label">#{{ itemRanks[entity.itemId] }}</text>
              </template>
              <text v-else :x="geometry.x(entityCenter(entity).x)" :y="geometry.y(entityCenter(entity).y) + 3.5" text-anchor="middle" class="entity-label" :class="{ 'compact-label': entity.width === 1 }">{{ entityShortLabel(entity, items) }}</text>
            </template>
          </g>
          <g v-if="arrows" class="arrows">
            <path v-for="step in moves" :key="step.id" :d="`M ${geometry.x(step.from!.x)} ${geometry.y(step.from!.y)} L ${geometry.x(step.to!.x)} ${geometry.y(step.to!.y)}`" fill="none" stroke="#c35c44" :stroke-width="selectedStep === step.id ? 3 : 1.5" :opacity="selectedStep ? (selectedStep === step.id ? 1 : .15) : .5" :marker-end="`url(#${uid}-arrow)`" />
          </g>
          <g v-for="marker in markers" :key="marker.step.id" class="step-marker" role="button" tabindex="0" :aria-label="`步骤 ${marker.index + 1}：${marker.step.name}`" @click="emit('select', marker.step.id)" @keydown.enter="emit('select', marker.step.id)" @keydown.space.prevent="emit('select', marker.step.id)">
            <title>步骤 {{ marker.indices.map((index) => index + 1).join('、') }}</title>
            <circle :cx="geometry.x(marker.point!.x) + geometry.cell * .32" :cy="geometry.y(marker.point!.y) - geometry.cell * .32" r="8.5" :fill="selectedStep === marker.step.id ? '#7d3828' : '#ad5944'" :stroke="selectedStep === marker.step.id ? '#fff' : 'none'" stroke-width="2" />
            <text :x="geometry.x(marker.point!.x) + geometry.cell * .32" :y="geometry.y(marker.point!.y) - geometry.cell * .32 + 3" text-anchor="middle" fill="white" font-size="8.5">{{ marker.index + 1 }}{{ marker.indices.length > 1 ? '+' : '' }}</text>
          </g>
        </g>
        <g class="axis-labels">
          <text v-for="column in columns" :key="`x${column}`" :x="geometry.x(column)" y="17" text-anchor="middle">{{ column }}</text>
          <text v-for="row in rows" :key="`y${row}`" x="15" :y="geometry.y(row) + 3" text-anchor="middle">{{ row }}</text>
        </g>
      </svg>
    </div>
    <p class="map-hover" aria-live="polite"><template v-if="hovered"><strong>{{ hovered.name }}</strong> {{ coordinates(hovered) }}<span v-if="hovered.tier"> · {{ hovered.tier }} 级</span><span v-if="itemName(hovered)"> · {{ itemName(hovered) }}</span><span v-if="!hovered.movable"> · 不可搬动</span></template><template v-else>点击对象查看详情 · 灵物下方数字为基础灵气 · {{ itemRanks ? '# 为全局名次' : '编号带 + 表示多步骤' }}</template></p>
  </div>
</template>

<style scoped>
.tower-map { min-width: 0; display: flex; flex-direction: column; height: 100%; }
.rank-label { font-size: 6.5px; fill: var(--accent); font-weight: 700; paint-order: stroke; stroke: var(--surface); stroke-width: 1.5px; }
.map-scroll { min-height: 0; flex: 1; overflow: auto; scrollbar-width: thin; }
svg { display: block; margin-inline: auto; }
.grid-line { stroke: var(--border); stroke-width: .8; }
.plot-background { fill: var(--surface-soft); stroke: var(--border); }
.entity-label { fill: #243149; font-size: 10px; pointer-events: none; }
.compact-label { font-size: 7.2px; }
.pedestal-label { font-size: 6.3px; }
.qi-label { fill: #2b6252; font-size: 7.5px; font-weight: 600; pointer-events: none; }
.person-label { fill: #512f1c; font-size: 6.3px; font-weight: 550; pointer-events: none; }
.map-sprite { pointer-events: none; color: #744628; }
.axis-labels { fill: var(--muted); font-size: 9px; }
.map-entity, .step-marker { cursor: pointer; }
.step-entity > .entity-shape { stroke: #8d3c2a; stroke-width: 3; }
.selected-entity > .entity-shape { stroke: #285e96; stroke-width: 3.2; }
.map-entity:focus { outline: none; }
.map-entity:focus > .entity-shape { stroke: #285e96; stroke-width: 3; }
.arrows { pointer-events: none; }
.map-hover { min-height: 30px; padding: 8px 14px 5px; border-top: 1px solid var(--border); font-size: 10px; color: var(--muted); }
.map-hover strong { color: var(--text); font-weight: 550; }
.step-marker:focus { outline: none; }
.step-marker:focus circle { stroke: #263e59; stroke-width: 3; }
</style>
