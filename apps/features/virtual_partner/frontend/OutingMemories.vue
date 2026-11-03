<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { RefreshCw, Trash2 } from '@lucide/vue';
import { errorMessage, type LimitedHost } from '../../../framework/module-sdk/src/index';
import type { Save } from './types';
import type { OutingRead } from './useOuting';

interface ReplyNote { id: string; incoming: string; draft: string; status: 'pending' | 'draft' | 'skipped' | 'failed'; note: string }
interface Entry extends OutingRead { replyNotes: ReplyNote[] }
interface Page { entries: Entry[]; nextBeforeId: string | null }
const props = defineProps<{host: LimitedHost; citedRead?: string; save: Save; busy: boolean}>();
const entries = ref<Entry[]>([]);
const cursor = ref<string | null>(null);
const focusId = ref(props.citedRead);
const loading = ref(false);
const removing = ref('');
const error = ref('');
const feedback = ref('');
const date = new Intl.DateTimeFormat('zh-CN', {year: 'numeric', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit'});
const replyStatus = {pending: '正在拟回复', draft: '回复草稿 · 未发布', skipped: '没有拟回复', failed: '回复拟稿失败'};

async function load(older = false) {
  if (loading.value) return;
  loading.value = true;
  error.value = '';
  try {
    const params = older && cursor.value ? {beforeId: cursor.value} : focusId.value ? {readId: focusId.value} : {};
    const page = await props.host.callWorker<Page>('partner.outing.memories', params);
    if (!Array.isArray(page?.entries) || !(page.nextBeforeId === null || typeof page.nextBeforeId === 'string')) {
      throw new Error('外出经历响应无效，请重新读取。');
    }
    entries.value = older ? [...entries.value, ...page.entries] : page.entries;
    cursor.value = page.nextBeforeId;
  } catch (cause) { error.value = errorMessage(cause); }
  finally { loading.value = false; }
}
async function forget(id: string) {
  if (await props.save('outing.forget', {id})) {
    entries.value = entries.value.filter(entry => entry.id !== id);
    removing.value = '';
    feedback.value = '已忘记这条外出经历。';
  } else {
    // A write acknowledgement can be lost. Re-read the committed book before allowing another attempt.
    await load();
  }
}
onMounted(() => { void load(); });
</script>

<template>
  <section class="vp-outing" aria-label="记忆簿中的外出经历">
    <div class="vp-section-top">
      <p class="vp-hint">{{ focusId ? '这次聊天引用的外出经历' : '伙伴读到的公开资料、你带回的评论和伙伴拟过的草稿。' }}</p>
      <button v-if="focusId" type="button" class="vp-mini" :disabled="loading" @click="focusId = undefined; load()">查看全部经历</button>
      <button v-else type="button" class="vp-mini" :disabled="loading" @click="load()"><RefreshCw :size="13" />刷新经历</button>
    </div>
    <p v-if="error" class="vp-alert" role="alert">{{ error }}</p>
    <p v-if="loading" class="vp-hint" role="status">正在读取外出经历…</p>
    <p v-if="feedback" class="vp-feedback" role="status">{{ feedback }}</p>
    <p v-if="!entries.length && !loading && !error" class="vp-hint">{{ focusId ? '这条外出经历已经不在记忆簿中。' : '伙伴成功读到公开资料后，会自动把这段经历记在这里。' }}</p>
    <ul class="vp-memory-list vp-outing-reads">
      <li v-for="entry in entries" :key="entry.id">
        <small class="vp-hint">B 站公开阅读 · {{ date.format(entry.readAt) }}</small>
        <h4>{{ entry.title }}</h4>
        <p class="vp-hint">UP 主 · {{ entry.author }}</p>
        <p>{{ entry.reason }}</p>
        <details :open="!!focusId" class="vp-memory-source"><summary>阅读证据与来源</summary>
          <p class="vp-pre">{{ entry.description || '来源只提供了标题与作者。' }}</p>
          <label class="vp-source-url">视频来源地址
            <input :value="entry.url" readonly spellcheck="false" @focus="($event.target as HTMLInputElement).select()" />
          </label>
        </details>
        <p v-if="entry.draft" class="vp-pre"><strong>评论草稿 · 未发布</strong><br />{{ entry.draft }}</p>
        <p v-else-if="entry.discussionNote" class="vp-hint">{{ entry.discussionNote }}</p>
        <div v-for="note in entry.replyNotes" :key="note.id" class="vp-discussion">
          <strong>用户带回的评论 · 未经站点核实</strong>
          <p class="vp-pre">{{ note.incoming }}</p>
          <strong>{{ replyStatus[note.status] }}</strong>
          <p v-if="note.draft" class="vp-pre">{{ note.draft }}</p>
          <p v-else-if="note.note" class="vp-hint">{{ note.note }}</p>
        </div>
        <div v-if="removing === entry.id" class="vp-actions"><span class="vp-hint">忘记这次阅读和相关草稿？以后的聊天不再引用它。</span>
          <button type="button" class="vp-subtle" :disabled="busy || loading" @click="forget(entry.id)">确认忘记经历</button>
          <button type="button" class="vp-subtle" :disabled="busy" @click="removing = ''">取消</button>
        </div>
        <div v-else class="vp-actions">
          <button type="button" class="vp-mini" :disabled="busy || loading" @click="removing = entry.id; feedback = ''"><Trash2 :size="13" />忘记经历</button>
        </div>
      </li>
    </ul>
    <button v-if="cursor" type="button" class="vp-subtle" :disabled="loading || busy" @click="load(true)">更早的外出经历</button>
  </section>
</template>
