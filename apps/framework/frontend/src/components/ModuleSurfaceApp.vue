<script setup lang="ts">
import { onMounted, onUnmounted, ref, shallowRef } from 'vue';
import { host as desktopHost, errorMessage, type HostAdapter, type PackageSurfaceContext } from '../core/host';
import { createPackageBroker, isModuleHello, packageAppearance } from '../core/package-broker';
import { packageSandbox } from '../core/packages';

const props = defineProps<{ adapter?: HostAdapter }>();
const host = props.adapter ?? desktopHost;
const frame = ref<HTMLIFrameElement | null>(null);
const source = ref(''); const failure = ref(''); const ready = ref(false);
const sandbox = ref(packageSandbox(null));
const binding = shallowRef<PackageSurfaceContext | null>(null);
let closed = false; let origin = ''; let channel: MessageChannel | null = null;
let broker: ReturnType<typeof createPackageBroker> | null = null;
let timer: ReturnType<typeof setTimeout> | undefined;
let appearance = packageAppearance('dark');

async function close() {
  const generation = binding.value?.generation ?? new URLSearchParams(window.location.search).get('surface-generation');
  if (!generation) { failure.value = '窗口身份不可用，请按 Alt+F4 关闭。'; return; }
  try { await host.closePackageSurface(generation); }
  catch (error) { failure.value = errorMessage(error); }
}
function receive(event: MessageEvent) {
  const current = binding.value;
  if (closed || !current || channel || !isModuleHello(event, frame.value?.contentWindow ?? null, origin, current.generation)) return;
  const transport = new MessageChannel(); channel = transport;
  broker = createPackageBroker(current.generation,
    (method, params) => host.callPackageSurface(current.generation, method, params),
    (identity) => {
      const instance = current.instance;
      if (identity.moduleId !== instance.moduleId || identity.version !== instance.version || identity.digest !== instance.digest) throw new Error('独立视图与当前模块包身份不一致。');
      ready.value = true; if (timer) clearTimeout(timer);
      // This view borrows the owning session. It must not activate or close it.
    },
    async (operation) => {
      switch (operation.action) {
        case 'close': return host.closePackageSurface(current.generation);
        case 'start-drag': return host.startPackageSurfaceDrag(current.generation);
        case 'always-on-top': return host.setPackageSurfaceAlwaysOnTop(current.generation, operation.params.enabled);
        default: throw new Error('请从主页面打开独立视图。');
      }
    });
  transport.port1.onmessage = (message: MessageEvent) => {
    void broker?.receive(message.data, (value) => { if (!closed) transport.port1.postMessage(value); });
  };
  transport.port1.start();
  frame.value!.contentWindow!.postMessage({
    type: 'nimdadesk:connect', protocol: 1,
    identity: { moduleId: current.instance.moduleId, version: current.instance.version, digest: current.instance.digest, generation: current.generation },
    ...appearance,
    moduleSurface: { context: { view: current.view, data: current.context } },
  }, origin, [transport.port2]);
}
onMounted(async () => {
  window.addEventListener('message', receive);
  try {
    const current = await host.getPackageSurfaceContext();
    if (closed) return;
    binding.value = current;
    const settings = await host.getSettings();
    if (closed) return;
    const theme = settings.theme === 'system' ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : settings.theme;
    appearance = packageAppearance(theme, settings.uiStyle, { material: 'glass', transparency: 60, motion: 'off', transparentBackground: true });
    const url = new URL(current.instance.url);
    if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !url.port || url.origin === window.location.origin) throw new Error('独立视图地址不符合本机来源要求。');
    sandbox.value = packageSandbox(current.instance); origin = url.origin;
    const fragment = new URLSearchParams({ 'nd-host-origin': window.location.origin, 'nd-generation': current.generation });
    url.hash = fragment.toString(); source.value = url.href;
    timer = setTimeout(() => { if (!ready.value && !closed) failure.value = '独立视图连接超时，请关闭后从主页面重新打开。'; }, 20_000);
  } catch (error) { if (!closed) failure.value = errorMessage(error); }
});
onUnmounted(() => {
  closed = true; if (timer) clearTimeout(timer);
  window.removeEventListener('message', receive); broker?.dispose();
  channel?.port1.close(); channel?.port2.close();
  // Native destruction and owner revocation manage the view, never the worker.
});
</script>

<template>
  <main class="module-surface-root">
    <div v-if="failure || !ready" class="module-surface-status" :role="failure ? 'alert' : 'status'">
      <span>{{ failure || '正在连接当前模块会话…' }}</span><button type="button" @click="close">关闭窗口</button>
    </div>
    <iframe v-if="source && !failure" ref="frame" :src="source" :sandbox="sandbox" class="module-surface-frame" title="模块独立视图" referrerpolicy="no-referrer" allow="" @error="failure = '独立视图资源加载失败。'"></iframe>
  </main>
</template>

<style>
html[data-surface="module-surface"], html[data-surface="module-surface"] body, html[data-surface="module-surface"] #app { width: 100%; height: 100%; min-width: 0; min-height: 0; margin: 0; padding: 0; overflow: hidden; background: transparent !important; }
.module-surface-root, .module-surface-frame { width: 100%; height: 100%; border: 0; margin: 0; background: transparent; }
.module-surface-frame { display: block; }
.module-surface-status { position: absolute; z-index: 1; inset: 12px 12px auto; padding: 14px; color: #fff; background: #222e; border-radius: 8px; font-size: 13px; }
.module-surface-status button { margin: 8px 0 0; display: block; }
</style>
