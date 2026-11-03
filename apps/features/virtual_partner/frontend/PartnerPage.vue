<script setup lang="ts">
import PartnerForm from './PartnerForm.vue';
import PartnerAvatar from './PartnerAvatar.vue';
import { computed, nextTick, ref, watch } from 'vue';
import { ArrowUp, BookOpen, BookmarkPlus, ChevronDown, Compass, MessageCircle, Notebook, Plus, Settings2, Square } from '@lucide/vue';
import type { LimitedHost } from '../../../framework/module-sdk/src/index';
import PartnerDialog from './PartnerDialog.vue';
import PartnerReply from './PartnerReply.vue';
import PartnerSettings from './PartnerSettings.vue';
import MemoryBook from './MemoryBook.vue';
import PartnerJournal from './PartnerJournal.vue';
import PartnerOuting from './PartnerOuting.vue';
import OutingClock from './OutingClock.vue';
import { usePartner } from './usePartner';
import { usePetPortraits } from './usePetPortraits';
import type { Message } from './types';

const props = defineProps<{ host: LimitedHost }>();
const { state, run, active, working, loading, loadingEarlier, stale, error, draft, load, earlier, save, share, createTopic, send, cancel } = usePartner(props.host);
const dialog = ref<'book' | 'journal' | 'outing' | 'settings' | 'topic' | null>(null);
const outingRevision = ref(0);
const previewDefault = ref(false);
const failedAvatar = ref('');
watch(dialog, () => { previewDefault.value = false; });
const needsDefault = computed(() => {
  const persona = state.value?.persona;
  return !!persona && (!persona.avatar?.startsWith('data:image/png;base64,') || persona.avatar === failedAvatar.value || previewDefault.value);
});
const {catalog, catalogError, fallback, fallbackError, refresh} = usePetPortraits(props.host.petPortraits, needsDefault);
const settingsAt = ref<'profile' | 'connection'>('profile');
const settingsDirty = ref(false);
const confirmDiscard = ref(false);
const source = ref<Message>();
const citedRead = ref<string>();
const title = ref('');
const input = ref<HTMLTextAreaElement>();
const transcript = ref<HTMLElement>();
let nearBottom = true;
const locked = computed(() => working.value || loading.value || active.value || stale.value);
const streaming = computed(() => active.value && run.value?.topicId === state.value?.currentTopicId);
const topicTitle = computed(() => state.value?.topics.find(t => t.id === state.value?.currentTopicId)?.title ?? '当前话题');
const suggestions = computed(() => new Map((state.value?.suggestions ?? []).map(item => [item.sourceMessageId, item])));
const savedSources = computed(() => new Set(state.value?.memories.map(item => item.sourceMessageId)));
const experiences = computed(() => new Map((state.value?.experiences ?? []).map(item => [item.id, item])));
const dialogTitle = computed(() => ({ book: '伙伴大脑 · 记忆簿', journal: '近况', outing: '外出', settings: '伙伴设置', topic: '开启新话题' }[dialog.value ?? 'book']));
const lastError = computed(() => {
  const latest = state.value?.run;
  return latest?.topicId === state.value?.currentTopicId && latest?.status === 'failed' ? latest.error : '';
});

function showBook(message?: Message, readId?: string) {
  source.value = message; citedRead.value = readId; error.value = ''; dialog.value = 'book';
}
function showJournal() { error.value = ''; dialog.value = 'journal'; }
function showOuting() { error.value = ''; dialog.value = 'outing'; }
async function decide(sourceId: string, decision: 'accept' | 'dismiss') {
  const suggestion = suggestions.value.get(sourceId);
  if (suggestion && !locked.value) await save('suggestion', {id: suggestion.id, decision});
}
function showSettings(tab: 'profile' | 'connection') {
  settingsAt.value = tab; settingsDirty.value = false; confirmDiscard.value = false;
  error.value = ''; dialog.value = 'settings';
}
function close() {
  if (working.value) return;
  if (dialog.value === 'settings' && settingsDirty.value) confirmDiscard.value = true;
  else dialog.value = null;
}
async function addTopic() {
  if (await createTopic(title.value.trim())) { dialog.value = null; title.value = ''; }
}
async function submit() { nearBottom = true; await send(); }
async function readEarlier() {
  const el = transcript.value;
  if (!el) return;
  const fromEnd = el.scrollHeight - el.scrollTop;
  if (await earlier()) {
    await nextTick();
    el.scrollTop = el.scrollHeight - fromEnd;
  }
}
function onKey(event: KeyboardEvent) {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing || event.keyCode === 229) return;
  event.preventDefault(); void submit();
}
function fillMemory() { draft.value = '记住：'; nextTick(() => input.value?.focus()); }
function onScroll() {
  const el = transcript.value;
  if (el) nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 72;
}
watch(() => [state.value?.currentTopicId, state.value?.messages.at(-1)?.id, run.value?.text] as const, async (now, before) => {
  const changed = now[0] !== before?.[0];
  await nextTick();
  if ((changed || nearBottom) && transcript.value) transcript.value.scrollTop = transcript.value.scrollHeight;
});
</script>

<template>
  <div v-if="!state" class="vp-load">
    <p :role="error ? 'alert' : 'status'">{{ error || '正在打开虚拟伙伴…' }}</p>
    <button v-if="error" class="vp-subtle" @click="load()">重新读取</button>
  </div>
  <main v-else class="vp-room">
    <OutingClock v-if="state.outingSchedule?.enabled" :host="host" :schedule="state.outingSchedule"
      @update="state.outingSchedule = $event" @started="outingRevision++" @failure="error = $event" />
    <header class="vp-head">
      <div class="vp-id"><PartnerAvatar :name="state.persona.name" :src="state.persona.avatar" :fallback="fallback" @unavailable="failedAvatar = $event" />
        <div><div class="vp-kicker">虚拟伙伴</div><h1>{{ state.persona.name }}</h1></div>
      </div>
      <div class="vp-actions">
        <button type="button" class="vp-subtle" title="外出 · 逛 B 站" aria-label="打开伙伴外出" @click="showOuting"><Compass :size="17" /><span>外出</span></button>
        <button type="button" class="vp-subtle" title="近况 · 分享给伙伴" aria-label="打开伙伴近况" @click="showJournal"><Notebook :size="17" /><span>近况</span></button>
        <button type="button" class="vp-subtle" aria-label="打开记忆簿" title="伙伴大脑 · 记忆簿" @click="showBook()"><BookOpen :size="17" /><span>记忆簿</span></button>
        <button type="button" class="vp-icon" title="伙伴设置" aria-label="伙伴设置" @click="showSettings('profile')"><Settings2 :size="18" /></button>
      </div>
    </header>
    <div class="vp-topic-row">
      <div class="vp-topic-select"><MessageCircle :size="14" aria-hidden="true" /><span>{{ topicTitle }}</span><ChevronDown :size="13" aria-hidden="true" />
        <select :value="state.currentTopicId" aria-label="切换话题" :disabled="working || loading" @change="load(($event.target as HTMLSelectElement).value)">
          <option v-for="topic in state.topics" :key="topic.id" :value="topic.id">{{ topic.title }}</option>
        </select>
      </div>
      <button type="button" class="vp-mini" :disabled="locked" @click="dialog = 'topic'; error = ''"><Plus :size="14" />新话题</button>
    </div>
    <div v-if="(error || stale || lastError) && !dialog" class="vp-alert" role="alert">
      {{ error || lastError || '需要刷新当前话题。' }}
      <button v-if="stale" class="vp-mini" @click="load()">重新读取</button>
    </div>
    <section ref="transcript" class="vp-transcript" aria-label="对话记录" @scroll="onScroll">
      <div v-if="!state.messages.length && !streaming" class="vp-empty">
        <PartnerAvatar class="vp-large" :name="state.persona.name" :src="state.persona.avatar" :fallback="fallback" @unavailable="failedAvatar = $event" />
        <h2>和{{ state.persona.name }}聊聊</h2>
        <p>{{ state.connection.hasKey ? '想到什么，就从那里开始。' : '先告诉伙伴一件想记住的事，或者接上模型开始聊天。' }}</p>
        <div class="vp-actions">
          <button v-if="!state.connection.hasKey" type="button" class="vp-primary" @click="showSettings('connection')">设置模型连接</button>
          <button type="button" class="vp-subtle" @click="fillMemory()"><BookmarkPlus :size="15" />记一件事</button>
        </div>
      </div>
      <div v-else class="vp-history">
        <button v-if="state.hasOlderMessages" type="button" class="vp-mini vp-earlier" :disabled="loadingEarlier || loading" @click="readEarlier">{{ loadingEarlier ? '读取中…' : '查看更早的消息' }}</button>
        <article v-for="message in state.messages" :key="message.id" class="vp-message" :class="message.role">
          <div class="vp-who">{{ message.role === 'user' ? '你' : state.persona.name }}</div>
          <PartnerReply v-if="message.role === 'assistant'" :text="message.text" />
          <p v-else class="vp-pre">{{ message.text }}</p>
          <small v-if="message.role === 'user' && ['cancelled','interrupted','failed'].includes(message.status)" class="vp-hint">{{ {cancelled:'已停止回复',interrupted:'回复已中断',failed:'回复失败'}[message.status as string] }}</small>
          <template v-if="message.role === 'user'">
            <div v-if="suggestions.has(message.id)" class="vp-suggestion" aria-label="待确认的记忆建议">
              <p>记住这项偏好？</p><p class="vp-pre">{{ suggestions.get(message.id)?.text }}</p>
              <div class="vp-actions">
                <button type="button" class="vp-subtle" :disabled="locked" @click="decide(message.id, 'accept')">记住</button>
                <button type="button" class="vp-mini" :disabled="locked" @click="decide(message.id, 'dismiss')">略过</button>
              </div>
            </div>
            <button v-else-if="savedSources.has(message.id)" type="button" class="vp-mini" @click="showBook()">已记到记忆簿</button>
            <button v-else type="button" class="vp-mini vp-remember" :disabled="locked" @click="showBook(message)"><BookmarkPlus :size="13"/>摘到记忆簿</button>
          </template>
          <details v-else-if="message.memoryIds?.length" class="vp-memory-source">
            <summary>本次参考了 {{ message.memoryIds.length }} 条记忆</summary>
            <ul><li v-for="id in message.memoryIds" :key="id">{{ state.memories.find(m => m.id === id)?.text ?? '记忆已移除' }}</li></ul>
          </details>
          <details v-if="message.role === 'assistant' && message.experienceIds?.length" class="vp-memory-source">
            <summary>本次参考了 {{ message.experienceIds.length }} 条近况</summary>
            <ul>
              <li v-for="id in message.experienceIds" :key="id">
                {{ experiences.get(id)?.title ?? '近况已移除' }}
                <template v-if="experiences.has(id)"> · {{ experiences.get(id)?.evidence === 'excerpt' ? '用户分享的摘录' : '仅有标题' }}</template>
              </li>
            </ul>
            <button type="button" class="vp-mini" @click="showJournal">查看近况</button>
          </details>
          <details v-if="message.role === 'assistant' && message.outingSources?.length" class="vp-memory-source">
            <summary>本次参考了 {{ message.outingSources.length }} 条外出见闻</summary>
            <ul><li v-for="item in message.outingSources" :key="item.id">B 站 · {{ item.title }}
              <button type="button" class="vp-mini" @click="showBook(undefined, item.id)">查看这条经历</button>
            </li></ul>
          </details>
        </article>
        <article v-if="streaming" class="vp-message assistant">
          <div class="vp-who">{{ state.persona.name }}<span role="status">{{ run?.status === 'cancelling' ? '正在停止…' : '正在回复…' }}</span></div>
          <PartnerReply :text="run?.text ?? ''" />
        </article>
      </div>
    </section>
    <footer class="vp-footer">
      <PartnerForm class="vp-composer" @confirm="submit">
        <textarea ref="input" v-model="draft" aria-label="给伙伴写消息" :placeholder="'给' + state.persona.name + '写消息…'" maxlength="4000" rows="2" @keydown="onKey" />
        <div class="vp-compose-actions">
          <span class="vp-hint">{{ !state.connection.hasKey ? '“记住：……” 可在本地保存' : state.connection.model }}</span>
          <button v-if="active" class="vp-send" type="button" aria-label="停止回复" title="停止回复" :disabled="working || run?.status === 'cancelling'" @click="cancel"><Square :size="15" /></button>
          <button v-else class="vp-send" type="submit" aria-label="发送" title="发送 · Enter" :disabled="locked || !draft.trim()"><ArrowUp :size="18" /></button>
        </div>
      </PartnerForm>
    </footer>
    <PartnerDialog v-if="dialog" :title="dialogTitle" :error="error" @close="close">
      <div v-if="dialog === 'settings' && confirmDiscard && settingsDirty" class="vp-alert">
        伙伴设置还有未保存的修改。
        <button class="vp-subtle" type="button" @click="confirmDiscard = false">继续编辑</button>
        <button class="vp-mini" type="button" :disabled="working" @click="dialog = null">放弃修改并关闭</button>
      </div>
      <button v-if="dialog === 'settings' && stale" class="vp-subtle" type="button" :disabled="loading || working" @click="load()">重新读取对话状态</button>
      <MemoryBook v-if="dialog === 'book'" :host="host" :memories="state.memories" :messages="state.messages" :source="source"
        :cited-read="citedRead" :save="save" :busy="locked" />
      <PartnerJournal v-else-if="dialog === 'journal'" :entries="state.experiences ?? []" :save="save" :share="share" :busy="locked" />
      <PartnerOuting v-else-if="dialog === 'outing'" :host="host" :revision="outingRevision" :schedule="state.outingSchedule"
        @schedule="state.outingSchedule = $event" />
      <PartnerSettings v-else-if="dialog === 'settings'" :host="host" :persona="state.persona" :connection="state.connection" :save="save" :busy="locked" :open-at="settingsAt"
        :catalog="catalog" :fallback="fallback" :pet-error="catalogError || fallbackError" @dirty="settingsDirty = $event"
        @default-preview="previewDefault = $event" @unavailable="failedAvatar = $event" @refresh-pets="refresh" />
      <PartnerForm v-else class="vp-form" @confirm="addTopic">
        <label>话题名称<input v-model="title" required maxlength="64" autofocus placeholder="例如，周末的画稿" /></label>
        <p class="vp-hint">新话题单独保存聊天记录，也能用到伙伴的记忆。</p>
        <button type="submit" class="vp-primary" :disabled="locked || !title.trim()">开始新话题</button>
      </PartnerForm>
    </PartnerDialog>
  </main>
</template>
