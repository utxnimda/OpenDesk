<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue';
import type { LimitedHost } from '../../../framework/module-sdk/src/index';
import { errorMessage } from '../../../framework/module-sdk/src/index';
import { createRequestId } from './usePartner';
import PartnerForm from './PartnerForm.vue';

type Reply = {id: string; incoming: string; draft: string; status: 'pending'|'draft'|'skipped'|'failed'; note: string};
const props = defineProps<{host: LimitedHost; readId: string}>();
const opened = ref(false);
const entries = ref<Reply[]>([]);
const incoming = ref('');
const busy = ref(false);
const error = ref('');
let timer: ReturnType<typeof setTimeout> | undefined;
let revision = 0;
let retry: {text: string; id: string} | undefined;

async function refresh() {
  clearTimeout(timer);
  const seq = ++revision;
  try {
    const result = await props.host.callWorker<{entries: Reply[]}>('partner.outing.replies', {readId: props.readId});
    if (seq !== revision) return;
    entries.value = result.entries;
    error.value = '';
    if (result.entries.some(entry => entry.status === 'pending')) timer = setTimeout(refresh, 750);
  } catch (cause) { if (seq === revision) error.value = errorMessage(cause); }
}
function toggle(event: Event) {
  opened.value = (event.currentTarget as HTMLDetailsElement).open;
  if (opened.value) void refresh();
  else { clearTimeout(timer); revision++; }
}
async function submit() {
  if (busy.value || !incoming.value.trim()) return;
  const text = incoming.value.trim();
  busy.value = true;
  error.value = '';
  if (retry?.text !== text) retry = {text, id: createRequestId()};
  try {
    await props.host.callWorker('partner.outing.reply', {readId: props.readId, requestId: retry.id, incoming: text});
    incoming.value = '';
    retry = undefined;
    if (opened.value) await refresh();
  } catch (cause) { error.value = errorMessage(cause); }
  finally { busy.value = false; }
}
function selectDraft(event: Event) { (event.currentTarget as HTMLTextAreaElement).select(); }
onBeforeUnmount(() => { opened.value = false; revision++; clearTimeout(timer); });
</script>

<template>
  <details class="vp-reply-notes" @toggle="toggle">
    <summary>针对一条来评拟回复</summary>
    <p class="vp-hint">把这条视频下你想回应的原话贴在这里。伙伴只根据原话和视频介绍拟稿；你可以复制到 B 站发表。</p>
    <PartnerForm class="vp-form" @confirm="submit">
      <label>对方评论<textarea v-model="incoming" maxlength="800" rows="2" placeholder="粘贴评论原文" /></label>
      <button class="vp-subtle" type="submit" :disabled="busy || !incoming.trim() || entries.some(e => e.status === 'pending')">拟一条回复</button>
    </PartnerForm>
    <p v-if="error" class="vp-alert" role="alert">{{ error }} <button type="button" class="vp-mini" @click="refresh">刷新记录</button></p>
    <article v-for="entry in entries" :key="entry.id">
      <p class="vp-pre">来评（你提供的） · {{ entry.incoming }}</p>
      <template v-if="entry.status === 'draft'">
        <label>回复草稿 · 未发布<textarea :value="entry.draft" rows="2" readonly @click="selectDraft" @focus="selectDraft" /></label>
      </template>
      <p v-else class="vp-hint" role="status">{{ entry.status === 'pending' ? '伙伴正在拟回复…' : entry.note }}</p>
    </article>
  </details>
</template>

<style scoped>
.vp-reply-notes { margin-top: .6rem; font-size: .85rem; }
.vp-reply-notes summary { cursor: pointer; color: var(--accent); }
.vp-reply-notes article { margin-top: .6rem; padding-top: .45rem; border-top: 1px solid var(--border); }
.vp-reply-notes textarea { display: block; width: 100%; box-sizing: border-box; color: var(--text); background: var(--surface); }
</style>
