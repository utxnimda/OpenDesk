<script setup lang="ts">
import { computed } from 'vue';
import type { ModelOption } from './types';

const props = defineProps<{ model: string; models: ModelOption[]; disabled?: boolean; loading?: boolean }>();
const emit = defineEmits<{ select: [model: string] }>();
const options = computed(() => props.model && !props.models.some(item => item.id === props.model)
  ? [{ id: props.model, label: `${props.model}（当前）` }, ...props.models] : props.models);

function selectModel(event: Event) {
  const select = event.target as HTMLSelectElement;
  const value = select.value;
  // Keep the confirmed model visible until the parent accepts the change.
  select.value = props.model;
  emit('select', value);
}
</script>

<template>
  <label class="model-choice"><span class="sr-only">模型</span><select :value="model" :disabled="disabled" aria-label="选择模型" @change="selectModel"><option value="" disabled>{{ loading ? '正在读取模型' : '选择模型' }}</option><option v-for="option in options" :key="option.id" :value="option.id">{{ option.label || option.id }}</option></select></label>
</template>
