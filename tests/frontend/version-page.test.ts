import { createSSRApp, h } from 'vue';
import { renderToString } from 'vue/server-renderer';
import { describe, expect, it } from 'vitest';
import VersionPage from '../../apps/framework/frontend/src/components/VersionPage.vue';
import { filterReleaseHistory, readBuildVersion, type BuildVersion, type ReleaseEntry } from '../../apps/framework/frontend/src/core/release';

function exampleVersion(): BuildVersion {
  return {
    appVersion: '0.2.0',
    git: { commit: '1234567890abcdef1234567890abcdef12345678', shortCommit: '1234567', dirty: false },
    features: [
      { id: 'sample.read', name: '读取工具', module: 'sample', version: '1.1.0', updatedIn: '0.2.0' },
      { id: 'sample.export', name: '导出工具', module: 'sample', version: '1.0.0', updatedIn: '0.1.0' },
    ],
    releases: [
      { version: '0.1.0', date: '2026-10-17', summary: '初始能力', changes: [
        { featureId: 'sample.read', version: '1.0.0', type: 'added', description: '读取现有文件。' },
        { featureId: 'sample.export', version: '1.0.0', type: 'added', description: '导出比较结果。' },
      ] },
      { version: '0.2.0', date: '2026-10-18', summary: '改善读取反馈', changes: [
        { featureId: 'sample.read', version: '1.1.0', type: 'changed', description: '显示读取失败的原因。' },
      ] },
    ],
  };
}

const render = (version?: BuildVersion, hostVersion?: string) => renderToString(createSSRApp({ render: () => h(VersionPage, { version, hostVersion }) }));

describe('version page build identity', () => {
  it('renders the build identity, independent feature versions and newest history first without a native host', async () => {
    const html = await render(exampleVersion());
    expect(html).toContain('v0.2.0');
    expect(html).toContain('1234567890abcdef1234567890abcdef12345678');
    expect(html).toContain('sample.read');
    expect(html).toContain('v1.1.0');
    expect(html).toContain('应用 v0.1.0');
    expect(html).toContain('显示读取失败的原因。');
    expect(html).toContain('aria-label="筛选功能迭代记录"');
    expect(html.indexOf('改善读取反馈')).toBeLessThan(html.indexOf('初始能力'));
    expect(html).not.toContain('桌面能力不可用');
  });

  it('keeps missing injected metadata unknown instead of inventing a version or clean state', async () => {
    expect(readBuildVersion()).toEqual({ appVersion: 'unknown', git: { commit: null, shortCommit: null, dirty: null }, features: [], releases: [] });
    const html = await render();
    expect(html).toContain('版本未知');
    expect(html).toContain('Git 提交未知');
    expect(html).toContain('工作区状态未知');
    expect(html).toContain('此构建未附带功能版本记录');
    expect(html).not.toContain('vunknown');
    expect(html).not.toContain('工作区干净');
  });

  it('makes uncommitted builds explicit even when a commit is known', async () => {
    const version = exampleVersion(); version.git.dirty = true;
    const html = await render(version);
    expect(html).toContain('未提交构建');
    expect(html).toContain('提交号不能完整代表当前代码');
    expect(html).not.toContain('工作区干净');
  });

  it('does not confuse a clean worktree with a formal release', async () => {
    const html = await render(exampleVersion());
    expect(html).toContain('工作区干净');
    expect(html).toContain('工作区干净不代表已发布正式版本');
  });

  it('reports a mismatched native host version and leaves matching versions unflagged', async () => {
    const mismatch = await render(exampleVersion(), '0.1.0');
    expect(mismatch).toContain('role="alert"');
    expect(mismatch).toContain('前端与桌面宿主版本不匹配');
    expect(mismatch).toContain('前端 v0.2.0，桌面宿主 v0.1.0');
    expect(await render(exampleVersion(), '0.2.0')).not.toContain('版本不匹配');
  });
});

describe('feature iteration history', () => {
  it('filters by stable ID while preserving application release context and original data', () => {
    const version = exampleVersion();
    const before = JSON.stringify(version.releases);
    const result = filterReleaseHistory(version.releases, 'sample.export');
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({ version: '0.1.0', date: '2026-10-17', summary: '初始能力' });
    expect(result[0].changes.map((change) => change.featureId)).toEqual(['sample.export']);
    expect(filterReleaseHistory(version.releases, 'missing.feature')).toEqual([]);
    expect(JSON.stringify(version.releases)).toBe(before);
  });

  it('orders SemVer numerically and puts a stable release above its prereleases', () => {
    const releases = ['0.2.0', '0.10.0-beta.2', '0.10.0-beta.11', '0.10.0', '0.10.0-alpha'].map((version): ReleaseEntry => ({ version, date: '2026-10-18', summary: version, changes: [] }));
    expect(filterReleaseHistory(releases).map((release) => release.version)).toEqual(['0.10.0', '0.10.0-beta.11', '0.10.0-beta.2', '0.10.0-alpha', '0.2.0']);
  });
});
