import { describe, expect, it } from 'vitest';
import {
  buildMcpSnippet,
  DSH_IFRAME_SANDBOX,
  dshSourceLabel,
  stateLabel,
  validateLoopbackProxyUrl,
} from '../integrations/opendesk/frontend/state';

describe('deepseek-harness frontend helpers', () => {
  it('accepts only distinct loopback HTTP proxy URLs', () => {
    const wrapper = 'http://127.0.0.1:50000';
    expect(validateLoopbackProxyUrl('http://127.0.0.1:51234/', wrapper)).toBe('http://127.0.0.1:51234/');
    for (const invalid of [
      '',
      'http://127.0.0.1:50000/',
      'https://127.0.0.1:51234/',
      'http://localhost:51234/',
      'http://user:pass@127.0.0.1:51234/',
      'http://example.com:51234/',
      'javascript:alert(1)',
    ]) {
      expect(validateLoopbackProxyUrl(invalid, wrapper)).toBe('');
    }
  });

  it('exposes complete web-app sandbox capabilities without host escape', () => {
    expect(DSH_IFRAME_SANDBOX).toContain('allow-scripts');
    expect(DSH_IFRAME_SANDBOX).toContain('allow-same-origin');
    expect(DSH_IFRAME_SANDBOX).toContain('allow-forms');
    expect(DSH_IFRAME_SANDBOX).toContain('allow-modals');
    expect(DSH_IFRAME_SANDBOX).toContain('allow-downloads');
    expect(DSH_IFRAME_SANDBOX).not.toContain('allow-top-navigation');
  });

  it('formats labels cleanly', () => {
    expect(stateLabel('running')).toBe('运行中');
    expect(stateLabel('starting')).toBe('启动中');
    expect(dshSourceLabel('managed-cli')).toContain('@deepseek-ai/dsh');
  });

  it('builds valid Streamable HTTP MCP configuration JSON', () => {
    const parsed = JSON.parse(buildMcpSnippet('http://127.0.0.1:54321/mcp', 'tok-xyz'));
    expect(parsed.mcpServers['opendesk-readonly-tools']).toEqual({
      type: 'http',
      url: 'http://127.0.0.1:54321/mcp',
      headers: { Authorization: 'Bearer tok-xyz' },
    });
  });
});
