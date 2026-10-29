<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue';
import { Check, Copy } from '@lucide/vue';
import { highlightCode } from './codeHighlight';
import { copyCode } from './copyCode';

const props = defineProps<{ text: string; language: string }>();
// Each completed block keeps its computed result while later tokens stream in.
const highlighted = computed(() => highlightCode(props.text, props.language));
const copied = ref(false);
const copying = ref(false);
const error = ref('');
let resetTimer: ReturnType<typeof setTimeout> | undefined;
let mounted = true;
function clearCopiedState() {
  copied.value = false;
  if (resetTimer) clearTimeout(resetTimer);
  resetTimer = undefined;
}
watch(() => props.text, clearCopiedState);
async function copy() {
  if (copying.value) return;
  copying.value = true;
  clearCopiedState();
  error.value = '';
  const source = props.text;
  try {
    await copyCode(source);
    if (!mounted || source !== props.text) return;
    copied.value = true;
    if (resetTimer) clearTimeout(resetTimer);
    resetTimer = setTimeout(() => { copied.value = false; }, 2000);
  } catch { if (mounted) error.value = '复制失败，请选择代码后手动复制。'; }
  finally { if (mounted) copying.value = false; }
}
onUnmounted(() => { mounted = false; if (resetTimer) clearTimeout(resetTimer); });
</script>

<template>
  <div class="markdown-code-block">
    <div class="markdown-code-header"><span class="markdown-code-language">{{ language || '文本' }}</span><button type="button" class="markdown-copy-button" :disabled="copying" :aria-label="copied ? '代码已复制' : '复制代码'" @click="copy"><Check v-if="copied" /><Copy v-else /><span>{{ copied ? '已复制' : copying ? '复制中' : '复制' }}</span></button></div>
    <pre><code v-if="highlighted.html !== undefined" class="hljs" v-html="highlighted.html"></code><code v-else>{{ text }}</code></pre>
    <p v-if="highlighted.note" class="markdown-format-note">{{ highlighted.note }}</p>
    <p v-if="error" class="markdown-copy-error" role="status">{{ error }}</p>
  </div>
</template>
