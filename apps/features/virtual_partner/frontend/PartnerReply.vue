<script setup lang="ts">
import { computed, nextTick, ref } from 'vue';
import { renderPartnerMarkdown } from './markdown';
import './markdown.css';

const props = defineProps<{ text: string }>();
const html = computed(() => renderPartnerMarkdown(props.text));
const address = ref('');
const addressInput = ref<HTMLInputElement>();

async function showLink(event: MouseEvent) {
  const button = event.target instanceof Element && event.target.closest<HTMLButtonElement>('button.vp-md-link');
  if (!button || !button.dataset.address) return;
  address.value = button.dataset.address;
  await nextTick();
  addressInput.value?.focus();
  addressInput.value?.select();
}
</script>

<template>
  <div class="vp-reply">
    <div class="vp-markdown" @click="showLink" v-html="html"></div>
    <label v-if="address" class="vp-link-address vp-source-url">链接地址 · 按 Ctrl+C 复制
      <input ref="addressInput" :value="address" readonly spellcheck="false" @focus="addressInput?.select()" @click="addressInput?.select()" />
    </label>
  </div>
</template>
