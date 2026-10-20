<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { errorMessage, type LimitedHost, type ModuleIdentity } from '../../../framework/module-sdk/src/index';

const props = defineProps<{ host: LimitedHost; identity: ModuleIdentity }>();
const version = __MODULE_VERSION__;
const marker = __MODULE_MARKER__;
const count = ref(0); const input = ref('你好，独立功能包'); const busy = ref(false); const failure = ref('');
const result = ref<{ version: string; marker: string; text: string; pid: number } | null>(null);
async function echo() {
  busy.value = true; failure.value = ''; result.value = null;
  try { result.value = await props.host.callWorker('test.echo', { text: input.value }); }
  catch (error) { failure.value = errorMessage(error); }
  finally { busy.value = false; }
}
// This test module's initial echo is deliberately side-effect free. It proves
// the package UI reached its own version-bound worker through the real bridge.
onMounted(echo);
</script>

<template>
  <div class="page-stack test-lab"><header class="module-standalone-title"><p class="eyebrow">PACKAGE TEST LAB</p><h2>模块测试台</h2><p>实际的独立前端与后台，用于验证导入、切换版本和运行实例隔离。</p></header>
    <section class="panel"><h3>当前包身份</h3><dl class="info-grid test-identities"><div><dt>前端版本</dt><dd data-testid="frontend-version">{{ version }}</dd></div><div><dt>前端构建标记</dt><dd data-testid="frontend-marker">{{ marker }}</dd></div><div><dt>宿主选定版本</dt><dd>{{ identity.version }}</dd></div><div><dt>本次实例</dt><dd class="mono">{{ identity.generation }}</dd></div><div class="info-full"><dt>包摘要</dt><dd class="mono path-value">{{ identity.digest }}</dd></div></dl><div v-if="version !== identity.version" class="alert alert-error">前端版本和宿主选定版本不一致。</div></section>
    <section class="panel"><h3>界面状态隔离</h3><p class="panel-description">切到其他页签再回来，计数应保留；切换本功能版本或停用再启用，新的 JS 上下文应从 0 开始。</p><div class="button-row"><button class="button button-primary" @click="count++">计数 +1</button><output class="test-counter" data-testid="instance-counter">{{ count }}</output></div></section>
    <section class="panel"><h3>真实后台调用</h3><p class="panel-description">发送到当前包自带的 worker。返回版本、构建标记与 PID，便于确认前后端来自同一版本。</p><label class="test-echo-input">测试内容<input v-model="input" maxlength="2000" /></label><button class="button button-primary" :disabled="busy || !host.available" @click="echo">{{ busy ? '正在调用…' : '测试 echo' }}</button><div v-if="failure" class="alert alert-error" role="alert">{{ failure }}</div><div v-if="result" class="worker-result" role="status"><div class="result-title">后台响应已返回</div><dl class="info-grid"><div><dt>后台版本</dt><dd data-testid="backend-version">{{ result.version }}</dd></div><div><dt>后台构建标记</dt><dd data-testid="backend-marker">{{ result.marker }}</dd></div><div><dt>PID</dt><dd>{{ result.pid }}</dd></div><div><dt>返回内容</dt><dd>{{ result.text }}</dd></div></dl><div v-if="result.version !== version || result.marker !== marker" class="alert alert-error">前后端版本或构建标记不匹配。</div></div></section>
  </div>
</template>

<style scoped>
.test-identities { margin-top: 20px; }
.test-counter { font-size: 24px; min-width: 50px; text-align: center; }
.test-echo-input { display: flex; flex-direction: column; gap: 8px; font-size: 12px; color: var(--muted); margin-bottom: 16px; }
.test-echo-input input { border: 1px solid var(--border); border-radius: 6px; padding: 10px; background: var(--surface-soft); color: var(--text); }
.test-lab .panel > h3 { margin-bottom: 16px; }
</style>
