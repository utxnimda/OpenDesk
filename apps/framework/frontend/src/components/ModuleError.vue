<script setup lang="ts">
import { errorMessage } from '../core/host';
defineProps<{ error: unknown }>();
</script>

<template>
  <div class="alert alert-error" role="alert">
    <strong>模块加载失败</strong>
    <span>{{ errorMessage(error) }}</span>
  </div>
</template>
