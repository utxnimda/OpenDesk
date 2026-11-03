<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { X } from '@lucide/vue';
defineProps<{ title: string; error: string }>();
const emit = defineEmits<{ close: [] }>();
const dialog = ref<HTMLDialogElement>();
onMounted(() => dialog.value?.showModal());
</script>

<template>
  <dialog ref="dialog" class="vp-dialog" :aria-label="title" @cancel.prevent="emit('close')">
    <header class="vp-dialog-head">
      <h2>{{ title }}</h2>
      <button type="button" class="vp-icon" aria-label="关闭" title="关闭" @click="emit('close')"><X :size="18" /></button>
    </header>
    <p v-if="error" class="vp-alert" role="alert">{{ error }}</p>
    <div class="vp-dialog-content"><slot /></div>
  </dialog>
</template>
