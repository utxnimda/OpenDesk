import { spawnSync } from 'node:child_process';
import { statSync } from 'node:fs';
import { readVersionManifest, versionSummary } from './versioning.mjs';

/** Build-time provenance only. No Git executable or repository access enters the application. */
export function readGitIdentity(root) {
  const unknown = { commit: null, shortCommit: null, dirty: null };
  const env = { ...process.env };
  for (const key of ['GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR']) delete env[key];
  const run = (args) => spawnSync('git', args, {
    cwd: root, env, encoding: 'utf8', windowsHide: true, timeout: 5000,
    maxBuffer: 4 * 1024 * 1024,
  });
  const repository = run(['rev-parse', '--show-toplevel']);
  if (repository.status !== 0) return unknown;
  try {
    // Windows can spell one directory with different case or 8.3 aliases.
    // Use its actual filesystem identity; Number can truncate NTFS file IDs.
    const actual = statSync(repository.stdout.trim(), { bigint: true });
    const expected = statSync(root, { bigint: true });
    if (!actual.isDirectory() || !expected.isDirectory() || actual.ino === 0n
      || actual.dev !== expected.dev || actual.ino !== expected.ino) return unknown;
  } catch { return unknown; }
  const head = run(['rev-parse', '--verify', 'HEAD']);
  const commit = head.status === 0 ? head.stdout.trim() : '';
  if (!/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(commit)) {
    return unknown;
  }
  const status = run(['status', '--porcelain', '--untracked-files=normal']);
  return {
    commit,
    shortCommit: commit.slice(0, 12),
    dirty: status.status === 0 ? Boolean(status.stdout.trim()) : null,
  };
}

export function buildVersion(root) {
  const manifest = readVersionManifest(root);
  const summary = versionSummary(manifest);
  return {
    appVersion: summary.appVersion,
    git: readGitIdentity(root),
    features: summary.features,
    releases: manifest.releases,
  };
}
