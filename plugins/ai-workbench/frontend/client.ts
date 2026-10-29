import type { AgentCatalog, AppState, Connection, ConnectionInput, Conversation, ModelOption, RunPage, WorkspaceList } from './types';

export class ApiError extends Error {
  constructor(message: string, readonly code: string, readonly status: number) { super(message); }
}

export function readSessionToken(hash: string): string {
  if (!hash.startsWith('#')) return '';
  const value = new URLSearchParams(hash.replace(/^#/, '')).get('session');
  if (!value || value.length > 1024 || /[\r\n]/.test(value)) return '';
  return value;
}

/** A token stays in memory and is never added to a URL or persistent browser storage. */
export function createApiClient(token: string, fetcher: typeof fetch = fetch) {
  async function request<T>(path: string, body?: unknown): Promise<T> {
    if (!token) throw new ApiError('连接凭证缺失，请从 OpenDesk 重新打开 AI 工作台。', 'SESSION_MISSING', 401);
    if (!path.startsWith('/api/') || path.includes('\\') || /[\r\n]/.test(path)) throw new Error('无效的工作台请求地址。');
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), 65_000);
    try {
      const response = await fetcher(path, {
        method: body === undefined ? 'GET' : 'POST',
        headers: { Authorization: `Bearer ${token}`, Accept: 'application/json', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
        body: body === undefined ? undefined : JSON.stringify(body),
        cache: 'no-store', credentials: 'omit', redirect: 'error', signal: abort.signal,
      });
      let result: unknown;
      try { result = await response.json(); }
      catch { throw new ApiError('工作台返回了无法识别的响应，请重试或重新打开页签。', 'INVALID_RESPONSE', response.status); }
      if (!response.ok) {
        const error = result && typeof result === 'object' && 'error' in result ? result.error : null;
        const fields = error && typeof error === 'object' ? error as Record<string, unknown> : {};
        throw new ApiError(typeof fields.message === 'string' ? fields.message : '请求失败，请检查连接状态。', typeof fields.code === 'string' ? fields.code : 'REQUEST_FAILED', response.status);
      }
      return result as T;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (abort.signal.aborted) throw new ApiError('等待响应超时；任务结果尚未确认，请刷新状态后再决定是否重试。', 'TIMEOUT', 0);
      throw new ApiError('无法连接 AI 工作台服务，请确认页签仍在运行。', 'NETWORK_ERROR', 0);
    } finally { clearTimeout(timer); }
  }
  const id = (value: string) => encodeURIComponent(value);
  return {
    state: () => request<AppState>('/api/state'),
    saveLayout: (sidebarWidth: number) => request<{ sidebarWidth: number }>('/api/layout', { sidebarWidth }),
    saveConnection: (input: ConnectionInput) => request<Connection>('/api/connections', input),
    models: (connectionId: string) => request<{ models: ModelOption[] }>(`/api/connections/${id(connectionId)}/models`, {}),
    workspaces: (connectionId: string) => request<WorkspaceList>(`/api/connections/${id(connectionId)}/workspaces`, {}),
    refreshAgents: () => request<AgentCatalog>('/api/agents/refresh', {}),
    saveWorkspace: (input: { connectionId: string; path: string; label?: string }) => request<unknown>('/api/workspaces', input),
    createConversation: (input: { connectionId: string; model: string; modelOptions?: Record<string, string>; title?: string; workspace?: string }) => request<Conversation>('/api/conversations', input),
    conversation: (conversationId: string) => request<Conversation>(`/api/conversations/${id(conversationId)}`),
    setConversationModel: (conversationId: string, model: string, modelOptions?: Record<string, string>) => request<Conversation>(`/api/conversations/${id(conversationId)}/model`, { model, ...(modelOptions ? { modelOptions } : {}) }),
    send: (conversationId: string, input: { text: string; tools?: unknown[]; context?: { name: string; result: unknown }[] }) => request<{ runId: string }>(`/api/conversations/${id(conversationId)}/messages`, input),
    run: (runId: string, after: number) => request<RunPage>(`/api/runs/${id(runId)}?after=${Math.max(0, Math.floor(after))}`),
    cancel: (runId: string) => request<unknown>(`/api/runs/${id(runId)}/cancel`, {}),
    approve: (runId: string, requestId: string, decision: 'accept' | 'decline') => request<unknown>(`/api/runs/${id(runId)}/approval`, { requestId, decision }),
    toolResult: (runId: string, input: { requestId: string; result?: unknown; error?: string }) => request<unknown>(`/api/runs/${id(runId)}/tool-result`, { requestId: input.requestId, ...(input.result === undefined ? {} : { result: input.result }), ...(input.error === undefined ? {} : { error: input.error }) }),
  };
}
