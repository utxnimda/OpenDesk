/** Build local, immutable-content .ndpkg artifacts. Never creates Git tags or publishes. */
import { createHash, randomUUID } from 'node:crypto';
import { cpSync, existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, realpathSync, writeFileSync } from 'node:fs';
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { spawnSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import { readGitIdentity } from './build-version.mjs';
import { localConfig, pythonExecutable, root, run, worker } from './environment.mjs';
import { MODULE_DEFINITIONS as definitions, assertBundleDirectory, assertOutputDirectory, assertUnlinkedPath, moduleSourceDirectory, parseModulePackArgs, readDistribution, sha256, writeImmutableFiles } from './distribution.mjs';

function underRoot(...parts) {
  const path = resolve(root, ...parts);
  const suffix = relative(root, path);
  if (!suffix || suffix === '..' || suffix.startsWith('..' + sep) || isAbsolute(suffix)) throw new Error('Build output must stay inside the project root.');
  let cursor = root;
  for (const segment of suffix.split(sep)) {
    cursor = join(cursor, segment);
    const entry = lstatSync(cursor, { throwIfNoEntry: false });
    if (entry?.isSymbolicLink()) throw new Error('Refusing to use a linked build path: ' + cursor);
  }
  return path;
}

export function validateManifest(manifest) {
  const keys = ['schemaVersion', 'id', 'label', 'version', 'hostApi', 'target', 'frontend', 'worker', 'methods', 'dataSchema', 'source', ...(Object.hasOwn(manifest ?? {}, 'webApp') ? ['webApp'] : [])].sort();
  if (!manifest || typeof manifest !== 'object' || Array.isArray(manifest) || Object.keys(manifest).sort().join('|') !== keys.join('|')) throw new Error('Unexpected module manifest fields.');
  if (manifest.schemaVersion !== 1 || !/^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/.test(manifest.id) || typeof manifest.label !== 'string' || !manifest.label.trim() || manifest.label.length > 100) throw new Error('Invalid module identity.');
  if (!/^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$/.test(manifest.version)) throw new Error('Module version must be a three-part semantic version.');
  if (!Number.isSafeInteger(manifest.hostApi) || manifest.hostApi < 1 || manifest.target !== 'windows-x64' || manifest.frontend !== 'frontend/index.html' || ![null, 'worker/module-worker.exe'].includes(manifest.worker)) throw new Error('Unsupported package entry contract.');
  if ((Object.hasOwn(manifest, 'webApp') && typeof manifest.webApp !== 'boolean') || (manifest.webApp === true && (manifest.hostApi !== 2 || manifest.worker === null)) || (manifest.hostApi === 2 && manifest.webApp !== true)) throw new Error('Web applications require hostApi 2, webApp true and a worker.');
  if (!Number.isSafeInteger(manifest.dataSchema) || manifest.dataSchema < 1 || !Array.isArray(manifest.methods) || new Set(manifest.methods).size !== manifest.methods.length || manifest.methods.some((method) => typeof method !== 'string' || !/^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)+$/.test(method) || /^(runtime|module)\./.test(method))) throw new Error('Invalid data schema or business methods.');
  if (manifest.worker === null && manifest.methods.length) throw new Error('Frontend-only packages cannot declare worker methods.');
  const source = manifest.source;
  if (!source || !['commit|dirty', 'commit|dirty|repository'].includes(Object.keys(source).sort().join('|')) || (source.commit !== null && !/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(source.commit)) || typeof source.dirty !== 'boolean') throw new Error('Invalid source provenance.');
  if (source.repository != null && (typeof source.repository !== 'string' || sanitizeRepository(source.repository) !== source.repository)) throw new Error('Repository URL must be a sanitized HTTPS URL.');
  return manifest;
}

export function sanitizeRepository(value) {
  try {
    if (typeof value !== 'string') return null;
    const scp = /^git@([a-zA-Z0-9.-]+):([^?#]+)$/.exec(value.trim());
    const url = new URL(scp ? `https://${scp[1]}/${scp[2]}` : value.trim());
    if (url.protocol !== 'https:' || !url.hostname || url.pathname === '/') return null;
    url.username = ''; url.password = ''; url.search = ''; url.hash = '';
    return url.href;
  } catch { return null; }
}

function provenance() {
  const sourceRoot = process.env.OPENDESK_SOURCE_ROOT ? resolve(process.env.OPENDESK_SOURCE_ROOT) : root;
  const identity = readGitIdentity(sourceRoot);
  const copiedBuild = realpathSync(sourceRoot).toLowerCase() !== realpathSync(root).toLowerCase();
  const env = { ...process.env };
  for (const key of ['GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR']) delete env[key];
  const origin = spawnSync('git', ['remote', 'get-url', 'origin'], { cwd: sourceRoot, env, encoding: 'utf8', windowsHide: true, timeout: 5000 });
  const repository = origin.status === 0 ? sanitizeRepository(origin.stdout.trim()) : null;
  // An alternate Git root establishes the baseline commit, not equality with a
  // staged source tree. Mark it dirty conservatively instead of claiming clean.
  return { commit: identity.commit, dirty: copiedBuild || identity.dirty !== false, repository };
}

let toolIdentity;
function buildFingerprint(source, manifest, marker) {
  const hash = createHash('sha256');
  const add = (name, content) => hash.update(name + '\0').update(content).update('\0');
  const visit = (directory) => {
    for (const entry of readdirSync(directory, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      if (['__pycache__', 'node_modules', 'dist', '.cache'].includes(entry.name)) continue;
      const path = join(directory, entry.name);
      if (entry.isSymbolicLink()) throw new Error('Linked source inputs are not supported.');
      if (entry.isDirectory()) visit(path);
      else if (entry.isFile() && !/\.py[cod]$/.test(entry.name)) add(relative(root, path).replaceAll('\\', '/'), readFileSync(path));
    }
  };
  for (const directory of [source, worker, underRoot('apps/framework/module-sdk')]) visit(directory);
  for (const file of ['apps/framework/frontend/src/styles.css', 'apps/framework/frontend/src/themes.css', 'apps/framework/frontend/src/workbench-styles.css', 'apps/framework/frontend/src/personalization.css', 'scripts/module-pack.mjs', 'scripts/distribution.mjs', 'scripts/module-pack-archive.py', 'packaging/requirements-build.txt', 'packaging/requirements-runtime.txt']) add(file, readFileSync(join(root, file)));
  const lock = JSON.parse(readFileSync(join(root, 'package-lock.json'), 'utf8'));
  delete lock.version;
  if (lock.packages?.['']) delete lock.packages[''].version;
  add('dependency-lock', JSON.stringify(lock));
  if (!toolIdentity) {
    const result = spawnSync(pythonExecutable(), ['-X', 'utf8', '-c', "import sys,json,platform,importlib.metadata as m; print(json.dumps({'python':sys.version,'platform':platform.platform(),'packages':{name:m.version(name) for name in ['pyinstaller','pyinstaller-hooks-contrib','Pillow']}}))"], { encoding: 'utf8', windowsHide: true });
    if (result.error || result.status !== 0) throw new Error('Could not identify the selected module build toolchain.');
    toolIdentity = JSON.stringify({ node: process.version, python: JSON.parse(result.stdout), epoch: '1577836800' });
  }
  const { source: _provenance, ...identity } = manifest;
  add('package-identity', JSON.stringify({ identity, marker }));
  add('toolchain', toolIdentity);
  return hash.digest('hex');
}

function writeJson(path, value) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify(value, null, 2) + '\n', 'utf8');
}

/** Only explicitly listed, ordinary module files enter the frozen worker. */
export function workerDataArguments(projectRoot, source, files = []) {
  if (!Array.isArray(files)) throw new Error('workerData must be an explicit list of files.');
  const seen = new Set();
  const args = [];
  for (const file of files) {
    if (typeof file !== 'string' || !file || file.split('/').some((part) => !/^[a-zA-Z0-9_.-]+$/.test(part) || part === '.' || part === '..')) throw new Error('Invalid worker resource path.');
    const path = resolve(source, file);
    const withinSource = relative(resolve(source), path);
    const withinProject = relative(resolve(projectRoot), path);
    if (!withinSource || withinSource.startsWith('..') || isAbsolute(withinSource) || !withinProject || withinProject.startsWith('..') || isAbsolute(withinProject)) throw new Error('Worker resource must belong to this module.');
    assertUnlinkedPath(path);
    if (!lstatSync(path).isFile()) throw new Error('Worker resource must be an ordinary file.');
    const key = withinProject.replaceAll('\\', '/').toLowerCase();
    if (seen.has(key)) throw new Error('Duplicate worker resource.');
    seen.add(key);
    args.push('--add-data', path + (process.platform === 'win32' ? ';' : ':') + dirname(withinProject).replaceAll('\\', '/'));
  }
  return args;
}

function workerEntry(definition, manifest, marker) {
  const businessRegistration = definition.folder === 'test_lab'
    ? `register(registry, version=${JSON.stringify(manifest.version)}, marker=${JSON.stringify(marker)})`
    : 'register(registry)';
  return [
    '"""Generated build composition. Identity is baked into this executable."""',
    'from opendesk_worker.__main__ import main',
    'from opendesk_worker.protocol import ProtocolError',
    'from opendesk_worker.registry import MethodResult',
    `from ${definition.backend} import register`,
    '',
    'def configure(registry):',
    `    ${businessRegistration}`,
    '    def identity(params):',
    '        if params:',
    '            raise ProtocolError("INVALID_PARAMS", "module.identity accepts an empty object.")',
    `        return MethodResult({"id": ${JSON.stringify(manifest.id)}, "version": ${JSON.stringify(manifest.version)}, "hostApi": 1})`,
    '    registry.register("module.identity", identity)',
    '',
    'if __name__ == "__main__":',
    '    raise SystemExit(main(configure))',
    '',
  ].join('\n');
}

function verifyFrozenWorker(executable, manifest, marker) {
  const requests = [
    { protocolVersion: 1, id: 'identity', method: 'module.identity', params: {} },
    ...(manifest.methods.includes('test.echo') ? [
      { protocolVersion: 1, id: 'echo', method: 'test.echo', params: { text: '独立模块 package verification' } },
      { protocolVersion: 1, id: 'reject', method: 'test.echo', params: { text: 7 } },
    ] : []),
    { protocolVersion: 1, id: 'stop', method: 'runtime.shutdown', params: {} },
  ];
  const env = { ...process.env };
  for (const key of ['PYTHONPATH', 'PYTHONHOME', 'OPENDESK_PYTHON', 'OPENDESK_WORKER_SOURCE']) delete env[key];
  const result = spawnSync(executable, [], { input: requests.map((request) => JSON.stringify(request)).join('\n') + '\n', encoding: 'utf8', timeout: 30_000, windowsHide: true, cwd: dirname(executable), env, maxBuffer: 4 * 1024 * 1024 });
  if (result.error || result.status !== 0) throw new Error('Frozen module worker failed: ' + (result.error?.message ?? result.stderr));
  const messages = result.stdout.trim().split(/\r?\n/).map((line) => JSON.parse(line));
  if (messages.length !== requests.length || messages.some((message, index) => message.id !== requests[index].id || message.protocolVersion !== 1)) throw new Error('Frozen worker response correlation failed.');
  const identity = messages[0].result;
  if (identity?.id !== manifest.id || identity.version !== manifest.version || identity.hostApi !== 1) throw new Error('Frozen worker identity does not match the package.');
  if (manifest.methods.includes('test.echo')) {
    const echo = messages[1].result;
    if (echo?.version !== manifest.version || echo.marker !== marker || echo.text !== requests[1].params.text || !Number.isInteger(echo.pid)) throw new Error('Frozen worker did not execute the selected test implementation.');
    if (messages[2].error?.code !== 'INVALID_PARAMS') throw new Error('Frozen worker accepted invalid test parameters.');
  }
  if (messages.at(-1).result?.status !== 'stopping') throw new Error('Frozen worker did not acknowledge shutdown.');
}

async function buildPackage(sourceId, destination, override = {}) {
  const definition = definitions[sourceId];
  if (!definition) throw new Error('Unknown build source module.');
  const source = underRoot(moduleSourceDirectory(root, sourceId));
  const original = sourceManifest(sourceId);
  if (definition.externalBuilder) {
    if (Object.keys(override).length) throw new Error('External builders do not accept fixture overrides.');
    return buildExternalPackage(sourceId, destination, original);
  }
  const { marker = definition.marker, filename, ...fields } = override;
  const manifest = validateManifest({ ...original, ...fields, source: provenance() });
  const archive = join(destination, filename ?? `${manifest.id}-${manifest.version}.ndpkg`);
  const fingerprint = buildFingerprint(source, manifest, marker);
  const cache = underRoot('.cache/module-package-cache');
  const cacheArchive = underRoot('.cache/module-package-cache', `${fingerprint}.ndpkg`);
  const cacheMetadata = underRoot('.cache/module-package-cache', `${fingerprint}.json`);
  if (existsSync(cacheArchive) !== existsSync(cacheMetadata)) throw new Error('Cached module artifact is incomplete; preserve and review the cache entry before rebuilding.');
  if (existsSync(cacheArchive) && existsSync(cacheMetadata)) {
    const saved = JSON.parse(readFileSync(cacheMetadata, 'utf8'));
    const bytes = readFileSync(cacheArchive);
    const cached = validateCachedPackage(fingerprint, manifest, saved, bytes);
    writeImmutableFiles(destination, { [basename(archive)]: bytes });
    console.log(`Reused unchanged ${manifest.id}@${manifest.version}; original source identity retained, sha256=${cached.sha256}`);
    return { entry: { id: manifest.id, version: manifest.version, file: basename(archive), sha256: cached.sha256 }, manifest: cached.manifest };
  }
  const frontend = underRoot('dist/module-build', manifest.id, manifest.version, 'frontend');
  if (!existsSync(join(source, 'frontend/index.html'))) throw new Error('Module frontend entry is missing: ' + manifest.id);
  const { build } = await import('vite');
  const { default: vue } = await import('@vitejs/plugin-vue');
  await build({
    configFile: false, root: join(source, 'frontend'), base: './', plugins: [vue()], envDir: false,
    define: { __MODULE_ID__: JSON.stringify(manifest.id), __MODULE_VERSION__: JSON.stringify(manifest.version), __MODULE_MARKER__: JSON.stringify(marker) },
    build: { outDir: frontend, emptyOutDir: true, target: 'es2022', sourcemap: false },
  });
  // Keep paths short enough for Windows DLL-copy APIs even in a long staging root.
  const temporary = underRoot('.cache/mp', randomUUID().replaceAll('-', '').slice(0, 12));
  const packageRoot = join(temporary, 'package');
  mkdirSync(packageRoot, { recursive: true });
  cpSync(frontend, join(packageRoot, 'frontend'), { recursive: true, dereference: false, errorOnExist: true });
  if (manifest.worker) {
    const python = pythonExecutable();
    const entry = join(temporary, 'freeze_entry.py');
    writeFileSync(entry, workerEntry(definition, manifest, marker), 'utf8');
    const pyDist = join(temporary, 'd');
    run(python, ['-X', 'utf8', '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--contents-directory', '_internal', '--name', 'module-worker',
      '--distpath', pyDist, '--workpath', join(temporary, 'w'), '--specpath', temporary,
      '--paths', root, '--paths', worker, ...workerDataArguments(root, source, definition.workerData), entry], { env: { ...process.env, PYINSTALLER_CONFIG_DIR: join(temporary, 'c'), SOURCE_DATE_EPOCH: '1577836800', PYTHONHASHSEED: '0' } });
    const frozen = join(pyDist, 'module-worker');
    if (!existsSync(join(frozen, 'module-worker.exe')) || !existsSync(join(frozen, '_internal/base_library.zip'))) throw new Error('Frozen worker output is incomplete.');
    verifyFrozenWorker(join(frozen, 'module-worker.exe'), manifest, marker);
    cpSync(frozen, join(packageRoot, 'worker'), { recursive: true, dereference: false, errorOnExist: true });
  }
  writeJson(join(packageRoot, 'manifest.json'), manifest);
  const stagedArchive = join(temporary, 'artifact.ndpkg');
  run(pythonExecutable(), ['-X', 'utf8', join(root, 'scripts/module-pack-archive.py'), packageRoot, stagedArchive]);
  const bytes = readFileSync(stagedArchive);
  const digest = sha256(bytes);
  writeImmutableFiles(destination, { [basename(archive)]: bytes });
  writeImmutableFiles(cache, { [basename(cacheArchive)]: bytes, [basename(cacheMetadata)]: JSON.stringify({ fingerprint, sha256: digest, manifest }, null, 2) + '\n' });
  console.log(`Built ${manifest.id}@${manifest.version}, source=${manifest.source.commit ?? 'unknown'}, dirty=${manifest.source.dirty}`);
  return { entry: { id: manifest.id, version: manifest.version, file: basename(archive), sha256: digest }, manifest };
}

function sourceManifest(id) {
  const definition = definitions[id];
  if (!definition) throw new Error('Unknown build source module.');
  const manifest = validateManifest(JSON.parse(readFileSync(underRoot(moduleSourceDirectory(root, id), 'manifest.json'), 'utf8')));
  if (manifest.id !== id) throw new Error('Source manifest does not match its registered module ID: ' + id);
  return manifest;
}

export function externalPython(definition, env = process.env, config = localConfig()) {
  const builder = definition?.externalBuilder;
  if (!builder) throw new Error('Module does not use an external builder.');
  // Environment overrides configuration; within each source the current name
  // wins over compatibility aliases. A present but invalid value never falls back.
  const candidates = [
    ...[builder.pythonEnv, ...(builder.pythonEnvAliases ?? [])].map((key) => [env, key]),
    ...[builder.pythonConfig, ...(builder.pythonConfigAliases ?? [])].map((key) => [config, key]),
  ];
  const selected = candidates.find(([values, key]) => Object.hasOwn(values, key));
  const python = selected?.[0][selected[1]];
  if (typeof python !== 'string' || !isAbsolute(python) || !existsSync(python) || !lstatSync(python).isFile()) throw new Error(`Configure an absolute build Python in ${builder.pythonEnv} or .local/dev.json ${builder.pythonConfig}.${selected ? ' Invalid setting: ' + selected[1] + '.' : ''}`);
  return python;
}

export function externalBuildArguments(projectRoot, id, python, output) {
  if (!Object.hasOwn(definitions, id) || !definitions[id].externalBuilder) throw new Error('Module does not use an external builder.');
  const definition = definitions[id];
  const builder = definition.externalBuilder;
  return [join(moduleSourceDirectory(projectRoot, id), builder.script), '--opendesk', projectRoot,
    '--project', join(projectRoot, builder.projectPath), '--python', python, '--output', output,
    '--work', join(projectRoot, builder.workDirectory)];
}

function buildExternalPackage(id, destination, requested) {
  const definition = definitions[id];
  const python = externalPython(definition);
  const args = externalBuildArguments(root, id, python, destination);
  underRoot(args[0]);
  underRoot(root, definition.externalBuilder.projectPath);
  run(process.execPath, args);
  const file = `${id}-${requested.version}.ndpkg`;
  assertOutputDirectory(destination, [file, 'manifest.json', 'SHA256SUMS']);
  const archive = join(destination, file);
  const checked = spawnSync(python, ['-X', 'utf8', join(root, 'scripts/release-archive.py'), 'inspect-module', archive], { encoding: 'utf8', windowsHide: true, timeout: 180_000, maxBuffer: 4 * 1024 * 1024 });
  if (checked.error || checked.status !== 0) throw new Error('External module archive verification failed: ' + (checked.error?.message ?? checked.stderr));
  const manifest = validateManifest(JSON.parse(checked.stdout));
  for (const key of new Set([...Object.keys(requested), ...Object.keys(manifest)])) {
    if (key !== 'source' && JSON.stringify(requested[key]) !== JSON.stringify(manifest[key])) throw new Error('External module identity differs from its source manifest.');
  }
  if (JSON.stringify(JSON.parse(readFileSync(join(destination, 'manifest.json'), 'utf8'))) !== JSON.stringify(manifest)) throw new Error('External module manifest sidecar differs from the archive.');
  const bytes = readFileSync(archive);
  if (bytes.length > 128 * 1024 * 1024) throw new Error('External module exceeds the host package size limit.');
  const digest = sha256(bytes);
  if (readFileSync(join(destination, 'SHA256SUMS'), 'utf8').trim() !== `${digest}  ${file}`) throw new Error('External module checksum sidecar differs from the archive.');
  return { entry: { id, version: manifest.version, file, sha256: digest }, manifest };
}

export function validateCachedPackage(fingerprint, requested, saved, bytes) {
  if (!saved || saved.fingerprint !== fingerprint || saved.sha256 !== sha256(bytes)) throw new Error('Cached module artifact failed integrity checking; do not reuse it.');
  validateManifest(saved.manifest);
  const { source: _requestedSource, ...requestedIdentity } = validateManifest(requested);
  const { source: _savedSource, ...savedIdentity } = saved.manifest;
  if (JSON.stringify(requestedIdentity) !== JSON.stringify(savedIdentity)) throw new Error('Cached module identity does not match the requested build.');
  return { sha256: saved.sha256, manifest: saved.manifest };
}

function buildUiFailureFixture(destination) {
  const original = JSON.parse(readFileSync(underRoot('apps/features/test_lab/manifest.json'), 'utf8'));
  const manifest = validateManifest({ ...original, version: '1.2.0', hostApi: 1, dataSchema: 1, worker: null, methods: [], source: provenance() });
  const temporary = underRoot('.cache/mp', randomUUID().replaceAll('-', '').slice(0, 12));
  mkdirSync(join(temporary, 'frontend'), { recursive: true });
  writeJson(join(temporary, 'manifest.json'), manifest);
  // A structurally valid page which deliberately never sends SDK hello. This
  // exercises the actual browser readiness timeout, not a simulated host error.
  writeFileSync(join(temporary, 'frontend/index.html'), '<!doctype html>\n<html lang="zh-CN"><head><meta charset="UTF-8"><title>模块加载失败测试</title></head><body><h1>故意加载失败测试</h1><p>test-lab 1.2.0：本测试页面故意不发送模块 SDK hello，用于验证超时与恢复原版本。</p></body></html>\n', 'utf8');
  const file = 'test-lab-1.2.0-ui-failure.ndpkg';
  const archive = join(dirname(temporary), randomUUID() + '.ndpkg');
  run(pythonExecutable(), ['-X', 'utf8', join(root, 'scripts/module-pack-archive.py'), temporary, archive]);
  const bytes = readFileSync(archive);
  writeImmutableFiles(destination, { [file]: bytes });
  return { id: manifest.id, version: manifest.version, file, sha256: sha256(bytes), purpose: 'ui-failure', description: 'Deliberately no SDK hello; verify real UI timeout, retained lastError and restoration of the previously active version. No worker.' };
}

async function main() {
  const distribution = readDistribution(root);
  const { mode, moduleId } = parseModulePackArgs(process.argv.slice(2), distribution);
  const selected = mode === 'module' ? sourceManifest(moduleId) : null;
  const destination = mode === 'module'
    ? underRoot('dist/modules', selected.id, selected.version)
    : underRoot(mode === 'bundled' ? 'dist/bundled-modules' : 'dist/module-fixtures');
  if (mode === 'bundled') {
    const names = distribution.bundledModules.map((id) => { const manifest = sourceManifest(id); return `${id}-${manifest.version}.ndpkg`; });
    assertOutputDirectory(destination, ['app-manifest.json', ...names]);
    if (existsSync(join(destination, 'app-manifest.json'))) {
      const previous = JSON.parse(readFileSync(join(destination, 'app-manifest.json'), 'utf8'));
      assertBundleDirectory(root, destination, previous.hostVersion);
    }
  } else if (mode === 'module') {
    assertOutputDirectory(destination, [`${selected.id}-${selected.version}.ndpkg`, 'manifest.json', 'SHA256SUMS']);
  } else if (mode === 'fixtures') {
    const baseline = sourceManifest('test-lab');
    assertOutputDirectory(destination, ['fixtures.json', `test-lab-${baseline.version}.ndpkg`, 'test-lab-1.1.0.ndpkg', 'test-lab-2.0.0-incompatible.ndpkg', 'test-extra-1.0.0.ndpkg', 'test-lab-1.2.0-ui-failure.ndpkg']);
  } else {
    const indexPath = underRoot('dist/module-fixtures', 'fixtures.json');
    const previous = existsSync(indexPath) ? JSON.parse(readFileSync(indexPath, 'utf8')) : { schemaVersion: 1, testFixtures: true, packages: [] };
    if (previous.schemaVersion !== 1 || previous.testFixtures !== true || !Array.isArray(previous.packages)) throw new Error('Existing fixture index is invalid; move the reviewed output aside before rebuilding.');
    assertOutputDirectory(destination, ['fixtures.json', 'test-lab-1.2.0-ui-failure.ndpkg', ...previous.packages.map((entry) => entry.file)]);
  }
  if (process.platform !== 'win32' || process.arch !== 'x64') throw new Error('Module packages currently target Windows x64.');
  const python = selected && definitions[moduleId].externalBuilder ? externalPython(definitions[moduleId]) : pythonExecutable();
  run(python, ['-X', 'utf8', '-c', "import platform, struct, sys; assert sys.platform == 'win32' and struct.calcsize('P') == 8 and platform.machine().lower() in ('amd64','x86_64') and sys.version_info >= (3,11), 'Windows x64 Python 3.11+ required'"]);
  mkdirSync(destination, { recursive: true });
  if (mode === 'bundled') {
    const modules = [];
    for (const id of distribution.bundledModules) modules.push({ ...(await buildPackage(id, destination)).entry, defaultEnabled: true });
    const hostVersion = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8')).version;
    writeJson(join(destination, 'app-manifest.json'), { schemaVersion: 1, hostVersion, modules });
    assertBundleDirectory(root, destination, hostVersion);
  } else if (mode === 'module') {
    const result = await buildPackage(moduleId, destination);
    writeImmutableFiles(destination, {
      'manifest.json': JSON.stringify(result.manifest, null, 2) + '\n',
      SHA256SUMS: `${result.entry.sha256}  ${result.entry.file}\n`,
    });
    console.log('Public module artifact: ' + join(destination, result.entry.file));
  } else if (mode === 'fixtures') {
    const fixtures = [];
    fixtures.push({ ...(await buildPackage('test-lab', destination)).entry, purpose: 'baseline', description: 'Current test-lab source version, imported explicitly for local smoke verification; never bundled or released.' });
    fixtures.push({ ...(await buildPackage('test-lab', destination, { version: '1.1.0', marker: 'v1.1-enhanced' })).entry, purpose: 'compatible-update', description: 'Compatible update with a distinct frontend and worker marker.' });
    fixtures.push({ ...(await buildPackage('test-lab', destination, { version: '2.0.0', hostApi: 99, marker: 'incompatible-api99', filename: 'test-lab-2.0.0-incompatible.ndpkg' })).entry, purpose: 'incompatible', description: 'Deliberately incompatible hostApi=99; preserve original import and reject activation.' });
    fixtures.push({ ...(await buildPackage('test-lab', destination, { id: 'test-extra', label: '导入新增测试页签', version: '1.0.0', marker: 'extra-module' })).entry, purpose: 'new-module', description: 'A new module ID to verify importing an additional tab.' });
    fixtures.push(buildUiFailureFixture(destination));
    writeJson(join(destination, 'fixtures.json'), { schemaVersion: 1, testFixtures: true, description: 'Local verification packages; these are not published release history.', packages: fixtures });
  } else {
    const indexPath = join(destination, 'fixtures.json');
    const previous = existsSync(indexPath) ? JSON.parse(readFileSync(indexPath, 'utf8')) : { packages: [] };
    if (!Array.isArray(previous.packages)) throw new Error('Existing fixture index is invalid.');
    const fixture = buildUiFailureFixture(destination);
    const packages = [...previous.packages.filter((entry) => entry.file !== fixture.file), fixture];
    writeJson(indexPath, { schemaVersion: 1, testFixtures: true, description: 'Local verification packages; these are not published release history.', packages });
  }
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => { console.error('Module package build failed: ' + error.message); process.exitCode = 1; });
}
