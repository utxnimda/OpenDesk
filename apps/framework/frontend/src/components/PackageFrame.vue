<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue';
import { errorMessage, type HostAdapter, type Theme, type UIStyle } from '../core/host';
import { packageSandbox, type InstalledModule, type PackageInstance } from '../core/packages';
import { createPackageBroker, isModuleHello, packageAppearance } from '../core/package-broker';
import type { SurfaceEffects } from '../../../module-sdk/src/index';

const props = defineProps<{ module: InstalledModule; host: HostAdapter; theme: Exclude<Theme, 'system'>; uiStyle: UIStyle; surfaceEffects?: SurfaceEffects }>();
const emit = defineEmits<{ failure: [message: string] }>();
const frame = ref<HTMLIFrameElement | null>(null);
const source = ref(''); const failure = ref(''); const ready = ref(false);
const sandbox = ref(packageSandbox(null));
let instance: PackageInstance | null = null;
let closed = false;
let origin = '';
let channel: MessageChannel | null = null;
let broker: ReturnType<typeof createPackageBroker> | null = null;
let timer: ReturnType<typeof setTimeout> | null = null;
let loadFailureReported = false;
let reportingFailure = false;

function detachFrame() {
  broker?.dispose(); channel?.port1.close(); channel?.port2.close(); channel = null;
  source.value = '';
  if (timer) clearTimeout(timer);
}

async function releaseInstance() {
  detachFrame();
  // modules_load_failed owns rollback. Do not race it with modules_close when
  // the restored catalog causes this failed frame to unmount.
  if (reportingFailure) return;
  const owned = instance; instance = null; ready.value = false;
  if (owned) {
    try { await props.host.closePackage(owned.token); }
    catch (error) { emit('failure', `关闭 ${props.module.label} 时失败：${errorMessage(error)}`); }
  }
}

async function reportLoadFailure(reason: string) {
  failure.value = reason;
  if (ready.value) {
    emit('failure', `${props.module.label}运行界面出现错误：${reason}。当前版本没有自动回退。`);
    return;
  }
  if (loadFailureReported) return;
  loadFailureReported = true;
  emit('failure', `${props.module.label} v${props.module.selectedVersion}加载失败：${reason}`);
  const owned = instance;
  detachFrame();
  if (!owned) return;
  reportingFailure = true;
  try {
    await props.host.reportPackageLoadFailed(owned.token, reason);
    if (instance?.token === owned.token) instance = null;
    // The host emits modules-changed; its current selection is authoritative.
    emit('failure', `${props.module.label} v${owned.version}加载失败：${reason}。宿主已处理本次失败，当前选择请查看「功能管理」。`);
  } catch (error) {
    emit('failure', `${props.module.label}加载失败：${reason}；报告失败状态时又出现错误：${errorMessage(error)}。请刷新功能目录检查当前选择。`);
  } finally {
    reportingFailure = false;
    if (closed && instance) await releaseInstance();
  }
}

function receive(event: MessageEvent) {
  if (closed || !instance || channel || !isModuleHello(event, frame.value?.contentWindow ?? null, origin, instance.generation)) return;
  const owned = instance;
  const transport = new MessageChannel(); channel = transport;
  broker = createPackageBroker(owned.generation, (method, params) => props.host.callPackage(owned.token, method, params), async (identity) => {
    if (identity.moduleId !== owned.moduleId || identity.version !== owned.version || identity.digest !== owned.digest) {
      const reason = '模块前端的编译身份与选定包版本不匹配，已拒绝执行。';
      await reportLoadFailure(reason);
      throw new Error(reason);
    }
    try { await props.host.reportPackageReady(owned.token, owned.moduleId, owned.version, owned.digest); }
    catch (error) { await reportLoadFailure(errorMessage(error)); throw error; }
    if (!closed) ready.value = true;
    if (timer) clearTimeout(timer);
  }, async (operation) => {
    if (operation.action !== 'open') throw new Error('请在独立窗口中操作其关闭、拖动和置顶。');
    await props.host.openPackageSurface(owned.token, operation.params);
  }, async operation => {
    if (operation.action === 'list' && props.host.listPackageTools) return props.host.listPackageTools(owned.token);
    if (operation.action === 'grant' && props.host.grantPackageTools) return props.host.grantPackageTools(owned.token, operation.params.tools);
    if (operation.action === 'call' && props.host.callPackageTool) return props.host.callPackageTool(owned.token, operation.params.tool, operation.params.arguments);
    throw new Error('当前宿主不支持此工具操作。');
  });
  transport.port1.onmessage = (message: MessageEvent) => {
    void broker?.receive(message.data, (value) => { if (!closed) transport.port1.postMessage(value); });
  };
  transport.port1.start();
  frame.value!.contentWindow!.postMessage({
    type: 'nimdadesk:connect', protocol: 1,
    identity: { moduleId: owned.moduleId, version: owned.version, digest: owned.digest, generation: owned.generation },
    ...packageAppearance(props.theme, props.uiStyle, props.surfaceEffects),
    moduleSurface: { context: null },
    moduleTools: 1,
  }, origin, [transport.port2]);
}
watch(() => [props.theme, props.uiStyle, props.surfaceEffects] as const, ([theme, uiStyle, effects]) => {
  if (channel && instance && !closed) channel.port1.postMessage({ type: 'theme', generation: instance.generation, ...packageAppearance(theme, uiStyle, effects) });
}, { deep: true });
onMounted(async () => {
  window.addEventListener('message', receive);
  try {
    const opened = await props.host.openPackage(props.module.id);
    if (closed) { await props.host.closePackage(opened.token); return; }
    instance = opened;
    if (opened.moduleId !== props.module.id || opened.version !== props.module.selectedVersion || opened.digest !== props.module.selectedDigest) throw new Error('宿主打开的功能版本与当前选择不一致。');
    const url = new URL(opened.url);
    if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !url.port || url.origin === window.location.origin) throw new Error('模块地址不符合独立本机来源要求。');
    // Only the host's verified running instance may grant the web-app capability.
    sandbox.value = packageSandbox(opened);
    origin = url.origin;
    const fragment = new URLSearchParams();
    fragment.set('nd-host-origin', window.location.origin); fragment.set('nd-generation', opened.generation);
    url.hash = fragment.toString(); source.value = url.href;
    timer = setTimeout(() => { void reportLoadFailure('模块界面连接超时，未能完成前端身份确认。'); }, 20_000);
  } catch (error) { await reportLoadFailure(errorMessage(error)); }
});
onUnmounted(() => {
  closed = true; if (timer) clearTimeout(timer);
  window.removeEventListener('message', receive);
  void releaseInstance();
});
</script>

<template>
  <section class="package-frame-panel" :aria-label="module.label">
    <div v-if="failure" class="alert alert-error" role="alert"><strong>{{ module.label }} · v{{ module.selectedVersion }}</strong><span>{{ failure }}</span></div>
    <div v-else-if="!ready" class="package-loading" role="status">正在启动 {{ module.label }} v{{ module.selectedVersion }} 的界面与后台…</div>
    <iframe v-if="source" ref="frame" :src="source" :title="`${module.label} v${module.selectedVersion}`" class="package-iframe" :sandbox="sandbox" referrerpolicy="no-referrer" allow="" :class="{ 'is-loading': !ready }" @error="reportLoadFailure('模块页面资源加载失败。')"></iframe>
  </section>
</template>
