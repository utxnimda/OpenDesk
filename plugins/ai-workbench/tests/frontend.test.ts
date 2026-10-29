import { describe, expect, it, vi } from 'vitest';
import { renderToString } from 'vue/server-renderer';
import { createSSRApp, h } from 'vue';
import { ApiError, createApiClient, readSessionToken } from '../frontend/client';
import { applyRunEvents, emptyRunView, findAllowedTool, isTerminal, messageParts, statusLabel } from '../frontend/runState';
import MessageBody from '../frontend/MessageBody.vue';
import ModelSelect from '../frontend/ModelSelect.vue';

describe('authenticated frontend transport', () => {
  it('keeps the session token in a header, uses no cookies and refuses redirects', async () => {
    const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ connections: [], conversations: [], shuttingDown: false }), { status: 200 }));
    const client = createApiClient('private-test-session', fetcher as typeof fetch);
    await client.state();
    const [url, options] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/state');
    expect(url).not.toContain('private-test-session');
    expect(options.headers).toMatchObject({ Authorization: 'Bearer private-test-session' });
    expect(options.credentials).toBe('omit');
    expect(options.redirect).toBe('error');
    expect(options.cache).toBe('no-store');
  });

  it('does not send any request without the session credential', async () => {
    const fetcher = vi.fn();
    await expect(createApiClient('', fetcher).state()).rejects.toMatchObject({ code: 'SESSION_MISSING', status: 401 });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('reads only a bounded, single-line hash credential', () => {
    expect(readSessionToken('#session=abc&hostTools=1')).toBe('abc');
    expect(readSessionToken('?session=abc')).toBe('');
    expect(readSessionToken('#session=abc%0A')).toBe('');
    expect(readSessionToken('#session=' + 'a'.repeat(1025))).toBe('');
  });

  it('preserves structured provider error categories without exposing an HTML error body', async () => {
    const invalid = createApiClient('session', vi.fn(async () => new Response('<html>private debug detail</html>', { status: 502 })) as typeof fetch);
    await expect(invalid.state()).rejects.toMatchObject({ code: 'INVALID_RESPONSE', status: 502 });
    const forbidden = createApiClient('session', vi.fn(async () => new Response(JSON.stringify({ error: { code: 'AUTH_FAILED', message: '密钥无效' } }), { status: 401 })) as typeof fetch);
    await expect(forbidden.state()).rejects.toMatchObject({ code: 'AUTH_FAILED', message: '密钥无效', status: 401 });
  });

  it('encodes conversation identity and does not leak view-only fields into tool replies', async () => {
    const fetcher = vi.fn<typeof fetch>(async () => new Response('{}', { status: 200 }));
    const client = createApiClient('session', fetcher as typeof fetch);
    await client.conversation('unsafe/name?secret=value');
    expect(fetcher.mock.calls[0]?.[0]).toBe('/api/conversations/unsafe%2Fname%3Fsecret%3Dvalue');
    const reply = { runId: 'view-only', requestId: 'request-1', result: { count: 4 } };
    await client.toolResult('run-1', reply);
    const options = (fetcher.mock.calls[1] as unknown as [string, RequestInit])[1];
    expect(JSON.parse(options.body as string)).toEqual({ requestId: 'request-1', result: { count: 4 } });
  });

  it('does not retry a timed-out mutation with an unknown result', async () => {
    vi.useFakeTimers();
    try {
      const fetcher = vi.fn((_path: string, options?: RequestInit) => new Promise<Response>((_resolve, reject) => {
        options?.signal?.addEventListener('abort', () => reject(new Error('aborted')));
      }));
      const promise = createApiClient('session', fetcher as typeof fetch).send('conversation-1', { text: '测试' });
      const assertion = expect(promise).rejects.toBeInstanceOf(ApiError);
      await vi.advanceTimersByTimeAsync(65_000);
      await assertion;
      expect(fetcher).toHaveBeenCalledTimes(1);
    } finally { vi.useRealTimers(); }
  });

  it('uses named, authenticated workspace and model-change operations without sending drafts or keys', async () => {
    const fetcher = vi.fn<typeof fetch>(async () => new Response('{}', { status: 200 }));
    const client = createApiClient('session', fetcher as typeof fetch);
    await client.workspaces('connection/1');
    await client.setConversationModel('conversation/1', 'selected-model');
    expect(fetcher.mock.calls[0]?.[0]).toBe('/api/connections/connection%2F1/workspaces');
    expect(fetcher.mock.calls[1]?.[0]).toBe('/api/conversations/conversation%2F1/model');
    expect(JSON.parse(fetcher.mock.calls[0]?.[1]?.body as string)).toEqual({});
    expect(JSON.parse(fetcher.mock.calls[1]?.[1]?.body as string)).toEqual({ model: 'selected-model' });
  });
});

describe('model selection rendering', () => {
  const catalog = Array.from({ length: 8 }, (_, index) => ({ id: `model-${index}`, label: `Model ${index}` }));
  it('renders the complete catalog for every current selection without text filtering', async () => {
    for (const selected of catalog) {
      const output = await renderToString(createSSRApp({ render: () => h(ModelSelect, { model: selected.id, models: catalog }) }));
      expect(output).toContain('<select');
      expect(output).not.toContain('<datalist');
      expect(output.match(/<option /g)).toHaveLength(9);
      for (const model of catalog) expect(output).toContain(`value="${model.id}"`);
    }
  });
  it('preserves a current model omitted from a refreshed catalog and distinguishes an empty catalog', async () => {
    const output = await renderToString(createSSRApp({ render: () => h(ModelSelect, { model: 'previous-model', models: catalog }) }));
    expect(output).toContain('previous-model（当前）');
    expect(output.match(/<option /g)).toHaveLength(10);
    const empty = await renderToString(createSSRApp({ render: () => h(ModelSelect, { model: '', models: [], loading: true, disabled: true }) }));
    expect(empty).toContain('正在读取模型');
    expect(empty.match(/<option /g)).toHaveLength(1);
    expect(empty).toContain('disabled');
  });
});

describe('incremental run view and permissions', () => {
  it('deduplicates overlapping event windows and tool requests', () => {
    const first = applyRunEvents(emptyRunView(), [
      { sequence: 1, type: 'delta', text: '分析' },
      { sequence: 2, type: 'tool_request', requestId: 'call-1', name: 'tower_stats', arguments: {} },
    ]);
    const second = applyRunEvents(first.view, [
      { sequence: 1, type: 'delta', text: '分析' },
      { sequence: 2, type: 'tool_request', requestId: 'call-1', name: 'tower_stats', arguments: {} },
      { sequence: 3, type: 'tool_result', name: 'tower_stats', result: { count: 5 } },
      { sequence: 4, type: 'delta', text: '完成' },
    ]);
    expect(second.view.text).toBe('分析完成');
    expect(second.toolRequests).toEqual([]);
    expect(second.view.tools).toHaveLength(1);
    expect(second.view.tools[0]).toMatchObject({ state: 'completed', result: { count: 5 } });
    expect(second.view.sequence).toBe(4);
  });

  it('keeps cancellation in progress until the backend reports a terminal status', () => {
    expect(isTerminal('cancelling')).toBe(false);
    expect(isTerminal('cancel_requested')).toBe(false);
    expect(statusLabel('cancelling')).toBe('正在停止');
    expect(isTerminal('cancelled')).toBe(true);
    expect(statusLabel('unknown')).toBe('结果待确认');
    const view = applyRunEvents(emptyRunView(), [{ sequence: 1, type: 'completed', text: '完整结果' }]).view;
    expect(view.status).toBe('queued');
    expect(view.text).toBe('完整结果');
  });

  it('requires exactly one read-only tool in the captured run allowlist', () => {
    const tool = { name: 'tower_stats', readOnly: true, digest: 'captured' };
    expect(findAllowedTool([tool], 'tower_stats')).toBe(tool);
    expect(() => findAllowedTool([tool], 'deployment_execute')).toThrow('授权范围');
    expect(() => findAllowedTool([{ ...tool, readOnly: false }], 'tower_stats')).toThrow();
    expect(() => findAllowedTool([tool, { ...tool }], 'tower_stats')).toThrow();
  });

  it('ignores malformed or old event sequence numbers', () => {
    const previous = { ...emptyRunView(), sequence: 4, text: '保留' };
    const view = applyRunEvents(previous, [{ sequence: Number.NaN, type: 'delta', text: '坏数据' }, { sequence: 3, type: 'error', message: '旧错误' }]).view;
    expect(view.text).toBe('保留');
    expect(view.error).toBe('');
  });
});

describe('safe conversation rendering', () => {
  it('preserves code fences and handles a partial streaming fence', () => {
    expect(messageParts('说明\n```python\nprint(1)')).toEqual([
      { code: false, text: '说明\n' }, { code: true, language: 'python', text: 'print(1)' },
    ]);
  });

  it('renders model-controlled HTML as text in prose and code', async () => {
    const output = await renderToString(createSSRApp({ render: () => h(MessageBody, { text: '<img src=x onerror=alert(1)>\n```html\n<script>evil()</script>\n```' }) }));
    expect(output).toContain('&lt;img');
    expect(output).toContain('hljs-name');
    expect(output).toContain('&lt;');
    expect(output).toContain('script');
    expect(output).not.toContain('<img');
    expect(output).not.toContain('<script>');
  });
});
