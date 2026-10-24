import assert from 'node:assert/strict';
import { existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import test from 'node:test';
import { MODULE_DEFINITIONS, assertBundleDirectory, assertOutputDirectory, assertPublicModule, moduleSourceDirectory, parseModulePackArgs, readDistribution, sha256, validateDistribution, writeImmutableFiles } from '../../scripts/distribution.mjs';
import { externalBuildArguments, externalPython, validateCachedPackage, validateManifest } from '../../scripts/module-pack.mjs';
import { parseBuildArgs } from '../../plugins/media-manager/integrations/opendesk/build.mjs';

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

test('media manager is an explicit standalone source module and does not enter the App bundle', () => {
  const current = readDistribution(resolve(import.meta.dirname, '../..'));
  assert.deepEqual(current.bundledModules, ['tower-assistant']);
  assert.ok(current.publicModules.includes('nimda-workspace'));
  assert.deepEqual(parseModulePackArgs(['module', 'nimda-workspace'], current), { mode: 'module', moduleId: 'nimda-workspace' });
  assert.equal(moduleSourceDirectory('project', 'nimda-workspace'), join('project', 'plugins/media-manager/integrations/opendesk'));
  assert.equal(moduleSourceDirectory('project', 'tower-assistant'), join('project', 'apps/features/tower_assistant'));
  assert.throws(() => moduleSourceDirectory('project', 'not-registered'));
  const args = externalBuildArguments('project', 'nimda-workspace', 'python', 'output');
  assert.deepEqual(args, [join('project', 'plugins/media-manager/integrations/opendesk/build.mjs'), '--opendesk', 'project', '--project', join('project', 'plugins/media-manager'), '--python', 'python', '--output', 'output', '--work', join('project', '.cache/media-manager-build')]);
});

test('module build aliases preserve identity and cannot bypass the public allowlist', () => {
  const current = readDistribution(resolve(import.meta.dirname, '../..'));
  assert.deepEqual(parseModulePackArgs(['module', 'media-manager'], current), { mode: 'module', moduleId: 'nimda-workspace' });
  assert.throws(() => parseModulePackArgs(['module', 'media-manager'], config()), /allowlist/);
  assert.throws(() => validateDistribution({ ...config(), publicModules: ['tower-assistant', 'media-manager'] }), /Unknown distribution module/);
  for (const name of ['__proto__', 'constructor', 'toString', 'hasOwnProperty']) {
    assert.throws(() => parseModulePackArgs(['module', name], current), /allowlist/);
    assert.throws(() => moduleSourceDirectory('project', name), /Unknown build source module/);
    assert.throws(() => externalBuildArguments('project', name, 'python', 'output'), /external builder/);
  }
});

test('application builder accepts the generic project option and rejects conflicting legacy options', () => {
  const required = ['--opendesk', 'host', '--python', 'python'];
  const canonical = parseBuildArgs([...required, '--project', 'application', '--output', 'output', '--work', 'work']);
  assert.deepEqual(canonical, { host: resolve('host'), project: resolve('application'), python: resolve('python'), output: resolve('output'), work: resolve('work') });
  assert.deepEqual(parseBuildArgs([...required, '--nimda', 'application', '--output', 'output', '--work', 'work']), canonical);
  assert.equal(parseBuildArgs([...required, '--project', 'application', '--nimda', './application']).project, canonical.project);
  assert.equal(parseBuildArgs(required).project, resolve(import.meta.dirname, '../../plugins/media-manager'));
  assert.throws(() => parseBuildArgs([...required, '--project', 'application', '--nimda', 'other']), /conflicts/);
  for (const extra of [['--project'], ['--project', ''], ['--project', '   '], ['--project', '--work'],
    ['--project', 'application', '--project', 'application'], ['--unknown', 'application'], ['--__proto__', 'application']]) {
    assert.throws(() => parseBuildArgs([...required, ...extra]), /Usage/);
  }
  assert.throws(() => parseBuildArgs(['--project', 'application']), /required/);
});

test('external build Python is explicit and an environment override wins', (t) => {
  const root = fixture(t);
  const configured = join(root, 'configured.exe');
  const overridden = join(root, 'override.exe');
  writeFileSync(configured, 'fixture'); writeFileSync(overridden, 'fixture');
  const definition = MODULE_DEFINITIONS['nimda-workspace'];
  assert.equal(externalPython(definition, {}, { mediaManagerPython: configured }), configured);
  assert.equal(externalPython(definition, { OPENDESK_MEDIA_MANAGER_PYTHON: overridden }, { mediaManagerPython: configured }), overridden);
  assert.throws(() => externalPython(definition, {}, {}), /Configure/);
  assert.throws(() => externalPython(definition, {}, { mediaManagerPython: 'relative.exe' }), /absolute/);
  assert.throws(() => externalPython(definition, { OPENDESK_MEDIA_MANAGER_PYTHON: join(root, 'missing') }, { mediaManagerPython: configured }), /Configure/);
});

test('external build Python compatibility names are metadata-driven with explicit precedence', (t) => {
  const root = fixture(t);
  const currentEnv = join(root, 'current-env.exe');
  const legacyEnv = join(root, 'legacy-env.exe');
  const currentConfig = join(root, 'current-config.exe');
  const legacyConfig = join(root, 'legacy-config.exe');
  for (const python of [currentEnv, legacyEnv, currentConfig, legacyConfig]) writeFileSync(python, 'fixture');
  const definition = MODULE_DEFINITIONS['nimda-workspace'];
  const config = { mediaManagerPython: currentConfig, workManagerPython: legacyConfig };
  assert.equal(externalPython(definition, { OPENDESK_MEDIA_MANAGER_PYTHON: currentEnv, OPENDESK_WORK_MANAGER_PYTHON: legacyEnv }, config), currentEnv);
  assert.equal(externalPython(definition, { OPENDESK_WORK_MANAGER_PYTHON: legacyEnv }, config), legacyEnv);
  assert.equal(externalPython(definition, {}, config), currentConfig);
  assert.equal(externalPython(definition, {}, { workManagerPython: legacyConfig }), legacyConfig);
  assert.equal(externalPython(definition, Object.create({ OPENDESK_MEDIA_MANAGER_PYTHON: currentEnv }), config), currentConfig);
  const generic = { externalBuilder: { pythonEnv: 'APP_PYTHON', pythonEnvAliases: ['OLD_APP_PYTHON'], pythonConfig: 'appPython', pythonConfigAliases: ['oldAppPython'] } };
  assert.equal(externalPython(generic, { OLD_APP_PYTHON: legacyEnv }, { appPython: currentConfig }), legacyEnv);
  assert.equal(externalPython(generic, {}, { oldAppPython: legacyConfig }), legacyConfig);
  assert.throws(() => externalPython(generic, {}, Object.create({ oldAppPython: legacyConfig })), /Configure/);
});

test('invalid explicit Python settings are rejected instead of falling back to valid compatibility settings', (t) => {
  const root = fixture(t);
  const python = join(root, 'python.exe');
  writeFileSync(python, 'fixture');
  const definition = MODULE_DEFINITIONS['nimda-workspace'];
  for (const invalid of ['', null, false, 42, 'relative.exe', join(root, 'missing.exe'), root]) {
    assert.throws(() => externalPython(definition, { OPENDESK_MEDIA_MANAGER_PYTHON: invalid, OPENDESK_WORK_MANAGER_PYTHON: python }, { mediaManagerPython: python }), /Invalid setting: OPENDESK_MEDIA_MANAGER_PYTHON/);
    assert.throws(() => externalPython(definition, {}, { mediaManagerPython: invalid, workManagerPython: python }), /Invalid setting: mediaManagerPython/);
    assert.throws(() => externalPython(definition, { OPENDESK_WORK_MANAGER_PYTHON: invalid }, { mediaManagerPython: python }), /Invalid setting: OPENDESK_WORK_MANAGER_PYTHON/);
  }
});

test('package manifest accepts HTTP apps only with their worker and host API 2 contract', () => {
  const manifest = { schemaVersion: 1, id: 'web-example', label: 'Example', version: '1.0.0', hostApi: 2, webApp: true, target: 'windows-x64', frontend: 'frontend/index.html', worker: 'worker/module-worker.exe', methods: ['example.status'], dataSchema: 1, source: { commit: null, dirty: true, repository: null } };
  assert.equal(validateManifest(manifest), manifest);
  for (const invalid of [{ ...manifest, hostApi: 1 }, { ...manifest, worker: null, methods: [] }, { ...manifest, webApp: false }, { ...manifest, webApp: 'true' }]) assert.throws(() => validateManifest(invalid), /Web applications/);
  const { webApp, ...staticManifest } = manifest;
  assert.throws(() => validateManifest(staticManifest), /Web applications/);
  assert.doesNotThrow(() => validateManifest({ ...staticManifest, hostApi: 1 }));
  assert.doesNotThrow(() => validateManifest({ ...staticManifest, hostApi: 99 }), 'incompatible fixture manifests remain buildable');
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
