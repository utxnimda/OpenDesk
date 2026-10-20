export interface FeatureVersion {
  id: string;
  name: string;
  module: string;
  version: string;
  updatedIn: string;
}

export type ChangeType = 'added' | 'changed' | 'fixed' | 'breaking';
export interface ReleaseChange {
  featureId: string;
  version: string;
  type: ChangeType;
  description: string;
}

export interface ReleaseEntry {
  version: string;
  date: string;
  summary: string;
  changes: ReleaseChange[];
}

export interface BuildVersion {
  appVersion: string;
  git: { commit: string | null; shortCommit: string | null; dirty: boolean | null };
  features: FeatureVersion[];
  releases: ReleaseEntry[];
}

// Supplied by the build. This module has no runtime Git or filesystem access.
declare const __OPENDESK_VERSION__: BuildVersion;

export function readBuildVersion(): BuildVersion {
  if (typeof __OPENDESK_VERSION__ !== 'undefined') return __OPENDESK_VERSION__;
  return { appVersion: 'unknown', git: { commit: null, shortCommit: null, dirty: null }, features: [], releases: [] };
}

export const buildVersion = readBuildVersion();
export const CHANGE_LABELS: Record<ChangeType, string> = { added: '新增', changed: '改进', fixed: '修复', breaking: '不兼容变更' };

/** Compare SemVer precedence, including prereleases; build metadata does not change order. */
function compareVersions(left: string, right: string): number {
  const parse = (value: string) => {
    const [base, prerelease] = value.split('+')[0].split(/-(.*)/s);
    return { base: base.split('.').map(Number), prerelease: prerelease?.split('.') };
  };
  const a = parse(left); const b = parse(right);
  for (let index = 0; index < 3; index++) {
    const difference = (a.base[index] ?? 0) - (b.base[index] ?? 0);
    if (difference) return difference;
  }
  if (!a.prerelease || !b.prerelease) return a.prerelease ? -1 : b.prerelease ? 1 : 0;
  for (let index = 0; index < Math.max(a.prerelease.length, b.prerelease.length); index++) {
    const x = a.prerelease[index]; const y = b.prerelease[index];
    if (x === y) continue;
    if (x === undefined || y === undefined) return x === undefined ? -1 : 1;
    const xNumeric = /^\d+$/.test(x); const yNumeric = /^\d+$/.test(y);
    if (xNumeric && yNumeric) return Number(x) - Number(y);
    if (xNumeric !== yNumeric) return xNumeric ? -1 : 1;
    return x < y ? -1 : 1;
  }
  return 0;
}

/** Keep release context while showing only the selected feature's changes. */
export function filterReleaseHistory(releases: readonly ReleaseEntry[], featureId = ''): ReleaseEntry[] {
  return releases
    .map((release) => ({ ...release, changes: release.changes.filter((change) => !featureId || change.featureId === featureId) }))
    .filter((release) => !featureId || release.changes.length > 0)
    .sort((left, right) => compareVersions(right.version, left.version));
}
