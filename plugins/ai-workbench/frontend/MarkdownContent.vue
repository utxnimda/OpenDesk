<script setup lang="ts">
import type { MarkdownNode } from './markdown';
import MessageCode from './MessageCode.vue';
defineProps<{ nodes: MarkdownNode[] }>();
</script>

<template>
  <template v-for="(node, index) in nodes" :key="index">
    <template v-if="node.kind === 'text'">{{ node.text }}</template>
    <MessageCode v-else-if="node.kind === 'code'" :text="node.text" :language="node.language" />
    <div v-else-if="node.tag === 'table'" class="markdown-table-scroll" tabindex="0" role="region" aria-label="表格"><table><MarkdownContent :nodes="node.children" /></table></div>
    <component :is="node.tag" v-else v-bind="node.attributes"><MarkdownContent :nodes="node.children" /></component>
  </template>
</template>
