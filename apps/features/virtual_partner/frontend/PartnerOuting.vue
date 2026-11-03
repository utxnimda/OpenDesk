<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import { Check, Compass, Copy, Plus } from '@lucide/vue';
import type { LimitedHost } from '../../../framework/module-sdk/src/index';
import OutingSchedule from './OutingSchedule.vue';
import OutingReplies from './OutingReplies.vue';
import type { OutingScheduleState } from './outingClock';
import PartnerForm from './PartnerForm.vue';
import { useOuting, type OutingRead } from './useOuting';

const props = defineProps<{host: LimitedHost; revision: number; schedule?: OutingScheduleState}>();
defineEmits<{schedule: [value: OutingScheduleState]}>();
const {state, interests, latest, reads, loading, stale, locked, dirty, error, refresh, start, saveInterests} = useOuting(props.host);
watch(() => props.revision, () => { void refresh(); });
const presets = ['游戏', '动画', '音乐', '科技', '美食', '绘画'];
const choices = computed(() => [...new Set([...presets, ...interests.value])]);
const custom = ref('');
const notice = ref('');
const manualId = ref('');
const copyStatus = ref('');
const copyId = ref('');
const manualInput = ref<HTMLTextAreaElement>();
const dateFormat = new Intl.DateTimeFormat('zh-CN', {month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit'});
const results = {running: '正在外出', completed: '本次外出已结束', skipped: '这次略过', failed: '外出失败', interrupted: '外出已中断'};

function toggle(term: string) {
  if (locked.value) return;
  notice.value = '';
  interests.value = interests.value.includes(term) ? interests.value.filter(item => item !== term) : [...interests.value, term];
}
function addCustom() {
  const term = custom.value.trim();
  if (locked.value || !term) return;
  if (interests.value.includes(term)) notice.value = '已经选了这个兴趣。';
  else if (interests.value.length >= 8) notice.value = '最多可选 8 个兴趣，请先移除一项。';
  else toggle(term);
  custom.value = '';
}
async function save() {
  notice.value = '';
  await saveInterests();
  if (!error.value && !dirty.value) notice.value = '兴趣已保存。';
}
async function copyDraft(entry: OutingRead) {
  copyId.value = entry.id;
  manualId.value = '';
  copyStatus.value = '';
  try {
    await navigator.clipboard.writeText(entry.draft);
    if (copyId.value === entry.id) copyStatus.value = '草稿已复制。可以在 B 站原视频下手动粘贴。';
  } catch {
    if (copyId.value !== entry.id) return;
    manualId.value = entry.id;
    copyStatus.value = '当前页面无法写入剪贴板。选中下面的草稿后按 Ctrl+C。';
    await nextTick();
    manualInput.value?.focus();
    manualInput.value?.select();
  }
}
const selectManualInput = (el: unknown) => { manualInput.value = el as HTMLTextAreaElement | undefined; };
function selectUrl(event: Event) { (event.currentTarget as HTMLInputElement).select(); }
</script>

<template>
  <div class="vp-outing">
    <section class="vp-trip">
      <div class="vp-section-top"><h3><Compass :size="17" />去 B 站逛逛</h3><small class="vp-hint">公开热门视频</small></div>
      <p>伙伴按兴趣挑一条视频，读它的公开标题和简介。有想说的话，就用当前模型拟一份评论草稿，留给你手动发布。</p>
      <p class="vp-hint">外出只读公开资料。当前 B 站渠道没有自动发表或读取回复通知的权限。收到来评时，可在对应见闻里贴入原话拟回复。</p>
    </section>
    <p v-if="error" class="vp-alert" role="alert">{{ error }}</p>
    <button v-if="stale" type="button" class="vp-subtle" :disabled="loading" @click="refresh">重新读取外出状态</button>
    <p v-if="!state && loading" class="vp-hint" role="status">正在读取外出记录…</p>
    <template v-if="state">
      <section class="vp-outing-interests" aria-label="外出兴趣">
        <h3>这次想找什么？</h3>
        <p class="vp-hint">最多选 8 项。留空时，伙伴会按自己的性格挑选，或从热门里尝鲜。</p>
        <div class="vp-interest-options">
          <button v-for="term in choices" :key="term" class="vp-subtle" type="button" :aria-pressed="interests.includes(term)"
            :disabled="locked || (!interests.includes(term) && interests.length >= 8)" @click="toggle(term)">
            <Check v-if="interests.includes(term)" :size="13" />{{ term }}
          </button>
        </div>
        <PartnerForm class="vp-form vp-interest-custom" @confirm="addCustom">
          <label><span>自定义兴趣</span><input v-model="custom" maxlength="32" :disabled="locked" placeholder="例如，水彩速写" /></label>
          <button class="vp-subtle" type="submit" :disabled="locked || !custom.trim()"><Plus :size="14" />添加</button>
        </PartnerForm>
        <div class="vp-actions">
          <button class="vp-primary" type="button" :disabled="locked || !!custom.trim()" @click="notice = ''; start()">
            <Compass :size="15" />{{ stale ? '请先核实外出状态' : latest?.status === 'running' ? '伙伴正在外出…' : dirty ? '保存兴趣并外出一次' : '外出一次' }}
          </button>
          <button class="vp-subtle" type="button" :disabled="locked || !dirty || !!custom.trim()" @click="save">只保存兴趣</button>
        </div>
        <p v-if="custom.trim()" class="vp-hint">点「添加」选中这个兴趣。</p>
        <p v-else-if="notice" class="vp-feedback" role="status">{{ notice }}</p>
      </section>
      <OutingSchedule v-if="schedule" :host="host" :schedule="schedule" :interests-pending="dirty || !!custom.trim()" @update="$emit('schedule', $event)" />
      <section v-if="latest" class="vp-trip vp-trip-status" aria-live="polite">
        <h3>{{ stale ? '上次读取 · ' : '' }}{{ results[latest.status] }}</h3><p>{{ latest.message }}</p>
      </section>
      <section aria-label="外出记录">
        <div class="vp-section-top"><h3>外出见闻</h3><span class="vp-hint">{{ reads.length }} 条</span></div>
        <p v-if="!reads.length" class="vp-hint">读到视频资料后，来源和伙伴的想法会出现在这里。</p>
        <ul v-else class="vp-memory-list vp-outing-reads">
          <li v-for="entry in reads" :key="entry.id">
            <div class="vp-hint">B 站 · {{ dateFormat.format(entry.readAt) }}</div>
            <h4>{{ entry.title }}</h4>
            <p v-if="entry.author" class="vp-hint">UP 主 · {{ entry.author }}</p>
            <p>{{ entry.reason }}</p>
            <details class="vp-memory-source"><summary>查看读到的{{ entry.description ? '标题和简介' : '标题' }}与来源</summary>
              <p v-if="entry.description" class="vp-pre">{{ entry.description }}</p>
              <p v-else>这条视频没有可用的简介。</p>
              <label class="vp-source-url">视频来源地址
                <input :value="entry.url" readonly spellcheck="false" @focus="selectUrl" @click="selectUrl" />
              </label>
            </details>
            <div class="vp-discussion">
              <template v-if="entry.discussion === 'draft'">
                <h5>评论草稿 · 未发布</h5><p class="vp-pre">{{ entry.draft }}</p>
                <button class="vp-subtle" type="button" @click="copyDraft(entry)"><Copy :size="13" />复制草稿</button>
                <p v-if="copyId === entry.id && copyStatus" class="vp-feedback" role="status">{{ copyStatus }}</p>
                <textarea v-if="manualId === entry.id" :ref="selectManualInput" :value="entry.draft" rows="3" readonly aria-label="手动复制评论草稿" />
              </template>
              <h5 v-else-if="entry.discussion === 'pending'">正在考虑是否写评论…</h5>
              <h5 v-else-if="entry.discussion === 'skipped'">这条略过评论</h5>
              <h5 v-else>草稿生成失败</h5>
              <p v-if="entry.discussionNote" class="vp-hint">{{ entry.discussionNote }}</p>
            </div>
            <OutingReplies :host="host" :read-id="entry.id" />
          </li>
        </ul>
      </section>
    </template>
  </div>
</template>
