<script setup lang="ts">
import PartnerForm from './PartnerForm.vue';
import OutingMemories from './OutingMemories.vue';
import { ref } from 'vue';
import { Bookmark, Pencil, Plus, Trash2 } from '@lucide/vue';
import type { Memory, Message, Save } from './types';
import type { LimitedHost } from '../../../framework/module-sdk/src/index';
const props = defineProps<{ host: LimitedHost; memories: Memory[]; messages: Message[]; source?: Message;
  citedRead?: string; save: Save; busy: boolean }>();
const section = ref<'manual' | 'outings'>(props.source || props.memories.length && !props.citedRead ? 'manual' : 'outings');
const editor = ref<string | null>(props.source ? 'new' : null);
const text = ref(props.source?.text.slice(0, 500) ?? '');
const sourceId = ref<string | undefined>(props.source?.id);
const removing = ref('');
const feedback = ref('');

function edit(memory?: Memory) {
  editor.value = memory?.id ?? 'new';
  text.value = memory?.text ?? '';
  sourceId.value = undefined;
  removing.value = ''; feedback.value = '';
}
async function saveEntry() {
  if (!text.value.trim() || !editor.value) return;
  const ok = editor.value === 'new'
    ? await props.save('remember', { text: text.value.trim(), ...(sourceId.value ? { sourceMessageId: sourceId.value } : {}) })
    : await props.save('revise', { id: editor.value, text: text.value.trim() });
  if (ok) { editor.value = null; feedback.value = '记忆已保存。'; }
}
async function forget(id: string) {
  if (await props.save('forget', { id })) { removing.value = ''; feedback.value = '已忘记这条记忆。'; }
}
function sourceText(memory: Memory) {
  return props.messages.find(m => m.id === memory.sourceMessageId)?.text || memory.sourceExcerpt;
}
</script>

<template>
  <div class="vp-tabs" aria-label="记忆分类">
    <button type="button" :aria-pressed="section === 'manual'" @click="section = 'manual'">你教我的事</button>
    <button type="button" :aria-pressed="section === 'outings'" @click="section = 'outings'">外出经历</button>
  </div>
  <OutingMemories v-if="section === 'outings'" :host="host" :cited-read="citedRead" :save="save" :busy="busy" />
  <template v-else>
  <div class="vp-section-top"><p class="vp-hint">换个话题，还可以聊起这些事。</p>
    <button v-if="editor === null" type="button" class="vp-subtle" :disabled="busy" @click="edit()"><Plus :size="15" />添加</button>
  </div>
  <PartnerForm v-if="editor !== null" class="vp-form vp-memory-editor" @confirm="saveEntry">
    <label>{{ editor === 'new' ? '想让伙伴记住什么？' : '修改这条记忆' }}
      <textarea v-model="text" maxlength="500" rows="3" autofocus required placeholder="例如，我更喜欢别人叫我的昵称。" />
    </label>
    <small v-if="sourceId" class="vp-hint">从你的消息摘录，可修改为希望保留的内容。</small>
    <div class="vp-actions"><button type="submit" class="vp-primary" :disabled="busy || !text.trim()">保存记忆</button>
      <button type="button" class="vp-subtle" :disabled="busy" @click="editor = null">取消</button>
    </div>
  </PartnerForm>
  <p v-if="feedback" class="vp-feedback" role="status">{{ feedback }}</p>
  <div v-if="!memories.length && editor === null" class="vp-book-empty">
    <Bookmark :size="23" /><h3>还没有记下你教我的事</h3><p>在这里添加一件事，或者在聊天时写下“记住：……”。</p>
  </div>
  <ul v-else class="vp-memory-list">
    <li v-for="memory in memories" :key="memory.id">
      <p class="vp-pre">{{ memory.text }}</p>
      <details v-if="sourceText(memory)" class="vp-memory-source"><summary>查看原话</summary><p class="vp-pre">{{ sourceText(memory) }}</p></details>
      <small v-else class="vp-hint">{{ memory.sourceMessageId ? '来自聊天' : '手动添加' }}</small>
      <div v-if="removing === memory.id" class="vp-actions"><span class="vp-hint">以后不再引用这条记忆？</span>
        <button type="button" class="vp-subtle" :disabled="busy" @click="forget(memory.id)">确认忘记</button>
        <button type="button" class="vp-subtle" @click="removing = ''">取消</button>
      </div>
      <div v-else class="vp-actions">
        <button type="button" class="vp-mini" :disabled="busy" @click="edit(memory)"><Pencil :size="13" />修改</button>
        <button type="button" class="vp-mini" :disabled="busy" @click="removing = memory.id; editor = null; feedback = ''"><Trash2 :size="13" />忘记</button>
      </div>
    </li>
  </ul>
  </template>
</template>
