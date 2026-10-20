<script setup lang="ts">
import { ref } from 'vue';
import { Activity, Check, CircleStop, LoaderCircle, Play, RefreshCw, Terminal } from '@lucide/vue';
import { errorMessage, type HostAdapter, type HostInfo, type PingResult } from '../core/host';

const props = defineProps<{ host: HostAdapter; info: HostInfo | null }>();
const emit = defineEmits<{ refresh: [] }>();
const pending = ref<'ping' | 'stop' | null>(null);
const failure = ref('');
const result = ref<PingResult | null>(null);
const stopped = ref(false);

async function ping() {
  pending.value = 'ping'; failure.value = ''; result.value = null; stopped.value = false;
  try {
    result.value = await props.host.callWorker<PingResult>('runtime.ping');
    emit('refresh');
  } catch (error) { failure.value = errorMessage(error); }
  finally { pending.value = null; }
}

async function stop() {
  pending.value = 'stop'; failure.value = ''; stopped.value = false;
  try {
    await props.host.stopWorker(); result.value = null; stopped.value = true; emit('refresh');
  } catch (error) { failure.value = errorMessage(error); }
  finally { pending.value = null; }
}
</script>

<template>
  <div class="page-stack">
    <div class="page-intro"><p class="eyebrow">RUNTIME</p><h2>运行诊断</h2><p>查看真实的宿主状态，验证前后端通信。</p></div>
    <section class="panel">
      <div class="panel-heading"><div class="panel-title"><Activity :size="19" /><h3>桌面宿主</h3></div><button class="button button-quiet button-small" :disabled="!host.available" @click="$emit('refresh')"><RefreshCw :size="14" /> 刷新</button></div>
      <dl class="info-grid">
        <div><dt>运行环境</dt><dd>{{ host.available ? 'Tauri 桌面' : '浏览器预览' }}</dd></div>
        <div><dt>应用版本</dt><dd>{{ info?.appVersion ?? '—' }}</dd></div>
        <div><dt>系统 / 架构</dt><dd>{{ info ? `${info.platform} / ${info.architecture}` : '—' }}</dd></div>
        <div><dt>运行模式</dt><dd>{{ info ? (info.packaged ? '正式打包' : '开发环境') : '—' }}</dd></div>
        <div class="info-full"><dt>数据目录</dt><dd class="mono path-value">{{ info?.dataDir ?? '桌面环境可用后显示' }}</dd></div>
      </dl>
    </section>
    <section class="panel">
      <div class="panel-heading"><div class="panel-title"><Terminal :size="19" /><h3>Python worker</h3></div><span class="status-pill" :class="{ 'is-running': info?.workerState === 'running' }"><i></i>{{ info ? (info.workerState === 'running' ? '运行中' : '未启动') : '状态不可用' }}</span></div>
      <p class="panel-description">发送一次 <code>runtime.ping</code>。这会按需启动 worker，并返回进程和协议版本；进入本页不会启动后台进程。</p>
      <div class="button-row">
        <button class="button button-primary" :disabled="!host.available || pending !== null" @click="ping"><LoaderCircle v-if="pending === 'ping'" class="spinning" :size="16" /><Play v-else :size="16" />{{ pending === 'ping' ? '正在检查…' : '检查 worker 通信' }}</button>
        <button class="button button-secondary" :disabled="!host.available || pending !== null || info?.workerState !== 'running'" @click="stop"><CircleStop :size="16" /> 停止 worker</button>
      </div>
      <div v-if="failure" class="alert alert-error" role="alert"><strong>操作失败</strong><span>{{ failure }}</span></div>
      <div v-if="result" class="worker-result" role="status"><div class="result-title"><Check :size="16" />通信成功 · {{ result.message }}</div><dl class="info-grid compact"><div><dt>PID</dt><dd class="mono">{{ result.pid }}</dd></div><div><dt>Python</dt><dd class="mono">{{ result.pythonVersion }}</dd></div><div><dt>协议版本</dt><dd class="mono">{{ result.protocolVersion }}</dd></div></dl></div>
      <div v-if="stopped" class="alert alert-success" role="status">worker 已停止。下次调用时将按需重新启动。</div>
      <p v-if="!host.available" class="inline-note">浏览器预览，桌面能力不可用。请启动 Tauri 桌面应用后验证通信。</p>
    </section>
  </div>
</template>
