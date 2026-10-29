<script setup lang="ts">
import { ChevronRight, Folder, MessageSquare, Plus } from '@lucide/vue';
import type { AgentGroup, AgentNode, WorkspaceNode } from './workspaceTree';
import { connectionReady, selectableConnection } from './workspaceTree';
import type { Connection } from './types';

defineProps<{ groups: AgentNode[]; selectedConversation?: string; selectedWorkspace?: string; busy: boolean }>();
const emit = defineEmits<{ conversation: [id: string]; workspace: [node: WorkspaceNode]; add: [group: AgentGroup] }>();
function sourceLabel(connection: Connection) { return connection.origin === 'detected' ? '自动发现' : '手动添加'; }
function usable(node: WorkspaceNode) { return node.available && node.connections.some(selectableConnection); }
function conversationConnection(node: WorkspaceNode, id: string) { return node.connections.find(item => item.id === id); }
function workspaceDescription(node: WorkspaceNode) {
  return [node.path || '未指定工作目录', ...node.connections.map(item => `${item.name} · ${sourceLabel(item)}${item.duplicateOf ? ' · 与自动发现的连接相同' : ''}${item.health ? ` · ${item.health.message}` : ''}`)].join('\n');
}
</script>

<template>
  <nav class="workspace-tree" aria-label="代理与工作区">
    <details v-for="group in groups" :key="group.id" class="agent-group" open>
      <summary class="agent-group-heading"><ChevronRight /><strong>{{ group.label }}</strong><span class="tree-count">{{ group.connections.filter(selectableConnection).length }}</span><button class="tree-add icon-button" :aria-label="`添加 ${group.label} 工作区`" :title="`添加 ${group.label} 工作区`" :disabled="busy" @click.prevent.stop="emit('add', group.id)"><Plus /></button></summary>
      <details v-for="node in group.workspaces" :key="node.key" class="workspace-group" :open="node.conversations.length > 0 || selectedWorkspace === node.key">
        <summary class="workspace-heading" :class="{ selected: selectedWorkspace === node.key }" :title="workspaceDescription(node)"><ChevronRight /><Folder /><button :title="workspaceDescription(node)" :disabled="busy || !usable(node)" @click.prevent.stop="emit('workspace', node)">{{ node.label }}</button><span v-if="!node.available && node.path" class="tree-warning" title="目录当前不可用">不可用</span><span v-else class="tree-count">{{ node.conversations.length }}</span></summary>
        <div v-if="node.connections.filter(item => !item.duplicateOf).length > 1" class="workspace-connections" :title="workspaceDescription(node)"><span v-for="connection in node.connections.filter(item => !item.duplicateOf)" :key="connection.id" :class="{ unavailable: !connectionReady(connection) }">{{ connection.name }}<small>{{ sourceLabel(connection) }}</small></span></div>
        <button v-for="item in node.conversations" :key="item.id" class="conversation-item tree-conversation" :class="{ selected: selectedConversation === item.id }" :aria-current="selectedConversation === item.id ? 'page' : undefined" :disabled="busy" @click="emit('conversation', item.id)"><MessageSquare /><span class="conversation-name">{{ item.title || '新对话' }}</span><span v-if="item.activeRun" class="run-dot" title="任务进行中"></span><span v-else-if="!connectionReady(conversationConnection(node, item.connectionId))" class="tree-warning" title="原连接不可用；历史记录保留">离线</span></button>
        <span v-if="!node.conversations.length" class="workspace-empty">暂无对话</span>
      </details>
      <span v-if="!group.workspaces.length" class="workspace-empty group-empty">{{ group.connections.length ? '没有匹配的工作区' : '尚未添加连接' }}</span>
    </details>
  </nav>
</template>
