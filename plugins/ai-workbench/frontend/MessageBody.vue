<script setup lang="ts">
import { computed } from 'vue';
import { markdownNodes } from './markdown';
import MarkdownContent from './MarkdownContent.vue';
import './markdown.css';
const props = defineProps<{ text: string }>();
const nodes = computed(() => markdownNodes(props.text));
</script>

<template>
  <div class="message-body markdown-body"><MarkdownContent :nodes="nodes" /></div>
</template>
