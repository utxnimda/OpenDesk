import fs from 'node:fs';
import { dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { randomUUID } from 'node:crypto';

const MANIFEST = 'config/version.json';
const DESKTOP = 'apps/framework/desktop/src-tauri';
const TYPES = new Set(['added', 'changed', 'fixed', 'breaking']);
const TYPE_LABELS = { added: '新增', changed: '调整', fixed: '修复', breaking: '不兼容变更' };
const ID = /^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$/;
const SERIALIZE = (value) => JSON.stringify(value, null, 2) + '\n';

export class VersionError extends Error {}

function requireThat(condition, message) {
  if (!condition) throw new VersionError(message);
}

function object(value, keys, required, label) {
  requireThat(value !== null && typeof value === 'object' && !Array.isArray(value), `${label} 必须是对象。`);
  requireThat(Object.keys(value).every((key) => keys.includes(key)), `${label} 包含未知字段。`);
  requireThat(required.every((key) => Object.hasOwn(value, key)), `${label} 缺少必填字段。`);
}

function text(value, label, limit = 1000) {
  requireThat(typeof value === 'string' && value.trim() === value && value.length > 0 && value.length <= limit
    && !/[\x00-\x1f\x7f]/.test(value), `${label} 必须是非空单行文本。`);
}

function semver(value, label) {
  requireThat(typeof value === 'string' && /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(value), `${label} 必须使用正式版本 major.minor.patch。`);
  const parts = value.split('.').map(Number);
  requireThat(parts.every(Number.isSafeInteger), `${label} 版本数值超出范围。`);
  return parts;
}

function bumpKind(previous, next, label) {
  const [a, b, c] = semver(previous, label);
  const [x, y, z] = semver(next, label);
  if (x === a && y === b && z === c + 1) return 1;
  if (x === a && y === b + 1 && z === 0) return 2;
  if (x === a + 1 && y === 0 && z === 0) return 3;
  throw new VersionError(`${label} 必须严格提升一个 patch、minor 或 major，不能重复、回退或跳档：${previous} → ${next}。`);
}

function feature(value) {
  object(value, ['id', 'name', 'module'], ['id', 'name', 'module'], '功能定义');
  requireThat(typeof value.id === 'string' && value.id.length <= 100 && ID.test(value.id), '功能 ID 必须是稳定的小写字母、数字、点或连字符标识。');
  text(value.name, '功能名称', 120);
  text(value.module, '功能模块', 160);
}

/** Validate the complete history by replaying it from oldest to newest. */
export function validateVersionManifest(manifest) {
  object(manifest, ['schemaVersion', 'features', 'releases'], ['schemaVersion', 'features', 'releases'], '版本清单');
  requireThat(manifest.schemaVersion === 1, '不支持该版本清单 schemaVersion。');
  requireThat(Array.isArray(manifest.features) && manifest.features.length > 0, '功能列表不能为空。');
  requireThat(Array.isArray(manifest.releases) && manifest.releases.length > 0, '发布历史不能为空。');
  const known = new Set();
  for (const entry of manifest.features) {
    feature(entry);
    requireThat(!known.has(entry.id), `功能 ID 重复：${entry.id}。`);
    known.add(entry.id);
  }
  const current = new Map();
  let previous;
  for (const release of [...manifest.releases].reverse()) {
    object(release, ['version', 'date', 'summary', 'changes'], ['version', 'date', 'summary', 'changes'], '发布记录');
    semver(release.version, '应用');
    requireThat(typeof release.date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(release.date)
      && Number.isFinite(Date.parse(release.date)) && new Date(release.date).toISOString().slice(0, 10) === release.date, '发布日期必须是有效的 YYYY-MM-DD。');
    text(release.summary, '发布摘要');
    requireThat(Array.isArray(release.changes) && release.changes.length > 0, `发布 ${release.version} 的 changes 不能为空。`);
    const seen = new Set();
    let severity = 1;
    for (const change of release.changes) {
      object(change, ['featureId', 'version', 'type', 'description'], ['featureId', 'version', 'type', 'description'], '功能变更');
      requireThat(known.has(change.featureId), `变更引用未知功能：${change.featureId}。`);
      requireThat(!seen.has(change.featureId), `发布 ${release.version} 内功能重复：${change.featureId}。`);
      seen.add(change.featureId);
      requireThat(TYPES.has(change.type), `功能 ${change.featureId} 的变更 type 无效。`);
      semver(change.version, `功能 ${change.featureId}`);
      text(change.description, '功能变更说明', 2000);
      const last = current.get(change.featureId);
      if (!last) {
        requireThat(change.version === '0.1.0' && change.type === 'added', `功能 ${change.featureId} 首次发布必须是 added 0.1.0。`);
      } else {
        requireThat(change.type !== 'added', `已有功能 ${change.featureId} 不能再次 added，请使用 changed、fixed 或 breaking。`);
        const kind = bumpKind(last.version, change.version, `功能 ${change.featureId}`);
        const valid = change.type === 'fixed' ? kind === 1
          : change.type === 'breaking' ? kind === 3 || (last.version.startsWith('0.') && kind === 2)
          : kind === 2;
        requireThat(valid, `功能 ${change.featureId} 的 ${change.type} 与版本涨幅不匹配。`);
      }
      severity = Math.max(severity, change.type === 'fixed' ? 1 : change.type === 'breaking' ? 3 : 2);
      current.set(change.featureId, { version: change.version, updatedIn: release.version });
    }
    if (previous) {
      requireThat(release.date >= previous.date, '发布记录必须按版本和日期从新到旧排列。');
      const kind = bumpKind(previous.version, release.version, '应用');
      const minimum = severity === 3 && previous.version.startsWith('0.') ? 2 : severity;
      requireThat(kind >= minimum, `应用 ${release.version} 涨幅不足以覆盖本次功能变更。`);
    }
    previous = release;
  }
  requireThat([...known].every((id) => current.has(id)), '功能定义缺少首次发布记录。');
  return manifest;
}

function pathInRoot(root, name, { optional = false } = {}) {
  const base = fs.realpathSync(resolve(root));
  const target = join(base, name);
  const parent = fs.realpathSync(dirname(target));
  const candidate = fs.existsSync(target) ? fs.realpathSync(target) : join(parent, target.slice(dirname(target).length + 1));
  const rel = relative(base, candidate);
  requireThat(rel !== '..' && !rel.startsWith('..' + sep) && !isAbsolute(rel), `版本文件越过工程目录：${name}。`);
  requireThat(parent === dirname(target), `版本文件目录不能通过链接跳转：${name}。`);
  if (!fs.existsSync(target)) {
    requireThat(optional, `缺少版本文件：${name}。`);
  } else {
    requireThat(!fs.lstatSync(target).isSymbolicLink() && fs.statSync(target).isFile(), `版本文件必须是普通文件：${name}。`);
  }
  return target;
}

function readJsonFile(path, label) {
  requireThat(fs.statSync(path).size <= 4 * 1024 * 1024, `${label} 文件过大。`);
  try { return JSON.parse(fs.readFileSync(path, 'utf8').replace(/^\uFEFF/, '')); }
  catch (error) { throw new VersionError(`${label} 不是有效 JSON：${error.message}`); }
}

export function readVersionManifest(root) {
  return validateVersionManifest(readJsonFile(pathInRoot(root, MANIFEST), MANIFEST));
}

export function versionSummary(manifest) {
  validateVersionManifest(manifest);
  const history = new Map();
  for (const release of [...manifest.releases].reverse()) {
    for (const change of release.changes) history.set(change.featureId, { version: change.version, updatedIn: release.version });
  }
  const latest = manifest.releases[0];
  return { appVersion: latest.version, date: latest.date, summary: latest.summary,
    features: manifest.features.map((entry) => ({ ...entry, ...history.get(entry.id) })) };
}

export function releaseTags(manifest) {
  validateVersionManifest(manifest);
  const latest = manifest.releases[0];
  return [`v${latest.version}`, ...latest.changes.map((change) => `feature/${change.featureId}/v${change.version}`)];
}

function markdown(value) { return value.replace(/[\\`*_{}\[\]<>#|]/g, '\\$&'); }

export function renderChangelog(manifest) {
  validateVersionManifest(manifest);
  const names = new Map(manifest.features.map((entry) => [entry.id, entry.name]));
  const lines = ['# 更新记录', '', '<!-- Generated from config/version.json by scripts/version.mjs. Do not edit directly. -->', ''];
  for (const release of manifest.releases) {
    lines.push(`## ${release.version} — ${release.date}`, '', markdown(release.summary), '');
    for (const change of release.changes) lines.push(`- **${markdown(names.get(change.featureId))} ${change.version}**（${TYPE_LABELS[change.type]}，\`${change.featureId}\`）：${markdown(change.description)}`);
    lines.push('');
  }
  return lines.join('\n');
}

function ownTomlVersion(textValue, filename, version, lock = false) {
  const blocks = lock ? textValue.split(/(?=^\[\[package\]\]\s*$)/m) : textValue.split(/(?=^\[[^\]]+\]\s*$)/m);
  const indices = blocks.map((block, index) => ({ block, index })).filter(({ block }) =>
    (lock ? /^\[\[package\]\]/ : /^\[package\]/).test(block)
    && /^name\s*=\s*"nimdadesk"\s*$/m.test(block));
  requireThat(indices.length === 1, `${filename} 必须包含唯一的 nimdadesk package。`);
  const { block, index } = indices[0];
  const matches = [...block.matchAll(/^version\s*=\s*"([^"]+)"\s*$/gm)];
  requireThat(matches.length === 1, `${filename} 自身版本字段无效。`);
  semver(matches[0][1], filename);
  blocks[index] = block.replace(/^(version\s*=\s*")[^"]+("\s*)$/m, `$1${version}$2`);
  return blocks.join('');
}

/** Precompute every replacement, including parsed lockfiles, before any write. */
function updatesFor(root, manifest, includeManifest = false) {
  validateVersionManifest(manifest);
  const version = manifest.releases[0].version;
  const updates = [];
  function add(name, transform, optional = false) {
    const path = pathInRoot(root, name, { optional });
    const before = fs.existsSync(path) ? fs.readFileSync(path) : null;
    const after = Buffer.from(transform(before?.toString('utf8') ?? ''));
    if (before === null || !before.equals(after)) updates.push({ name, path, before, after });
  }
  function jsonVersion(name, validate, change) {
    add(name, (raw) => {
      let value;
      try { value = JSON.parse(raw.replace(/^\uFEFF/, '')); }
      catch { throw new VersionError(`${name} 不是有效 JSON。`); }
      validate(value);
      const original = JSON.stringify(value);
      change(value);
      return JSON.stringify(value) === original ? raw : SERIALIZE(value);
    });
  }
  jsonVersion('package.json', (value) => {
    requireThat(value?.name === 'nimdadesk', 'package.json 名称不是 nimdadesk。');
    semver(value.version, 'package.json');
  }, (value) => { value.version = version; });
  jsonVersion('package-lock.json', (value) => {
    requireThat(value?.name === 'nimdadesk' && [2, 3].includes(value.lockfileVersion)
      && value.packages?.['']?.name === 'nimdadesk', 'package-lock.json 根 package 结构无效。');
    semver(value.version, 'package-lock.json');
    semver(value.packages[''].version, 'package-lock.json packages[""]');
  }, (value) => { value.version = version; value.packages[''].version = version; });
  jsonVersion(`${DESKTOP}/tauri.conf.json`, (value) => {
    requireThat(value?.productName === 'NimdaDesk', 'tauri.conf.json 应用名称无效。');
    semver(value.version, 'tauri.conf.json');
  }, (value) => { value.version = version; });
  for (const [name, lock] of [['Cargo.toml', false], ['Cargo.lock', true]]) {
    add(`${DESKTOP}/${name}`, (raw) => ownTomlVersion(raw, name, version, lock));
  }
  add('CHANGELOG.md', () => renderChangelog(manifest), true);
  if (includeManifest) add(MANIFEST, () => SERIALIZE(manifest));
  return updates;
}

/** Stage all files, then replace; on failure restore exact original bytes. */
function writeUpdates(updates) {
  const staged = [];
  const applied = [];
  const nonce = randomUUID();
  try {
    for (const update of updates) {
      const now = fs.existsSync(update.path) ? fs.readFileSync(update.path) : null;
      requireThat(now === null ? update.before === null : update.before !== null && now.equals(update.before), `版本文件已被其他操作修改：${update.name}。`);
      fs.accessSync(dirname(update.path), fs.constants.W_OK);
      if (update.before !== null) fs.accessSync(update.path, fs.constants.W_OK);
    }
    for (const update of updates) {
      const temp = `${update.path}.version-${nonce}.tmp`;
      staged.push(temp);
      fs.writeFileSync(temp, update.after, { flag: 'wx' });
    }
    for (let index = 0; index < updates.length; index++) {
      const update = updates[index];
      const now = fs.existsSync(update.path) ? fs.readFileSync(update.path) : null;
      requireThat(now === null ? update.before === null : update.before !== null && now.equals(update.before), `版本文件已被其他操作修改：${update.name}。`);
      fs.renameSync(staged[index], update.path);
      applied.push(update);
    }
  } catch (error) {
    const rollbackErrors = [];
    for (const update of applied.reverse()) {
      try {
        if (update.before === null) fs.unlinkSync(update.path);
        else fs.writeFileSync(update.path, update.before);
      } catch (rollbackError) { rollbackErrors.push(`${update.name}: ${rollbackError.message}`); }
    }
    if (rollbackErrors.length) throw new VersionError(`版本同步失败且恢复未完成：${rollbackErrors.join('; ')}；原错误：${error.message}`);
    throw error;
  } finally {
    for (const path of staged) if (fs.existsSync(path)) fs.unlinkSync(path);
  }
  return updates.map((entry) => entry.name);
}

export function checkVersions(root) {
  const manifest = readVersionManifest(root);
  const changes = updatesFor(root, manifest);
  requireThat(changes.length === 0, `版本文件与清单不一致，请运行 version sync：${changes.map((entry) => entry.name).join(', ')}。`);
  return versionSummary(manifest);
}

export function syncVersions(root) {
  const manifest = readVersionManifest(root);
  return { ...versionSummary(manifest), changedFiles: writeUpdates(updatesFor(root, manifest)) };
}

export function prepareRelease(root, draft) {
  object(draft, ['version', 'date', 'summary', 'features', 'changes'], ['version', 'date', 'summary', 'changes'], '发布草稿');
  if (draft.features !== undefined) requireThat(Array.isArray(draft.features), '草稿 features 必须是新增功能数组。');
  // Release preparation starts from an already consistent checkout. Use sync to
  // repair deliberate manifest edits before preparing the next release.
  checkVersions(root);
  const previous = readVersionManifest(root);
  const { features = [], ...release } = draft;
  const manifest = { ...previous, features: [...previous.features, ...features], releases: [release, ...previous.releases] };
  validateVersionManifest(manifest);
  const changedFiles = writeUpdates(updatesFor(root, manifest, true));
  return { ...versionSummary(manifest), changedFiles };
}

export function readReleaseDraft(path) { return readJsonFile(resolve(path), '发布草稿'); }
