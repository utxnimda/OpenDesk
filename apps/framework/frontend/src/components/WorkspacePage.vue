<script setup lang="ts">
import { computed } from 'vue';
import { Activity, ArrowUpRight, Blocks, Grid2X2, Layers3, Monitor, Package, Plug, Plus, Settings2, Sparkles } from '@lucide/vue';
import type { UIStyle } from '../core/host';
import type { InstalledModule } from '../core/packages';

const props = defineProps<{
  modules: readonly Pick<InstalledModule, 'id' | 'label' | 'selectedVersion'>[];
  desktop: boolean;
  uiStyle: UIStyle;
  loading?: boolean;
}>();
defineEmits<{ navigate: [page: string] }>();

const copyByStyle: Record<UIStyle, {
  eyebrow: string;
  title: string;
  subtitle: string;
  description: string;
  moduleTitle: string;
}> = {
  simple: {
    eyebrow: '你的本地工作台',
    title: '留出空间，',
    subtitle: '让工具各就其位。',
    description: '从一个清晰的框架开始。每个新功能，都有独立的位置和明确的边界。',
    moduleTitle: '功能模块',
  },
  codex: {
    eyebrow: '从这里开始',
    title: '今天，想做些什么？',
    subtitle: '打开工具，专注眼前。',
    description: '常用功能集中在这里。选择一个打开，或管理你的功能包。',
    moduleTitle: '选择一个工具',
  },
  cursor: {
    eyebrow: 'OPENDESK / WORKSPACE',
    title: '一个工作区，',
    subtitle: '有序切换每项工作。',
    description: '浏览可用功能与已选版本，按需打开工具。',
    moduleTitle: '可用功能',
  },
  web: {
    eyebrow: '你的工具，一站直达',
    title: '让常用工具，',
    subtitle: '触手可及。',
    description: '从这里打开功能、整理工具，或调整工作台外观。',
    moduleTitle: '工具入口',
  },
  anime: {
    eyebrow: '欢迎来到工具小站',
    title: '打开今天的灵感，',
    subtitle: '从一个小工具开始。',
    description: '工具都有自己的位置。挑选今天要用的那一个，轻松开始。',
    moduleTitle: '今天的工具伙伴',
  },
  glass: {
    eyebrow: 'OPENDESK / CONTROL PANEL',
    title: '工作区总览',
    subtitle: '每项工具，随时就绪。',
    description: '从左侧选择功能，或在这里查看和管理可用工具。',
    moduleTitle: '可用功能',
  },
};
const copy = computed(() => copyByStyle[props.uiStyle]);
const shortcuts = [
  { page: 'workspace', label: '工作台', icon: Grid2X2 },
  { page: 'packages', label: '功能管理', icon: Package },
  { page: 'settings', label: '外观与设置', icon: Settings2 },
  { page: 'diagnostics', label: '运行诊断', icon: Activity },
];
</script>

<template>
  <div class="workspace-page">
    <section class="welcome-panel">
      <div class="welcome-content">
        <p class="eyebrow"><span class="small-line"></span> {{ copy.eyebrow }}</p>
        <h2>{{ copy.title }}<br /><span>{{ copy.subtitle }}</span></h2>
        <p v-if="uiStyle === 'simple'" class="welcome-description">从一个清晰的框架开始。<br />每个新功能，都有独立的位置和明确的边界。</p>
        <p v-else class="welcome-description">{{ copy.description }}</p>
        <button class="button button-primary" @click="$emit('navigate', 'packages')">
          管理功能 <ArrowUpRight :size="16" />
        </button>
      </div>
      <div class="workspace-art" aria-hidden="true">
        <div class="art-orbit orbit-one"></div><div class="art-orbit orbit-two"></div>
        <div class="art-tile tile-back"><Layers3 :size="28" :stroke-width="1.5" /></div>
        <div class="art-tile tile-main"><Blocks :size="48" :stroke-width="1.3" /></div>
        <div class="art-tile tile-small"><Plus :size="22" :stroke-width="1.5" /></div>
        <span class="art-dot dot-one"></span><span class="art-dot dot-two"></span>
        <template v-if="uiStyle === 'anime'">
          <span class="workspace-sparkles"><Sparkles :size="32" :stroke-width="1.5" /></span>
          <span class="workspace-art-label">TOOL STATION</span>
        </template>
      </div>
    </section>

    <nav class="workspace-shortcuts" aria-label="工作台快捷入口">
      <button v-for="shortcut in shortcuts" :key="shortcut.page" class="workspace-shortcut" @click="$emit('navigate', shortcut.page)">
        <component :is="shortcut.icon" :size="17" :stroke-width="1.7" aria-hidden="true" />
        <span>{{ shortcut.label }}</span>
      </button>
    </nav>

    <section class="modules-section" aria-labelledby="modules-heading">
      <div class="section-heading">
        <div><p class="eyebrow">WORKSPACE</p><h3 id="modules-heading">{{ copy.moduleTitle }}</h3></div>
        <span class="subtle-badge">{{ !desktop ? '浏览器预览' : loading ? '正在读取' : `${modules.length} 个可打开` }}</span>
      </div>
      <div v-if="!desktop" class="empty-modules">
        <div class="empty-icon"><Monitor :size="25" :stroke-width="1.5" aria-hidden="true" /></div>
        <div><h4>当前为浏览器预览</h4><p>可以预览界面与外观；打开桌面应用后，才会读取和运行本地功能。</p></div>
      </div>
      <div v-else-if="loading" class="empty-modules" role="status">
        <div class="empty-icon"><Layers3 :size="25" :stroke-width="1.5" aria-hidden="true" /></div>
        <div><h4>正在读取功能目录…</h4><p>可打开的功能将在这里显示。</p></div>
      </div>
      <div v-else-if="modules.length === 0" class="empty-modules">
        <div class="empty-icon"><Plug :size="25" :stroke-width="1.5" aria-hidden="true" /></div>
        <div><h4>暂无可打开的功能</h4><p>在「功能管理」中导入功能包，或启用已有的兼容版本。</p></div>
        <span class="outline-tag">等待扩展</span>
      </div>
      <div v-else class="workspace-module-grid">
        <button v-for="module in modules" :key="module.id" class="workspace-module-card" :aria-label="`打开${module.label}`" @click="$emit('navigate', module.id)">
          <span class="workspace-module-icon"><Blocks :size="24" :stroke-width="1.5" aria-hidden="true" /></span>
          <span class="workspace-module-copy"><strong>{{ module.label }}</strong><small>{{ module.selectedVersion ? `已选版本 ${module.selectedVersion}` : '版本信息待确认' }}</small></span>
          <ArrowUpRight class="workspace-module-arrow" :size="18" aria-hidden="true" />
        </button>
      </div>
    </section>

    <div class="foundation-grid">
      <article class="foundation-card"><span class="card-index">01 / DESKTOP</span><h4>桌面宿主</h4><p>窗口与系统能力，统一管理。</p><span class="card-footnote">{{ desktop ? 'Tauri 桌面环境' : '当前为浏览器预览' }}</span></article>
      <article class="foundation-card"><span class="card-index">02 / MODULES</span><h4>独立功能包</h4><p>各自启用，各自选择版本。</p><span class="card-footnote">前端与后台随同一个包交付</span></article>
      <article class="foundation-card"><span class="card-index">03 / WORKER</span><h4>按需运行</h4><p>需要后台能力时，再启动处理。</p><span class="card-footnote">运行状态可在诊断页查看</span></article>
    </div>
  </div>
</template>
