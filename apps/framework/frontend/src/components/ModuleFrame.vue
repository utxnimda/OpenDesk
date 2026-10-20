<script setup lang="ts">
import { computed, defineAsyncComponent } from 'vue';
import type { ModuleDefinition, ModuleServices } from '../core/modules';
import ModuleLoading from './ModuleLoading.vue';
import ModuleError from './ModuleError.vue';

const props = defineProps<{ definition: ModuleDefinition; services: ModuleServices }>();
const page = computed(() => defineAsyncComponent({
  loader: props.definition.load,
  loadingComponent: ModuleLoading,
  errorComponent: ModuleError,
  delay: 120,
  timeout: 30_000,
}));
</script>

<template>
  <component :is="page" :services="services" />
</template>
