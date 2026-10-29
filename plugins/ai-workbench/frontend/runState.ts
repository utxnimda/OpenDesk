import type { Approval, RunEvent } from './types';

const TERMINAL = new Set(['completed', 'succeeded', 'failed', 'cancelled', 'canceled', 'interrupted', 'unknown']);
export function isTerminal(status: string): boolean { return TERMINAL.has(status); }
export function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    queued: '等待开始', preparing: '准备中', running: '正在处理', waiting_approval: '等待你的决定',
    awaiting_approval: '等待你的决定', waiting_tool: '读取工具结果', awaiting_tool: '读取工具结果',
    cancelling: '正在停止', cancel_requested: '正在停止', completed: '已完成', succeeded: '已完成',
    failed: '执行失败', cancelled: '已停止', canceled: '已停止', interrupted: '任务已中断', unknown: '结果待确认',
  };
  return labels[status] ?? (status ? '处理中' : '就绪');
}

export interface ToolActivity { requestId: string; name: string; arguments?: Record<string, unknown>; result?: unknown; state: 'running' | 'completed' | 'failed'; error?: string }
export interface RunView {
  sequence: number;
  text: string;
  status: string;
  error: string;
  approval: Approval | null;
  tools: ToolActivity[];
}
export function emptyRunView(): RunView { return { sequence: 0, text: '', status: 'queued', error: '', approval: null, tools: [] }; }

/** Replay only new events: reconnects may return an overlapping window. */
export function applyRunEvents(previous: RunView, events: RunEvent[]): { view: RunView; toolRequests: RunEvent[] } {
  const view: RunView = { ...previous, tools: previous.tools.map(tool => ({ ...tool })) };
  const toolRequests: RunEvent[] = [];
  for (const event of events) {
    if (!Number.isSafeInteger(event.sequence) || event.sequence <= view.sequence) continue;
    view.sequence = event.sequence;
    if (event.type === 'delta' && typeof event.text === 'string') view.text += event.text;
    else if (event.type === 'status' && typeof event.status === 'string') view.status = event.status;
    else if (event.type === 'approval' && typeof event.requestId === 'string') view.approval = { requestId: event.requestId, title: event.title, detail: event.detail };
    else if (event.type === 'tool_request' && typeof event.requestId === 'string' && typeof event.name === 'string') {
      view.tools.push({ requestId: event.requestId, name: event.name, arguments: event.arguments, state: 'running' });
      toolRequests.push(event);
    } else if (event.type === 'tool_result' && typeof event.name === 'string') {
      const tool = [...view.tools].reverse().find(entry => entry.name === event.name && entry.state === 'running');
      if (tool) { tool.result = event.result; tool.state = 'completed'; }
    } else if (event.type === 'completed') {
      if (typeof event.text === 'string') view.text = event.text;
      view.approval = null;
    } else if (event.type === 'error') view.error = typeof event.message === 'string' ? event.message : '任务失败，请查看连接设置。';
  }
  return { view, toolRequests };
}

/** Authorize against the immutable tool snapshot captured when a run started. */
export function findAllowedTool<T extends { name: string; readOnly: boolean }>(tools: readonly T[], name: string): T {
  const matches = tools.filter(tool => tool.name === name && tool.readOnly === true);
  if (matches.length !== 1) throw new Error('该工具不在本次对话授权范围内，请重新选择工具后发起新任务。');
  return matches[0];
}

export interface MessagePart { code: boolean; language?: string; text: string }
/** Render text and fenced code as escaped Vue text nodes, never executable HTML. */
export function messageParts(text: string): MessagePart[] {
  const result: MessagePart[] = [];
  const fence = /```([^\n`]*)(?:\r?\n)([\s\S]*?)(?:```|$)/g;
  let start = 0;
  for (const match of text.matchAll(fence)) {
    if (match.index! > start) result.push({ code: false, text: text.slice(start, match.index) });
    result.push({ code: true, language: match[1].trim().slice(0, 40), text: match[2] });
    start = match.index! + match[0].length;
  }
  if (start < text.length) result.push({ code: false, text: text.slice(start) });
  return result;
}
