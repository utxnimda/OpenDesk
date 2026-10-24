/** Explicit distribution boundary shared by package builds and release staging. */
import { createHash } from 'node:crypto';
import { existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';

export const MODULE_DEFINITIONS = Object.freeze({
  'tower-assistant': Object.freeze({ folder: 'tower_assistant', backend: 'apps.features.tower_assistant.backend.service', marker: 'tower-baseline', testFixture: false }),
  'test-lab': Object.freeze({ folder: 'test_lab', backend: 'apps.features.test_lab.backend.service', marker: 'v1-baseline', testFixture: true }),
  'nimda-workspace': Object.freeze({ sourcePath: 'plugins/media-manager/integrations/opendesk', testFixture: false,
    externalBuilder: Object.freeze({ script: 'build.mjs', projectPath: 'plugins/media-manager', workDirectory: '.cache/media-manager-build',
      pythonEnv: 'OPENDESK_MEDIA_MANAGER_PYTHON', pythonEnvAliases: Object.freeze(['OPENDESK_WORK_MANAGER_PYTHON']),
      pythonConfig: 'mediaManagerPython', pythonConfigAliases: Object.freeze(['workManagerPython']) }) }),
});
// CLI names may change independently of installed package identities.
export const MODULE_BUILD_ALIASES = Object.freeze({ 'media-manager': 'nimda-workspace' });
const SEMVER = /^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$/;
const DIGEST = /^[a-f0-9]{64}$/;
const plainObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const exactKeys = (value, keys) => plainObject(value) && Object.keys(value).sort().join('|') === [...keys].sort().join('|');
export const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');

export function validateDistribution(value) {
  if (!exactKeys(value, ['schemaVersion', 'bundledModules', 'publicModules']) || value.schemaVersion !== 1) throw new Error('Distribution must use schemaVersion 1 and explicit bundledModules/publicModules lists.');
  for (const field of ['bundledModules', 'publicModules']) {
    if (!Array.isArray(value[field]) || new Set(value[field]).size !== value[field].length) throw new Error(`Distribution ${field} must be an array without duplicates.`);
    for (const id of value[field]) {
      if (typeof id !== 'string' || !Object.hasOwn(MODULE_DEFINITIONS, id)) throw new Error(`Unknown distribution module in ${field}: ${String(id)}`);
      if (MODULE_DEFINITIONS[id].testFixture) throw new Error(`Test fixture ${id} cannot be bundled or publicly distributed.`);
    }
  }
  if (value.bundledModules.some((id) => !value.publicModules.includes(id))) throw new Error('Bundled modules must be a subset of publicModules.');
  return Object.freeze({ schemaVersion: 1, bundledModules: Object.freeze([...value.bundledModules]), publicModules: Object.freeze([...value.publicModules]) });
}

export function readDistribution(projectRoot) {
  return validateDistribution(JSON.parse(readFileSync(join(projectRoot, 'config/distribution.json'), 'utf8')));
}

export function moduleSourceDirectory(projectRoot, id) {
  if (!Object.hasOwn(MODULE_DEFINITIONS, id)) throw new Error('Unknown build source module.');
  const definition = MODULE_DEFINITIONS[id];
  return join(projectRoot, definition.sourcePath ?? join('apps/features', definition.folder));
}

export function assertPublicModule(distribution, id) {
  validateDistribution(distribution);
  if (!distribution.publicModules.includes(id)) throw new Error(`Module ${String(id)} is not in the public distribution allowlist.`);
  return MODULE_DEFINITIONS[id];
}

export function parseModulePackArgs(args, distribution) {
  const [mode, ...extra] = args;
  if (mode === 'module' && extra.length === 1) {
    const moduleId = Object.hasOwn(MODULE_BUILD_ALIASES, extra[0]) ? MODULE_BUILD_ALIASES[extra[0]] : extra[0];
    assertPublicModule(distribution, moduleId);
    return { mode, moduleId };
  }
  if (['bundled', 'fixtures', 'ui-failure-fixture'].includes(mode) && extra.length === 0) return { mode };
  throw new Error('Usage: node scripts/module-pack.mjs bundled|fixtures|ui-failure-fixture|module <public-module-id-or-alias>');
}

function assertSafeName(name) {
  if (typeof name !== 'string' || !name || name === '.' || name === '..' || /[\\/:\x00-\x1f]/.test(name) || /[. ]$/.test(name)) throw new Error('Artifact names must be ordinary file basenames.');
}

/** Refuse linked ancestors even if the immediate output directory is ordinary. */
export function assertUnlinkedPath(path) {
  const absolute = resolve(path);
  let current = absolute;
  while (true) {
    const entry = lstatSync(current, { throwIfNoEntry: false });
    if (entry?.isSymbolicLink()) throw new Error('Linked artifact paths are not allowed: ' + current);
    const parent = dirname(current);
    if (parent === current) break;
    current = parent;
  }
  return absolute;
}

/** Preflight before any builds or writes. Unknown content is preserved for review. */
export function assertOutputDirectory(directory, allowedNames) {
  assertUnlinkedPath(directory);
  const allowed = new Set(allowedNames);
  for (const name of allowed) assertSafeName(name);
  if (!existsSync(directory)) return;
  if (!lstatSync(directory).isDirectory()) throw new Error('Artifact output is not a directory: ' + directory);
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    if (!allowed.has(entry.name) || !entry.isFile() || entry.isSymbolicLink()) throw new Error(`Unexpected artifact content: ${join(directory, entry.name)}. Move it out before rebuilding; nothing was deleted.`);
  }
}

/** No replacement of different bytes, including sidecars and a concurrent writer. */
export function writeImmutableFiles(directory, files) {
  assertUnlinkedPath(directory);
  const entries = Object.entries(files).map(([name, bytes]) => {
    assertSafeName(name);
    return [name, Buffer.isBuffer(bytes) ? bytes : Buffer.from(bytes)];
  });
  const compare = (name, bytes) => {
    const path = join(directory, name);
    const existing = lstatSync(path, { throwIfNoEntry: false });
    if (!existing) return false;
    if (!existing.isFile() || existing.isSymbolicLink() || !readFileSync(path).equals(bytes)) throw new Error(`Immutable artifact conflict: ${path}. Existing bytes were preserved; use a new module version or move the reviewed old output aside.`);
    return true;
  };
  for (const [name, bytes] of entries) compare(name, bytes);
  mkdirSync(directory, { recursive: true });
  for (const [name, bytes] of entries) {
    if (compare(name, bytes)) continue;
    try { writeFileSync(join(directory, name), bytes, { flag: 'wx' }); }
    catch (error) { if (error.code !== 'EEXIST' || !compare(name, bytes)) throw error; }
  }
}

/** Validate the exact portable resource directory, not just its declared entries. */
export function assertBundleDirectory(projectRoot, directory, expectedHostVersion) {
  const distribution = readDistribution(projectRoot);
  assertUnlinkedPath(directory);
  const indexPath = join(directory, 'app-manifest.json');
  const indexEntry = lstatSync(indexPath, { throwIfNoEntry: false });
  if (!indexEntry?.isFile() || indexEntry.isSymbolicLink()) throw new Error('Bundled app-manifest.json must be an ordinary file.');
  const catalog = JSON.parse(readFileSync(indexPath, 'utf8'));
  if (!exactKeys(catalog, ['schemaVersion', 'hostVersion', 'modules']) || catalog.schemaVersion !== 1 || !SEMVER.test(catalog.hostVersion) || catalog.hostVersion !== expectedHostVersion || !Array.isArray(catalog.modules)) throw new Error('Bundled catalog does not match the expected host version or schema.');
  const seen = new Set();
  for (const entry of catalog.modules) {
    if (!exactKeys(entry, ['id', 'version', 'file', 'sha256', 'defaultEnabled']) || !distribution.bundledModules.includes(entry.id) || seen.has(entry.id) || typeof entry.version !== 'string' || !SEMVER.test(entry.version) || entry.file !== `${entry.id}-${entry.version}.ndpkg` || typeof entry.sha256 !== 'string' || !DIGEST.test(entry.sha256) || typeof entry.defaultEnabled !== 'boolean') throw new Error('Unexpected, duplicated or malformed bundled module entry.');
    seen.add(entry.id);
  }
  if (seen.size !== distribution.bundledModules.length) throw new Error('Bundled catalog is missing an allowed default module.');
  assertOutputDirectory(directory, ['app-manifest.json', ...catalog.modules.map((entry) => entry.file)]);
  for (const entry of catalog.modules) {
    const path = join(directory, entry.file);
    if (!existsSync(path) || sha256(readFileSync(path)) !== entry.sha256) throw new Error('Bundled module digest mismatch or missing archive: ' + entry.file);
  }
  return catalog;
}
