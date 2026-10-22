import assert from 'node:assert/strict';
import { existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import test from 'node:test';
import { assertBundleDirectory, assertOutputDirectory, assertPublicModule, parseModulePackArgs, readDistribution, sha256, validateDistribution, writeImmutableFiles } from '../../scripts/distribution.mjs';
import { validateCachedPackage } from '../../scripts/module-pack.mjs';

const config = () => ({ schemaVersion: 1, bundledModules: ['tower-assistant'], publicModules: ['tower-assistant'] });
const serialize = (value) => JSON.stringify(value, null, 2) + '\n';

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'opendesk-distribution-'));
  t.after(() => {
    assert.equal(dirname(resolve(root)), resolve(tmpdir()));
    rmSync(root, { recursive: true, force: true });
  });
  mkdirSync(join(root, 'config'));
  writeFileSync(join(root, 'config/distribution.json'), serialize(config()));
  return root;
}

function bundle(t) {
  const root = fixture(t);
  const directory = join(root, 'bundled');
  mkdirSync(directory);
  const bytes = Buffer.from('synthetic tower package bytes');
  const entry = { id: 'tower-assistant', version: '0.1.7', file: 'tower-assistant-0.1.7.ndpkg', sha256: sha256(bytes), defaultEnabled: true };
  const catalog = { schemaVersion: 1, hostVersion: '0.12.0', modules: [entry] };
  writeFileSync(join(directory, entry.file), bytes);
  const save = () => writeFileSync(join(directory, 'app-manifest.json'), serialize(catalog));
  save();
  return { root, directory, bytes, entry, catalog, save };
}

test('distribution accepts explicit known public modules and an empty framework', (t) => {
  assert.deepEqual(validateDistribution(config()), config());
  assert.deepEqual(validateDistribution({ schemaVersion: 1, bundledModules: [], publicModules: [] }), { schemaVersion: 1, bundledModules: [], publicModules: [] });
  assert.deepEqual(readDistribution(fixture(t)), config());
  const input = config();
  const validated = validateDistribution(input);
  input.publicModules.push('not-owned');
  assert.deepEqual(validated.publicModules, ['tower-assistant']);
});

test('distribution rejects unknown schemas, extra fields, duplicate lists and unregistered imports', () => {
  for (const invalid of [null, {}, { ...config(), schemaVersion: 2 }, { ...config(), extra: true },
    { ...config(), bundledModules: 'tower-assistant' }, { ...config(), publicModules: [null] },
    { ...config(), bundledModules: ['tower-assistant', 'tower-assistant'] },
    { ...config(), publicModules: ['tower-assistant', 'tower-assistant'] },
    { ...config(), publicModules: ['third-party-import'] }, { ...config(), publicModules: ['__proto__'] }]) {
    assert.throws(() => validateDistribution(invalid));
  }
});

test('test fixtures cannot enter either distribution list, even if explicitly listed', () => {
  assert.throws(() => validateDistribution({ ...config(), publicModules: ['tower-assistant', 'test-lab'] }), /Test fixture/);
  assert.throws(() => validateDistribution({ ...config(), bundledModules: ['test-lab'] }), /Test fixture/);
  assert.throws(() => validateDistribution({ ...config(), publicModules: [] }), /subset/);
});

test('single-module CLI only accepts an allowlisted public module', () => {
  const distribution = validateDistribution(config());
  assert.equal(assertPublicModule(distribution, 'tower-assistant').folder, 'tower_assistant');
  assert.deepEqual(parseModulePackArgs(['module', 'tower-assistant'], distribution), { mode: 'module', moduleId: 'tower-assistant' });
  assert.deepEqual(parseModulePackArgs(['fixtures'], distribution), { mode: 'fixtures' });
  assert.throws(() => parseModulePackArgs(['module', 'test-lab'], distribution), /allowlist/);
  assert.throws(() => parseModulePackArgs(['module', '../private'], distribution), /allowlist/);
  assert.throws(() => parseModulePackArgs(['module'], distribution), /Usage/);
  assert.throws(() => parseModulePackArgs(['module', 'tower-assistant', '--force'], distribution), /Usage/);
  assert.throws(() => parseModulePackArgs(['bundled', 'test-lab'], distribution), /Usage/);
});

test('bundled directory matches the exact allowlist and verifies archive bytes', (t) => {
  const built = bundle(t);
  assert.deepEqual(assertBundleDirectory(built.root, built.directory, '0.12.0'), built.catalog);
  writeFileSync(join(built.directory, built.entry.file), 'changed bytes');
  assert.throws(() => assertBundleDirectory(built.root, built.directory, '0.12.0'), /digest mismatch/);
});

test('bundled preflight refuses old test-lab and unknown contents without deleting them', (t) => {
  const built = bundle(t);
  const foreign = join(built.directory, 'test-lab-1.0.6.ndpkg');
  writeFileSync(foreign, 'test-only package');
  assert.throws(() => assertOutputDirectory(built.directory, ['app-manifest.json', built.entry.file]), /Move it out/);
  assert.throws(() => assertBundleDirectory(built.root, built.directory, '0.12.0'), /Unexpected artifact/);
  assert.equal(readFileSync(foreign, 'utf8'), 'test-only package');
  assert.equal(readFileSync(join(built.directory, built.entry.file), 'utf8'), built.bytes.toString());
});

test('bundled catalog rejects missing, repeated, private, traversing and wrong-version entries', (t) => {
  const built = bundle(t);
  assert.throws(() => assertBundleDirectory(built.root, built.directory, '0.13.0'), /host version/);
  for (const entries of [[], [built.entry, built.entry], [{ ...built.entry, id: 'test-lab' }], [{ ...built.entry, id: 'third-party-import' }],
    [{ ...built.entry, file: '../tower-assistant-0.1.7.ndpkg' }], [{ ...built.entry, purpose: 'baseline' }], [{ ...built.entry, sha256: 'bad' }]]) {
    built.catalog.modules = entries;
    built.save();
    assert.throws(() => assertBundleDirectory(built.root, built.directory, '0.12.0'));
  }
});

test('immutable outputs reuse identical bytes but never replace an existing package or sidecar', (t) => {
  const root = fixture(t);
  const output = join(root, 'module');
  writeImmutableFiles(output, { 'tower-assistant-0.1.7.ndpkg': 'original bytes', 'manifest.json': 'original source identity' });
  writeImmutableFiles(output, { 'tower-assistant-0.1.7.ndpkg': 'original bytes', 'manifest.json': 'original source identity' });
  assert.throws(() => writeImmutableFiles(output, { 'tower-assistant-0.1.7.ndpkg': 'different bytes' }), /Immutable artifact conflict/);
  assert.throws(() => writeImmutableFiles(output, { 'new.ndpkg': 'new file', 'manifest.json': 'rewritten provenance' }), /Immutable artifact conflict/);
  assert.equal(existsSync(join(output, 'new.ndpkg')), false, 'preflight rejects the whole set before any writes');
  assert.equal(readFileSync(join(output, 'tower-assistant-0.1.7.ndpkg'), 'utf8'), 'original bytes');
  assert.equal(readFileSync(join(output, 'manifest.json'), 'utf8'), 'original source identity');
  assert.throws(() => writeImmutableFiles(output, { '../outside.ndpkg': 'bad' }), /basenames/);
  assert.throws(() => writeImmutableFiles(output, { 'C:escape.ndpkg': 'bad' }), /basenames/);
});

test('linked output directories are refused rather than followed', (t) => {
  const root = fixture(t);
  const target = join(root, 'target');
  const linked = join(root, 'linked');
  mkdirSync(target);
  try { symlinkSync(target, linked, process.platform === 'win32' ? 'junction' : 'dir'); }
  catch (error) { if (error.code === 'EPERM') { t.skip('This environment cannot create test links.'); return; } throw error; }
  assert.equal(lstatSync(linked).isSymbolicLink(), true);
  assert.throws(() => assertOutputDirectory(linked, []), /Linked/);
  assert.throws(() => writeImmutableFiles(join(linked, 'nested'), { 'pkg.ndpkg': 'bad' }), /Linked/);
  assert.equal(existsSync(join(target, 'nested')), false);
});

test('matching cached inputs preserve original provenance, and corruption or identity mismatch fails', () => {
  const manifest = { schemaVersion: 1, id: 'tower-assistant', label: 'Tower fixture', version: '0.1.6', hostApi: 1, target: 'windows-x64', frontend: 'frontend/index.html', worker: null, methods: [], dataSchema: 1,
    source: { commit: 'a'.repeat(40), dirty: false, repository: 'https://github.com/example/repo' } };
  const bytes = Buffer.from('immutable cached fixture');
  const saved = { fingerprint: 'fingerprint', sha256: sha256(bytes), manifest };
  const requested = { ...manifest, source: { commit: 'b'.repeat(40), dirty: true } };
  assert.deepEqual(validateCachedPackage('fingerprint', requested, saved, bytes), { sha256: saved.sha256, manifest });
  assert.throws(() => validateCachedPackage('changed', requested, saved, bytes), /integrity/);
  assert.throws(() => validateCachedPackage('fingerprint', requested, saved, Buffer.from('tampered')), /integrity/);
  assert.throws(() => validateCachedPackage('fingerprint', { ...requested, version: '0.1.7' }, saved, bytes), /identity/);
});
