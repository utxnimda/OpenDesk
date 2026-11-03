<script setup lang="ts">
import { computed, ref, watch } from 'vue';

const props = defineProps<{ name: string; src?: string | null; fallback?: string }>();
const emit = defineEmits<{unavailable: [src: string]}>();
const failed = ref('');
const failedFallback = ref('');
watch(() => props.src, () => { failed.value = ''; });
watch(() => props.fallback, () => { failedFallback.value = ''; });
const image = computed(() => {
  if (props.src?.startsWith('data:image/png;base64,') && props.src !== failed.value) return props.src;
  if (props.fallback?.startsWith('data:image/png;base64,') && props.fallback !== failedFallback.value) return props.fallback;
});
function unavailable(event: Event) {
  const src = (event.currentTarget as HTMLImageElement).src;
  if (src === props.src) { failed.value = src; emit('unavailable', src); }
  else if (src === props.fallback) failedFallback.value = src;
}
const initial = computed(() => Array.from(props.name.trim())[0] ?? '伴');
</script>

<template>
  <span class="vp-avatar" aria-hidden="true">
    <img v-if="image" :key="image" :src="image" alt="" @error="unavailable" />
    <template v-else>{{ initial }}</template>
  </span>
</template>
