import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const workflow = readFileSync(join(root, '.github/workflows/check.yml'), 'utf8');
const packageJson = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));

test('CI keeps Rust host checks in an independent Windows job', () => {
  assert.match(workflow, /^\s{2}desktop-checks:\s*$/m);
  assert.match(workflow, /npm run build:worker/);
  assert.match(workflow, /npm run build:modules/);
  assert.match(workflow, /npm run desktop:check/);
  assert.match(workflow, /npm run desktop:test/);
});

test('CI runs the complete media manager suite with its locked environment', () => {
  assert.match(workflow, /^\s{2}media-manager-checks:\s*$/m);
  assert.match(workflow, /plugins\/media-manager\/packaging\/requirements-lock\.txt/);
  assert.match(workflow, /plugins\/media-manager\/scripts\/test\.ps1/);
  assert.equal(
    packageJson.scripts['test:media-manager'],
    'powershell -NoProfile -ExecutionPolicy Bypass -File plugins/media-manager/scripts/test.ps1',
  );
  assert.equal(
    packageJson.scripts['check:all'],
    'npm run check && npm run test:media-manager',
  );
});
