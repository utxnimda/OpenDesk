<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue';
import { AppWindow, EyeOff, Monitor, Pause, Pin, Play, X } from '@lucide/vue';
import type { PetSettings } from '../core/personalization';
import type { CompanionModel } from './contracts';
import { companionMenu, type CompanionMenuChoice, type CompanionMenuEntry } from './menu';

const props = withDefaults(defineProps<{
  model: CompanionModel;
  settings: PetSettings;
  canAnimate: boolean;
  desktopAvailable?: boolean;
  busy?: boolean;
  error?: string;
}>(), { desktopAvailable: true, busy: false, error: '' });
const emit = defineEmits<{ select: [choice: CompanionMenuChoice]; close: [] }>();
type MenuItem = Extract<CompanionMenuEntry, { kind: 'item' }>;
const element = ref<HTMLElement | null>(null);
const flatten = (entries: CompanionMenuEntry[]): MenuItem[] => entries.flatMap((entry) =>
  entry.kind === 'item' ? [entry] : entry.kind === 'group' ? flatten(entry.items) : []);
const entries = computed(() => flatten(companionMenu(props.model, props.settings, props.canAnimate)));
const animation = computed(() => entries.value.find((entry) => entry.id === 'toggle-animation'));
const positions = computed(() => entries.value.filter((entry) => entry.id.startsWith('mode:')));
const actions = computed(() => entries.value.filter((entry) => entry.id.startsWith('action:')));
const sizes = computed(() => entries.value.filter((entry) => entry.id.startsWith('size:')));
const top = computed(() => entries.value.find((entry) => entry.id === 'toggle-top'));
const footer = computed(() => entries.value.filter((entry) => entry.id === 'hide' || entry.id === 'open-app'));
const status = computed(() => props.busy ? '正在更新…' : !props.canAnimate ? '静态陪伴' : props.settings.animated ? '正在陪伴' : '动画已暂停');
let disposed = false;

function disabled(entry: MenuItem) {
  return props.busy || entry.enabled === false
    || (!props.desktopAvailable && ['mode:desktop', 'toggle-top', 'open-app'].includes(entry.id));
}
function select(entry: MenuItem) { if (!disabled(entry)) emit('select', entry.id); }
function tabIndex(entry: MenuItem, group: MenuItem[]) {
  const enabled = group.filter((item) => !disabled(item));
  return (enabled.find((item) => item.checked) ?? enabled[0])?.id === entry.id ? 0 : -1;
}
function focusable() {
  return [...element.value?.querySelectorAll<HTMLButtonElement>('button:not(:disabled):not([tabindex="-1"])') ?? []];
}
function key(event: KeyboardEvent) {
  if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); emit('close'); return; }
  const target = event.target instanceof Element ? event.target.closest('button') : null;
  const group = target?.closest('[role="radiogroup"]');
  if (group && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) {
    event.preventDefault(); event.stopPropagation();
    const options = [...group.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')];
    if (!options.length) return;
    const index = options.indexOf(target as HTMLButtonElement);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1
      : (index + (['ArrowLeft', 'ArrowUp'].includes(event.key) ? -1 : 1) + options.length) % options.length;
    options[next].focus(); options[next].click();
    return;
  }
  if (!['Tab', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
  event.preventDefault(); event.stopPropagation();
  const options = focusable();
  if (!options.length) { element.value?.focus(); return; }
  const index = options.indexOf(document.activeElement as HTMLButtonElement);
  const backwards = event.key === 'ArrowUp' || (event.key === 'Tab' && event.shiftKey);
  const next = event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1
    : index < 0 ? (backwards ? options.length - 1 : 0) : (index + (backwards ? -1 : 1) + options.length) % options.length;
  options[next].focus();
}
onMounted(async () => {
  await nextTick();
  if (disposed) return;
  (element.value?.querySelector<HTMLButtonElement>('.pet-menu-play:not(:disabled)') ?? focusable()[0] ?? element.value)?.focus();
});
onUnmounted(() => { disposed = true; });
</script>

<template>
  <div ref="element" class="pet-menu-panel" role="dialog" :aria-label="`${model.label}的宠物菜单`" aria-modal="true" :aria-busy="busy" tabindex="-1" @keydown="key" @pointerdown.stop @click.stop @contextmenu.prevent.stop>
    <div class="pet-menu-card">
      <header class="pet-menu-header">
        <div class="pet-menu-avatar"><img :src="model.assets.still" alt="" draggable="false" /></div>
        <div class="pet-menu-identity"><strong>{{ model.label }}</strong><span aria-live="polite"><i :class="{ 'is-playing': settings.animated && canAnimate }" aria-hidden="true" />{{ status }}</span></div>
        <button type="button" class="pet-menu-close" aria-label="关闭宠物菜单" @click="emit('close')"><X :size="16" aria-hidden="true" /></button>
      </header>
      <div class="pet-menu-content">
        <button v-if="animation" type="button" class="pet-menu-play" :data-choice="animation.id" :disabled="disabled(animation)" @click="select(animation)">
          <Pause v-if="settings.animated" :size="16" aria-hidden="true" /><Play v-else :size="16" aria-hidden="true" />{{ animation.label }}
        </button>
        <section class="pet-menu-section">
          <h3>显示位置</h3>
          <div class="pet-menu-segment" role="radiogroup" aria-label="显示位置">
            <button v-for="entry in positions" :key="entry.id" type="button" role="radio" :data-choice="entry.id" :aria-checked="entry.checked" :tabindex="tabIndex(entry, positions)" :disabled="disabled(entry)" :title="entry.id === 'mode:desktop' && !desktopAvailable ? '桌面版中可用' : undefined" @click="select(entry)">
              <Monitor v-if="entry.id === 'mode:desktop'" :size="14" aria-hidden="true" /><AppWindow v-else :size="14" aria-hidden="true" />{{ entry.label }}
            </button>
          </div>
        </section>
        <section class="pet-menu-section">
          <h3>动作模式</h3>
          <div class="pet-menu-chips" role="radiogroup" aria-label="动作模式">
            <button v-for="entry in actions" :key="entry.id" type="button" role="radio" :data-choice="entry.id" :aria-checked="entry.checked" :tabindex="tabIndex(entry, actions)" :disabled="disabled(entry)" @click="select(entry)">{{ entry.label }}</button>
          </div>
        </section>
        <section class="pet-menu-section">
          <h3>形象大小<span>px</span></h3>
          <div class="pet-menu-sizes" role="radiogroup" aria-label="形象大小">
            <button v-for="entry in sizes" :key="entry.id" type="button" role="radio" :data-choice="entry.id" :aria-label="entry.label" :aria-checked="entry.checked" :tabindex="tabIndex(entry, sizes)" :disabled="disabled(entry)" @click="select(entry)">{{ entry.id.slice(5) }}</button>
          </div>
        </section>
        <button v-if="top && settings.mode === 'desktop'" type="button" class="pet-menu-top" role="switch" :data-choice="top.id" :aria-checked="top.checked" :disabled="disabled(top)" @click="select(top)">
          <span><Pin :size="14" aria-hidden="true" />{{ top.label }}</span><i class="pet-menu-switch" aria-hidden="true"><i /></i>
        </button>
        <p v-if="error" class="pet-menu-error" role="alert">{{ error }}</p>
      </div>
      <footer class="pet-menu-footer">
        <button v-for="entry in footer" :key="entry.id" type="button" :data-choice="entry.id" :disabled="disabled(entry)" @click="select(entry)">
          <EyeOff v-if="entry.id === 'hide'" :size="14" aria-hidden="true" /><AppWindow v-else :size="14" aria-hidden="true" />{{ entry.label }}
        </button>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.pet-menu-panel {
  --pet-card: #fcfefd; --pet-ink: #31554f; --pet-muted: #5d766c; --pet-line: #e2ece7;
  --pet-soft: #f3f7f4; --pet-mint: #e4f5ed; --pet-mint-line: #b4dccd; --pet-accent: #327a65;
  --pet-strong: #d4efe3; --pet-avatar: linear-gradient(145deg, #e5f6ed, #faedf0); --pet-shadow: #324b4820;
  --pet-error-bg: #fff1f1; --pet-error-ink: #a33d46;
  display: flex; flex-direction: column; min-height: 0; width: 320px; max-width: 100%; max-height: 100%; padding: 8px;
  color: var(--pet-ink); font: 12px/1.4 system-ui, sans-serif; pointer-events: auto; outline: none;
}
.pet-menu-panel, .pet-menu-panel * { box-sizing: border-box; }
.pet-menu-card { display: flex; flex: 0 1 auto; flex-direction: column; min-height: 0; max-height: min(444px, calc(100dvh - 16px)); overflow: hidden; border: 1px solid var(--pet-line); border-radius: 18px; background: var(--pet-card); box-shadow: 0 6px 18px var(--pet-shadow); }
.pet-menu-panel button { box-sizing: border-box; display: inline-flex; align-items: center; justify-content: center; gap: 6px; margin: 0; border: 1px solid transparent; border-radius: 9px; color: inherit; font: inherit; line-height: 1.4; cursor: pointer; transition: background-color .12s, border-color .12s; }
.pet-menu-panel button:disabled { opacity: .42; cursor: default; }
.pet-menu-panel button:focus-visible { outline: 2px solid var(--pet-accent); outline-offset: 2px; }
.pet-menu-panel button:not(:disabled):hover { background: var(--pet-mint); border-color: var(--pet-mint-line); }
.pet-menu-header { display: flex; flex: 0 0 auto; align-items: center; gap: 10px; padding: 14px 14px 10px; }
.pet-menu-avatar { flex: 0 0 42px; width: 42px; height: 42px; overflow: hidden; border: 1px solid var(--pet-line); border-radius: 13px; background: var(--pet-avatar); }
.pet-menu-avatar img { display: block; width: 100%; height: 100%; object-fit: contain; }
.pet-menu-identity { display: flex; flex: 1; flex-direction: column; min-width: 0; gap: 3px; }
.pet-menu-identity strong { overflow: hidden; color: var(--pet-ink); font-size: 14px; font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }
.pet-menu-identity > span { display: flex; align-items: center; gap: 5px; color: var(--pet-muted); font-size: 10px; }
.pet-menu-identity i { width: 5px; height: 5px; border-radius: 50%; background: var(--pet-muted); }
.pet-menu-identity i.is-playing { background: var(--pet-accent); }
.pet-menu-panel .pet-menu-close { flex: 0 0 27px; width: 27px; height: 27px; padding: 0; border-radius: 50%; background: var(--pet-soft); color: var(--pet-muted); }
.pet-menu-content { min-height: 0; overflow: auto; overscroll-behavior: contain; padding: 2px 14px 12px; scrollbar-width: thin; scrollbar-color: var(--pet-mint-line) transparent; }
.pet-menu-panel .pet-menu-play { width: 100%; min-height: 35px; background: var(--pet-mint); border-color: var(--pet-mint-line); color: var(--pet-accent); font-weight: 600; }
.pet-menu-panel .pet-menu-play:not(:disabled):hover { background: var(--pet-strong); }
.pet-menu-section { margin-top: 13px; }
.pet-menu-section h3 { display: flex; align-items: center; gap: 5px; margin: 0 0 6px; color: var(--pet-muted); font: 500 11px/1.4 system-ui, sans-serif; }
.pet-menu-section h3 span { margin-left: auto; font-size: 10px; }
.pet-menu-segment { display: flex; gap: 3px; padding: 3px; border-radius: 10px; background: var(--pet-soft); }
.pet-menu-segment button { flex: 1; min-width: 0; min-height: 29px; padding: 4px 2px; background: transparent; font-size: 11px; }
.pet-menu-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.pet-menu-chips button { min-height: 28px; padding: 4px 10px; background: var(--pet-soft); font-size: 11px; }
.pet-menu-sizes { display: flex; gap: 5px; }
.pet-menu-sizes button { flex: 1; min-width: 0; height: 29px; padding: 3px 0; background: var(--pet-soft); font-size: 11px; font-variant-numeric: tabular-nums; }
.pet-menu-panel [role="radio"][aria-checked="true"] { background: var(--pet-mint); border-color: var(--pet-mint-line); color: var(--pet-accent); font-weight: 600; }
.pet-menu-panel .pet-menu-top { display: flex; justify-content: space-between; width: 100%; min-height: 35px; margin-top: 12px; padding: 4px 1px; background: transparent; border-radius: 6px; font-size: 11px; }
.pet-menu-top > span { display: inline-flex; align-items: center; gap: 6px; }
.pet-menu-switch { display: block; box-sizing: border-box; width: 29px; height: 17px; padding: 2px; border: 1px solid var(--pet-line); border-radius: 10px; background: var(--pet-muted); }
.pet-menu-switch > i { display: block; width: 11px; height: 11px; border-radius: 50%; background: var(--pet-card); }
.pet-menu-top[aria-checked="true"] .pet-menu-switch { background: var(--pet-accent); border-color: var(--pet-accent); }
.pet-menu-top[aria-checked="true"] .pet-menu-switch > i { transform: translateX(12px); }
.pet-menu-error { margin: 10px 0 0; padding: 8px; border-radius: 8px; background: var(--pet-error-bg); color: var(--pet-error-ink); font-size: 11px; overflow-wrap: anywhere; }
.pet-menu-footer { display: flex; flex: 0 0 auto; gap: 4px; padding: 8px 10px; border-top: 1px solid var(--pet-line); }
.pet-menu-footer button { flex: 1; min-width: 0; min-height: 29px; padding: 3px; background: transparent; color: var(--pet-muted); font-size: 11px; white-space: nowrap; }
:root[data-theme="dark"] .pet-menu-panel, :root[data-theme="graphite"] .pet-menu-panel {
  --pet-card: #222d2c; --pet-ink: #dcebe5; --pet-muted: #a1b4ac; --pet-line: #374843;
  --pet-soft: #2b3733; --pet-mint: #2e4a40; --pet-mint-line: #527d6c; --pet-accent: #a4dec6;
  --pet-strong: #38594c; --pet-avatar: linear-gradient(145deg, #2e4a40, #453b43); --pet-shadow: #0005;
  --pet-error-bg: #452c33; --pet-error-ink: #f2bdc3;
}
@media (prefers-reduced-motion: reduce) { .pet-menu-panel button { transition: none; } }
</style>
