<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, useId, watch } from 'vue';
import { errorMessage, type PetPortraitCatalog, type PetPortraitsClient } from '../../../framework/module-sdk/src/index';
import {
  avatarFrameFromRgba, clampAvatarCrop, defaultAvatarCrop, drawAvatarCrop, exportAvatarCrop,
  openAvatarSource, panAvatarCrop, type AvatarCrop, type PreparedAvatar,
} from './avatar';

const props = defineProps<{
  busy: boolean; savedAvatar?: string | null; draft?: PreparedAvatar | null;
  client?: PetPortraitsClient; catalog?: PetPortraitCatalog; petError: string;
}>();
const emit = defineEmits<{
  edit: [value: PreparedAvatar | null | undefined]; pending: [value: boolean]; refresh: [];
}>();
interface Source {
  label: string; count: number; frame: (index: number) => Promise<HTMLCanvasElement>; close?: () => void;
}

const source = shallowRef<Source>();
const frame = shallowRef<HTMLCanvasElement>();
const crop = shallowRef(defaultAvatarCrop());
const target = ref<HTMLCanvasElement>();
const index = ref(0);
const pending = ref(false);
const error = ref('');
const petMenu = ref(false);
const chosenPet = ref('');
const chosenAction = ref('');
const pet = computed(() => props.catalog?.pets.find(item => item.id === chosenPet.value));
const hint = useId();
let revision = 0;
let drag: {id: number; x: number; y: number; crop: AvatarCrop} | undefined;
let appliedCrop = crop.value;

watch(pending, value => emit('pending', value), {flush: 'sync'});
watch([target, frame, crop], () => {
  if (target.value && frame.value) drawAvatarCrop(target.value, frame.value, crop.value);
}, {flush: 'post'});

function startRead() {
  if (drag) crop.value = appliedCrop;
  drag = undefined;
  pending.value = true;
  error.value = '';
  return ++revision;
}

function accept(ticket: number, next: Source, image: HTMLCanvasElement, currentIndex = 0, nextCrop = defaultAvatarCrop()) {
  if (ticket !== revision) return false;
  const bounded = clampAvatarCrop(image.width, image.height, nextCrop);
  const prepared = exportAvatarCrop(image, bounded);
  if (source.value !== next) source.value?.close?.();
  source.value = next;
  frame.value = image;
  index.value = currentIndex;
  appliedCrop = crop.value = bounded;
  emit('edit', prepared);
  return true;
}

async function openFile(file: Blob, label: string) {
  const ticket = startRead();
  try {
    const opened = await openAvatarSource(file);
    let accepted = false;
    try {
      if (ticket !== revision) return;
      const image = await opened.frame(0);
      accepted = accept(ticket, {label, count: opened.frameCount, frame: i => opened.frame(i), close: () => opened.close()}, image);
      if (accepted) petMenu.value = false;
    } finally { if (!accepted) opened.close(); }
  } catch (cause) { if (ticket === revision) error.value = errorMessage(cause); }
  finally { if (ticket === revision) pending.value = false; }
}
function chooseFile(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = '';
  if (file && !props.busy) return openFile(file, '本地图片');
}
function reopenSaved() {
  const prefix = 'data:image/png;base64,';
  if (!props.savedAvatar?.startsWith(prefix) || props.busy) return;
  try {
    const bytes = Uint8Array.from(atob(props.savedAvatar.slice(prefix.length)), ch => ch.charCodeAt(0));
    return openFile(new Blob([bytes], {type: 'image/png'}), '已保存头像');
  } catch (cause) { error.value = '无法打开已保存头像。' + errorMessage(cause); }
}

async function chooseAction(id: string) {
  const client = props.client;
  const selected = pet.value;
  const action = selected?.sources.find(item => item.id === id);
  if (!client || !selected || !action || props.busy) return;
  chosenAction.value = id;
  const ticket = startRead();
  try {
    const raw = await client.frame(selected.id, id, 0);
    if (ticket !== revision) return;
    accept(ticket, {
      label: selected.label + ' · ' + action.label, count: raw.frameCount,
      frame: async i => avatarFrameFromRgba(await client.frame(selected.id, id, i)),
    }, avatarFrameFromRgba(raw));
  } catch (cause) { if (ticket === revision) error.value = '无法读取桌宠动作。' + errorMessage(cause); }
  finally { if (ticket === revision) pending.value = false; }
}
function choosePet(id: string) {
  chosenPet.value = id;
  const initial = pet.value?.sources.find(item => item.id === 'idle') ?? pet.value?.sources[0];
  if (initial) return chooseAction(initial.id);
}
function showPets() {
  petMenu.value = true;
  const list = props.catalog;
  if (list?.pets.length) return choosePet(list.activeId || list.pets[0].id);
}

async function selectFrame(next: number) {
  const selected = source.value;
  if (!selected || !Number.isInteger(next) || next < 0 || next >= selected.count || props.busy) return;
  const ticket = startRead();
  try {
    const image = await selected.frame(next);
    accept(ticket, selected, image, next, crop.value);
  } catch (cause) { if (ticket === revision) error.value = '无法读取这一帧。' + errorMessage(cause); }
  finally { if (ticket === revision) pending.value = false; }
}

function stopEditing() {
  ++revision;
  source.value?.close?.();
  source.value = undefined;
  frame.value = undefined;
  drag = undefined;
  petMenu.value = false;
  pending.value = false;
  error.value = '';
}
function reset(useDefault: boolean) {
  stopEditing();
  emit('edit', useDefault && props.savedAvatar ? null : undefined);
}
onBeforeUnmount(stopEditing);

function setCrop(next: AvatarCrop) {
  const image = frame.value;
  if (!image || props.busy || pending.value) return;
  crop.value = clampAvatarCrop(image.width, image.height, next);
}
function publishCrop() {
  if (!frame.value) return;
  try {
    emit('edit', exportAvatarCrop(frame.value, crop.value));
    appliedCrop = crop.value;
    error.value = '';
  } catch (cause) { crop.value = appliedCrop; error.value = errorMessage(cause); }
}
function zoom(event: Event) {
  setCrop({...crop.value, zoom: Number((event.target as HTMLInputElement).value)});
  publishCrop();
}
function pointerDown(event: PointerEvent) {
  if (props.busy || pending.value || event.button !== 0 || drag || !target.value) return;
  target.value.setPointerCapture(event.pointerId);
  drag = {id: event.pointerId, x: event.clientX, y: event.clientY, crop: crop.value};
}
function pointerMove(event: PointerEvent) {
  if (!drag || drag.id !== event.pointerId || !target.value || !frame.value) return;
  setCrop(panAvatarCrop(frame.value.width, frame.value.height, drag.crop, event.clientX - drag.x,
    event.clientY - drag.y, target.value.getBoundingClientRect().width));
}
function finishDrag(event: PointerEvent) {
  if (!drag || drag.id !== event.pointerId) return;
  if (event.type === 'pointerup') pointerMove(event);
  drag = undefined;
  publishCrop();
}
function keyPan(event: KeyboardEvent) {
  if (!frame.value || !target.value || props.busy || pending.value) return;
  const offsets: Record<string, [number, number]> = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]};
  const offset = offsets[event.key];
  if (!offset) return;
  event.preventDefault();
  const step = event.shiftKey ? 16 : 4;
  setCrop(panAvatarCrop(frame.value.width, frame.value.height, crop.value, offset[0] * step, offset[1] * step,
    target.value.getBoundingClientRect().width));
  publishCrop();
}
function finishBeforeSave() {
  if (drag) { drag = undefined; publishCrop(); }
}
defineExpose({finishBeforeSave});
</script>

<template>
  <section class="vp-avatar-editor" aria-label="更换伙伴头像">
    <div class="vp-actions vp-avatar-actions">
      <label class="vp-file-button vp-subtle">选择图片
        <input type="file" accept="image/png,image/apng,image/jpeg,image/webp,image/gif,.apng" aria-label="选择伙伴头像" :aria-describedby="hint" :disabled="busy" @change="chooseFile" />
      </label>
      <button v-if="client" class="vp-subtle" type="button" :disabled="busy || !catalog?.pets.length" @click="showPets">从桌宠选择</button>
      <button v-if="savedAvatar && !source && draft === undefined" class="vp-mini" type="button" :disabled="busy" @click="reopenSaved">调整已保存头像</button>
      <button v-if="savedAvatar || source || pending" class="vp-mini" type="button" :disabled="busy" @click="reset(true)">恢复默认头像</button>
    </div>
    <p :id="hint" class="vp-hint">支持 PNG、JPEG、GIF、APNG、WebP，最大 8 MiB。{{ client ? '默认头像跟随当前桌宠，关闭桌宠时显示名字首字。' : '此宿主使用名字首字作为默认头像。' }}</p>
    <div v-if="petMenu && catalog?.pets.length" class="vp-pet-options">
      <label>桌宠<select aria-label="头像桌宠" :value="chosenPet" :disabled="busy" @change="choosePet(($event.target as HTMLSelectElement).value)">
        <option v-for="item in catalog.pets" :key="item.id" :value="item.id">{{ item.label }}</option>
      </select></label>
      <label>动作<select aria-label="桌宠动作" :value="chosenAction" :disabled="busy" @change="chooseAction(($event.target as HTMLSelectElement).value)">
        <option v-for="item in pet?.sources" :key="item.id" :value="item.id">{{ item.label }}</option>
      </select></label>
    </div>
    <p v-if="petError" class="vp-hint">{{ petError }} <button class="vp-mini" type="button" @click="emit('refresh')">重试桌宠头像</button></p>
    <div v-if="source" class="vp-crop-panel">
      <strong>{{ source.label }}</strong>
      <canvas ref="target" width="320" height="320" class="vp-crop-viewport" tabindex="0" aria-label="头像取景，拖动或按方向键移动图片"
        @pointerdown="pointerDown" @pointermove="pointerMove" @pointerup="finishDrag" @pointercancel="finishDrag" @lostpointercapture="finishDrag" @keydown="keyPan" />
      <span class="vp-hint">拖动图片或按方向键调整圆形取景。放大后可选取局部。</span>
      <label class="vp-crop-scale">缩放 · {{ crop.zoom.toFixed(1) }}×
        <input type="range" aria-label="头像缩放" min="1" max="8" step="0.05" :value="crop.zoom" :disabled="busy || pending" @input="zoom" />
      </label>
      <div v-if="source.count > 1" class="vp-frames">
        <label>动画帧 · {{ index + 1 }} / {{ source.count }}
          <input type="range" aria-label="选择动画帧" min="0" :max="source.count - 1" step="1" :value="index" :disabled="busy || pending"
            @change="selectFrame(Number(($event.target as HTMLInputElement).value))" />
        </label>
        <div class="vp-actions">
          <button class="vp-subtle" type="button" :disabled="busy || pending || index === 0" @click="selectFrame(index - 1)">上一帧</button>
          <button class="vp-subtle" type="button" :disabled="busy || pending || index === source.count - 1" @click="selectFrame(index + 1)">下一帧</button>
        </div>
      </div>
    </div>
    <p v-if="pending" class="vp-hint" role="status">正在读取头像帧…</p>
    <p v-if="error" class="vp-feedback" role="alert">{{ error }}</p>
    <div v-if="draft !== undefined || pending" class="vp-actions">
      <span class="vp-hint">{{ draft === null ? '保存后将使用默认头像。' : '头像修改在保存设定后生效。' }}</span>
      <button class="vp-mini" type="button" :disabled="busy" @click="reset(false)">撤销头像修改</button>
    </div>
  </section>
</template>
