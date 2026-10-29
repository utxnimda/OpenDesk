<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue';
import { ArrowUp, Check, ChevronDown, Folder, LoaderCircle, MessageSquare, PanelLeft, Plus, RefreshCw, Search, Settings2, Square, SquarePen, Terminal, Wrench, X } from '@lucide/vue';
import MessageBody from './MessageBody.vue';
import ModelSelect from './ModelSelect.vue';
import ModelParameters from './ModelParameters.vue';
import WorkspaceTree from './WorkspaceTree.vue';
import SidebarDivider from './SidebarDivider.vue';
import { createSidebarWidthSaver, DEFAULT_SIDEBAR_WIDTH, savedSidebarWidth, sidebarBounds, visibleSidebarWidth } from './sidebarResize';
import { agentGroup, buildWorkspaceTree, connectionReady, modelDefaults, resolveDraftConnection, resolveModelOption, selectableConnection, workspaceKey, type AgentGroup, type WorkspaceNode } from './workspaceTree';
import { ApiError, createApiClient, readSessionToken } from './client';
import { applyRunEvents, emptyRunView, findAllowedTool, isTerminal, statusLabel } from './runState';
import { available, callTool, grantTools, listTools, type HostTool } from './hostTools';
import type { Approval, CatalogWorkspace, Connection, ConnectionInput, Conversation, ConversationSummary, ModelOption, Run, RunEvent, WorkspaceOption } from './types';

const api = createApiClient(readSessionToken(window.location.hash));
const connections = ref<Connection[]>([]);
const conversations = ref<ConversationSummary[]>([]);
const conversation = ref<Conversation | null>(null);
const selectedConnectionId = ref('');
const selectedModel = ref('');
const selectedModelOptions = ref<Record<string, string>>({});
const workspace = ref('');
const modelCatalogs = ref<Record<string, ModelOption[]>>({});
const customModelOpen = ref(false);
const customModel = ref('');
const modelSaving = ref(false);
const modelUncertain = ref(false);
const workspaces = ref<WorkspaceOption[]>([]);
const workspaceLoading = ref(false);
const workspacePickerOpen = ref(false);
const workspaceWarning = ref('');
const availableWorkspaces = computed(() => workspaces.value.filter(item => item.available));
const workspaceTarget = ref<'conversation' | 'connection'>('conversation');
const workspaceCatalog = ref<CatalogWorkspace[]>([]);
const agentsRefreshing = ref(false);
const agentWarnings = ref<{ connectionId: string; message: string }[]>([]);
const addWorkspaceOpen = ref(false);
const workspaceSaving = ref(false);
const workspaceForm = ref({ connectionId: '', path: '', label: '' });
const workspaceFormError = ref('');
const message = ref('');
const error = ref('');
const notice = ref('');
const loaded = ref(false);
const refreshing = ref(false);
const creating = ref(false);
const sending = ref(false);
const switching = ref(false);
const modelLoading = ref(false);
const shuttingDown = ref(false);
const settingsOpen = ref(false);
const sidebarOpen = ref(true);
const workbenchElement = ref<HTMLElement | null>(null);
const preferredSidebarWidth = ref(DEFAULT_SIDEBAR_WIDTH);
const containerWidth = ref(window.innerWidth);
const sidebarResizing = ref(false);
const layoutDraftDirty = ref(false);
const layoutError = ref('');
const sidebarLimits = computed(() => sidebarBounds(containerWidth.value));
const renderedSidebarWidth = computed(() => visibleSidebarWidth(preferredSidebarWidth.value, containerWidth.value));
const search = ref('');
const connectionSaving = ref(false);
const connectionError = ref('');
const connectionNotice = ref('');
const form = ref<ConnectionInput>({ name: '', kind: 'openai-responses', baseUrl: 'https://api.openai.com/v1', apiKey: '', executable: '', workspace: '' });
const currentRun = ref<Run | null>(null);
const runView = ref(emptyRunView());
const cancelPending = ref(false);
const approvalPending = ref(false);
const scrollArea = ref<HTMLElement | null>(null);
const composer = ref<HTMLTextAreaElement | null>(null);
const followBottom = ref(true);
const hostAvailable = ref(available());
const tools = ref<HostTool[]>([]);
const selectedToolNames = ref<string[]>([]);
const grantedTools = ref<HostTool[]>([]);
const grantConnectionId = ref('');
const toolLoading = ref(false);
const toolError = ref('');
const toolPanelOpen = ref(false);
const runTools = new Map<string, HostTool[]>();
const handledToolRequests = new Set<string>();
const pendingToolResults = ref<{ runId: string; requestId: string; result?: unknown; error?: string }[]>([]);
let pollTimer: ReturnType<typeof setTimeout> | undefined;
let viewGeneration = 0;
let mounted = true;
let modelRequestGeneration = 0;
let workspaceRequestGeneration = 0;
let initialConnectionSelection = true;
let layoutGeneration = 0;
let savedLayoutWidth = DEFAULT_SIDEBAR_WIDTH;
let dragStartWidth = DEFAULT_SIDEBAR_WIDTH;
let workbenchObserver: ResizeObserver | undefined;
const compactViewport = window.matchMedia('(max-width: 700px)');
const compactLayout = ref(compactViewport.matches);
function updateCompactLayout(event: MediaQueryListEvent) { compactLayout.value = event.matches; sidebarOpen.value = !event.matches; }
function refreshOnFocus() { if (mounted && loaded.value) void refreshState(); }
const layoutSaver = createSidebarWidthSaver(width => api.saveLayout(width), width => {
  savedLayoutWidth = width;
  layoutError.value = '';
  if (!sidebarResizing.value) { preferredSidebarWidth.value = width; layoutDraftDirty.value = false; }
}, cause => { layoutError.value = `侧栏宽度保存未确认：${explain(cause)}`; });

function resizeSidebar(active: boolean) {
  if (active) { dragStartWidth = preferredSidebarWidth.value; layoutGeneration++; }
  sidebarResizing.value = active;
}
function previewSidebarWidth(width: number) { preferredSidebarWidth.value = savedSidebarWidth(width); }
function cancelSidebarResize() { preferredSidebarWidth.value = dragStartWidth; layoutDraftDirty.value = dragStartWidth !== savedLayoutWidth; }
function commitSidebarWidth(width: number) {
  const next = savedSidebarWidth(width);
  if (next === savedLayoutWidth && !layoutDraftDirty.value) { preferredSidebarWidth.value = next; return; }
  preferredSidebarWidth.value = next;
  layoutDraftDirty.value = true;
  layoutError.value = '';
  layoutGeneration++;
  layoutSaver.queue(next);
}

const selectedConnection = computed(() => connections.value.find(connection => connection.id === (conversation.value?.connectionId ?? selectedConnectionId.value)));
const editingConnection = computed(() => connections.value.find(connection => connection.id === form.value.id));
const usableConnections = computed(() => connections.value.filter(selectableConnection));
const localAgent = (kind?: string) => kind === 'codex' || kind === 'cursor';
const isLocalAgent = computed(() => localAgent(selectedConnection.value?.kind));
const active = computed(() => Boolean(currentRun.value && !isTerminal(currentRun.value.status)));
const selectedTools = computed(() => tools.value.filter(tool => selectedToolNames.value.includes(tool.name)));
const currentConnectionId = computed(() => conversation.value?.connectionId ?? selectedConnectionId.value);
const permittedTools = computed(() => hostAvailable.value && !isLocalAgent.value && grantConnectionId.value === currentConnectionId.value ? selectedTools.value.filter(tool => grantedTools.value.some(granted => sameTool(granted, tool))) : []);
const ungrantedTools = computed(() => selectedTools.value.some(tool => grantConnectionId.value !== currentConnectionId.value || !grantedTools.value.some(granted => sameTool(granted, tool))));
const workspaceTree = computed(() => buildWorkspaceTree(connections.value, workspaceCatalog.value, conversations.value, search.value));
const selectedWorkspaceKey = computed(() => selectedConnection.value ? `${agentGroup(selectedConnection.value)}:${workspaceKey(workspace.value)}` : '');
const providerName = (kind?: string) => ({ 'openai-responses': 'OpenAI Responses', 'openai-chat': '兼容 API', codex: 'Codex', cursor: 'Cursor' }[kind ?? ''] ?? '选择连接');
const canSend = computed(() => loaded.value && connectionReady(selectedConnection.value) && !shuttingDown.value && !sending.value && !switching.value && !modelSaving.value && !modelUncertain.value && !active.value && Boolean(message.value.trim()) && Boolean(conversation.value || (selectedConnectionId.value && selectedModel.value.trim())));
const pendingApproval = computed<Approval | null>(() => currentRun.value?.approval ?? runView.value.approval);
const visibleModelOptions = computed(() => modelCatalogs.value[currentConnectionId.value] ?? []);
const modelParameters = computed(() => resolveModelOption(visibleModelOptions.value, selectedModel.value, selectedConnection.value?.kind)?.parameters ?? []);
const statusText = computed(() => currentRun.value ? statusLabel(currentRun.value.status) : shuttingDown.value ? '正在安全关闭' : '准备好开始');

function sameTool(left: HostTool, right: HostTool): boolean {
  return left.name === right.name && left.moduleId === right.moduleId && left.version === right.version && left.digest === right.digest && left.generation === right.generation;
}
function explain(cause: unknown): string { return cause instanceof Error ? cause.message : '操作未完成，请稍后重试。'; }
function pretty(value: unknown): string {
  if (typeof value === 'string') return value.slice(0, 12_000);
  try { return JSON.stringify(value, null, 2)?.slice(0, 12_000) ?? ''; }
  catch { return '无法展示此结果。'; }
}
function stopPolling() { if (pollTimer) clearTimeout(pollTimer); pollTimer = undefined; }
async function scrollToLatest(force = false) {
  await nextTick();
  if ((force || followBottom.value) && scrollArea.value) scrollArea.value.scrollTop = scrollArea.value.scrollHeight;
}
function trackScroll() {
  const element = scrollArea.value;
  if (element) followBottom.value = element.scrollHeight - element.scrollTop - element.clientHeight < 100;
}

function reconcileDraftConnection() {
  if (conversation.value) return;
  const previousId = selectedConnectionId.value;
  const resolved = resolveDraftConnection(connections.value, previousId, initialConnectionSelection);
  if (resolved.reason === 'unchanged' || resolved.reason === 'empty') return;
  initialConnectionSelection = false;
  selectedConnectionId.value = resolved.id;
  modelRequestGeneration++;
  modelLoading.value = false;
  workspaceRequestGeneration++;
  workspaceLoading.value = false;
  workspacePickerOpen.value = false;
  if (resolved.reason === 'equivalent') {
    if (!modelCatalogs.value[resolved.id] && modelCatalogs.value[previousId]) modelCatalogs.value[resolved.id] = modelCatalogs.value[previousId]!;
    return;
  }
  selectedModel.value = '';
  selectedModelOptions.value = {};
  if (resolved.reason === 'initial') workspace.value = connections.value.find(item => item.id === resolved.id)?.workspace ?? '';
  else notice.value = '原连接当前不可用。草稿已保留，请明确选择一个可用连接后再发送。';
}

async function refreshState() {
  if (refreshing.value) return;
  refreshing.value = true;
  const layoutReadGeneration = layoutGeneration;
  const canApplyLayout = !sidebarResizing.value && !layoutDraftDirty.value;
  try {
    const state = await api.state();
    if (!mounted) return;
    connections.value = state.connections;
    conversations.value = state.conversations;
    shuttingDown.value = state.shuttingDown;
    if (state.layout && canApplyLayout && layoutReadGeneration === layoutGeneration && !sidebarResizing.value && !layoutDraftDirty.value) {
      savedLayoutWidth = savedSidebarWidth(state.layout.sidebarWidth);
      preferredSidebarWidth.value = savedLayoutWidth;
    }
    reconcileDraftConnection();
    loaded.value = true;
    hostAvailable.value = available();
  } catch (cause) { if (mounted) error.value = explain(cause); }
  finally { refreshing.value = false; }
}

async function refreshAgents() {
  if (agentsRefreshing.value) return;
  agentsRefreshing.value = true;
  try {
    const result = await api.refreshAgents();
    if (!mounted) return;
    connections.value = result.connections;
    workspaceCatalog.value = result.workspaces;
    agentWarnings.value = result.warnings;
    reconcileDraftConnection();
  } catch (cause) { if (mounted) notice.value = `本地工具刷新未完成：${explain(cause)}`; }
  finally { agentsRefreshing.value = false; }
}

function startInWorkspace(node: WorkspaceNode) {
  if (sending.value || creating.value || modelSaving.value || !node.available) return;
  const candidates = node.connections.filter(selectableConnection);
  const connection = candidates.find(item => item.id === selectedConnectionId.value) ?? candidates[0];
  if (!connection) return;
  resetDraft();
  selectedConnectionId.value = connection.id;
  selectConnection();
  workspace.value = node.path;
}

function addWorkspace(group: AgentGroup) {
  const connection = usableConnections.value.find(item => agentGroup(item) === group);
  if (!connection) {
    editConnection();
    form.value.kind = group === 'api' ? 'openai-responses' : group;
    return;
  }
  workspaceForm.value = { connectionId: connection.id, path: '', label: '' };
  workspaceFormError.value = '';
  settingsOpen.value = false;
  addWorkspaceOpen.value = true;
}

async function saveWorkspace() {
  if (workspaceSaving.value || !workspaceForm.value.path.trim()) return;
  workspaceSaving.value = true;
  workspaceFormError.value = '';
  try {
    await api.saveWorkspace({ connectionId: workspaceForm.value.connectionId, path: workspaceForm.value.path.trim(), label: workspaceForm.value.label.trim() || undefined });
    if (!mounted) return;
    await refreshAgents();
    addWorkspaceOpen.value = false;
  } catch (cause) { if (mounted) workspaceFormError.value = explain(cause); }
  finally { workspaceSaving.value = false; }
}

function resetDraft() {
  if (sending.value || creating.value || modelSaving.value) return;
  const previousConnection = selectedConnection.value;
  const previousWorkspace = workspace.value;
  viewGeneration++;
  stopPolling();
  conversation.value = null;
  if (!previousConnection || !selectableConnection(previousConnection)) {
    const replacement = usableConnections.value.find(item => item.id === previousConnection?.duplicateOf) ?? usableConnections.value[0];
    selectedConnectionId.value = replacement?.id ?? '';
    selectConnection();
    if (replacement?.kind === previousConnection?.kind) workspace.value = previousWorkspace;
  }
  currentRun.value = null;
  runView.value = emptyRunView();
  cancelPending.value = false;
  approvalPending.value = false;
  message.value = '';
  error.value = '';
  notice.value = '';
  followBottom.value = true;
  workspacePickerOpen.value = false;
  customModelOpen.value = false;
  modelUncertain.value = false;
  selectedModelOptions.value = modelDefaults(resolveModelOption(visibleModelOptions.value, selectedModel.value, selectedConnection.value?.kind));
  if (window.matchMedia('(max-width: 700px)').matches) sidebarOpen.value = false;
  void nextTick(() => composer.value?.focus());
}

async function openConversation(id: string) {
  if (sending.value || creating.value || modelSaving.value) return;
  initialConnectionSelection = false;
  const generation = ++viewGeneration;
  stopPolling();
  switching.value = true;
  error.value = '';
  notice.value = '';
  try {
    const result = await api.conversation(id);
    if (!mounted || generation !== viewGeneration) return;
    conversation.value = result;
    selectedConnectionId.value = result.connectionId;
    selectedModel.value = result.model;
    selectedModelOptions.value = { ...result.modelOptions };
    workspace.value = result.workspace ?? '';
    workspacePickerOpen.value = false;
    customModelOpen.value = false;
    modelUncertain.value = false;
    if (window.matchMedia('(max-width: 700px)').matches) sidebarOpen.value = false;
    message.value = '';
    runView.value = emptyRunView();
    currentRun.value = null;
    cancelPending.value = false;
    approvalPending.value = false;
    followBottom.value = true;
    if (result.activeRun) {
      currentRun.value = { id: result.activeRun, status: 'running', conversationId: result.id };
      if (!runTools.has(result.activeRun)) notice.value = '已恢复任务状态。此前工具授权不会自动恢复；需要工具时请在下一条消息前重新授权。';
      schedulePoll(result.activeRun, generation, 0);
    }
    await scrollToLatest(true);
  } catch (cause) { if (generation === viewGeneration) error.value = explain(cause); }
  finally { if (generation === viewGeneration) switching.value = false; }
}

function selectConnection() {
  initialConnectionSelection = false;
  const connection = connections.value.find(item => item.id === selectedConnectionId.value);
  workspace.value = connection?.workspace ?? '';
  notice.value = '';
  error.value = '';
  selectedModel.value = modelCatalogs.value[selectedConnectionId.value]?.[0]?.id ?? '';
  selectedModelOptions.value = modelDefaults(modelCatalogs.value[selectedConnectionId.value]?.[0]);
  customModelOpen.value = false;
  workspacePickerOpen.value = false;
  workspaceRequestGeneration++;
  workspaceLoading.value = false;
  modelRequestGeneration++;
  modelLoading.value = false;
}

async function loadModels() {
  if (!currentConnectionId.value || modelLoading.value) return;
  const id = currentConnectionId.value;
  const generation = ++modelRequestGeneration;
  modelLoading.value = true;
  error.value = '';
  try {
    const result = await api.models(id);
    if (!mounted || generation !== modelRequestGeneration) return;
    modelCatalogs.value[id] = result.models;
    if (id !== currentConnectionId.value) return;
    if (!selectedModel.value && result.models.length) selectedModel.value = result.models[0].id;
    if (!conversation.value) selectedModelOptions.value = { ...modelDefaults(resolveModelOption(result.models, selectedModel.value, selectedConnection.value?.kind)), ...selectedModelOptions.value };
    notice.value = result.models.length ? `已读取 ${result.models.length} 个模型。` : '此连接没有返回模型列表，可以手动填写模型 ID。';
  } catch (cause) { if (generation === modelRequestGeneration) error.value = explain(cause); }
  finally { if (generation === modelRequestGeneration) modelLoading.value = false; }
}

async function chooseModel(value: string, options?: Record<string, string>) {
  if (!value.trim() || modelSaving.value || modelUncertain.value || active.value || sending.value || switching.value) return;
  const nextModel = value.trim();
  const nextOptions = options ?? (nextModel === selectedModel.value ? selectedModelOptions.value : modelDefaults(resolveModelOption(visibleModelOptions.value, nextModel, selectedConnection.value?.kind)));
  const target = conversation.value;
  if (!target) { selectedModel.value = nextModel; selectedModelOptions.value = { ...nextOptions }; customModelOpen.value = false; return; }
  if (target.model === nextModel && JSON.stringify(target.modelOptions ?? {}) === JSON.stringify(nextOptions)) { customModelOpen.value = false; return; }
  modelSaving.value = true;
  error.value = '';
  const generation = viewGeneration;
  try {
    const updated = await api.setConversationModel(target.id, nextModel, nextOptions);
    if (!mounted || generation !== viewGeneration) return;
    conversation.value = updated;
    selectedModel.value = updated.model;
    selectedModelOptions.value = { ...updated.modelOptions };
    customModelOpen.value = false;
    await refreshState();
  } catch (cause) {
    if (generation === viewGeneration) {
      error.value = explain(cause);
      if (cause instanceof ApiError && ['NETWORK_ERROR', 'TIMEOUT'].includes(cause.code)) {
        modelUncertain.value = true;
        try {
          const confirmed = await api.conversation(target.id);
          if (mounted && generation === viewGeneration) {
            conversation.value = confirmed;
            selectedModel.value = confirmed.model;
            selectedModelOptions.value = { ...confirmed.modelOptions };
            modelUncertain.value = false;
          }
        } catch { notice.value = '模型状态尚未确认，请刷新状态后再发送。'; }
      }
    }
  }
  finally { modelSaving.value = false; }
}

function chooseParameter(id: string, value: string) {
  if (modelParameters.value.find(item => item.id === id)?.options.some(item => item.value === value)) void chooseModel(selectedModel.value, { ...selectedModelOptions.value, [id]: value });
}

async function loadWorkspaces(target: 'conversation' | 'connection') {
  const id = target === 'connection' ? form.value.id : currentConnectionId.value;
  if (!id || workspaceLoading.value) return;
  workspaceTarget.value = target;
  workspaceLoading.value = true;
  workspacePickerOpen.value = true;
  workspaceWarning.value = '';
  workspaces.value = [];
  const generation = ++workspaceRequestGeneration;
  try {
    const result = await api.workspaces(id);
    if (!mounted || generation !== workspaceRequestGeneration) return;
    workspaces.value = result.workspaces;
    workspaceWarning.value = result.warning ?? '';
  } catch (cause) { if (generation === workspaceRequestGeneration) workspaceWarning.value = explain(cause); }
  finally { if (generation === workspaceRequestGeneration) workspaceLoading.value = false; }
}

function chooseWorkspace(item: WorkspaceOption) {
  if (!item.available || (workspaceTarget.value === 'conversation' && conversation.value)) return;
  if (workspaceTarget.value === 'connection') form.value.workspace = item.path;
  else workspace.value = item.path;
  workspacePickerOpen.value = false;
}

function editConnection(connection?: Connection) {
  if (connectionSaving.value) return;
  addWorkspaceOpen.value = false;
  form.value = connection
    ? { id: connection.id, name: connection.name, kind: connection.kind, baseUrl: connection.baseUrl ?? '', apiKey: '', executable: connection.executable ?? '', workspace: connection.workspace ?? '' }
    : { name: '', kind: 'openai-responses', baseUrl: 'https://api.openai.com/v1', apiKey: '', executable: '', workspace: '' };
  connectionError.value = '';
  connectionNotice.value = '';
  workspacePickerOpen.value = false;
  workspaceRequestGeneration++;
  workspaceLoading.value = false;
  settingsOpen.value = true;
}

function duplicateConnection() {
  if (connectionSaving.value) return;
  form.value = { ...form.value, id: undefined, name: `${form.value.name}（新连接）`, executable: '', apiKey: '' };
  connectionError.value = '';
  connectionNotice.value = '将另存为新连接，并自动查找代理程序。原对话仍使用原连接。';
  workspacePickerOpen.value = false;
  workspaceRequestGeneration++;
  workspaceLoading.value = false;
}

async function saveConnection() {
  if (connectionSaving.value || !form.value.name.trim()) return;
  connectionSaving.value = true;
  connectionError.value = '';
  connectionNotice.value = '';
  const input: ConnectionInput = { id: form.value.id, name: form.value.name.trim(), kind: form.value.kind };
  if (localAgent(input.kind)) {
    input.executable = form.value.executable?.trim() || undefined;
    input.workspace = form.value.workspace?.trim() || undefined;
  } else {
    input.baseUrl = form.value.baseUrl?.trim();
    if (form.value.apiKey) input.apiKey = form.value.apiKey;
  }
  // Clear secrets immediately after handing them to the authenticated request.
  const saving = api.saveConnection(input);
  form.value.apiKey = '';
  try {
    const saved = await saving;
    if (!mounted) return;
    // A connection edit invalidates cached model availability and data-sharing consent.
    grantedTools.value = [];
    grantConnectionId.value = '';
    delete modelCatalogs.value[saved.id];
    await refreshState();
    await refreshAgents();
    form.value.id = saved.id;
    if (!conversation.value) {
      const refreshed = connections.value.find(item => item.id === saved.id);
      const replacement = resolveDraftConnection(connections.value, refreshed?.id ?? saved.id, false);
      selectedConnectionId.value = replacement.id;
      selectConnection();
    }
    connectionNotice.value = '连接已保存。点击「读取模型」检查连接并选择模型。';
  } catch (cause) { if (mounted) connectionError.value = explain(cause); }
  finally { connectionSaving.value = false; }
}

async function sendMessage() {
  if (!canSend.value) return;
  const text = message.value.trim();
  const generation = viewGeneration;
  sending.value = true;
  error.value = '';
  notice.value = '';
  try {
    let target = conversation.value;
    if (!target) {
      creating.value = true;
      target = await api.createConversation({ connectionId: selectedConnectionId.value, model: selectedModel.value.trim(), modelOptions: { ...selectedModelOptions.value }, workspace: workspace.value.trim() || undefined });
      if (!mounted || generation !== viewGeneration) return;
      conversation.value = target;
    }
    const allowed = permittedTools.value.map(tool => ({ ...tool }));
    const result = await api.send(target.id, { text, tools: allowed });
    if (!mounted || generation !== viewGeneration) return;
    runTools.set(result.runId, allowed);
    message.value = '';
    runView.value = emptyRunView();
    currentRun.value = { id: result.runId, status: 'running', conversationId: target.id };
    conversation.value = await api.conversation(target.id);
    if (!mounted || generation !== viewGeneration) return;
    cancelPending.value = false;
    followBottom.value = true;
    await refreshState();
    schedulePoll(result.runId, generation, 0);
    await scrollToLatest(true);
  } catch (cause) {
    if (generation === viewGeneration) {
      error.value = explain(cause);
      // A failed transport may have delivered the request. Read authority before enabling a retry.
      if (conversation.value && cause instanceof ApiError && ['NETWORK_ERROR', 'TIMEOUT'].includes(cause.code)) {
        const recoveryId = conversation.value.id;
        notice.value = '正在检查服务端是否已经接收消息，请勿重复发送。';
        try {
          const recovered = await api.conversation(recoveryId);
          if (generation === viewGeneration) {
            conversation.value = recovered;
            if (recovered.activeRun) {
              currentRun.value = { id: recovered.activeRun, status: 'running', conversationId: recoveryId };
              message.value = '';
              schedulePoll(recovered.activeRun, generation, 0);
            }
          }
        } catch { notice.value = '消息是否已接收尚未确认。请先点击刷新状态，再决定是否重发。'; }
      }
    }
  } finally { sending.value = false; creating.value = false; }
}

function schedulePoll(runId: string, generation: number, delay = 500) {
  stopPolling();
  if (mounted && generation === viewGeneration) pollTimer = setTimeout(() => void poll(runId, generation), delay);
}
async function poll(runId: string, generation: number) {
  try {
    const page = await api.run(runId, runView.value.sequence);
    if (!mounted || generation !== viewGeneration || currentRun.value?.id !== runId) return;
    const applied = applyRunEvents(runView.value, page.events);
    applied.view.sequence = Math.max(applied.view.sequence, page.nextSequence);
    applied.view.status = page.run.status;
    if (page.run.error) applied.view.error = page.run.error;
    if ('approval' in page.run) applied.view.approval = page.run.approval ?? null;
    runView.value = applied.view;
    currentRun.value = page.run;
    if (isTerminal(page.run.status)) {
      if (cancelPending.value) notice.value = '';
      cancelPending.value = false;
      runView.value.approval = null;
      const saved = await api.conversation(page.run.conversationId);
      if (!mounted || generation !== viewGeneration) return;
      conversation.value = saved;
      // The persisted assistant message is authoritative once the run finishes.
      runView.value.text = '';
      await refreshState();
      await scrollToLatest();
      return;
    }
    await scrollToLatest();
    for (const event of applied.toolRequests) await executeTool(runId, event);
    if (mounted && generation === viewGeneration) schedulePoll(runId, generation);
  } catch (cause) {
    if (mounted && generation === viewGeneration) {
      error.value = `读取任务状态失败：${explain(cause)}`;
      notice.value = '任务是否结束尚未确认。可以刷新状态，或请求停止。';
    }
  }
}

async function executeTool(runId: string, event: RunEvent) {
  if (!event.requestId || !event.name) return;
  const key = `${runId}:${event.requestId}`;
  if (handledToolRequests.has(key)) return;
  handledToolRequests.add(key);
  const response: { runId: string; requestId: string; result?: unknown; error?: string } = { runId, requestId: event.requestId };
  try {
    const tool = findAllowedTool(runTools.get(runId) ?? [], event.name);
    if (!event.arguments || typeof event.arguments !== 'object' || Array.isArray(event.arguments)) throw new Error('工具参数格式不正确。');
    response.result = await callTool(tool, event.arguments);
  } catch (cause) { response.error = explain(cause); }
  try { await api.toolResult(runId, response); }
  catch (cause) {
    pendingToolResults.value.push(response);
    toolError.value = `工具结果尚未送达：${explain(cause)}。重试只会重新发送该结果，不会再次执行工具。`;
  }
}

async function retryToolResults() {
  const pending = [...pendingToolResults.value];
  toolLoading.value = true;
  try {
    for (const item of pending) {
      await api.toolResult(item.runId, item);
      pendingToolResults.value = pendingToolResults.value.filter(entry => entry !== item);
    }
    toolError.value = '';
    if (currentRun.value && active.value) schedulePoll(currentRun.value.id, viewGeneration, 0);
  } catch (cause) { toolError.value = explain(cause); }
  finally { toolLoading.value = false; }
}

async function cancelRun() {
  const run = currentRun.value;
  if (!run || !active.value || cancelPending.value) return;
  cancelPending.value = true;
  error.value = '';
  try {
    await api.cancel(run.id);
    if (currentRun.value?.id === run.id) {
      notice.value = '已请求停止，正在等待后台确认。';
      schedulePoll(run.id, viewGeneration, 0);
    }
  } catch (cause) { cancelPending.value = false; error.value = explain(cause); }
}

async function answerApproval(decision: 'accept' | 'decline') {
  const run = currentRun.value;
  const approval = pendingApproval.value;
  if (!run || !approval || approvalPending.value) return;
  approvalPending.value = true;
  error.value = '';
  try {
    await api.approve(run.id, approval.requestId, decision);
    if (currentRun.value?.id === run.id) {
      currentRun.value.approval = null;
      runView.value.approval = null;
      schedulePoll(run.id, viewGeneration, 0);
    }
  } catch (cause) { error.value = explain(cause); }
  finally { approvalPending.value = false; }
}

async function refreshRun() {
  error.value = '';
  await refreshState();
  if (currentRun.value && active.value) schedulePoll(currentRun.value.id, viewGeneration, 0);
  else if (conversation.value) {
    const id = conversation.value.id;
    const generation = viewGeneration;
    try {
      const confirmed = await api.conversation(id);
      if (!mounted || generation !== viewGeneration) return;
      conversation.value = confirmed;
      selectedModel.value = confirmed.model;
      selectedModelOptions.value = { ...confirmed.modelOptions };
      modelUncertain.value = false;
      if (confirmed.activeRun) {
        currentRun.value = { id: confirmed.activeRun, status: 'running', conversationId: id };
        schedulePoll(confirmed.activeRun, generation, 0);
      }
    } catch (cause) { if (generation === viewGeneration) error.value = explain(cause); }
  }
}

async function discoverTools() {
  if (isLocalAgent.value) {
    toolError.value = '本地代理使用自身工具；OpenDesk 插件工具请通过 API 连接使用。';
    return;
  }
  toolPanelOpen.value = true;
  toolLoading.value = true;
  toolError.value = '';
  try {
    hostAvailable.value = available();
    if (!hostAvailable.value) throw new Error('当前页面未连接支持工具能力的 OpenDesk 宿主。API 聊天仍然可用。');
    const discovered = await listTools();
    tools.value = discovered.filter(tool => tool.readOnly === true);
    grantedTools.value = grantedTools.value.filter(granted => tools.value.some(tool => sameTool(tool, granted)));
    selectedToolNames.value = selectedToolNames.value.filter(name => tools.value.some(tool => tool.name === name));
    toolPanelOpen.value = true;
    if (!tools.value.length) toolError.value = '当前没有可用的只读工具。请先打开支持 AI 工具的目标页签，再刷新列表。';
  } catch (cause) { toolError.value = explain(cause); }
  finally { toolLoading.value = false; }
}

async function authorizeTools() {
  if (!selectedTools.value.length || active.value) return;
  toolLoading.value = true;
  toolError.value = '';
  const requested = selectedTools.value.map(tool => ({ ...tool }));
  try {
    await grantTools(requested);
    grantedTools.value = requested;
    grantConnectionId.value = currentConnectionId.value;
    notice.value = '工具范围已确认。仅勾选的工具会提供给下一次任务。';
  } catch (cause) { toolError.value = explain(cause); }
  finally { toolLoading.value = false; }
}

function composerKey(event: KeyboardEvent) {
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !event.isComposing) { event.preventDefault(); void sendMessage(); }
}

onMounted(async () => {
  sidebarOpen.value = !compactViewport.matches;
  compactViewport.addEventListener('change', updateCompactLayout);
  window.addEventListener('focus', refreshOnFocus);
  if (workbenchElement.value) {
    containerWidth.value = workbenchElement.value.clientWidth;
    workbenchObserver = new ResizeObserver(entries => { const entry = entries[0]; if (entry) containerWidth.value = entry.contentRect.width; });
    workbenchObserver.observe(workbenchElement.value);
  }
  await refreshState();
  if (mounted) await refreshAgents();
  initialConnectionSelection = false;
  if (mounted && !connections.value.length) settingsOpen.value = true;
});
onUnmounted(() => {
  workbenchObserver?.disconnect();
  layoutSaver.dispose();
  compactViewport.removeEventListener('change', updateCompactLayout);
  window.removeEventListener('focus', refreshOnFocus);
  mounted = false; viewGeneration++; modelRequestGeneration++; workspaceRequestGeneration++; stopPolling(); form.value.apiKey = '';
});
</script>

<template>
  <main ref="workbenchElement" class="ai-workbench" :class="{ 'sidebar-collapsed': !sidebarOpen, 'settings-visible': settingsOpen, 'sidebar-resizing': sidebarResizing }" :style="{ '--ai-sidebar-width': `${renderedSidebarWidth}px` }">
    <button v-if="sidebarOpen" class="sidebar-scrim" aria-label="关闭会话列表" @click="sidebarOpen = false"></button>
    <aside v-if="sidebarOpen" id="ai-workspace-sidebar" class="conversation-sidebar" aria-label="会话列表">
      <div class="brand-row"><strong>AI 工作台</strong><button class="icon-button" aria-label="收起会话列表" title="收起会话列表" @click="sidebarOpen = false"><PanelLeft /></button></div>
      <button class="new-chat-button" :disabled="sending || creating || modelSaving" @click="resetDraft"><SquarePen />新对话</button>
      <label class="conversation-search"><Search /><input v-model="search" aria-label="搜索会话" placeholder="搜索工作区和对话" type="search" /></label>
      <div class="section-label"><span>工具与工作区</span><button class="icon-button" :disabled="agentsRefreshing" aria-label="刷新本地工具与工作区" title="刷新本地工具与工作区" @click="refreshAgents"><RefreshCw :class="{ spin: agentsRefreshing }" /></button></div>
      <WorkspaceTree :groups="workspaceTree" :selected-conversation="conversation?.id" :selected-workspace="!conversation ? selectedWorkspaceKey : undefined" :busy="sending || creating || modelSaving" @conversation="openConversation" @workspace="startInWorkspace" @add="addWorkspace" />
      <button class="sidebar-settings" :aria-expanded="settingsOpen" @click="settingsOpen = !settingsOpen"><Settings2 />连接设置<span>{{ connections.length }}</span></button>
    </aside>
    <SidebarDivider v-if="sidebarOpen && !compactLayout" :width="renderedSidebarWidth" :min="sidebarLimits.min" :max="sidebarLimits.max" @preview="previewSidebarWidth" @commit="commitSidebarWidth" @cancel="cancelSidebarResize" @resizing="resizeSidebar" />

    <section class="chat-panel" aria-label="AI 对话">
      <header class="chat-header">
        <div class="chat-heading"><button v-if="!sidebarOpen" class="icon-button" aria-label="展开会话列表" title="展开会话列表" @click="sidebarOpen = true"><PanelLeft /></button><h1>{{ conversation?.title || '新对话' }}</h1></div>
        <div class="header-actions"><span v-if="selectedConnection" class="connection-caption">{{ selectedConnection.name }}</span><button class="icon-button" :class="{ selected: settingsOpen }" :aria-expanded="settingsOpen" aria-label="连接与设置" title="连接与设置" @click="settingsOpen = !settingsOpen"><Settings2 /></button></div>
      </header>
      <div v-if="error" class="banner error-banner" role="alert"><span>{{ error }}</span><button class="text-button" :disabled="refreshing" @click="refreshRun">刷新状态</button><button class="icon-button" aria-label="关闭错误提示" @click="error = ''"><X /></button></div>
      <div v-if="layoutError" class="banner error-banner" role="alert"><span>{{ layoutError }}</span><button class="text-button" @click="commitSidebarWidth(preferredSidebarWidth)">重试保存宽度</button></div>
      <div v-if="notice || modelUncertain" class="banner notice-banner" role="status"><span>{{ notice || '模型状态尚未确认，请刷新状态后再发送。' }}</span><button v-if="modelUncertain" class="text-button" :disabled="refreshing" @click="refreshRun">刷新状态</button><button v-else class="icon-button" aria-label="关闭提示" @click="notice = ''"><X /></button></div>
      <div v-if="shuttingDown" class="banner notice-banner" role="status">正在等待后台任务结束。</div>
      <div v-if="selectedConnection && !connectionReady(selectedConnection)" class="banner error-banner" role="status"><span>{{ selectedConnection.health?.message || '此连接当前不可用' }}。历史对话仍保留。</span><button class="text-button" @click="editConnection(selectedConnection)">检查连接</button></div>

      <div ref="scrollArea" class="message-scroll" @scroll="trackScroll">
        <section v-if="!conversation && !switching" class="welcome">
          <h2>{{ usableConnections.length ? '开始一项任务' : '连接你的 AI' }}</h2>
          <p>{{ usableConnections.length ? '从左侧选择工作区，继续对话或开始新任务。' : agentsRefreshing ? '正在检查本地工具' : '使用本地 Codex、Cursor，或自己的 API。' }}</p>
          <button v-if="!usableConnections.length && !agentsRefreshing" class="secondary-button" @click="editConnection()"><Plus />添加连接</button>
        </section>
        <div v-if="switching" class="loading-state" role="status"><LoaderCircle class="spin" />正在读取对话</div>
        <div v-else-if="conversation" class="message-list">
          <article v-for="item in conversation.messages" :key="item.id" class="message" :class="item.role === 'user' ? 'user-message' : 'assistant-message'">
            <div class="message-content"><div class="message-label">{{ item.role === 'user' ? '你' : item.role === 'tool' ? '工具结果' : selectedConnection?.name || '助手' }}</div><MessageBody :text="item.text" /></div>
          </article>
          <article v-if="active || runView.text" class="message assistant-message streaming-message">
            <div class="message-content"><div class="message-label">{{ selectedConnection?.name || '助手' }}<span v-if="active" class="inline-status">{{ statusText }}</span></div><MessageBody v-if="runView.text" :text="runView.text" /><div v-else class="thinking-placeholder" role="status"><LoaderCircle class="spin" />{{ pendingApproval ? '等待确认' : '等待响应' }}</div></div>
          </article>
          <div v-if="runView.tools.length" class="tool-activity-list" aria-label="工具执行记录"><details v-for="tool in runView.tools" :key="tool.requestId" class="tool-activity"><summary><Wrench /><span>{{ tools.find(item => item.name === tool.name)?.title || tool.name }}</span><span class="activity-state">{{ tool.state === 'completed' ? '已完成' : tool.state === 'failed' ? '失败' : '处理中' }}</span><ChevronDown /></summary><div class="tool-detail"><span class="detail-label">参数</span><pre>{{ pretty(tool.arguments) }}</pre><template v-if="tool.result !== undefined"><span class="detail-label">结果</span><pre>{{ pretty(tool.result) }}</pre></template></div></details></div>
          <section v-if="pendingApproval && active" class="approval-card" role="region" aria-label="操作确认"><h3>{{ pendingApproval.title || '代理请求执行操作' }}</h3><pre v-if="pendingApproval.detail">{{ pretty(pendingApproval.detail) }}</pre><div class="approval-actions"><button class="secondary-button" :disabled="approvalPending" @click="answerApproval('decline')">拒绝</button><button class="primary-button" :disabled="approvalPending" @click="answerApproval('accept')">{{ approvalPending ? '正在提交' : '允许这一次' }}</button></div></section>
          <div v-if="runView.error" class="run-error" role="alert">{{ runView.error }}</div>
          <p v-if="currentRun && isTerminal(currentRun.status)" class="run-end" role="status">{{ statusText }}</p>
        </div>
      </div>

      <div class="composer-area">
        <section v-if="toolPanelOpen" class="tool-picker" aria-label="本次可用工具"><div class="tool-picker-header"><strong>插件工具</strong><button class="text-button" :disabled="toolLoading || active" @click="discoverTools">刷新</button><button class="icon-button" aria-label="关闭工具列表" @click="toolPanelOpen = false"><X /></button></div><p>勾选的工具结果会发送给当前连接。</p><div class="tool-options"><label v-for="tool in tools" :key="`${tool.moduleId}:${tool.name}:${tool.digest}`"><input v-model="selectedToolNames" :value="tool.name" type="checkbox" :disabled="active || toolLoading" /><span><strong>{{ tool.title }}</strong><small>{{ tool.description }}</small><em>{{ tool.moduleId }} · v{{ tool.version }}</em></span></label></div><button v-if="tools.length" class="secondary-button" :disabled="toolLoading || active || !selectedTools.length || !ungrantedTools" @click="authorizeTools">{{ toolLoading ? '等待授权' : ungrantedTools ? '确认授权' : '已授权' }}</button><p v-if="toolError" class="field-error" role="alert">{{ toolError }}</p></section>
        <div v-if="pendingToolResults.length" class="banner error-banner"><span>{{ pendingToolResults.length }} 个工具结果尚未送达。</span><button class="text-button" :disabled="toolLoading" @click="retryToolResults">重试</button></div>
        <div v-if="isLocalAgent || workspace" class="workspace-bar">
          <Folder /><input v-if="!conversation" v-model="workspace" placeholder="工作目录" :disabled="sending || switching" :aria-label="`${providerName(selectedConnection?.kind)} 工作目录`" spellcheck="false" /><span v-else class="workspace-path" :title="workspace">{{ workspace }}</span>
          <button v-if="selectedConnection?.kind === 'codex' && !conversation" class="text-button" :disabled="workspaceLoading || sending" @click="loadWorkspaces('conversation')">{{ workspaceLoading ? '读取中' : '从 Codex 选择' }}</button>
        </div>
        <section v-if="workspacePickerOpen && workspaceTarget === 'conversation'" class="workspace-picker" aria-label="Codex 工作区"><div class="picker-heading"><strong>Codex 工作区</strong><button class="icon-button" aria-label="关闭工作区列表" @click="workspacePickerOpen = false"><X /></button></div><p v-if="workspaceLoading" class="subtle-text">正在读取已登记的目录</p><button v-for="item in availableWorkspaces" :key="item.path" class="workspace-option" :disabled="!item.available" :title="item.path" @click="chooseWorkspace(item)"><Folder /><span><strong>{{ item.label }}</strong><small>{{ item.path }}</small></span><span v-if="!item.available">不可用</span><Check v-else-if="item.path === workspace" /></button><p v-if="workspaceWarning" class="field-error" role="status">{{ workspaceWarning }}</p><p v-else-if="!workspaceLoading && !availableWorkspaces.length" class="subtle-text">没有可用的本地工作区。</p></section>
        <div class="composer" :class="{ focused: Boolean(message) }">
          <textarea ref="composer" v-model="message" :disabled="sending || shuttingDown || switching" :placeholder="!connections.length ? '添加连接后开始对话' : active ? '先写好下一条消息' : '描述任务，或提出问题'" rows="3" maxlength="16384" aria-label="发送给 AI 的消息" @keydown="composerKey"></textarea>
          <div class="composer-bottom">
            <div class="composer-options" aria-label="当前对话连接">
              <button v-if="!isLocalAgent" class="icon-button context-button" :class="{ selected: toolPanelOpen || permittedTools.length }" :disabled="toolLoading || active" :aria-expanded="toolPanelOpen" aria-label="选择插件工具" :title="permittedTools.length ? `${permittedTools.length} 个工具已授权` : '选择插件工具'" @click="toolPanelOpen ? toolPanelOpen = false : discoverTools()"><Plus /><span v-if="permittedTools.length">{{ permittedTools.length }}</span></button>
              <label v-if="!conversation" class="connection-choice"><span class="sr-only">连接</span><select v-model="selectedConnectionId" :disabled="sending || switching || !usableConnections.length" aria-label="选择 AI 连接" @change="selectConnection"><option value="" disabled>选择连接</option><option v-for="connection in usableConnections" :key="connection.id" :value="connection.id">{{ connection.name }}</option></select></label>
              <ModelSelect :model="selectedModel" :models="visibleModelOptions" :disabled="active || sending || switching || modelSaving || modelUncertain || !currentConnectionId" :loading="modelLoading" @select="chooseModel" />
              <button class="icon-button fetch-models" :class="{ 'has-label': !visibleModelOptions.length }" :disabled="modelLoading || !connectionReady(selectedConnection) || sending || modelSaving" aria-label="刷新模型列表" :title="visibleModelOptions.length ? `刷新模型（当前 ${visibleModelOptions.length} 个）` : '读取此连接的模型列表'" @click="loadModels"><RefreshCw :class="{ spin: modelLoading }" /><span v-if="!visibleModelOptions.length">{{ modelLoading ? '读取中' : '读取模型' }}</span></button>
              <button class="text-button manual-model" :disabled="active || sending || switching || modelSaving || modelUncertain || !currentConnectionId" :aria-expanded="customModelOpen" @click="customModelOpen = !customModelOpen; customModel = selectedModel">手填</button>
            </div>
            <button v-if="active" class="send-button stop-button" :disabled="cancelPending" :aria-label="cancelPending ? '正在停止' : '停止任务'" :title="cancelPending ? '正在停止' : '停止任务'" @click="cancelRun"><LoaderCircle v-if="cancelPending" class="spin" /><Square v-else /></button><button v-else class="send-button" :disabled="!canSend" aria-label="发送消息" title="发送消息（Ctrl + Enter）" @click="sendMessage"><ArrowUp /></button>
          </div>
          <ModelParameters :parameters="modelParameters" :values="selectedModelOptions" :disabled="active || sending || switching || modelSaving || modelUncertain" @select="chooseParameter" />
          <form v-if="customModelOpen" class="custom-model-form" @submit.prevent="chooseModel(customModel)"><input v-model="customModel" aria-label="模型 ID" placeholder="填写模型 ID" maxlength="200" :disabled="modelSaving" /><button class="text-button" :disabled="!customModel.trim() || modelSaving">{{ modelSaving ? '应用中' : '应用' }}</button></form>
        </div>
        <div class="composer-footer"><span><i class="status-dot" :class="{ muted: !loaded, busy: active }"></i>{{ modelSaving ? '正在切换模型' : statusText }}</span><span>{{ isLocalAgent ? `${providerName(selectedConnection?.kind)} · 本地代理` : 'API 连接' }}</span><span class="keyboard-hint">Ctrl + Enter 发送</span></div>
      </div>
    </section>

    <button v-if="addWorkspaceOpen" class="settings-scrim" aria-label="关闭添加工作区" @click="addWorkspaceOpen = false"></button>
    <aside v-if="addWorkspaceOpen" class="settings-panel" aria-label="添加工作区"><div class="settings-heading"><h2>添加工作区</h2><button class="icon-button" aria-label="关闭添加工作区" @click="addWorkspaceOpen = false"><X /></button></div><form class="settings-scroll connection-form" @submit.prevent="saveWorkspace"><label>连接<select v-model="workspaceForm.connectionId" :disabled="workspaceSaving" aria-label="工作区连接"><option v-for="connection in usableConnections" :key="connection.id" :value="connection.id">{{ connection.name }}</option></select></label><label>目录<input v-model="workspaceForm.path" required :disabled="workspaceSaving" aria-label="新工作区目录" placeholder="现有目录的绝对路径" spellcheck="false" /></label><label>名称（可选）<input v-model="workspaceForm.label" :disabled="workspaceSaving" maxlength="120" placeholder="默认使用目录名称" /></label><p class="subtle-text">添加到 OpenDesk 的工作区列表。</p><p v-if="workspaceFormError" class="field-error" role="alert">{{ workspaceFormError }}</p><button type="submit" class="primary-button" :disabled="workspaceSaving || !workspaceForm.path.trim()">{{ workspaceSaving ? '保存中' : '添加工作区' }}</button></form></aside>

    <button v-if="settingsOpen" class="settings-scrim" aria-label="关闭连接设置" @click="settingsOpen = false"></button>
    <aside v-if="settingsOpen" class="settings-panel" aria-label="连接与设置">
      <div class="settings-heading"><h2>连接设置</h2><button class="icon-button" aria-label="关闭连接设置" @click="settingsOpen = false"><X /></button></div>
      <div class="settings-scroll">
        <section class="saved-connections"><div class="section-label"><span>已保存</span><button class="text-button" @click="editConnection()"><Plus />添加</button></div><button v-for="connection in connections" :key="connection.id" class="saved-connection" :class="{ selected: form.id === connection.id }" @click="editConnection(connection)"><Terminal v-if="localAgent(connection.kind)" /><MessageSquare v-else /><span><strong>{{ connection.name }}</strong><small>{{ providerName(connection.kind) }} · {{ connection.origin === 'detected' ? '自动发现' : '手动添加' }}</small></span><span class="credential-badge" :class="{ unavailable: !connectionReady(connection) }" :title="connection.health?.message">{{ connection.duplicateOf ? '重复连接' : !connectionReady(connection) ? '需处理' : localAgent(connection.kind) ? '程序可用' : '已配置' }}</span></button><p v-if="!connections.length" class="subtle-text">还没有连接。</p></section>
        <details v-if="agentWarnings.length" class="agent-check-warnings"><summary>本地检查提醒（{{ agentWarnings.length }}）</summary><p v-for="(warning, index) in agentWarnings" :key="`${warning.connectionId}:${index}`">{{ warning.message }}</p></details><form class="connection-form" @submit.prevent="saveConnection"><div v-if="editingConnection?.health" class="connection-health" :class="{ unavailable: !connectionReady(editingConnection) }"><span>{{ editingConnection.health.message }}</span><small v-if="editingConnection.health.resolvedExecutable">{{ editingConnection.health.resolvedExecutable }}</small><small v-if="editingConnection.health.autoRepaired">已定位到更新后的官方程序。</small></div><div class="section-label"><span>{{ form.id ? '编辑连接' : '新建连接' }}</span><button v-if="form.id && localAgent(form.kind)" type="button" class="text-button" @click="duplicateConnection">另存为新连接</button></div><label>名称<input v-model="form.name" placeholder="连接名称" required maxlength="80" :disabled="connectionSaving" /></label><label>接入方式<select v-model="form.kind" :disabled="connectionSaving" @change="form.apiKey = ''; workspacePickerOpen = false"><option value="openai-responses">OpenAI Responses API</option><option value="openai-chat">OpenAI 兼容聊天 API</option><option value="codex">Codex 本地代理</option><option value="cursor">Cursor 本地代理</option></select></label>
          <template v-if="!localAgent(form.kind)"><label>接口地址<input v-model="form.baseUrl" type="url" placeholder="https://api.openai.com/v1" required :disabled="connectionSaving" spellcheck="false" /><small>密钥只发送到此地址。</small></label><label>API Key<input v-model="form.apiKey" type="password" :placeholder="editingConnection?.hasKey ? '已保存；留空保留' : '输入 API Key'" autocomplete="off" spellcheck="false" :disabled="connectionSaving" /><small>加密保存，界面不读回密钥。</small></label></template>
          <template v-else><p class="connection-info">{{ form.kind === 'codex' ? '使用本机已登录的 Codex，自动查找当前版本。' : '使用已安装并登录的 Cursor Agent CLI。Cursor 编辑器本身不是代理入口。' }}</p><label>代理程序路径<input v-model="form.executable" :placeholder="form.kind === 'codex' ? '留空自动查找 Codex（推荐）' : '留空自动查找 cursor-agent / agent'" :disabled="connectionSaving" spellcheck="false" /><small>固定旧版本路径可能在软件升级后失效。已有对话的连接需要另存后更换程序。</small></label><label>默认工作目录<input v-model="form.workspace" placeholder="项目目录的绝对路径" :disabled="connectionSaving" spellcheck="false" /></label><button v-if="form.kind === 'codex'" type="button" class="secondary-button" :disabled="!form.id || workspaceLoading || connectionSaving" @click="loadWorkspaces('connection')"><Folder />{{ workspaceLoading ? '正在读取' : '从 Codex 选择工作区' }}</button><small v-if="form.kind === 'codex' && !form.id" class="subtle-text">保存连接后可读取 Codex 已登记的工作区。</small>
            <section v-if="workspacePickerOpen && workspaceTarget === 'connection'" class="workspace-picker in-settings" aria-label="Codex 工作区"><div class="picker-heading"><strong>Codex 工作区</strong><button type="button" class="icon-button" aria-label="关闭工作区列表" @click="workspacePickerOpen = false"><X /></button></div><button v-for="item in availableWorkspaces" :key="item.path" type="button" class="workspace-option" :disabled="!item.available" :title="item.path" @click="chooseWorkspace(item)"><Folder /><span><strong>{{ item.label }}</strong><small>{{ item.path }}</small></span><span v-if="!item.available">不可用</span></button><p v-if="workspaceWarning" class="field-error" role="status">{{ workspaceWarning }}</p><p v-else-if="!workspaceLoading && !availableWorkspaces.length" class="subtle-text">没有可用的本地工作区。</p></section>
          </template>
          <p v-if="connectionError" class="field-error" role="alert">{{ connectionError }}</p><p v-if="connectionNotice" class="field-success" role="status">{{ connectionNotice }}</p><button type="submit" class="primary-button save-connection" :disabled="connectionSaving || !form.name.trim()">{{ connectionSaving ? '保存中' : '保存连接' }}</button>
        </form>
        <p class="connection-footnote">模型列表由连接返回。点击刷新模型或发送消息时才会访问提供方。</p>
      </div>
    </aside>
  </main>
</template>
