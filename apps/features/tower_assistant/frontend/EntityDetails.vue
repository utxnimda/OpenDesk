<script setup lang="ts">
import { computed } from 'vue';
import { ArrowRight, X } from '@lucide/vue';
import { coordinates, ENTITY_STYLE, formatQi, itemForEntity } from './map';
import type { Entity, SpiritItem, VisualCatalog } from './types';
import SpriteIcon from './SpriteIcon.vue';

const props = withDefaults(defineProps<{
  before?: Entity;
  after?: Entity;
  items: SpiritItem[];
  visuals?: VisualCatalog | null;
  hasPlan: boolean;
  neighbours?: Entity[];
  selectedSide?: 'before' | 'after';
}>(), { visuals: null, neighbours: () => [], selectedSide: 'before' });
const emit = defineEmits<{ close: []; select: [entityId: string] }>();
const entity = computed(() => (props.selectedSide === 'after' ? props.after : props.before) ?? props.before ?? props.after);
const currentItem = computed(() => props.before ? itemForEntity(props.before, props.items) : undefined);
const plannedItem = computed(() => props.after ? itemForEntity(props.after, props.items) : undefined);
const positionChanged = computed(() => props.hasPlan && props.before && props.after && (props.before.x !== props.after.x || props.before.y !== props.after.y));
const itemChanged = computed(() => props.hasPlan && props.before?.itemId !== props.after?.itemId);
const itemCards = computed(() => itemChanged.value ? [
  { id: 'before', label: '当前灵物', item: currentItem.value },
  { id: 'after', label: '建议灵物', item: plannedItem.value },
] : [{ id: 'current', label: props.hasPlan ? '灵物保持不变' : '当前灵物', item: currentItem.value }]);
const equipment = computed(() => {
  if (entity.value?.equipment?.length) return entity.value.equipment;
  return entity.value?.weapon ? [{ ...entity.value.weapon, slot: entity.value.weapon.slot ?? '武器' }] : [];
});
const attributeOrder = ['金', '木', '水', '火', '土'];
</script>

<template>
  <section v-if="entity" class="entity-details" aria-label="对象详情" @keydown.esc.stop="emit('close')">
    <header class="entity-heading">
      <SpriteIcon :icon-key="entity.iconKey" :visuals="visuals" :label="entity.name" :size="38" :fallback="entity.kind === 'person' ? 'person' : 'building'" />
      <div><span class="entity-kind">{{ ENTITY_STYLE[entity.kind].label }} · 对象详情</span><h3>{{ entity.name }}<span v-if="entity.kind === 'person' && entity.job">{{ entity.job }}</span></h3></div>
      <span class="entity-identity">{{ entity.id }}</span>
      <button class="close-details" aria-label="关闭对象详情" title="关闭详情（Esc）" @click="emit('close')"><X :size="17" /></button>
    </header>

    <div v-if="neighbours.length > 1" class="neighbour-picker" aria-label="同格对象切换"><span>{{ selectedSide === 'after' ? '建议位置' : '当前位置' }}的同格对象</span><button v-for="neighbour in neighbours" :key="neighbour.id" :class="{ selected: neighbour.id === entity.id }" :aria-pressed="neighbour.id === entity.id" @click="emit('select', neighbour.id)"><SpriteIcon :icon-key="neighbour.iconKey" :visuals="visuals" :label="neighbour.name" :size="18" :fallback="neighbour.kind === 'person' ? 'person' : 'building'" />{{ neighbour.name }}</button></div>

    <div class="detail-body" :class="{ 'person-details': entity.kind === 'person' }">
      <div class="object-facts">
        <div class="position-comparison"><div><span>当前位置</span><strong>{{ before ? coordinates(before) : '无' }}</strong></div><ArrowRight v-if="hasPlan" :size="16" /><div v-if="hasPlan"><span>建议位置</span><strong :class="{ changed: positionChanged }">{{ after ? coordinates(after) : '无' }}</strong></div><small v-if="hasPlan">{{ positionChanged ? '需要搬动' : '位置不变' }}</small></div>
        <dl class="entity-facts"><template v-if="entity.tier !== undefined"><dt>等级</dt><dd>{{ entity.tier }}</dd></template><template v-if="entity.kind !== 'person'"><dt>占地</dt><dd>{{ entity.width }} × {{ entity.height }} 格</dd></template><template v-if="entity.kind === 'person'"><dt>当前工作</dt><dd>{{ entity.job || '未记录' }}</dd><dt v-if="entity.profession">职业</dt><dd v-if="entity.profession">{{ entity.profession }}</dd><dt v-if="entity.appearance">外观</dt><dd v-if="entity.appearance">{{ entity.appearance }}</dd></template><dt>方案约束</dt><dd>{{ entity.movable ? '允许搬动' : '保持原位' }}</dd></dl>
        <p v-if="entity.lockedReason" class="locked-reason">{{ entity.lockedReason }}</p>
        <p v-if="entity.professionDescription" class="profession-description">{{ entity.professionDescription }}</p>
        <ul v-if="entity.detailWarnings?.length" class="detail-warnings"><li v-for="(warning, index) in entity.detailWarnings" :key="index">{{ warning }}</li></ul>
        <div v-if="entity.kind === 'person'" class="equipment-section"><h4>当前穿戴</h4><ul v-if="equipment.length" class="equipment-list"><li v-for="(entry, index) in equipment" :key="`${entry.slot}:${index}`"><SpriteIcon :icon-key="entry.iconKey" :visuals="visuals" :label="entry.name" :size="28" fallback="item" /><div><strong>{{ entry.name }}</strong><span>{{ entry.slot || '装备' }}{{ entry.type ? ` · ${entry.type}` : '' }}{{ entry.level !== undefined ? ` · ${entry.level} 级` : '' }}{{ entry.qualityName ? ` · ${entry.qualityName}` : '' }}</span></div></li></ul><p v-else class="no-equipment">存档中未记录穿戴信息。</p></div>
      </div>

      <div v-if="entity.kind === 'pedestal'" class="spirit-comparison" :class="{ 'has-change': itemChanged }">
        <article v-for="card in itemCards" :key="card.id" class="spirit-card" :class="{ 'planned-card': card.id === 'after' }"><p class="spirit-card-label">{{ card.label }}</p><template v-if="card.item"><div class="spirit-heading"><SpriteIcon :icon-key="card.item.iconKey" :visuals="visuals" :label="card.item.name" :size="34" fallback="item" /><div><h4>{{ card.item.name }}</h4><span v-if="card.item.level !== undefined">{{ card.item.level }} 级</span><span v-if="card.item.qualityName || card.item.quality !== undefined">{{ card.item.qualityName || `品质 ${card.item.quality}` }}</span></div><div class="spirit-qi"><strong>{{ formatQi(card.item.qi) }}</strong><span>基础灵气</span></div></div><div v-if="card.item.attributes" class="attribute-grid"><div v-for="attribute in attributeOrder" :key="attribute"><span>{{ attribute }}</span><strong>{{ card.item.attributes[attribute] === undefined ? '—' : formatQi(card.item.attributes[attribute]) }}</strong></div></div><p class="item-identity">{{ card.item.location === 'warehouse' ? '来源：仓库' : '来源：城中已放置' }} · {{ card.item.id }}</p></template><p v-else class="empty-pedestal">空台 · 未放置灵物</p></article>
      </div>
      <p v-else-if="entity.kind !== 'person'" class="object-note">地图展示的是此对象在存档中的占地。可在迁移步骤中查看本次需要执行的调整。</p>
    </div>
  </section>
</template>

<style scoped>
.entity-details { background: var(--surface); border-top: 1px solid var(--border); }
.entity-heading { display: flex; align-items: center; gap: 11px; padding: 15px 18px; }
.entity-kind { color: var(--muted); font-size: 9px; }
.entity-heading h3 { font-size: 15px; font-weight: 550; margin-top: 4px; }
.entity-heading h3 span { margin-left: 10px; font-size: 10px; font-weight: 400; color: var(--muted); }
.entity-identity { margin-left: auto; font-size: 10px; color: var(--muted); }
.close-details { display: grid; place-items: center; width: 28px; height: 28px; color: var(--muted); background: transparent; border: 1px solid var(--border); border-radius: 5px; }
.close-details:hover { color: var(--text); background: var(--surface-soft); }
.neighbour-picker { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; padding: 0 18px 13px; }
.neighbour-picker > span { color: var(--muted); font-size: 10px; margin-right: 3px; }
.neighbour-picker button { display: flex; align-items: center; gap: 5px; color: var(--text); background: var(--surface-soft); border: 1px solid var(--border); border-radius: 5px; padding: 5px 8px; font-size: 10px; }
.neighbour-picker button.selected { color: var(--accent); background: var(--accent-soft); border-color: var(--accent); }
.detail-body { display: grid; grid-template-columns: minmax(220px, .7fr) minmax(0, 1.7fr); gap: 24px; padding: 0 18px 18px; }
.detail-body.person-details { grid-template-columns: 1fr; }
.person-details .entity-facts { grid-template-columns: auto 1fr auto 1fr; }
.position-comparison { display: flex; align-items: center; gap: 13px; padding: 11px 12px; background: var(--surface-soft); border-radius: 6px; }
.position-comparison > div { display: flex; flex-direction: column; gap: 5px; }
.position-comparison span, .position-comparison small { font-size: 9px; color: var(--muted); }
.position-comparison strong { font-size: 12px; font-weight: 500; font-variant-numeric: tabular-nums; }
.position-comparison strong.changed { color: var(--success-text); }
.position-comparison svg { color: var(--muted); }
.position-comparison small { margin-left: auto; white-space: nowrap; }
.entity-facts { display: grid; grid-template-columns: auto 1fr; gap: 8px 14px; font-size: 10px; margin: 14px 2px 0; line-height: 1.7; }
.entity-facts dt { color: var(--muted); }
.entity-facts dd { margin: 0; color: var(--text); }
.locked-reason { margin: 10px 0 0; color: var(--muted); font-size: 10px; line-height: 1.8; }
.profession-description { margin-top: 10px; color: var(--muted); font-size: 10px; line-height: 1.8; max-width: 700px; }
.detail-warnings { margin: 10px 0 0; padding-left: 17px; color: var(--error-text); font-size: 10px; line-height: 1.8; }
.equipment-section { margin-top: 15px; }
.equipment-section h4 { font-size: 11px; font-weight: 550; }
.equipment-list { list-style: none; margin: 9px 0 0; padding: 0; display: flex; flex-wrap: wrap; gap: 12px 20px; }
.equipment-list li { display: flex; gap: 8px; align-items: center; }
.equipment-list li > div { display: flex; flex-direction: column; gap: 5px; }
.equipment-list strong { font-size: 11px; font-weight: 500; }
.equipment-list span, .no-equipment { font-size: 10px; color: var(--muted); }
.no-equipment { margin-top: 9px; }
.spirit-comparison { display: grid; grid-template-columns: 1fr; gap: 12px; }
.spirit-comparison.has-change { grid-template-columns: 1fr 1fr; }
.spirit-card { border: 1px solid var(--border); border-radius: 7px; padding: 12px 13px; }
.planned-card { border-color: var(--success-text); }
.spirit-card-label { color: var(--muted); font-size: 10px; margin-bottom: 12px; }
.spirit-heading { display: flex; align-items: center; gap: 9px; }
.spirit-heading h4 { font-size: 12px; font-weight: 550; line-height: 1.7; }
.spirit-heading > div > span { color: var(--muted); font-size: 9px; margin-right: 8px; }
.spirit-qi { display: flex; flex-direction: column; gap: 5px; margin-left: auto; text-align: right; }
.spirit-qi strong { color: var(--success-text); font-size: 19px; font-weight: 550; }
.spirit-qi span { font-size: 9px; color: var(--muted); white-space: nowrap; }
.attribute-grid { display: grid; grid-template-columns: repeat(5,1fr); gap: 5px; margin-top: 15px; }
.attribute-grid > div { display: flex; flex-direction: column; gap: 5px; padding: 8px 5px; background: var(--surface-soft); border-radius: 4px; text-align: center; }
.attribute-grid span { color: var(--muted); font-size: 9px; }
.attribute-grid strong { color: var(--text); font-size: 11px; font-weight: 500; font-variant-numeric: tabular-nums; }
.item-identity { color: var(--muted); font-size: 9px; margin-top: 13px; }
.empty-pedestal { color: var(--muted); font-size: 12px; padding: 17px 0; }
.object-note { color: var(--muted); font-size: 11px; line-height: 1.8; align-self: start; padding-top: 12px; }
@media (max-width: 1100px) { .detail-body { grid-template-columns: 1fr; gap: 18px; } .entity-facts { grid-template-columns: auto 1fr auto 1fr; } }
@media (max-width: 820px) { .spirit-comparison.has-change { grid-template-columns: 1fr; } .entity-identity { display: none; } .close-details { margin-left: auto; } }
</style>
