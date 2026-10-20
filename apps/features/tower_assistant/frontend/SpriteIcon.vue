<script setup lang="ts">
import { computed } from 'vue';
import type { VisualCatalog } from './types';
import { resolveSprite } from './map';

const props = withDefaults(defineProps<{
  iconKey?: string;
  visuals?: VisualCatalog | null;
  label: string;
  size?: number;
  fallback?: 'person' | 'item' | 'building';
}>(), { visuals: null, size: 24, fallback: 'building' });
const resolved = computed(() => resolveSprite(props.iconKey, props.visuals));
const sprite = computed(() => resolved.value?.frame);
const sheet = computed(() => resolved.value?.sheet);
const available = computed(() => Boolean(resolved.value));
const viewBox = computed(() => available.value && sprite.value ? `${sprite.value.x} ${sprite.value.y} ${sprite.value.width} ${sprite.value.height}` : '0 0 32 32');
</script>

<template>
  <svg class="sprite-icon" :width="size" :height="size" :viewBox="viewBox" preserveAspectRatio="xMidYMid meet" role="img" :aria-label="available ? label : `${label}（图标未提供）`">
    <title>{{ label }}{{ available ? '' : ' · 图标未提供' }}</title>
    <image v-if="available && sheet" :href="sheet.dataUrl" x="0" y="0" :width="sheet.width" :height="sheet.height" />
    <g v-else-if="fallback === 'person'" fill="currentColor"><circle cx="16" cy="10" r="5.5" /><path d="M6 28v-4a10 10 0 0 1 20 0v4z" /></g>
    <g v-else-if="fallback === 'item'" fill="none" stroke="currentColor" stroke-width="1.6"><path d="m16 3 11 10-11 16L5 13zM5 13h22M16 3l-4 10 4 16 4-16z" /></g>
    <g v-else fill="none" stroke="currentColor" stroke-width="1.6"><path d="m4 13 12-9 12 9M7 12v16h18V12M13 28v-9h6v9" /></g>
  </svg>
</template>

<style scoped>
.sprite-icon { display: inline-block; flex-shrink: 0; overflow: hidden; color: var(--accent); vertical-align: middle; image-rendering: pixelated; }
</style>
