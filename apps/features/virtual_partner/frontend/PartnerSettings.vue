<script setup lang="ts">
import PartnerForm from './PartnerForm.vue';
import { computed, ref, watch } from 'vue';
import { Plus } from '@lucide/vue';
import type { LimitedHost, PetPortraitCatalog } from '../../../framework/module-sdk/src/index';
import type { Connection, Persona, Save } from './types';
import PartnerConnections from './PartnerConnections.vue';
import PartnerAvatar from './PartnerAvatar.vue';
import PartnerAvatarEditor from './PartnerAvatarEditor.vue';
import type { PreparedAvatar } from './avatar';

const props = defineProps<{
  host: LimitedHost; persona: Persona; connection: Connection; save: Save; busy: boolean; openAt: 'profile' | 'connection';
  catalog?: PetPortraitCatalog; fallback?: string; petError?: string;
}>();
const selected = ref(props.openAt);
const name = ref(props.persona.name);
const personality = ref(props.persona.personality);
const examples = ref((props.persona.examples ?? []).map(pair => ({ ...pair })));
const validExamples = computed(() => examples.value.every(pair => pair.user.trim() && pair.assistant.trim()));
const feedback = ref('');
const avatarDraft = ref<PreparedAvatar | null>();
const avatarPreview = computed(() => avatarDraft.value === undefined ? props.persona.avatar : avatarDraft.value?.preview);
const decoding = ref(false);
const editor = ref<InstanceType<typeof PartnerAvatarEditor>>();
const editorVersion = ref(0);
watch([name, personality, examples, avatarDraft], () => { feedback.value = ''; }, { deep: true, flush: 'sync' });
const visitedConnection = ref(selected.value === 'connection');
watch(selected, tab => { if (tab === 'connection') visitedConnection.value = true; });
const connectionDirty = ref(false);
const emit = defineEmits<{dirty: [value: boolean]; defaultPreview: [value: boolean]; refreshPets: []; unavailable: [src: string]}>();
const profileDirty = computed(() => name.value !== props.persona.name || personality.value !== props.persona.personality
  || JSON.stringify(examples.value) !== JSON.stringify(props.persona.examples ?? []) || decoding.value || avatarDraft.value !== undefined);
watch(() => connectionDirty.value || profileDirty.value, dirty => emit('dirty', dirty), {flush: 'sync'});
watch(() => avatarDraft.value === null, waiting => emit('defaultPreview', waiting), {flush: 'sync'});
async function saveProfile() {
  feedback.value = '';
  if (!validExamples.value || decoding.value || props.busy) return;
  editor.value?.finishBeforeSave();
  const pairs = examples.value.map(pair => ({ user: pair.user.trim(), assistant: pair.assistant.trim() }));
  const avatarChange = avatarDraft.value === undefined ? {} : {avatarPixels: avatarDraft.value?.pixels ?? null};
  if (await props.save('profile', { name: name.value.trim(), personality: personality.value.trim(), examples: pairs, ...avatarChange })) {
    name.value = props.persona.name;
    personality.value = props.persona.personality;
    examples.value = (props.persona.examples ?? []).map(pair => ({ ...pair }));
    avatarDraft.value = undefined;
    editorVersion.value++;
    feedback.value = '角色设定已保存。';
  }
}
</script>

<template>
  <div class="vp-tabs" aria-label="设置类别">
    <button :aria-pressed="selected === 'profile'" type="button" @click="selected = 'profile'; feedback = ''">角色设定</button>
    <button :aria-pressed="selected === 'connection'" type="button" @click="selected = 'connection'; feedback = ''">模型连接</button>
  </div>
  <PartnerForm v-show="selected === 'profile'" class="vp-form" @confirm="saveProfile">
    <div class="vp-profile-identity">
      <PartnerAvatar class="vp-profile-avatar" :name="name" :src="avatarPreview"
        :fallback="(avatarDraft && persona.avatar) || fallback" @unavailable="emit('unavailable', $event)" />
      <label>伙伴名字<input v-model="name" :disabled="busy" required maxlength="32" /></label>
    </div>
    <PartnerAvatarEditor ref="editor" :key="editorVersion" :busy="busy" :saved-avatar="persona.avatar" :draft="avatarDraft"
      :client="host.petPortraits" :catalog="catalog" :pet-error="petError || ''"
      @edit="avatarDraft = $event" @pending="decoding = $event" @refresh="emit('refreshPets')" />
    <label>性格与说话方式<textarea v-model="personality" :disabled="busy" required maxlength="1000" rows="5" placeholder="例如，爱听故事，聊起正事时直截了当。" /></label>
    <fieldset class="vp-examples" :disabled="busy">
      <legend>说话示例 <small>{{ examples.length }} / 3</small></legend>
      <p class="vp-hint">写一小段你期待的对话，帮伙伴找到自己的语气。</p>
      <div v-for="(pair, i) in examples" :key="i" class="vp-example">
        <div class="vp-section-top"><span class="vp-hint">示例 {{ i + 1 }}</span>
          <button type="button" class="vp-mini" :aria-label="'移除示例 ' + (i + 1)" @click="examples.splice(i, 1)">移除</button>
        </div>
        <label>你说<input v-model="pair.user" maxlength="240" required placeholder="今天有点累。" /></label>
        <label>伙伴会说<textarea v-model="pair.assistant" maxlength="240" rows="2" required placeholder="要聊聊，还是陪你安静一会儿？" /></label>
      </div>
      <button v-if="examples.length < 3" type="button" class="vp-subtle" @click="examples.push({user:'', assistant:''})"><Plus :size="14" />添加示例</button>
      <p v-if="!validExamples" class="vp-hint">请补全示例里的两句话，或移除这组示例后保存。</p>
    </fieldset>
    <button type="submit" class="vp-primary" :disabled="busy || decoding || !name.trim() || !personality.trim() || !validExamples">保存设定</button>
  </PartnerForm>
  <PartnerConnections v-if="visitedConnection" v-show="selected === 'connection'" :visible="selected === 'connection'"
    :host="host" :connection="connection" :save="save" :busy="busy" @dirty="connectionDirty = $event" />
  <p v-if="selected === 'profile' && feedback" class="vp-feedback" role="status">{{ feedback }}</p>
</template>
