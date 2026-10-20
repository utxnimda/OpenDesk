import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { execFileSync } from 'node:child_process';
import { test } from 'node:test';
import { readGitIdentity } from '../../scripts/build-version.mjs';

test('Git identity distinguishes source archives, committed trees and untracked changes', () => {
  const root = mkdtempSync(join(tmpdir(), 'nimdadesk-identity-'));
  const run = (...args) => execFileSync('git', args, {
    cwd: root, encoding: 'utf8', windowsHide: true,
    env: { ...process.env, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: join(root, 'empty-gitconfig') },
  }).trim();
  try {
    assert.deepEqual(readGitIdentity(root), { commit: null, shortCommit: null, dirty: null });
    run('init', '--quiet');
    writeFileSync(join(root, 'tracked.txt'), 'source\n');
    run('add', 'tracked.txt');
    run('-c', 'user.name=Version Test', '-c', 'user.email=version-test@example.invalid', '-c', 'commit.gpgsign=false', 'commit', '--quiet', '-m', 'fixture');
    const commit = run('rev-parse', 'HEAD');
    assert.deepEqual(readGitIdentity(root), { commit, shortCommit: commit.slice(0, 12), dirty: false });
    if (process.platform === 'win32') {
      assert.deepEqual(readGitIdentity(root.toLowerCase()), { commit, shortCommit: commit.slice(0, 12), dirty: false });
      assert.deepEqual(readGitIdentity(root.toUpperCase()), { commit, shortCommit: commit.slice(0, 12), dirty: false });
    }
    const nestedArchive = join(root, 'source-archive');
    mkdirSync(nestedArchive);
    assert.deepEqual(readGitIdentity(nestedArchive), { commit: null, shortCommit: null, dirty: null });
    writeFileSync(join(root, 'new.txt'), 'not committed\n');
    assert.equal(readGitIdentity(root).dirty, true);
    rmSync(join(root, 'new.txt'));
    writeFileSync(join(root, 'tracked.txt'), 'modified\n');
    assert.equal(readGitIdentity(root).dirty, true);
    assert.equal(readGitIdentity(root).commit, commit);
  } finally {
    assert.equal(dirname(resolve(root)), resolve(tmpdir()));
    rmSync(root, { recursive: true, force: true });
  }
});
