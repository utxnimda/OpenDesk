export interface GitIdentity {
  commit: string | null;
  shortCommit: string | null;
  dirty: boolean | null;
}
export function readGitIdentity(root: string): GitIdentity;
export function buildVersion(root: string): {
  appVersion: string;
  git: GitIdentity;
  features: Array<{ id: string; name: string; module: string; version: string; updatedIn: string }>;
  releases: Array<{
    version: string; date: string; summary: string;
    changes: Array<{ featureId: string; version: string; type: 'added' | 'changed' | 'fixed' | 'breaking'; description: string }>;
  }>;
};
