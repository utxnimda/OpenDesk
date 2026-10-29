export type ConnectionKind = 'openai-responses' | 'openai-chat' | 'codex' | 'cursor';

export interface Connection {
  id: string;
  name: string;
  kind: ConnectionKind;
  baseUrl?: string;
  executable?: string;
  workspace?: string;
  hasKey: boolean;
  origin?: 'manual' | 'detected';
  duplicateOf?: string;
  health?: { status: 'ready' | 'missing' | 'unconfigured'; message: string; resolvedExecutable?: string; autoRepaired?: boolean };
}

export interface ConnectionInput {
  id?: string;
  name: string;
  kind: ConnectionKind;
  baseUrl?: string;
  apiKey?: string;
  executable?: string;
  workspace?: string;
}

export interface ModelParameter { id: string; label: string; description?: string; options: { value: string; label: string; description?: string }[]; defaultValue?: string }
export interface ModelOption { id: string; label?: string; providerModelId?: string; parameters?: ModelParameter[] }
export interface WorkspaceOption { path: string; label: string; source: string; available: boolean }
export interface CatalogWorkspace extends WorkspaceOption { connectionId: string }
export interface AgentCatalog { connections: Connection[]; workspaces: CatalogWorkspace[]; warnings: { connectionId: string; message: string }[] }
export interface WorkspaceList { workspaces: WorkspaceOption[]; warning?: string }
export interface Message { id: string; role: string; text: string; createdAt?: string }
export interface ConversationSummary {
  id: string;
  title: string;
  connectionId: string;
  model: string;
  workspace?: string;
  modelOptions?: Record<string, string>;
  updatedAt?: string;
  activeRun?: string | null;
}
export interface Conversation extends ConversationSummary { messages: Message[] }
export interface AppState {
  connections: Connection[];
  conversations: ConversationSummary[];
  shuttingDown: boolean;
  layout?: { sidebarWidth: number };
}

export interface Approval { requestId: string; title?: string; detail?: unknown }
export interface Run {
  id: string;
  status: string;
  conversationId: string;
  error?: string;
  approval?: Approval | null;
}
export interface RunEvent {
  sequence: number;
  type: string;
  text?: string;
  status?: string;
  requestId?: string;
  title?: string;
  detail?: unknown;
  name?: string;
  arguments?: Record<string, unknown>;
  result?: unknown;
  message?: string;
}
export interface RunPage { run: Run; events: RunEvent[]; nextSequence: number }
