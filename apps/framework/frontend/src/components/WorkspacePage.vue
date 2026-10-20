<script setup lang="ts">
import { ArrowUpRight, Blocks, Layers3, Plug, Plus } from '@lucide/vue';
defineProps<{ moduleCount: number; desktop: boolean }>();
defineEmits<{ navigate: [page: string] }>();
</script>

<template>
  <div class="workspace-page">
    <section class="welcome-panel">
      <div class="welcome-content">
        <p class="eyebrow"><span class="small-line"></span> 你的本地工作台</p>
        <h2>留出空间，<br /><span>让工具各就其位。</span></h2>
        <p class="welcome-description">从一个清晰的框架开始。<br />每个新功能，都有独立的位置和明确的边界。</p>
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
      </div>
    </section>

    <section class="modules-section" aria-labelledby="modules-heading">
      <div class="section-heading">
        <div><p class="eyebrow">WORKSPACE</p><h3 id="modules-heading">功能模块</h3></div>
        <span class="subtle-badge">{{ moduleCount }} 个已启用</span>
      </div>
      <div v-if="moduleCount === 0" class="empty-modules">
        <div class="empty-icon"><Plug :size="25" :stroke-width="1.5" /></div>
        <div><h4>暂未启用功能包</h4><p>在「功能管理」中导入完整包，或启用已有功能。</p></div>
        <span class="outline-tag">等待扩展</span>
      </div>
      <div v-else class="empty-modules">
        <div class="empty-icon"><Blocks :size="25" :stroke-width="1.5" /></div>
        <div><h4>功能包已就绪</h4><p>从左侧打开功能。每个页签使用自己选定的版本，首次打开时启动。</p></div>
      </div>
    </section>

    <div class="foundation-grid">
      <article class="foundation-card"><span class="card-index">01 / DESKTOP</span><h4>桌面宿主</h4><p>窗口与系统能力，统一管理。</p><span class="card-footnote">{{ desktop ? 'Tauri 桌面环境' : '当前为浏览器预览' }}</span></article>
      <article class="foundation-card"><span class="card-index">02 / MODULES</span><h4>独立功能包</h4><p>各自启用，各自选择版本。</p><span class="card-footnote">前端与后台随同一个包交付</span></article>
      <article class="foundation-card"><span class="card-index">03 / WORKER</span><h4>按需运行</h4><p>需要后台能力时，再启动任务。</p><span class="card-footnote">Python worker 默认不启动</span></article>
    </div>
  </div>
</template>
