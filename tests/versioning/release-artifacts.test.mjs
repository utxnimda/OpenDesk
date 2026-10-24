import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { inventory, assertInventory, assertPortableDataAbsent } from '../../scripts/release-artifacts.mjs';

function fixture(action) {
  const parent = realpathSync(tmpdir());
  const directory = mkdtempSync(join(parent, 'opendesk-release-test-'));
  try { action(directory); }
  finally {
    const target = realpathSync(directory);
    assert.equal(dirname(target), parent);
    rmSync(target, { recursive: true });
  }
}

test('release receipt accepts byte-identical artifacts and rejects same-size tampering', () => fixture((directory) => {
  const path = join(directory, 'OpenDesk.exe');
  writeFileSync(path, 'first');
  const receipt = inventory(directory);
  assert.doesNotThrow(() => assertInventory(inventory(directory), receipt));
  writeFileSync(path, 'other');
  assert.throws(() => assertInventory(inventory(directory), receipt), /content changed/);
}));

test('release receipt rejects an extra imported package in the portable tree', () => fixture((directory) => {
  writeFileSync(join(directory, 'OpenDesk.exe'), 'app');
  const receipt = inventory(directory);
  writeFileSync(join(directory, 'user-import.ndpkg'), 'external');
  assert.throws(() => assertInventory(inventory(directory), receipt), /inventory changed/);
}));

test('release receipt rejects a missing runtime file', () => fixture((directory) => {
  writeFileSync(join(directory, 'OpenDesk.exe'), 'app');
  const runtime = join(directory, 'opendesk-worker.exe');
  writeFileSync(runtime, 'runtime');
  const receipt = inventory(directory);
  rmSync(runtime);
  assert.throws(() => assertInventory(inventory(directory), receipt), /inventory changed/);
}));

test('release staging refuses a local portable marker or user data without removing it', () => fixture((directory) => {
  assert.doesNotThrow(() => assertPortableDataAbsent(directory));
  for (const name of ['opendesk.portable.json', 'runtime', 'data']) {
    const path = join(directory, name);
    writeFileSync(path, 'local-only');
    assert.throws(() => assertPortableDataAbsent(directory), /cannot be published/);
    assert.equal(inventory(directory)[0].file, name);
    rmSync(path);
  }
}));
