import type { CatalogWorkspace, Connection, ConnectionKind, ConversationSummary, ModelOption } from './types';

export type AgentGroup = 'codex' | 'cursor' | 'api';
export interface WorkspaceNode {
  key: string;
  group: AgentGroup;
  path: string;
  label: string;
  available: boolean;
  connections: Connection[];
  conversations: ConversationSummary[];
}
export interface AgentNode { id: AgentGroup; label: string; connections: Connection[]; workspaces: WorkspaceNode[] }
export const connectionReady = (connection?: Connection) => Boolean(connection && (!connection.health || connection.health.status === 'ready'));
export const selectableConnection = (connection: Connection) => connectionReady(connection) && !connection.duplicateOf;
export const agentGroup = (connection: Connection): AgentGroup => connection.kind === 'codex' || connection.kind === 'cursor' ? connection.kind : 'api';

/** Refreshes may clear a draft target, but must never redirect it to a different provider. */
export function resolveDraftConnection(connections: Connection[], selectedId: string, allowInitialSelection: boolean): { id: string; reason: 'initial' | 'unchanged' | 'equivalent' | 'cleared' | 'empty' } {
  if (!selectedId) {
    const initial = allowInitialSelection ? connections.find(selectableConnection) : undefined;
    return initial ? { id: initial.id, reason: 'initial' } : { id: '', reason: 'empty' };
  }
  const selected = connections.find(item => item.id === selectedId);
  if (selected && selectableConnection(selected)) return { id: selectedId, reason: 'unchanged' };
  const equivalent = selected?.duplicateOf ? connections.find(item => item.id === selected.duplicateOf && item.kind === selected.kind && selectableConnection(item)) : undefined;
  return equivalent ? { id: equivalent.id, reason: 'equivalent' } : { id: '', reason: 'cleared' };
}

// Normalize Windows spelling for display grouping only; execution keeps the authoritative path.
export function workspaceKey(path: string): string {
  const normalized = path.replace(/\\/g, '/').replace(/\/+$/, '');
  return /^[a-z]:\//i.test(normalized) || normalized.startsWith('//') ? normalized.toLocaleLowerCase() : normalized;
}

export function buildWorkspaceTree(connections: Connection[], workspaces: CatalogWorkspace[], conversations: ConversationSummary[], search = ''): AgentNode[] {
  const groups: AgentNode[] = [{ id: 'codex', label: 'Codex', connections: [], workspaces: [] }, { id: 'cursor', label: 'Cursor', connections: [], workspaces: [] }, { id: 'api', label: 'API', connections: [], workspaces: [] }];
  const byConnection = new Map(connections.map(item => [item.id, item]));
  const nodeMap = new Map<string, WorkspaceNode>();
  function add(connection: Connection, path: string, label?: string, available?: boolean) {
    const group = groups.find(item => item.id === agentGroup(connection))!;
    const key = `${group.id}:${workspaceKey(path)}`;
    let node = nodeMap.get(key);
    if (!node) {
      node = { key, group: group.id, path, label: label || path.replace(/\\/g, '/').split('/').filter(Boolean).at(-1) || '未分配工作区', available: available ?? !path, connections: [], conversations: [] };
      group.workspaces.push(node); nodeMap.set(key, node);
    } else if (available !== undefined) node.available ||= available;
    if (!node.connections.some(item => item.id === connection.id)) node.connections.push(connection);
    return node;
  }
  for (const connection of connections) groups.find(item => item.id === agentGroup(connection))!.connections.push(connection);
  for (const entry of workspaces) { const connection = byConnection.get(entry.connectionId); if (connection) add(connection, entry.path, entry.label, entry.available); }
  for (const connection of connections) {
    if (connection.workspace) add(connection, connection.workspace);
    if (!connection.duplicateOf && !workspaces.some(item => item.connectionId === connection.id) && !connection.workspace) add(connection, '');
  }
  for (const conversation of conversations) {
    const connection = byConnection.get(conversation.connectionId);
    if (connection) add(connection, conversation.workspace ?? '').conversations.push(conversation);
  }
  const query = search.trim().toLocaleLowerCase();
  for (const group of groups) {
    group.workspaces = group.workspaces.filter(node => node.available);
    group.workspaces.sort((a, b) => Number(!a.path) - Number(!b.path) || a.label.localeCompare(b.label, 'zh-CN'));
    if (query) group.workspaces = group.workspaces.filter(node => {
      if (`${group.label} ${node.label} ${node.path}`.toLocaleLowerCase().includes(query)) return true;
      node.conversations = node.conversations.filter(item => `${item.title} ${item.model}`.toLocaleLowerCase().includes(query));
      return node.conversations.length > 0;
    });
  }
  return groups;
}

export function modelDefaults(model: ModelOption | undefined): Record<string, string> {
  const result: Record<string, string> = {};
  for (const parameter of model?.parameters ?? []) {
    if (parameter.defaultValue !== undefined && parameter.options.some(option => option.value === parameter.defaultValue)) result[parameter.id] = parameter.defaultValue;
  }
  return result;
}

export function resolveModelOption(models: ModelOption[], id: string, kind?: ConnectionKind): ModelOption | undefined {
  const exact = models.find(item => item.id === id);
  if (exact || kind !== 'cursor') return exact;
  const canonical = /^([^\[\]]+)\[[^\[\]]*\]$/.exec(id);
  if (!canonical) return undefined;
  return models.find(item => item.providerModelId === canonical[1]);
}
