<script setup lang="ts">
import PartnerForm from './PartnerForm.vue';
import { ref } from 'vue';
import { Notebook, Pencil, Plus, Trash2 } from '@lucide/vue';
import type { Experience, ExperienceInput, Save, Share } from './types';

const props = defineProps<{ entries: Experience[]; share: Share; save: Save; busy: boolean }>();
const editor = ref<{ id?: string; fields: ExperienceInput } | null>(null);
const removing = ref('');
const feedback = ref('');

function edit(entry?: Experience) {
  editor.value = { id: entry?.id, fields: {
    title: entry?.title ?? '', excerpt: entry?.excerpt ?? '', url: entry?.url ?? '', impression: entry?.impression ?? '',
  } };
  removing.value = '';
  feedback.value = '';
}

async function submit() {
  if (!editor.value || props.busy) return;
  const {id, fields} = editor.value;
  const values = {
    title: fields.title.trim(), excerpt: fields.excerpt.trim(), url: fields.url.trim(), impression: fields.impression.trim(),
  };
  if (!values.title) return;
  const saved = id ? await props.save('experience.edit', { id, ...values }) : await props.share(values);
  if (saved) {
    editor.value = null;
    feedback.value = '近况已保存。';
  }
}

async function remove(id: string) {
  if (await props.save('experience.remove', { id })) {
    removing.value = '';
    feedback.value = '近况已移除。';
  }
}
</script>

<template>
  <PartnerForm v-if="editor" class="vp-form" @confirm="submit">
    <div class="vp-section-top"><h3>{{ editor.id ? '修改近况' : '分享给伙伴' }}</h3><small class="vp-hint">仅私聊可用</small></div>
    <label>标题<input v-model="editor.fields.title" :disabled="busy" required maxlength="80" autofocus placeholder="例如，画纸船时怎样画折痕" /></label>
    <label><span>摘录 <small>选填</small></span><textarea v-model="editor.fields.excerpt" :disabled="busy" rows="4" maxlength="1000" placeholder="粘贴想让伙伴了解的原文。" /></label>
    <label><span>来源链接 <small>选填</small></span><input v-model="editor.fields.url" :disabled="busy" type="url" pattern="https?://.+" maxlength="512" spellcheck="false" placeholder="https://…" /></label>
    <p class="vp-hint">只填标题或链接时，伙伴只知道标题。要聊原文内容，请在上方粘贴摘录。</p>
    <label><span>你的感想 <small>选填</small></span><textarea v-model="editor.fields.impression" :disabled="busy" maxlength="300" rows="2" placeholder="为什么想把它分享给伙伴？" /></label>
    <div class="vp-actions">
      <button type="submit" class="vp-primary" :disabled="busy || !editor.fields.title.trim()">保存近况</button>
      <button type="button" class="vp-subtle" :disabled="busy" @click="editor = null">取消</button>
    </div>
  </PartnerForm>
  <template v-else>
    <div class="vp-section-top"><span class="vp-hint">你和伙伴私聊时分享的见闻</span>
      <button type="button" class="vp-subtle" :disabled="busy" @click="edit()"><Plus :size="15" />分享给伙伴</button>
    </div>
    <p v-if="feedback" class="vp-feedback" role="status">{{ feedback }}</p>
    <div v-if="!entries.length" class="vp-book-empty">
      <Notebook :size="23" /><h3>还没有一起聊过的见闻</h3><p>分享一段读到的文字，换个话题也能再聊起。</p>
    </div>
    <ul v-else class="vp-memory-list vp-journal-list">
      <li v-for="entry in entries" :key="entry.id">
        <h3>{{ entry.title }}</h3>
        <p class="vp-hint">用户分享 · {{ entry.evidence === 'excerpt' ? '附原文摘录' : '仅有标题' }}</p>
        <details v-if="entry.excerpt || entry.url || entry.impression" class="vp-memory-source">
          <summary>查看分享内容</summary>
          <dl>
            <template v-if="entry.excerpt"><dt>原文摘录</dt><dd>{{ entry.excerpt }}</dd></template>
            <template v-if="entry.url"><dt>来源链接</dt><dd>{{ entry.url }}</dd></template>
            <template v-if="entry.impression"><dt>你的感想</dt><dd>{{ entry.impression }}</dd></template>
          </dl>
        </details>
        <div v-if="removing === entry.id" class="vp-actions">
          <span class="vp-hint">以后不再引用这条近况？</span>
          <button type="button" class="vp-subtle" :disabled="busy" @click="remove(entry.id)">确认移除</button>
          <button type="button" class="vp-subtle" :disabled="busy" @click="removing = ''">取消</button>
        </div>
        <div v-else class="vp-actions">
          <button type="button" class="vp-mini" :disabled="busy" @click="edit(entry)"><Pencil :size="13" />修改</button>
          <button type="button" class="vp-mini" :disabled="busy" @click="removing = entry.id; feedback = ''"><Trash2 :size="13" />移除</button>
        </div>
      </li>
    </ul>
  </template>
</template>
