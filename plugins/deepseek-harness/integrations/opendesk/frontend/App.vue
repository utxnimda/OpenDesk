<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue';
import type { HostTool, HostToolIdentity, ModuleBridge } from '@opendesk/module-sdk';
import { errorMessage } from '@opendesk/module-sdk';
import {
  buildMcpSnippet,
  DSH_IFRAME_SANDBOX,
  dshSourceLabel,
  formatLogTime,
  stateLabel,
  validateLoopbackProxyUrl,
  type DshConfigDto,
  type DshStatusDto,
  type LogEntryDto,
  type McpBridgeDto,
} from './state';

const props = defineProps<{ bridge: ModuleBridge }>();

const status = ref<DshStatusDto | null>(null);
const logs = ref<LogEntryDto[]>([]);
const busy = ref(false);
const bannerError = ref('');
const bannerNotice = ref('');
const showConsole = ref(false);
const toolbarCollapsed = ref(false);
const iframeKey = ref(0);
const dirtyConfig = ref(false);

// Controlled OpenDesk read-only host tools -> local Streamable HTTP MCP bridge
// Note: `bridge.host.tools` is populated asynchronously after the `nimdadesk:connect` handshake
// (not a Vue reactive object), so track support in a ref updated after `await props.bridge.ready`.
const hostToolsSupported = ref(Boolean(props.bridge.host.tools));
const availableHostTools = ref<HostTool[]>([]);
const selectedToolKeys = ref<string[]>([]);
const mcpState = ref<McpBridgeDto | null>(null);
const mcpBusy = ref(false);
const mcpError = ref('');
const mcpNotice = ref('');
const revealMcpSnippet = ref(false);

const draft = reactive<DshConfigDto>({
  schemaVersion: 1,
  launchMode: 'managed',
  dshSource: 'auto',
  sourceLaunchMode: 'built',
  customDshPath: '',
  nodePath: '',
  homeMode: 'isolated',
  customHomeDir: '',
  workspaceDir: '',
  profile: 'web',
  autoStart: true,
  externalUrl: '',
});

let pollTimer: ReturnType<typeof setInterval> | null = null;
let mcpTimer: ReturnType<typeof setInterval> | null = null;
let mcpPolling = false;
let disposed = false;

function hostToolKey(tool: Pick<HostToolIdentity, 'moduleId' | 'name' | 'generation'>): string {
  return `${tool.moduleId}::${tool.name}::${tool.generation}`;
}

const mcpSnippet = computed(() => {
  if (!mcpState.value?.active || !mcpState.value.url || !mcpState.value.token) return '';
  return buildMcpSnippet(mcpState.value.url, mcpState.value.token);
});

async function refreshHostTools() {
  const client = props.bridge.host.tools;
  if (!client) return;
  mcpBusy.value = true;
  mcpError.value = '';
  try {
    const listed = await client.list();
    if (disposed) return;
    availableHostTools.value = listed;
    const validKeys = new Set(listed.map(hostToolKey));
    selectedToolKeys.value = selectedToolKeys.value.filter((k) => validKeys.has(k));
  } catch (error) {
    if (!disposed) mcpError.value = errorMessage(error);
  } finally {
    if (!disposed) mcpBusy.value = false;
  }
}

function toggleToolSelection(key: string) {
  if (selectedToolKeys.value.includes(key)) {
    selectedToolKeys.value = selectedToolKeys.value.filter((k) => k !== key);
  } else {
    selectedToolKeys.value = [...selectedToolKeys.value, key];
  }
}

function stopMcpPoller() {
  if (mcpTimer) {
    clearInterval(mcpTimer);
    mcpTimer = null;
  }
}

function ensureMcpPoller() {
  if (mcpTimer || !mcpState.value?.active || !props.bridge.host.tools) return;
  mcpTimer = setInterval(() => {
    void drainMcpCalls();
  }, 400);
}

async function drainMcpCalls() {
  if (disposed || mcpPolling || !mcpState.value?.active) return;
  const client = props.bridge.host.tools;
  if (!client) return;
  mcpPolling = true;
  try {
    const reply = await props.bridge.host.callWorker<{
      calls: Array<{ callId: string; tool: HostToolIdentity }>;
    }>('dsh.mcp.poll', {});
    for (const item of reply.calls ?? []) {
      if (disposed) break;
      try {
        const result = await client.call(item.tool, {});
        await props.bridge.host.callWorker('dsh.mcp.respond', {
          callId: item.callId,
          ok: true,
          result,
        });
      } catch (error) {
        await props.bridge.host.callWorker('dsh.mcp.respond', {
          callId: item.callId,
          ok: false,
          error: errorMessage(error),
        });
      }
    }
  } catch {
    // Transient polling error will retry on next interval; worker state stays authoritative.
  } finally {
    mcpPolling = false;
  }
}

async function handleGrantMcpTools() {
  const client = props.bridge.host.tools;
  if (!client) return;
  const picked = availableHostTools.value.filter((tool) => selectedToolKeys.value.includes(hostToolKey(tool)));
  if (!picked.length) {
    mcpError.value = '请至少勾选一个只读工具。';
    return;
  }
  mcpBusy.value = true;
  mcpError.value = '';
  mcpNotice.value = '';
  try {
    await client.grant(
      picked.map((t) => ({
        name: t.name,
        moduleId: t.moduleId,
        version: t.version,
        digest: t.digest,
        generation: t.generation,
      })),
    );
    const synced = await props.bridge.host.callWorker<McpBridgeDto>('dsh.mcp.sync', {
      tools: picked.map((t) => ({
        name: t.name,
        moduleId: t.moduleId,
        version: t.version,
        digest: t.digest,
        generation: t.generation,
        title: t.title,
        description: t.description,
        readOnly: true,
        contractVersion: 1,
      })),
    });
    if (disposed) return;
    mcpState.value = synced;
    ensureMcpPoller();
    mcpNotice.value = `已授权 ${synced.toolCount} 个只读工具并启动本机 MCP 端点，可复制下方配置填入 DeepSeek Harness MCP 设置。`;
  } catch (error) {
    if (!disposed) mcpError.value = errorMessage(error);
  } finally {
    if (!disposed) mcpBusy.value = false;
  }
}

async function handleClearMcpTools() {
  mcpBusy.value = true;
  mcpError.value = '';
  mcpNotice.value = '';
  try {
    const synced = await props.bridge.host.callWorker<McpBridgeDto>('dsh.mcp.sync', { tools: [] });
    if (disposed) return;
    mcpState.value = synced;
    revealMcpSnippet.value = false;
    stopMcpPoller();
    mcpNotice.value = '已撤销 MCP 工具桥接。';
  } catch (error) {
    if (!disposed) mcpError.value = errorMessage(error);
  } finally {
    if (!disposed) mcpBusy.value = false;
  }
}

async function handleCopyMcpSnippet() {
  if (!mcpSnippet.value) return;
  try {
    await navigator.clipboard.writeText(mcpSnippet.value);
    mcpNotice.value = 'MCP 配置 JSON 已复制到剪贴板。';
  } catch {
    revealMcpSnippet.value = true;
    mcpNotice.value = '剪贴板不可用，已展开配置文本供手动复制。';
  }
}

const runtimeState = computed(() => status.value?.runtime.state ?? 'idle');
const installState = computed(() => status.value?.runtime.install.state ?? 'idle');
const sourceTaskState = computed(() => status.value?.runtime.sourceTask?.state ?? 'idle');
const sourceTaskKind = computed(() => status.value?.runtime.sourceTask?.kind ?? '');
const sourceRepo = computed(() => status.value?.probe.sourceRepo ?? null);
const safeProxyUrl = computed(() =>
  validateLoopbackProxyUrl(status.value?.runtime.proxyUrl ?? '', window.location.origin),
);
const canShowIframe = computed(
  () => runtimeState.value === 'running' && Boolean(safeProxyUrl.value) && !showConsole.value,
);

function syncDraftFromStatus(data: DshStatusDto) {
  if (dirtyConfig.value) return;
  draft.launchMode = data.config.launchMode;
  draft.dshSource = data.config.dshSource;
  draft.sourceLaunchMode = data.config.sourceLaunchMode ?? 'built';
  draft.customDshPath = data.config.customDshPath;
  draft.nodePath = data.config.nodePath;
  draft.homeMode = data.config.homeMode;
  draft.customHomeDir = data.config.customHomeDir;
  draft.workspaceDir = data.config.workspaceDir;
  draft.profile = data.config.profile;
  draft.autoStart = data.config.autoStart;
  draft.externalUrl = data.config.externalUrl;
}

async function refreshStatus(includeLogs = false) {
  try {
    const previousState = status.value?.runtime.state;
    const next = await props.bridge.host.callWorker<DshStatusDto>('dsh.status', {});
    if (disposed) return;
    status.value = next;
    syncDraftFromStatus(next);
    if (previousState !== 'running' && next.runtime.state === 'running') {
      iframeKey.value += 1;
    }
    if (
      includeLogs ||
      showConsole.value ||
      next.runtime.state !== 'running' ||
      next.runtime.install.state === 'running' ||
      next.runtime.sourceTask?.state === 'running'
    ) {
      const logReply = await props.bridge.host.callWorker<{ entries: LogEntryDto[] }>('dsh.logs', { limit: 200 });
      if (!disposed) logs.value = logReply.entries;
    }
  } catch (error) {
    if (!disposed) bannerError.value = errorMessage(error);
  }
}

async function runAction(task: () => Promise<DshStatusDto>, notice = '') {
  busy.value = true;
  bannerError.value = '';
  bannerNotice.value = '';
  try {
    const next = await task();
    if (disposed) return;
    status.value = next;
    syncDraftFromStatus(next);
    if (notice) bannerNotice.value = notice;
    await refreshStatus(true);
  } catch (error) {
    if (!disposed) bannerError.value = errorMessage(error);
  } finally {
    if (!disposed) busy.value = false;
  }
}

async function handleProbe() {
  busy.value = true;
  bannerError.value = '';
  try {
    const reply = await props.bridge.host.callWorker<{ status: DshStatusDto }>('dsh.probe', {});
    if (!disposed) {
      status.value = reply.status;
      syncDraftFromStatus(reply.status);
      bannerNotice.value = '环境检测已刷新。';
    }
  } catch (error) {
    if (!disposed) bannerError.value = errorMessage(error);
  } finally {
    if (!disposed) busy.value = false;
  }
}

async function handleSaveConfig() {
  const payload = {
    launchMode: draft.launchMode,
    dshSource: draft.dshSource,
    sourceLaunchMode: draft.sourceLaunchMode,
    customDshPath: draft.customDshPath,
    nodePath: draft.nodePath,
    homeMode: draft.homeMode,
    customHomeDir: draft.customHomeDir,
    workspaceDir: draft.workspaceDir,
    profile: draft.profile,
    autoStart: draft.autoStart,
    externalUrl: draft.externalUrl,
  };
  await runAction(async () => {
    const next = await props.bridge.host.callWorker<DshStatusDto>('dsh.configure', payload);
    dirtyConfig.value = false;
    return next;
  }, '配置已保存。若当前实例正在运行，点击「重启」使新配置生效。');
}

async function handleStart() {
  if (dirtyConfig.value) {
    await handleSaveConfig();
    if (bannerError.value) return;
  }
  showConsole.value = false;
  await runAction(() => props.bridge.host.callWorker<DshStatusDto>('dsh.start', {}));
}

async function handleStop() {
  await runAction(() => props.bridge.host.callWorker<DshStatusDto>('dsh.stop', {}), '已停止。');
}

async function handleRestart() {
  if (dirtyConfig.value) {
    await handleSaveConfig();
    if (bannerError.value) return;
  }
  showConsole.value = false;
  await runAction(() => props.bridge.host.callWorker<DshStatusDto>('dsh.restart', {}), '正在重启 DeepSeek Harness…');
}

async function handleInstall() {
  showConsole.value = true;
  await runAction(
    () => props.bridge.host.callWorker<DshStatusDto>('dsh.install', {}),
    '正在将 @deepseek-ai/dsh 安装到插件数据目录，请查看下方实时日志…',
  );
}

async function handleCancelInstall() {
  await runAction(() => props.bridge.host.callWorker<DshStatusDto>('dsh.install.cancel', {}), '已请求取消安装。');
}

async function handleSourceInstall() {
  if (dirtyConfig.value) {
    await handleSaveConfig();
    if (bannerError.value) return;
  }
  showConsole.value = true;
  await runAction(
    () => props.bridge.host.callWorker<DshStatusDto>('dsh.source.install', {}),
    '正在源码仓执行 pnpm install，请查看下方实时日志…',
  );
}

async function handleSourceBuild(restartAfter = false) {
  if (dirtyConfig.value) {
    await handleSaveConfig();
    if (bannerError.value) return;
  }
  showConsole.value = true;
  await runAction(
    () => props.bridge.host.callWorker<DshStatusDto>('dsh.source.build', { restartAfter }),
    restartAfter
      ? '正在构建源码仓产物（pnpm run build:artifacts），完成后将自动重启 DeepSeek Harness…'
      : '正在构建源码仓产物（pnpm run build:artifacts），请查看下方实时日志…',
  );
}

async function handleCancelSourceTask() {
  await runAction(() => props.bridge.host.callWorker<DshStatusDto>('dsh.source.cancel', {}), '已请求取消源码任务。');
}

async function handleOpenExternal() {
  bannerError.value = '';
  try {
    await props.bridge.host.callWorker('dsh.browser.open', {});
  } catch (error) {
    bannerError.value = errorMessage(error);
  }
}

function reloadIframe() {
  iframeKey.value += 1;
}

onMounted(async () => {
  await props.bridge.ready;
  hostToolsSupported.value = Boolean(props.bridge.host.tools);
  await refreshStatus(true);
  if (hostToolsSupported.value) {
    void refreshHostTools();
  }
  pollTimer = setInterval(() => {
    const st = status.value?.runtime.state;
    const inst = status.value?.runtime.install.state;
    const srcTask = status.value?.runtime.sourceTask?.state;
    if (st === 'starting' || st === 'stopping' || inst === 'running' || srcTask === 'running' || showConsole.value) {
      void refreshStatus(true);
    } else {
      void refreshStatus(false);
    }
  }, 1500);
});

onUnmounted(() => {
  disposed = true;
  if (pollTimer) clearInterval(pollTimer);
  stopMcpPoller();
});
</script>

<template>
  <div class="dsh-shell">
    <header v-if="!toolbarCollapsed || !canShowIframe" class="dsh-toolbar">
      <div class="dsh-toolbar-left">
        <span class="dsh-title">DeepSeek Harness</span>
        <span class="dsh-badge" :class="`is-${runtimeState}`">
          <span class="dsh-dot"></span>
          {{ stateLabel(runtimeState) }}
        </span>
        <span v-if="status?.runtime.mode === 'external'" class="dsh-badge">外部实例</span>
        <span v-else-if="status?.runtime.launchKind === 'source-dev'" class="dsh-badge is-starting">源码热开发</span>
        <span v-else-if="sourceRepo?.isRepo" class="dsh-badge">本地源码</span>
        <span v-if="mcpState?.active" class="dsh-badge is-running">MCP: {{ mcpState.toolCount }} 个只读工具</span>
        <span v-if="status?.runtime.effectiveDshHome" class="dsh-meta" :title="status.runtime.effectiveDshHome">
          HOME: {{ status.runtime.effectiveDshHome }}
        </span>
      </div>

      <div class="dsh-toolbar-right">
        <button
          v-if="sourceRepo?.isRepo && status?.runtime.launchKind !== 'source-dev' && sourceTaskState !== 'running'"
          class="dsh-btn dsh-btn-compact"
          :disabled="busy || !sourceRepo.depsInstalled || !status?.probe.pnpm?.available"
          title="执行 pnpm run build:artifacts 并在构建成功后自动重启"
          @click="handleSourceBuild(true)"
        >
          重建并重启
        </button>
        <button
          v-if="runtimeState === 'running'"
          class="dsh-btn dsh-btn-compact"
          :disabled="busy"
          @click="reloadIframe"
        >
          刷新视图
        </button>
        <button
          v-if="runtimeState === 'running'"
          class="dsh-btn dsh-btn-compact"
          :disabled="busy"
          @click="handleOpenExternal"
        >
          浏览器打开
        </button>
        <button
          v-if="runtimeState === 'running' || runtimeState === 'starting'"
          class="dsh-btn dsh-btn-compact"
          :disabled="busy"
          @click="handleRestart"
        >
          重启
        </button>
        <button
          v-if="runtimeState === 'running' || runtimeState === 'starting'"
          class="dsh-btn dsh-btn-compact dsh-btn-danger"
          :disabled="busy"
          @click="handleStop"
        >
          {{ status?.runtime.mode === 'external' ? '断开' : '停止' }}
        </button>
        <button
          v-else
          class="dsh-btn dsh-btn-compact dsh-btn-primary"
          :disabled="busy"
          @click="handleStart"
        >
          启动
        </button>
        <button
          v-if="runtimeState === 'running'"
          class="dsh-btn dsh-btn-compact"
          @click="showConsole = !showConsole; if (showConsole && hostToolsSupported) void refreshHostTools()"
        >
          {{ showConsole ? '返回 DSH' : '设置与日志' }}
        </button>
        <button
          v-if="canShowIframe"
          class="dsh-btn dsh-btn-compact"
          title="收起顶栏以全屏使用 DeepSeek Harness"
          @click="toolbarCollapsed = true"
        >
          收起
        </button>
      </div>
    </header>

    <button
      v-if="toolbarCollapsed && canShowIframe"
      class="dsh-btn dsh-btn-compact dsh-floating-toggle"
      @click="toolbarCollapsed = false"
    >
      DSH 控制
    </button>

    <div class="dsh-stage">
      <iframe
        v-if="canShowIframe"
        :key="iframeKey"
        class="dsh-iframe"
        title="DeepSeek Harness Web UI"
        :src="safeProxyUrl"
        :sandbox="DSH_IFRAME_SANDBOX"
        allow="clipboard-write"
        referrerpolicy="no-referrer"
      ></iframe>

      <div v-else class="dsh-panel-scroll">
        <div class="dsh-panel-inner">
          <div v-if="status?.configError" class="dsh-alert" role="alert">
            配置文件读取失败（已保留原文件，不会自动覆盖）：{{ status.configError }}
          </div>
          <div v-if="bannerError" class="dsh-alert" role="alert">{{ bannerError }}</div>
          <div v-if="status?.runtime.error" class="dsh-alert" role="alert">{{ status.runtime.error }}</div>
          <div v-if="bannerNotice" class="dsh-alert dsh-alert-info" role="status">{{ bannerNotice }}</div>

          <section class="dsh-card">
            <div class="dsh-card-head">
              <div>
                <h2>DeepSeek Harness 运行环境</h2>
                <p>通过本地 HTTP/WebSocket 反向代理完整承载原生 dsh web（含 Cordis 插件、MCP、Subagents、Plan 与定时任务）。</p>
              </div>
              <button class="dsh-btn" :disabled="busy" @click="handleProbe">重新检测</button>
            </div>

            <div v-if="status" class="dsh-env-grid">
              <div class="dsh-env-item">
                <span class="dsh-env-label">Node.js（要求 >= {{ status.probe.minNodeVersion }}）</span>
                <span
                  class="dsh-env-value"
                  :class="status.probe.node.available && status.probe.node.meetsMinimum ? 'dsh-ok' : 'dsh-err'"
                >
                  {{ status.probe.node.available ? `v${status.probe.node.version}` : '未检测到' }}
                </span>
                <span class="dsh-env-sub">{{ status.probe.node.error || status.probe.node.path }}</span>
              </div>

              <div class="dsh-env-item">
                <span class="dsh-env-label">DeepSeek Harness CLI</span>
                <span class="dsh-env-value" :class="status.probe.dsh.available ? 'dsh-ok' : 'dsh-warn'">
                  {{ status.probe.dsh.available ? (status.probe.dsh.version ? `v${status.probe.dsh.version}` : '已就绪') : '未安装 / 未配置' }}
                </span>
                <span class="dsh-env-sub">
                  {{ dshSourceLabel(status.probe.dsh.source) }}
                  <template v-if="status.probe.dsh.script || status.probe.dsh.executable">
                    · {{ status.probe.dsh.script || status.probe.dsh.executable }}
                  </template>
                </span>
              </div>

              <div class="dsh-env-item">
                <span class="dsh-env-label">运行状态与本地代理</span>
                <span class="dsh-env-value">{{ stateLabel(runtimeState) }}</span>
                <span class="dsh-env-sub">
                  {{ status.runtime.proxyUrl || '等待初始化' }}
                  <template v-if="status.runtime.pid"> · PID {{ status.runtime.pid }}</template>
                </span>
              </div>
            </div>

            <div class="dsh-actions">
              <button
                class="dsh-btn dsh-btn-primary"
                :disabled="busy || runtimeState === 'starting' || runtimeState === 'stopping'"
                @click="handleStart"
              >
                {{ runtimeState === 'starting' ? '正在启动…' : (draft.launchMode === 'external' ? '连接外部 DSH' : '启动 DeepSeek Harness') }}
              </button>

              <button
                v-if="installState !== 'running'"
                class="dsh-btn"
                :disabled="busy || !status?.probe.node.meetsMinimum || !status?.probe.npmPath"
                @click="handleInstall"
              >
                {{ status?.probe.managedCliInstalled ? '更新插件目录中的 @deepseek-ai/dsh' : '安装 @deepseek-ai/dsh 到插件数据目录' }}
              </button>

              <button
                v-else
                class="dsh-btn dsh-btn-danger"
                :disabled="busy"
                @click="handleCancelInstall"
              >
                取消安装
              </button>

              <button
                v-if="runtimeState === 'running'"
                class="dsh-btn"
                @click="showConsole = false"
              >
                进入 DSH 界面
              </button>
            </div>

            <p v-if="installState === 'running'" class="dsh-field-hint">
              正在通过本机 npm 将 @deepseek-ai/dsh 安装到 {{ status?.probe.managedCliDir }}，不会修改全局环境。
            </p>
            <p v-else-if="status?.runtime.install.error" class="dsh-err">
              {{ status.runtime.install.error }}
            </p>
          </section>

          <section v-if="sourceRepo?.isRepo" class="dsh-card">
            <div class="dsh-card-head">
              <div>
                <h2>DeepSeek Harness 本地源码工作区</h2>
                <p>
                  已识别本地源码仓，支持直接修改源码后一键安装依赖、构建产物或使用 <code>dev:web</code> 热开发。
                </p>
              </div>
              <span v-if="sourceRepo.gitBranch" class="dsh-badge">
                {{ sourceRepo.gitBranch }}@{{ sourceRepo.gitCommit }}{{ sourceRepo.gitDirty ? ' (已修改)' : '' }}
              </span>
            </div>

            <div class="dsh-env-grid">
              <div class="dsh-env-item">
                <span class="dsh-env-label">源码仓库</span>
                <span class="dsh-env-value">v{{ sourceRepo.version || '未知' }}</span>
                <span class="dsh-env-sub">{{ sourceRepo.repoRoot }}</span>
              </div>
              <div class="dsh-env-item">
                <span class="dsh-env-label">依赖与包管理器</span>
                <span class="dsh-env-value" :class="sourceRepo.depsInstalled ? 'dsh-ok' : 'dsh-warn'">
                  {{ sourceRepo.depsInstalled ? '依赖已安装' : '尚未安装依赖' }}
                </span>
                <span class="dsh-env-sub">
                  {{ status?.probe.pnpm?.available ? `工具：${status.probe.pnpm.display}` : (status?.probe.pnpm?.error || '缺少 pnpm/corepack') }}
                </span>
              </div>
              <div class="dsh-env-item">
                <span class="dsh-env-label">构建产物与热开发</span>
                <span class="dsh-env-value" :class="sourceRepo.artifactsBuilt ? 'dsh-ok' : 'dsh-warn'">
                  {{ sourceRepo.artifactsBuilt ? '产物已就绪' : '尚未构建产物' }}
                </span>
                <span class="dsh-env-sub">
                  {{ sourceRepo.hasDevWeb ? '支持 dev:web 热开发监听' : '未找到 scripts/dev-web.ts' }}
                  <template v-if="sourceRepo.enginesNode"> · engines.node {{ sourceRepo.enginesNode }}</template>
                </span>
              </div>
            </div>

            <div class="dsh-actions">
              <template v-if="sourceTaskState !== 'running'">
                <button
                  class="dsh-btn"
                  :disabled="busy || !status?.probe.pnpm?.available || !status?.probe.node.meetsMinimum"
                  @click="handleSourceInstall"
                >
                  {{ sourceRepo.depsInstalled ? '重新同步依赖 (pnpm install)' : '1. 安装依赖 (pnpm install)' }}
                </button>
                <button
                  class="dsh-btn"
                  :disabled="busy || !sourceRepo.depsInstalled || !status?.probe.pnpm?.available || !status?.probe.node.meetsMinimum"
                  @click="handleSourceBuild(false)"
                >
                  2. 构建产物 (build:artifacts)
                </button>
                <button
                  class="dsh-btn dsh-btn-primary"
                  :disabled="busy || !sourceRepo.depsInstalled || !status?.probe.pnpm?.available || !status?.probe.node.meetsMinimum"
                  @click="handleSourceBuild(true)"
                >
                  构建并启动 / 重启
                </button>
              </template>
              <button
                v-else
                class="dsh-btn dsh-btn-danger"
                :disabled="busy"
                @click="handleCancelSourceTask"
              >
                取消源码任务（{{ sourceTaskKind === 'install' ? 'pnpm install' : 'build:artifacts' }}）
              </button>
            </div>

            <p v-if="sourceTaskState === 'running'" class="dsh-field-hint">
              正在源码仓执行 {{ sourceTaskKind === 'install' ? 'pnpm install' : 'pnpm run build:artifacts' }}，实时输出见下方日志…
            </p>
            <p v-else-if="status?.runtime.sourceTask?.error" class="dsh-err">
              {{ status.runtime.sourceTask.error }}
            </p>
          </section>

          <section class="dsh-card">
            <div class="dsh-card-head">
              <h2>运行与隔离配置</h2>
              <button class="dsh-btn dsh-btn-primary" :disabled="busy || !dirtyConfig" @click="handleSaveConfig">
                保存配置
              </button>
            </div>

            <div class="dsh-form-grid">
              <div class="dsh-field">
                <label for="dsh-launch-mode">运行方式</label>
                <select id="dsh-launch-mode" v-model="draft.launchMode" @change="dirtyConfig = true">
                  <option value="managed">托管模式（由 OpenDesk 启动并管理进程）</option>
                  <option value="external">外部模式（连接已独立运行的 dsh web）</option>
                </select>
                <span class="dsh-field-hint">
                  托管模式随 OpenDesk 页签/宿主退出而优雅关闭；外部模式不会终止外部进程。
                </span>
              </div>

              <div v-if="draft.launchMode === 'managed'" class="dsh-field">
                <label for="dsh-profile">Profile 名称</label>
                <input
                  id="dsh-profile"
                  v-model="draft.profile"
                  type="text"
                  placeholder="web"
                  @input="dirtyConfig = true"
                />
                <span class="dsh-field-hint">默认 web；支持自定义 profile，不允许使用保留名 desktop。</span>
              </div>

              <template v-if="draft.launchMode === 'managed'">
                <div class="dsh-field">
                  <label for="dsh-source">DSH 程序来源</label>
                  <select id="dsh-source" v-model="draft.dshSource" @change="dirtyConfig = true">
                    <option value="auto">自动检测（npm 预编译包：插件隔离目录 / 系统 PATH）</option>
                    <option value="custom">本地源码仓 / 自定义入口（支持自行修改 DSH 源码）</option>
                  </select>
                </div>

                <div class="dsh-field">
                  <label for="dsh-home-mode">DSH_HOME 数据目录策略</label>
                  <select id="dsh-home-mode" v-model="draft.homeMode" @change="dirtyConfig = true">
                    <option value="isolated">独立隔离目录（推荐，不读写本机 ~/.dsh）</option>
                    <option value="system">复用本机默认 ~/.dsh</option>
                    <option value="custom">自定义绝对目录</option>
                  </select>
                </div>

                <div v-if="draft.dshSource === 'custom'" class="dsh-field full">
                  <label for="dsh-custom-path">本地源码仓目录或入口文件（推荐填 deepseek-harness 仓库根目录）</label>
                  <input
                    id="dsh-custom-path"
                    v-model="draft.customDshPath"
                    type="text"
                    placeholder="例如 D:\MyWorkspace\deepseek-harness 或 D:\MyWorkspace\deepseek-harness\apps\cli\lib\bin.js"
                    @input="dirtyConfig = true"
                  />
                </div>

                <div v-if="draft.dshSource === 'custom'" class="dsh-field full">
                  <label for="dsh-source-mode">源码仓运行模式（当上方填写源码仓根目录时生效）</label>
                  <select id="dsh-source-mode" v-model="draft.sourceLaunchMode" @change="dirtyConfig = true">
                    <option value="built">已构建产物模式（推荐：启动快、资源省，改完代码点「重建并重启」）</option>
                    <option value="dev">源码热开发模式（pnpm run dev:web：自动监听前端与插件变更热重建）</option>
                  </select>
                  <span class="dsh-field-hint">
                    修改 Host 后端代码需在产物模式下点击「重建并重启」或重启进程；修改前端 / Client 插件代码可用热开发模式自动重建。
                  </span>
                </div>

                <div v-if="draft.homeMode === 'custom'" class="dsh-field full">
                  <label for="dsh-custom-home">自定义 DSH_HOME 目录</label>
                  <input
                    id="dsh-custom-home"
                    v-model="draft.customHomeDir"
                    type="text"
                    placeholder="绝对路径，例如 D:\Data\dsh-home"
                    @input="dirtyConfig = true"
                  />
                </div>

                <div class="dsh-field">
                  <label for="dsh-workspace">默认启动工作目录（可选）</label>
                  <input
                    id="dsh-workspace"
                    v-model="draft.workspaceDir"
                    type="text"
                    placeholder="留空则使用插件数据目录下的 workspace"
                    @input="dirtyConfig = true"
                  />
                </div>

                <div class="dsh-field">
                  <label for="dsh-node-path">指定 Node.js 路径（可选）</label>
                  <input
                    id="dsh-node-path"
                    v-model="draft.nodePath"
                    type="text"
                    placeholder="留空自动从系统 PATH 查找 (>= 24.9)"
                    @input="dirtyConfig = true"
                  />
                </div>
              </template>

              <div v-else class="dsh-field full">
                <label for="dsh-external-url">外部 dsh web 地址（含可选 ?token=...）</label>
                <input
                  id="dsh-external-url"
                  v-model="draft.externalUrl"
                  type="text"
                  placeholder="例如 http://127.0.0.1:3080/?token=..."
                  @input="dirtyConfig = true"
                />
                <span class="dsh-field-hint">
                  仅允许本机回环地址。令牌仅由本地代理在服务端换取会话 Cookie，不会暴露到浏览器地址栏。
                </span>
              </div>

              <div class="dsh-field full">
                <label class="dsh-checkbox">
                  <input v-model="draft.autoStart" type="checkbox" @change="dirtyConfig = true" />
                  <span>打开页签时自动启动 / 连接（仅当环境已就绪时生效，不会自动联网下载依赖）</span>
                </label>
              </div>
            </div>
          </section>

          <section class="dsh-card">
            <div class="dsh-card-head">
              <div>
                <h2>OpenDesk 只读工具桥接（MCP）</h2>
                <p>将其他已打开 OpenDesk 页签导出的只读工具，经由宿主原生授权弹窗后，通过本机受控 Streamable HTTP MCP 端点安全提供给 DeepSeek Harness。</p>
              </div>
              <button
                v-if="hostToolsSupported"
                class="dsh-btn dsh-btn-compact"
                :disabled="mcpBusy"
                @click="refreshHostTools"
              >
                刷新工具
              </button>
            </div>

            <div v-if="!hostToolsSupported" class="dsh-field-hint">
              当前宿主未开放模块只读工具桥接能力（需 OpenDesk 0.19+ 宿主）。
            </div>

            <template v-else>
              <div v-if="mcpError" class="dsh-alert" role="alert">{{ mcpError }}</div>
              <div v-if="mcpNotice" class="dsh-alert dsh-alert-info" role="status">{{ mcpNotice }}</div>

              <div v-if="!availableHostTools.length" class="dsh-field-hint">
                暂未发现其他已打开页签导出的只读工具。先在 OpenDesk 中打开提供只读工具的页签（如作品管理器或辅助页签），再点击「刷新工具」。
              </div>

              <div v-else class="dsh-mcp-list">
                <label
                  v-for="tool in availableHostTools"
                  :key="hostToolKey(tool)"
                  class="dsh-mcp-item"
                >
                  <input
                    type="checkbox"
                    :checked="selectedToolKeys.includes(hostToolKey(tool))"
                    @change="toggleToolSelection(hostToolKey(tool))"
                  />
                  <div class="dsh-mcp-item-body">
                    <div class="dsh-mcp-item-title">
                      <strong>{{ tool.title }}</strong>
                      <code>{{ tool.name }}</code>
                      <span class="dsh-badge">{{ tool.moduleId }} v{{ tool.version }}</span>
                    </div>
                    <span class="dsh-field-hint">{{ tool.description }}</span>
                  </div>
                </label>
              </div>

              <div class="dsh-actions">
                <button
                  class="dsh-btn dsh-btn-primary"
                  :disabled="mcpBusy || !selectedToolKeys.length"
                  @click="handleGrantMcpTools"
                >
                  请求宿主授权并启用 MCP 桥接
                </button>
                <button
                  v-if="mcpState?.active"
                  class="dsh-btn dsh-btn-danger"
                  :disabled="mcpBusy"
                  @click="handleClearMcpTools"
                >
                  撤销桥接
                </button>
              </div>

              <div v-if="mcpState?.active && mcpSnippet" class="dsh-mcp-endpoint">
                <div class="dsh-actions">
                  <span class="dsh-ok">MCP 端点已就绪：<code>{{ mcpState.url }}</code></span>
                  <button class="dsh-btn dsh-btn-compact" @click="handleCopyMcpSnippet">复制 MCP 配置 JSON</button>
                  <button class="dsh-btn dsh-btn-compact" @click="revealMcpSnippet = !revealMcpSnippet">
                    {{ revealMcpSnippet ? '隐藏令牌配置' : '查看配置文本' }}
                  </button>
                </div>
                <pre v-if="revealMcpSnippet" class="dsh-logs">{{ mcpSnippet }}</pre>
                <span class="dsh-field-hint">
                  令牌仅对本次页签会话有效，关闭页签或撤销桥接后自动失效；每次工具调用都会由 OpenDesk 宿主重新核验版本、摘要与只读授权。
                </span>
              </div>
            </template>
          </section>

          <section class="dsh-card">
            <div class="dsh-card-head">
              <h2>运行与安装日志（敏感令牌已脱敏）</h2>
              <button class="dsh-btn dsh-btn-compact" @click="refreshStatus(true)">刷新日志</button>
            </div>
            <div class="dsh-logs">
              <div v-if="!logs.length" class="dsh-field-hint">暂无日志输出。</div>
              <div v-for="(entry, idx) in logs" :key="`${entry.ts}-${idx}`" class="dsh-log-line">
                <span class="dsh-log-ts">{{ formatLogTime(entry.ts) }}</span>
                <span class="dsh-log-stream">[{{ entry.stream }}]</span>
                <span>{{ entry.line }}</span>
              </div>
            </div>
          </section>
        </div>
      </div>
    </div>
  </div>
</template>
