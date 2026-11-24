export type LaunchMode = 'managed' | 'external';
export type DshSource = 'auto' | 'custom';
export type SourceLaunchMode = 'built' | 'dev';
export type HomeMode = 'isolated' | 'system' | 'custom';
export type RuntimeState = 'idle' | 'starting' | 'running' | 'stopping' | 'error' | 'exited';
export type InstallState = 'idle' | 'running' | 'succeeded' | 'failed' | 'cancelled';

export interface DshConfigDto {
  schemaVersion: number;
  launchMode: LaunchMode;
  dshSource: DshSource;
  sourceLaunchMode: SourceLaunchMode;
  customDshPath: string;
  nodePath: string;
  homeMode: HomeMode;
  customHomeDir: string;
  workspaceDir: string;
  profile: string;
  autoStart: boolean;
  externalUrl: string;
  externalUrlRedacted?: string;
  hasExternalToken?: boolean;
}

export interface NodeProbeDto {
  available: boolean;
  path: string;
  version: string;
  meetsMinimum: boolean;
  error: string | null;
}

export interface PnpmProbeDto {
  available: boolean;
  kind: string;
  executable: string;
  script: string;
  display: string;
  error: string | null;
}

export interface SourceRepoDto {
  isRepo: boolean;
  repoRoot: string;
  version: string;
  enginesNode: string;
  depsInstalled: boolean;
  artifactsBuilt: boolean;
  hasDevWeb: boolean;
  gitBranch: string;
  gitCommit: string;
  gitDirty: boolean;
}

export interface DshEntryProbeDto {
  available: boolean;
  kind: string;
  source: string;
  executable: string;
  script: string;
  version: string;
  error: string | null;
}

export interface ProbeDto {
  node: NodeProbeDto;
  npmPath: string;
  pnpm?: PnpmProbeDto;
  dsh: DshEntryProbeDto;
  sourceRepo?: SourceRepoDto | null;
  managedCliDir: string;
  managedCliInstalled: boolean;
  systemDshHome: string;
  minNodeVersion: string;
}

export interface RuntimeSnapshotDto {
  state: RuntimeState;
  mode: LaunchMode;
  error: string | null;
  pid: number | null;
  upstreamPort: number | null;
  proxyUrl: string;
  effectiveDshHome: string;
  effectiveWorkspace: string;
  startedAt: number | null;
  launchKind?: string;
  install: {
    state: InstallState;
    error: string | null;
  };
  sourceTask?: {
    kind: string;
    state: InstallState;
    error: string | null;
  };
}

export interface McpToolItemDto {
  name: string;
  moduleId: string;
  version: string;
  digest: string;
  generation: string;
  title: string;
  description: string;
  readOnly: true;
  contractVersion: 1;
}

export interface McpBridgeDto {
  active: boolean;
  url: string;
  toolCount: number;
  tools: McpToolItemDto[];
  token?: string;
}

export interface DshStatusDto {
  version: string;
  dataDir: string;
  config: DshConfigDto;
  configError: string | null;
  probe: ProbeDto;
  runtime: RuntimeSnapshotDto;
  mcp?: McpBridgeDto;
}

export interface LogEntryDto {
  ts: number;
  stream: string;
  line: string;
}

export function buildMcpSnippet(url: string, token: string): string {
  return JSON.stringify(
    {
      mcpServers: {
        'opendesk-readonly-tools': {
          type: 'http',
          url,
          headers: {
            Authorization: `Bearer ${token}`,
          },
        },
      },
    },
    null,
    2,
  );
}

export const DSH_IFRAME_SANDBOX =
  'allow-scripts allow-same-origin allow-forms allow-modals allow-downloads allow-popups allow-popups-to-escape-sandbox';

export function validateLoopbackProxyUrl(raw: string, wrapperOrigin: string): string {
  if (!raw) return '';
  try {
    const parsed = new URL(raw);
    if (
      parsed.protocol !== 'http:' ||
      parsed.hostname !== '127.0.0.1' ||
      !parsed.port ||
      parsed.username ||
      parsed.password ||
      parsed.origin === wrapperOrigin
    ) {
      return '';
    }
    return parsed.href;
  } catch {
    return '';
  }
}

export function stateLabel(state: RuntimeState): string {
  switch (state) {
    case 'running':
      return '运行中';
    case 'starting':
      return '启动中';
    case 'stopping':
      return '停止中';
    case 'error':
      return '异常';
    case 'exited':
      return '已退出';
    default:
      return '未启动';
  }
}

export function dshSourceLabel(source: string): string {
  switch (source) {
    case 'managed-cli':
      return '插件隔离目录 (@deepseek-ai/dsh)';
    case 'path':
      return '系统 PATH (dsh)';
    case 'custom-repo':
      return '自定义源码仓库';
    case 'custom-file':
      return '自定义可执行文件';
    default:
      return '未检测到';
  }
}

export function formatLogTime(ts: number): string {
  const date = new Date(ts);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}
