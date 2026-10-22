/** Prepare versioned release assets locally. This never commits, tags or uploads. */
import { createHash } from 'node:crypto';
import { copyFileSync, existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { spawnSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import { root, desktop, pythonExecutable } from './environment.mjs';
import { readGitIdentity } from './build-version.mjs';
import { readDistribution, assertPublicModule, assertBundleDirectory, MODULE_DEFINITIONS } from './distribution.mjs';
import { validateManifest } from './module-pack.mjs';

const repository = 'https://github.com/utxnimda/OpenDesk';
const readJson = (path) => JSON.parse(readFileSync(path, 'utf8').replace(/^\uFEFF/, ''));
const digest = (path) => createHash('sha256').update(readFileSync(path)).digest('hex');
const writeJson = (path, value) => writeFileSync(path, JSON.stringify(value, null, 2) + '\n');

function workspacePath(...parts) {
  const path = resolve(root, ...parts);
  const suffix = relative(root, path);
  if (!suffix || isAbsolute(suffix) || suffix === '..' || suffix.startsWith('..' + sep)) throw new Error('Release paths must stay within this project.');
  let cursor = root;
  for (const part of suffix.split(sep)) {
    cursor = join(cursor, part);
    if (lstatSync(cursor, { throwIfNoEntry: false })?.isSymbolicLink()) throw new Error('Linked release paths are not supported.');
  }
  return path;
}

function fileRecord(path, base = dirname(path)) {
  if (!lstatSync(path).isFile() || lstatSync(path).isSymbolicLink()) throw new Error('Expected a regular artifact file.');
  return { file: relative(base, path).replaceAll('\\', '/'), bytes: lstatSync(path).size, sha256: digest(path) };
}

export function inventory(directory) {
  const result = [];
  function visit(folder) {
    for (const child of readdirSync(folder, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      const path = join(folder, child.name);
      if (child.isSymbolicLink()) throw new Error('Linked artifacts are not supported.');
      if (child.isDirectory()) visit(path);
      else if (child.isFile()) result.push(fileRecord(path, directory));
      else throw new Error('Unexpected artifact entry.');
    }
  }
  visit(directory);
  return result;
}

export function assertInventory(actual, recorded) {
  if (!Array.isArray(recorded) || actual.length !== recorded.length) throw new Error('Build artifact inventory changed; rebuild before releasing.');
  for (let i = 0; i < actual.length; i++) {
    for (const key of ['file', 'bytes', 'sha256']) if (actual[i][key] !== recorded[i]?.[key]) throw new Error('Build artifact content changed; rebuild before releasing.');
  }
}

function cleanIdentity() {
  const source = readGitIdentity(root);
  if (!source.commit || source.dirty !== false) throw new Error('Release staging requires a committed, clean working tree. Build after committing the reviewed sources.');
  return { repository, commit: source.commit, dirty: false };
}

function python(operation, source, output) {
  const result = spawnSync(pythonExecutable(), ['-X', 'utf8', join(root, 'scripts/release-archive.py'), operation, source, ...(output ? [output] : [])], { encoding: 'utf8', windowsHide: true, timeout: 180_000, maxBuffer: 4 * 1024 * 1024 });
  if (result.error || result.status !== 0) throw new Error('Release archive verification failed: ' + (result.error?.message ?? result.stderr));
  return JSON.parse(result.stdout);
}

function moduleManifest(path, expected) {
  const manifest = validateManifest(python('inspect-module', path));
  if (manifest.id !== expected.id || manifest.version !== expected.version || digest(path) !== expected.sha256) throw new Error('Module archive identity or digest mismatch.');
  if (!manifest.source.commit || manifest.source.dirty !== false) throw new Error('Public module artifacts must have a clean, committed source identity; use a new version if replacing a previous local artifact.');
  if (manifest.source.repository?.replace(/\.git\/?$/, '') !== repository) throw new Error('Module source belongs to a different repository.');
  const env = { ...process.env };
  for (const key of ['GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR']) delete env[key];
  const ancestor = spawnSync('git', ['merge-base', '--is-ancestor', manifest.source.commit, 'HEAD'], { cwd: root, env, encoding: 'utf8', timeout: 5000, windowsHide: true });
  if (ancestor.error || ancestor.status !== 0) throw new Error('Module source commit is not in this development branch history.');
  return manifest;
}

function newDestination(path) {
  if (existsSync(path)) throw new Error('Release version directory already exists; inspect and preserve it instead of overwriting.');
  mkdirSync(path, { recursive: true });
}

function finish(directory, metadata) {
  const artifacts = inventory(directory);
  writeJson(join(directory, 'release-manifest.json'), { schemaVersion: 1, ...metadata, artifacts });
  const withManifest = inventory(directory);
  writeFileSync(join(directory, 'SHA256SUMS.txt'), withManifest.map((file) => `${file.sha256}  ${file.file}`).join('\n') + '\n');
  console.log(JSON.stringify({ directory, tag: metadata.tag, artifacts: inventory(directory) }, null, 2));
}

function recordBuild() {
  const version = readJson(join(root, 'package.json')).version;
  const portable = workspacePath('dist/OpenDesk');
  const bundle = assertBundleDirectory(root, join(portable, 'bundled-modules'), version);
  const installer = workspacePath('apps/framework/desktop/src-tauri/target/release/bundle/nsis', `OpenDesk_${version}_x64-setup.exe`);
  writeJson(workspacePath('dist/app-build.json'), { schemaVersion: 1, version, source: { repository, ...readGitIdentity(root) }, modules: bundle.modules, portableFiles: inventory(portable), installer: fileRecord(installer) });
  console.log(`Recorded App ${version} build inventory; no release uploaded.`);
}

function stageApp() {
  const source = cleanIdentity();
  const version = readJson(join(root, 'package.json')).version;
  const portable = workspacePath('dist/OpenDesk');
  const receipt = readJson(workspacePath('dist/app-build.json'));
  if (receipt.schemaVersion !== 1 || receipt.version !== version || receipt.source.commit !== source.commit || receipt.source.dirty !== false) throw new Error('App build does not belong to the current clean source commit; rebuild.');
  assertInventory(inventory(portable), receipt.portableFiles);
  const bundle = assertBundleDirectory(root, join(portable, 'bundled-modules'), version);
  const installer = workspacePath('apps/framework/desktop/src-tauri/target/release/bundle/nsis', `OpenDesk_${version}_x64-setup.exe`);
  assertInventory([fileRecord(installer)], [receipt.installer]);
  const modules = bundle.modules.map((entry) => ({ ...entry, manifest: moduleManifest(join(portable, 'bundled-modules', entry.file), entry) }));
  const destination = workspacePath('dist/releases/app', version);
  newDestination(destination);
  const zip = join(destination, `OpenDesk_${version}_windows-x64_portable.zip`);
  const archive = python('zip', portable, zip);
  copyFileSync(installer, join(destination, receipt.installer.file));
  copyFileSync(join(portable, 'bundled-modules/app-manifest.json'), join(destination, 'app-manifest.json'));
  for (const entry of modules) copyFileSync(join(portable, 'bundled-modules', entry.file), join(destination, entry.file));
  finish(destination, { kind: 'app', version, tag: `v${version}`, target: 'windows-x64', source, portableFiles: archive.files, zipAllFileHashesVerified: archive.allFileHashesVerified, modules: modules.map(({ manifest, ...entry }) => ({ ...entry, source: manifest.source })) });
}

function stageModule(id) {
  cleanIdentity();
  const distribution = readDistribution(root);
  assertPublicModule(distribution, id);
  const template = validateManifest(readJson(join(root, 'apps/features', MODULE_DEFINITIONS[id].folder, 'manifest.json')));
  const origin = workspacePath('dist/modules', id, template.version);
  const filename = `${id}-${template.version}.ndpkg`;
  const path = join(origin, filename);
  const manifest = moduleManifest(path, { id, version: template.version, sha256: digest(path) });
  if (JSON.stringify(readJson(join(origin, 'manifest.json'))) !== JSON.stringify(manifest)) throw new Error('Standalone manifest differs from the package manifest.');
  const destination = workspacePath('dist/releases/modules', id, template.version);
  newDestination(destination);
  copyFileSync(path, join(destination, filename));
  writeJson(join(destination, 'manifest.json'), manifest);
  finish(destination, { kind: 'module', id, label: manifest.label, version: template.version, tag: `modules/${id}/v${template.version}`, target: manifest.target, hostApi: manifest.hostApi, dataSchema: manifest.dataSchema, source: manifest.source });
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  try {
    const [mode, id, ...extra] = process.argv.slice(2);
    if (extra.length || (mode === 'module' ? !id : id) || !['record', 'app', 'module'].includes(mode)) throw new Error('Usage: node scripts/release-artifacts.mjs record|app|module <id>');
    if (mode === 'record') recordBuild();
    else if (mode === 'app') stageApp();
    else stageModule(id);
  } catch (error) { console.error('Release staging failed: ' + error.message); process.exitCode = 1; }
}
