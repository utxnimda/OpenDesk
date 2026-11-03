<script setup lang="ts">
import PartnerForm from './PartnerForm.vue';
import { computed, onMounted, onScopeDispose, ref, watch } from 'vue';
import { Plus } from '@lucide/vue';
import { errorMessage, type LimitedHost } from '../../../framework/module-sdk/src/index';
import type { Connection, Connections, Save } from './types';
import { useModelPicker } from './useModelPicker';
import { createRequestId } from './usePartner';

const props = defineProps<{host: LimitedHost; connection: Connection; save: Save; busy: boolean; visible: boolean}>();
const emit = defineEmits<{dirty: [value: boolean]}>();
const listing = ref<Connections>();
const draftId = ref('');
const creating = computed(() => !!draftId.value);
const saved = computed(() => creating.value ? undefined : listing.value?.profiles.find(p => p.id === listing.value?.activeId));
const profileId = computed(() => saved.value?.id ?? null);
const defaultUrl = 'https://api.openai.com/v1';
const name = ref('');
const baseUrl = ref('');
const model = ref('');
const apiKey = ref('');
const keepsKey = computed(() => !!saved.value?.hasKey && baseUrl.value.trim() === saved.value.baseUrl);
const dirty = computed(() => name.value !== (saved.value?.name ?? '') || !!apiKey.value ||
  model.value !== (saved.value?.model ?? '') || ((creating.value || !!saved.value) && baseUrl.value !== (saved.value?.baseUrl ?? defaultUrl)));
watch(dirty, value => emit('dirty', value));

const reading = ref(true);
const writing = ref(false);
const readError = ref('');
const writeUnconfirmed = ref(false);
const feedback = ref('');
const locked = computed(() => props.busy || reading.value || writing.value || !!readError.value);
const canLookup = computed(() => !!baseUrl.value.trim() && (!!apiKey.value.trim() || keepsKey.value));
const { catalog, models, custom, customOption, selection, refresh, reset, readSaved } =
  useModelPicker(props.host, profileId, baseUrl, apiKey, model);
let mounted = true;
let readRevision = 0;

function fillDraft(fetchIfMissing: boolean) {
  reset();
  name.value = saved.value?.name ?? '';
  baseUrl.value = saved.value?.baseUrl ?? defaultUrl;
  model.value = saved.value?.model ?? '';
  apiKey.value = '';
  custom.value = false;
  if (props.visible && saved.value) void readSaved(fetchIfMissing && keepsKey.value);
}

async function readProfiles(fetchIfMissing: boolean) {
  const ticket = ++readRevision;
  reading.value = true;
  readError.value = '';
  reset();
  try {
    const result = await props.host.callWorker<Connections>('partner.connections', {});
    if (!mounted || ticket !== readRevision) return false;
    if (!Array.isArray(result?.profiles) || !result.profiles.every(p => p && typeof p.id === 'string' &&
      typeof p.name === 'string' && typeof p.baseUrl === 'string' && typeof p.model === 'string' && typeof p.hasKey === 'boolean') ||
      (result.profiles.length ? !result.profiles.some(p => p.id === result.activeId) : result.activeId !== null)) {
      throw new Error('API 配置列表格式无效。');
    }
    listing.value = result;
    draftId.value = '';
    writeUnconfirmed.value = false;
    fillDraft(fetchIfMissing);
    return true;
  } catch (cause) {
    if (mounted && ticket === readRevision) readError.value = errorMessage(cause);
    return false;
  } finally { if (ticket === readRevision) reading.value = false; }
}

function add() {
  if (locked.value || dirty.value || creating.value) return;
  feedback.value = '';
  draftId.value = createRequestId();
  fillDraft(false);
}
function discard() {
  if (locked.value) return;
  feedback.value = '';
  draftId.value = '';
  fillDraft(false);
}
async function write(action: 'connection.select' | 'connection.upsert', values: Record<string, unknown>) {
  if (locked.value) return;
  writing.value = true;
  feedback.value = '';
  reset();
  try {
    const confirmed = await props.save(action, values);
    if (!mounted) return;
    if (!confirmed) {
      writeUnconfirmed.value = true;
      return;
    }
    // Saving also selects this profile for chat. A new credential may have no catalog yet.
    if (await readProfiles(true)) {
      feedback.value = action === 'connection.select' ? '已切换当前 API。' : 'API 配置已保存，并已用于对话。';
    }
  } finally { writing.value = false; }
}
function select(event: Event) {
  const control = event.target as HTMLSelectElement;
  const id = control.value;
  // Show the persisted selection until the worker acknowledges the switch.
  control.value = listing.value?.activeId ?? '';
  if (!id || id === control.value || dirty.value || creating.value) return;
  void write('connection.select', {id});
}
function saveDraft() {
  if (!canLookup.value || !name.value.trim() || !model.value.trim()) return;
  const values: Record<string, unknown> = {
    id: draftId.value || profileId.value, create: creating.value, name: name.value.trim(),
    baseUrl: baseUrl.value.trim(), model: model.value.trim(),
  };
  if (apiKey.value.trim()) values.apiKey = apiKey.value.trim();
  void write('connection.upsert', values);
}
watch([name, baseUrl, model, apiKey], () => { feedback.value = ''; }, {flush: 'sync'});
watch(() => props.visible, visible => {
  if (!visible) reset();
  else if (!writing.value && !creating.value && !dirty.value && !writeUnconfirmed.value) void readProfiles(true);
});
onMounted(() => { if (props.visible) void readProfiles(true); });
onScopeDispose(() => { mounted = false; readRevision++; });
</script>

<template>
  <div class="vp-form">
    <p class="vp-hint">发送消息时，会向所选 API 提供角色设定和语气示例、近期对话，以及相关的已确认记忆和分享片段。来源链接保存在本地。</p>
    <p v-if="!listing && reading" role="status" class="vp-hint">正在读取本机 API 配置…<template v-if="connection.model"> 当前模型：{{ connection.model }}</template></p>
    <div v-if="listing" class="vp-model-row">
      <label>当前 API
        <select aria-label="当前 API" :value="creating ? '' : listing.activeId" :disabled="locked || dirty || creating || !listing.profiles.length" @change="select">
          <option v-if="creating || !listing.profiles.length" value="">{{ creating ? '新增 API 草稿' : '尚未配置 API' }}</option>
          <option v-for="profile in listing.profiles" :key="profile.id" :value="profile.id">{{ profile.name }}</option>
        </select>
      </label>
      <button class="vp-subtle" type="button" :disabled="locked || dirty || creating" @click="add"><Plus :size="14" />新增 API</button>
    </div>
    <div v-if="readError || writeUnconfirmed" class="vp-alert" role="alert">
      {{ readError || '未确认设置是否写入。可以重试刚才的操作，或重新读取本机配置。' }}
      <button class="vp-mini" type="button" :disabled="reading || writing" @click="readProfiles(false)">{{ dirty ? '放弃草稿并重新读取' : '重新读取 API' }}</button>
    </div>
    <PartnerForm v-if="creating || saved" class="vp-form" autocomplete="off" @confirm="saveDraft">
      <label>API 名称<input v-model="name" :disabled="locked" required maxlength="80" placeholder="例如，工作账号" /></label>
      <label>API 地址<input v-model="baseUrl" :disabled="locked" type="url" required spellcheck="false" placeholder="https://api.openai.com/v1" /></label>
      <label>API Key<input v-model="apiKey" :disabled="locked" :required="!keepsKey" type="password" autocomplete="new-password" spellcheck="false" :placeholder="keepsKey ? '已保存，留空沿用此配置的密钥' : '输入此 API 的密钥'" /></label>
      <div class="vp-model-row">
        <label>模型
          <select v-model="selection" aria-label="模型" :disabled="locked">
            <option disabled value="">请选择模型</option>
            <option v-if="saved?.model && !models.includes(saved.model)" :value="saved.model">{{ saved.model }}（已保存）</option>
            <option v-if="model && model !== saved?.model && !models.includes(model)" :value="model">{{ model }}（当前填写）</option>
            <option v-for="id in models" :key="id" :value="id">{{ id }}</option>
            <option :value="customOption">自定义 ID…</option>
          </select>
        </label>
        <button type="button" class="vp-subtle" :disabled="locked || catalog.status === 'loading' || !canLookup" @click="refresh">{{ catalog.status === 'loading' ? '读取中…' : catalog.status === 'ready' ? '刷新模型' : '读取模型' }}</button>
      </div>
      <label v-if="custom">自定义模型 ID<input v-model="model" :disabled="locked" required maxlength="256" spellcheck="false" placeholder="填写服务商提供的模型 ID" /></label>
      <p v-if="catalog.status === 'loading'" class="vp-hint" role="status">正在读取此 API 的模型列表…</p>
      <p v-else-if="catalog.status === 'ready'" class="vp-hint" role="status">{{ models.length ? '此 API 的目录有 ' + models.length + ' 个模型。' : '服务未返回模型，可以选择「自定义 ID」手动填写。' }}</p>
      <p v-else-if="catalog.status === 'failed'" class="vp-alert" role="alert">{{ catalog.error }} 可重新读取或选择「自定义 ID」。</p>
      <p class="vp-hint">请选择支持流式 Chat Completions 的文本模型。{{ creating ? '保存后会使用新 API 聊天。' : dirty ? '保存或取消修改后可以切换 API。' : '' }}</p>
      <div class="vp-actions">
        <button type="submit" class="vp-primary" :disabled="locked || !name.trim() || !model.trim() || !canLookup">保存连接</button>
        <button v-if="creating || dirty" type="button" class="vp-subtle" :disabled="locked || writeUnconfirmed" @click="discard">取消修改</button>
      </div>
    </PartnerForm>
    <p v-if="feedback" class="vp-feedback" role="status">{{ feedback }}</p>
  </div>
</template>
