import { describe, expect, it } from 'vitest';
import { createSSRApp } from 'vue';
import { renderToString } from 'vue/server-renderer';
import PackagesPage from '../../apps/framework/frontend/src/components/PackagesPage.vue';
import { createHostAdapter } from '../../apps/framework/frontend/src/core/host';
import { canOpenPackage, canSelectPackage, packageKey, safeRepositoryLink, type InstalledModule, type PackageVersion } from '../../apps/framework/frontend/src/core/packages';

const oldVersion: PackageVersion = { version: '1.0.0', digest: 'old-digest', source: 'bundled', compatible: true, error: null };
const newVersion: PackageVersion = { version: '1.1.0', digest: 'new-digest', source: 'imported', compatible: true, error: null };
const module: InstalledModule = { id: 'test-lab', label: '测试', enabled: true, selectedVersion: '1.1.0', selectedDigest: 'new-digest', state: 'ready', error: null, versions: [oldVersion, newVersion] };
describe('package selection contract', () => {
  it('shows the retained failed-load reason alongside the restored current version', async () => {
    const restored = { ...module, selectedVersion: oldVersion.version, selectedDigest: oldVersion.digest, error: null, lastError: 'v1.1.0 UI identity mismatch; restored v1.0.0' };
    const html = await renderToString(createSSRApp(PackagesPage, {
      host: createHostAdapter(), loading: false,
      catalog: { hostApi: 1, modules: [restored], imports: [] },
    }));
    expect(html).toContain('最近一次加载失败');
    expect(html).toContain(restored.lastError);
    expect(html).toContain('<strong>v1.0.0</strong>');
    expect(html).toContain('当前生效版本以上方');
  });
  it('only exposes credential-free HTTPS repository links from package metadata', () => {
    expect(safeRepositoryLink('https://github.com/example/project')).toBe('https://github.com/example/project');
    expect(safeRepositoryLink('javascript:alert(1)')).toBeUndefined();
    expect(safeRepositoryLink('https://user:password@example.com/project')).toBeUndefined();
    expect(safeRepositoryLink('file:///C:/private')).toBeUndefined();
  });
  it('does not activate disabled, missing or incompatible packages', () => {
    expect(canOpenPackage(module)).toBe(true);
    expect(canOpenPackage({ ...module, enabled: false })).toBe(false);
    expect(canOpenPackage({ ...module, selectedDigest: 'missing' })).toBe(false);
    expect(canOpenPackage({ ...module, versions: [{ ...newVersion, compatible: false }] })).toBe(false);
  });
  it('allows selecting an older compatible package without treating a version string as identity', () => {
    expect(canSelectPackage(module, oldVersion)).toBe(true);
    expect(canSelectPackage(module, newVersion)).toBe(false);
    expect(canSelectPackage(module, { ...oldVersion, compatible: false })).toBe(false);
    expect(packageKey(module)).not.toBe(packageKey({ ...module, selectedDigest: 'different-content' }));
  });
  it('keeps A instance identity unchanged when only B selection changes', () => {
    const a = { ...module, id: 'module-a' }; const b = { ...module, id: 'module-b' };
    const originalA = packageKey(a); const originalB = packageKey(b);
    const switchedB = { ...b, selectedVersion: oldVersion.version, selectedDigest: oldVersion.digest };
    expect(packageKey(a)).toBe(originalA); expect(packageKey(switchedB)).not.toBe(originalB);
  });
});
