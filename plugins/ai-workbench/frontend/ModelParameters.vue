<script setup lang="ts">
import type { ModelParameter } from './types';
const props = defineProps<{ parameters: ModelParameter[]; values: Record<string, string>; disabled: boolean }>();
const emit = defineEmits<{ select: [id: string, value: string] }>();
function help(parameter: ModelParameter) { return parameter.options.find(item => item.value === props.values[parameter.id])?.description ?? parameter.description ?? ''; }
function choose(parameter: ModelParameter, event: Event) {
  const select = event.target as HTMLSelectElement;
  const value = select.value;
  select.value = props.values[parameter.id] ?? '';
  if (parameter.options.some(item => item.value === value)) emit('select', parameter.id, value);
}
</script>

<template>
  <div v-if="parameters.length" class="model-parameters" aria-label="模型参数"><label v-for="parameter in parameters" :key="parameter.id" :title="help(parameter)"><span>{{ parameter.label }}</span><select :value="values[parameter.id] ?? ''" :aria-label="parameter.label" :disabled="disabled" @change="choose(parameter, $event)"><option value="" disabled>跟随原设置</option><option v-for="option in parameter.options" :key="option.value" :value="option.value" :title="option.description">{{ option.label }}</option></select></label><small v-for="parameter in parameters.filter(item => help(item))" :key="`${parameter.id}-help`" class="parameter-help">{{ help(parameter) }}</small></div>
</template>
