import { createModuleBridge, errorMessage } from '@opendesk/module-sdk';
import './style.css';

type Mode = 'embedded' | 'popup';
interface SessionStatus {
  url: string;
  mode: Mode;
  configured: boolean;
  workspace?: string;
  error?: string | null;
  busy?: boolean;
}

const element = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const state = element('state');
const error = element('error');
const setup = element('setup');
const detached = element('detached');
const viewport = element('viewport');
const embedded = element<HTMLButtonElement>('embedded');
const popup = element<HTMLButtonElement>('popup');
const retry = element<HTMLButtonElement>('retry');
const workspace = element<HTMLInputElement>('workspace');
const configure = element<HTMLButtonElement>('configure');
const bridge = createModuleBridge();
let connected = false;
let working = false;
let polling = false;
let disposed = false;
let current: SessionStatus | null = null;
let frame: HTMLIFrameElement | null = null;
let serviceOrigin = '';
let operationError = '';
let pollTimer: ReturnType<typeof setTimeout> | undefined;

function showError(message: string) {
  error.textContent = message;
  error.hidden = !message;
}

function render(status: SessionStatus) {
  if (!status || typeof status.configured !== 'boolean' || !['embedded', 'popup'].includes(status.mode)) {
    throw new Error('作品管理器 返回的运行状态无效。');
  }
  const url = new URL(status.url);
  if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !url.port || url.username || url.password ||
      (serviceOrigin && serviceOrigin !== url.origin)) throw new Error('作品管理器 服务地址发生异常变化，请重新打开模块。');
  serviceOrigin = url.origin;
  current = status;
  if (!workspace.value && status.workspace) workspace.value = status.workspace;
  setup.hidden = status.configured;
  configure.disabled = working || status.busy === true;
  workspace.disabled = configure.disabled;
  embedded.disabled = !status.configured || working;
  popup.disabled = !status.configured || working;
  embedded.setAttribute('aria-pressed', String(status.mode === 'embedded'));
  popup.setAttribute('aria-pressed', String(status.mode === 'popup'));
  detached.hidden = !status.configured || status.mode !== 'popup';
  viewport.hidden = !status.configured || status.mode !== 'embedded';
  state.textContent = status.busy && !status.configured ? '正在载入工作区…' :
    !status.configured ? '等待连接工作区' : status.mode === 'popup' ? '独立窗口' : '嵌入模式';
  retry.hidden = true;
  showError(operationError || status.error || '');
  if (status.configured && !frame) {
    frame = document.createElement('iframe');
    frame.title = '作品管理器';
    frame.referrerPolicy = 'no-referrer';
    frame.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-forms allow-modals allow-downloads allow-popups allow-popups-to-escape-sandbox');
    frame.src = url.href;
    viewport.appendChild(frame);
  }
  // Hiding the embedded view never destroys its DOM or any unsaved input.
}

async function refresh() {
  if (disposed || !connected || polling || working) return;
  polling = true;
  try { render(await bridge.host.callWorker<SessionStatus>('nimda.status', {})); }
  catch (cause) {
    showError(errorMessage(cause));
    state.textContent = '连接暂不可用';
    retry.hidden = false;
  } finally { polling = false; }
}

async function changeMode(mode: Mode) {
  if (working || polling || !current?.configured) return;
  working = true;
  operationError = '';
  embedded.disabled = popup.disabled = true;
  try { render(await bridge.host.callWorker<SessionStatus>('nimda.presentation', { mode })); }
  catch (cause) { operationError = errorMessage(cause); }
  finally {
    working = false;
    if (current) render(current);
  }
}

embedded.addEventListener('click', () => void changeMode('embedded'));
popup.addEventListener('click', () => void changeMode('popup'));
element('return').addEventListener('click', () => void changeMode('embedded'));
retry.addEventListener('click', () => void refresh());
workspace.addEventListener('input', () => {
  operationError = '';
  showError(current?.error || '');
});
element<HTMLFormElement>('configuration').addEventListener('submit', async event => {
  event.preventDefault();
  if (working || polling) return;
  const path = workspace.value.trim();
  if (!path) return;
  working = true;
  operationError = '';
  showError('');
  configure.disabled = true;
  try { render(await bridge.host.callWorker<SessionStatus>('nimda.configure', { workspace: path })); }
  catch (cause) { operationError = errorMessage(cause); }
  finally { working = false; if (current) render(current); }
});

function schedulePoll() {
  pollTimer = setTimeout(async () => {
    await refresh();
    if (!disposed) schedulePoll();
  }, 1800);
}

void bridge.ready.then(async () => {
  connected = true;
  await refresh();
  schedulePoll();
}).catch(cause => { state.textContent = '连接失败'; showError(errorMessage(cause)); });

window.addEventListener('pagehide', () => {
  disposed = true;
  if (pollTimer) clearTimeout(pollTimer);
  bridge.dispose();
}, { once: true });
