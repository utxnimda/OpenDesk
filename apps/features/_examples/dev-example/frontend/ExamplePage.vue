<script setup lang="ts">
import { ref } from 'vue';
import type { ModuleServices } from '../../../../framework/frontend/src/core/modules';
import { errorMessage, type PingResult } from '../../../../framework/frontend/src/core/host';

const props = defineProps<{ services: ModuleServices }>();
const pending = ref(false); const result = ref<PingResult | null>(null); const failure = ref('');
async function run() {
  pending.value = true; failure.value = ''; result.value = null;
  try { result.value = await props.services.host.callWorker<PingResult>('runtime.ping'); }
  catch (error) { failure.value = errorMessage(error); }
  finally { pending.value = false; }
}
</script>

<template>
  <div class="page-stack"><div class="page-intro"><p class="eyebrow">DEVELOPMENT ONLY</p><h2>开发示例</h2><p>通过模块注册加载页面，通过框架服务调用后台；无需修改界面外壳。</p></div><section class="panel"><h3>检查扩展链路</h3><p class="panel-description">此页面只在开发环境且 VITE_ENABLE_EXAMPLE_MODULE=1 时出现。它不包含业务功能。</p><button class="button button-primary" :disabled="pending || !services.host.available" @click="run">{{ pending ? '正在调用…' : '调用 runtime.ping' }}</button><pre v-if="result" class="code-output" role="status">{{ JSON.stringify(result, null, 2) }}</pre><div v-if="failure" class="alert alert-error" role="alert">{{ failure }}</div><p v-if="!services.host.available" class="inline-note">浏览器预览，桌面能力不可用。</p></section></div>
</template>
