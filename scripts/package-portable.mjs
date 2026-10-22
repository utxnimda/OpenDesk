import { copyFileSync, existsSync, lstatSync, mkdirSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { root, desktop } from './environment.mjs';
import { assertBundleDirectory } from './distribution.mjs';

// This entry packages existing build products; it never launches a build or removes files.
if (process.platform !== 'win32' || process.arch !== 'x64' ||
    (process.env.OPENDESK_TARGET && process.env.OPENDESK_TARGET !== 'x86_64-pc-windows-msvc')) {
  throw new Error('The initial portable package supports Windows x64 only.');
}
if (process.argv.length > 3) throw new Error('Usage: node scripts/package-portable.mjs [host-executable]');

const hostExecutable = process.argv[2]
  ? resolve(process.argv[2])
  : join(desktop, 'src-tauri/target/release/opendesk.exe');
const workerBundle = join(root, 'dist/worker/opendesk-worker');
const internal = join(workerBundle, '_internal');
const bundledModules = join(root, 'dist/bundled-modules');
const destination = join(root, 'dist/OpenDesk');
const files = [
  [hostExecutable, join(destination, 'OpenDesk.exe')],
  [join(workerBundle, 'opendesk-worker.exe'), join(destination, 'opendesk-worker.exe')],
];
const directories = [destination, join(destination, '_internal'), join(destination, 'bundled-modules')];

function requireFile(path, allowEmpty = false) {
  if (!existsSync(path)) throw new Error('Build output is missing: ' + path);
  const entry = lstatSync(path);
  if (!entry.isFile() || entry.isSymbolicLink() || (!allowEmpty && entry.size === 0)) {
    throw new Error('Expected a valid regular build output file: ' + path);
  }
}

function collectRuntime(source, target) {
  if (!existsSync(source)) throw new Error('Worker runtime directory is missing: ' + source);
  const entry = lstatSync(source);
  if (!entry.isDirectory() || entry.isSymbolicLink()) throw new Error('Expected a regular runtime directory: ' + source);
  for (const child of readdirSync(source, { withFileTypes: true })) {
    const sourcePath = join(source, child.name);
    const targetPath = join(target, child.name);
    if (child.isSymbolicLink()) throw new Error('Runtime links are not supported in the Windows package: ' + sourcePath);
    if (child.isDirectory()) {
      directories.push(targetPath);
      collectRuntime(sourcePath, targetPath);
    } else if (child.isFile()) {
      files.push([sourcePath, targetPath]);
    } else {
      throw new Error('Unexpected runtime filesystem entry: ' + sourcePath);
    }
  }
}

function checkDestination(path, directory) {
  const withinRoot = relative(root, path);
  if (!withinRoot || withinRoot.startsWith('..' + sep) || withinRoot === '..' || isAbsolute(withinRoot)) {
    throw new Error('Package destination must stay inside the project directory.');
  }
  const segments = withinRoot.split(sep);
  let current = root;
  for (let index = 0; index < segments.length; index++) {
    current = join(current, segments[index]);
    const entry = lstatSync(current, { throwIfNoEntry: false });
    if (!entry) continue;
    if (entry.isSymbolicLink()) throw new Error('Refusing to write through a destination link: ' + current);
    const expectedDirectory = index < segments.length - 1 || directory;
    if (expectedDirectory ? !entry.isDirectory() : !entry.isFile()) {
      throw new Error('Package destination has an incompatible entry: ' + current);
    }
  }
}

function checkExistingPackage() {
  const allowedFiles = new Set(files.map(([, target]) => resolve(target).toLowerCase()));
  const allowedDirectories = new Set(directories.map((path) => resolve(path).toLowerCase()));
  const unknown = [];
  let existingFiles = 0;
  let existingDirectories = 0;
  function inspect(directory) {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name);
      const key = resolve(path).toLowerCase();
      if (entry.isSymbolicLink()) {
        unknown.push(relative(destination, path) + ' [link]');
      } else if (entry.isDirectory() && allowedDirectories.has(key)) {
        existingDirectories++;
        inspect(path);
      } else if (entry.isFile() && allowedFiles.has(key)) {
        existingFiles++;
      } else {
        unknown.push(relative(destination, path) + (entry.isDirectory() ? '/' : ''));
      }
    }
  }
  if (existsSync(destination)) inspect(destination);
  if (unknown.length) {
    const listed = unknown.slice(0, 20).map((path) => '  - ' + path).join('\n');
    const extra = unknown.length > 20 ? '\n  ... and ' + (unknown.length - 20) + ' more entries.' : '';
    throw new Error('Portable output contains entries outside this build manifest:\n' + listed + extra +
      '\nMove this output directory aside to start a fresh package, or manually clean the listed entries. No files were modified.');
  }
  return { existingFiles, existingDirectories };
}

// Complete validation before copying anything, so missing build products cannot
// leave a partial package. Unknown destination entries reject the operation;
// existing user data is never silently deleted or carried into a new release.
requireFile(hostExecutable);
requireFile(join(workerBundle, 'opendesk-worker.exe'));
collectRuntime(internal, join(destination, '_internal'));
requireFile(join(bundledModules, 'app-manifest.json'));
assertBundleDirectory(root, bundledModules, JSON.parse(readFileSync(join(root, 'package.json'), 'utf8')).version);
collectRuntime(bundledModules, join(destination, 'bundled-modules'));
requireFile(join(internal, 'base_library.zip'));
const pythonRuntime = readdirSync(internal).find((name) => /^python3\d+\.dll$/i.test(name));
if (!pythonRuntime) {
  throw new Error('Worker runtime is incomplete: Python runtime DLL is missing.');
}
requireFile(join(internal, pythonRuntime));
for (const [source, target] of files) {
  requireFile(source, true);
  checkDestination(target, false);
}
for (const directory of directories) checkDestination(directory, true);
const existing = checkExistingPackage();
console.log('Validated copy plan: OpenDesk.exe, opendesk-worker.exe, _internal/ (' + (files.length - 2) + ' runtime files).');
console.log('Existing output matches this manifest: ' + existing.existingFiles + ' files, ' + existing.existingDirectories + ' directories.');

for (const directory of directories) mkdirSync(directory, { recursive: true });
for (const [source, target] of files) {
  mkdirSync(dirname(target), { recursive: true });
  copyFileSync(source, target);
}
console.log('Portable package written to ' + destination + ' (' + files.length + ' files).');
