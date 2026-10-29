import { describe, expect, it } from 'vitest';
import { createSSRApp, h } from 'vue';
import { renderToString } from 'vue/server-renderer';
import { buildWorkspaceTree, connectionReady, modelDefaults, resolveDraftConnection, resolveModelOption, selectableConnection, workspaceKey } from '../frontend/workspaceTree';
import WorkspaceTree from '../frontend/WorkspaceTree.vue';
import ModelParameters from '../frontend/ModelParameters.vue';
import type { Connection, ModelParameter } from '../frontend/types';

const detected: Connection = { id: 'detected', name: 'Codex', kind: 'codex', origin: 'detected', hasKey: false, health: { status: 'ready', message: '程序存在' } };
const manual: Connection = { ...detected, id: 'manual', name: '手动 Codex', origin: 'manual', duplicateOf: 'detected' };
const missing: Connection = { ...detected, id: 'missing', name: '失效路径', health: { status: 'missing', message: '程序不存在' } };

describe('agent and workspace navigation', () => {
  it('chooses an initial target only while initialization explicitly allows it', () => {
    expect(resolveDraftConnection([detected], '', true)).toEqual({ id: 'detected', reason: 'initial' });
    expect(resolveDraftConnection([detected], '', false)).toEqual({ id: '', reason: 'empty' });
  });
  it('clears a missing selection instead of redirecting its draft to another provider', () => {
    const cursor: Connection = { ...detected, id: 'cursor', kind: 'cursor' };
    expect(resolveDraftConnection([cursor, missing], 'missing', false)).toEqual({ id: '', reason: 'cleared' });
    expect(resolveDraftConnection([cursor, detected], '', false)).toEqual({ id: '', reason: 'empty' });
  });
  it('automatically merges only an explicit equivalent connection of the same kind', () => {
    expect(resolveDraftConnection([manual, detected], 'manual', false)).toEqual({ id: 'detected', reason: 'equivalent' });
    expect(resolveDraftConnection([manual, { ...detected, kind: 'cursor' }], 'manual', false)).toEqual({ id: '', reason: 'cleared' });
  });
  it('merges Windows path spellings within a provider while preserving each historical binding', () => {
    const cursor: Connection = { ...detected, id: 'cursor', name: 'Cursor', kind: 'cursor' };
    const result = buildWorkspaceTree([detected, manual, cursor], [
      { connectionId: 'detected', path: 'D:/Projects/Demo', label: 'Demo', source: 'codex-app', available: true },
      { connectionId: 'manual', path: 'd:\\projects\\demo\\', label: 'Demo', source: 'manual', available: true },
      { connectionId: 'cursor', path: 'D:/Projects/Demo', label: 'Demo', source: 'cursor', available: true },
    ], [{ id: 'old-chat', connectionId: 'manual', title: '保留旧记录', model: 'model', workspace: 'D:\\Projects\\Demo' }]);
    const codex = result[0]!;
    expect(codex.workspaces).toHaveLength(1);
    expect(codex.workspaces[0]?.conversations[0]?.connectionId).toBe('manual');
    expect(codex.workspaces[0]?.connections.filter(selectableConnection).map(item => item.id)).toEqual(['detected']);
    expect(result[1]?.workspaces).toHaveLength(1);
    expect(workspaceKey('/Users/A')).not.toBe(workspaceKey('/users/a'));
  });

  it('keeps an available directory history visible when only its connection is offline', async () => {
    const tree = buildWorkspaceTree([missing], [{ connectionId: 'missing', path: 'D:/Gone', label: 'Gone', source: 'manual', available: true }], [{ id: 'old', connectionId: 'missing', title: '历史任务', model: 'model', workspace: 'D:/Gone' }]);
    expect(tree[0]?.workspaces[0]?.available).toBe(true);
    expect(connectionReady(missing)).toBe(false);
    const output = await renderToString(createSSRApp({ render: () => h(WorkspaceTree, { groups: tree, busy: false }) }));
    expect(output).toContain('历史任务');
    expect(output).toContain('离线');
    expect(output).not.toContain('title="目录当前不可用"');
    expect(output).toContain('disabled');
  });

  it('hides unavailable directories even when configured or referenced by history, then restores them after refresh', () => {
    const connection = { ...detected, workspace: 'D:/Unavailable' };
    const directory = { connectionId: detected.id, path: 'D:/Unavailable', label: 'Unavailable', source: 'codex-app', available: false };
    const history = [{ id: 'preserved', connectionId: detected.id, title: '原对话', model: 'model', workspace: directory.path }];
    expect(buildWorkspaceTree([connection], [directory], history)[0]?.workspaces).toHaveLength(0);
    expect(buildWorkspaceTree([connection], [directory], history, '原对话')[0]?.workspaces).toHaveLength(0);
    expect(history).toHaveLength(1);
    expect(directory.available).toBe(false);
    const restored = buildWorkspaceTree([connection], [{ ...directory, available: true }], history);
    expect(restored[0]?.workspaces[0]?.conversations[0]?.id).toBe('preserved');
  });

  it('places API conversations without a workspace under an unassigned node and filters by conversation title', () => {
    const connection: Connection = { id: 'api', kind: 'openai-responses', name: 'API', hasKey: true };
    const result = buildWorkspaceTree([connection], [], [{ id: 'chat', connectionId: 'api', title: '待办清单', model: 'example' }], '待办');
    expect(result[2]?.workspaces[0]?.label).toBe('未分配工作区');
    expect(result[2]?.workspaces[0]?.conversations[0]?.id).toBe('chat');
    expect(selectableConnection({ ...connection, health: { status: 'unconfigured', message: '请配置密钥' } })).toBe(false);
  });

  it('starts empty workspaces collapsed and keeps their source in a tooltip', async () => {
    const tree = buildWorkspaceTree([detected], [{ connectionId: detected.id, path: 'D:/Empty', label: 'Empty', source: 'codex-app', available: true }], []);
    const render = (selectedWorkspace?: string) => renderToString(createSSRApp({ render: () => h(WorkspaceTree, { groups: tree, busy: false, selectedWorkspace }) }));
    const collapsed = await render();
    expect(collapsed).toContain('class="workspace-group"');
    expect(collapsed).not.toContain('class="workspace-group" open');
    expect(collapsed).not.toContain('class="workspace-connections"');
    expect(collapsed).toContain('自动发现');
    const selected = await render('codex:d:/empty');
    expect(selected).toContain('class="workspace-group" open');
  });
});

describe('provider-declared model parameters', () => {
  const parameters: ModelParameter[] = [
    { id: 'reasoningEffort', label: '推理强度', defaultValue: 'high', options: [{ value: 'high', label: 'High' }, { value: 'ultra', label: 'Ultra' }] },
    { id: 'serviceTier', label: '速度', defaultValue: 'default', options: [{ value: 'default', label: '标准' }, { value: 'priority', label: 'Fast', description: '更快响应，消耗更多额度。' }] },
  ];
  it('uses only declared defaults without inventing unsupported effort or enabling Fast', () => {
    expect(modelDefaults({ id: 'model', parameters })).toEqual({ reasoningEffort: 'high', serviceTier: 'default' });
    expect(modelDefaults({ id: 'api-model' })).toEqual({});
    expect(modelDefaults({ id: 'bad-default', parameters: [{ ...parameters[0]!, defaultValue: 'extraMax' }] })).toEqual({});
  });
  it('shows inherited settings for old conversations and the selected Fast usage explanation', async () => {
    const inherited = await renderToString(createSSRApp({ render: () => h(ModelParameters, { parameters, values: {}, disabled: false }) }));
    expect(inherited).toContain('跟随原设置');
    expect(inherited).not.toContain('class="parameter-help"');
    const fast = await renderToString(createSSRApp({ render: () => h(ModelParameters, { parameters, values: { serviceTier: 'priority' }, disabled: true }) }));
    expect(fast).toContain('class="parameter-help"');
    expect(fast).toContain('消耗更多额度');
    expect(fast).toContain('disabled');
  });
  it('resolves old Cursor canonical ids by exact provider base without guessing prefixes or mutating history', () => {
    const entry = { id: 'provider-model[high]', providerModelId: 'provider-model', parameters };
    const catalog = [entry];
    expect(resolveModelOption(catalog, 'provider-model[low,fast]', 'cursor')).toBe(entry);
    expect(resolveModelOption(catalog, 'provider-model-other[low]', 'cursor')).toBeUndefined();
    expect(resolveModelOption(catalog, 'provider-model[low]', 'codex')).toBeUndefined();
    expect(resolveModelOption(catalog, 'provider-model[low]suffix', 'cursor')).toBeUndefined();
    expect(resolveModelOption(catalog, 'provider-model[low', 'cursor')).toBeUndefined();
    expect(entry.id).toBe('provider-model[high]');
  });
});
