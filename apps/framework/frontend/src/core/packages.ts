export interface PackageVersion {
  version: string;
  digest: string;
  source: 'bundled' | 'imported';
  compatible: boolean;
  webApp?: boolean;
  error: string | null;
  sourceCommit?: string | null;
  sourceRepository?: string | null;
  sourceDirty?: boolean | null;
}
export interface InstalledModule {
  id: string;
  label: string;
  enabled: boolean;
  selectedVersion: string | null;
  selectedDigest: string | null;
  state: string;
  error: string | null;
  lastError?: string | null;
  uiRevision?: number;
  versions: PackageVersion[];
}
export interface PackageImport {
  id: string;
  fileName: string;
  storedPath: string;
  status: 'installed' | 'duplicate' | 'rejected';
  error: string | null;
  moduleId: string | null;
  version: string | null;
  digest: string | null;
  createdAt: number;
}
export interface PackageCatalog { hostApi: 1 | 2; modules: InstalledModule[]; imports: PackageImport[] }
export interface PackageInstance {
  moduleId: string;
  version: string;
  digest: string;
  generation: string;
  url: string;
  token: string;
  webApp?: boolean;
}
export const EMPTY_CATALOG: PackageCatalog = { hostApi: 1, modules: [], imports: [] };
export function selectedPackage(module: InstalledModule): PackageVersion | undefined {
  return module.versions.find((version) => version.version === module.selectedVersion && version.digest === module.selectedDigest);
}
export function canOpenPackage(module: InstalledModule): boolean {
  return module.enabled && selectedPackage(module)?.compatible === true;
}
export function packageKey(module: InstalledModule): string {
  return `${module.id}:${module.selectedVersion ?? ''}:${module.selectedDigest ?? ''}:${module.uiRevision ?? 0}`;
}
export function packageSandbox(instance: Pick<PackageInstance, 'webApp'> | null): string {
  const base = 'allow-scripts allow-same-origin allow-downloads';
  return instance?.webApp === true
    ? `${base} allow-forms allow-modals allow-popups allow-popups-to-escape-sandbox`
    : base;
}
export function canSelectPackage(module: InstalledModule, version: PackageVersion): boolean {
  return version.compatible && !(module.selectedVersion === version.version && module.selectedDigest === version.digest);
}
export function safeRepositoryLink(value: string | null | undefined): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password ? url.href : undefined;
  } catch { return undefined; }
}
