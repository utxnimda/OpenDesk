/** Build the media-management module from this OpenDesk Git source tree. */
import { createHash, randomUUID } from 'node:crypto';
import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
import { spawnSync } from 'node:child_process';

const integration = dirname(fileURLToPath(import.meta.url));
function copyTree(source, destination) {
  mkdirSync(destination, { recursive: true });
  for (const entry of readdirSync(source, { withFileTypes: true })) {
    if (entry.name === '__pycache__') continue;
    if (entry.isSymbolicLink()) throw new Error('Linked module resources are unsupported.');
    if (entry.isDirectory()) copyTree(join(source, entry.name), join(destination, entry.name));
    else if (entry.isFile()) copyFileSync(join(source, entry.name), join(destination, entry.name));
    else throw new Error('Only ordinary files may be packaged.');
  }
}
export function parseBuildArgs(args) {
  const options = Object.create(null);
  for (let i = 0; i < args.length; i += 2) {
    const key = args[i];
    const value = args[i + 1];
    if (!['--opendesk', '--project', '--nimda', '--python', '--output', '--work'].includes(key) ||
        typeof value !== 'string' || !value.trim() || value.startsWith('--') || Object.hasOwn(options, key)) {
      throw new Error('Usage: node build.mjs --opendesk <host source> --python <build Python> [--project <application source>] [--output <directory>] [--work <directory>]. --nimda remains a compatibility alias for --project.');
    }
    options[key] = resolve(value);
  }
  if (!options['--opendesk'] || !options['--python']) throw new Error('--opendesk and --python are required.');
  if (options['--project'] && options['--nimda'] && options['--project'] !== options['--nimda']) {
    throw new Error('--project conflicts with the compatibility --nimda source path.');
  }
  return { host: options['--opendesk'], project: options['--project'] ?? options['--nimda'] ?? resolve(integration, '../..'),
    python: options['--python'], output: options['--output'], work: options['--work'] };
}

async function main() {
  const options = parseBuildArgs(process.argv.slice(2));
  if (process.platform !== 'win32' || process.arch !== 'x64') throw new Error('Windows x64 build required.');
  const { host, project, python } = options;
  const manifest = JSON.parse(readFileSync(join(integration, 'manifest.json'), 'utf8'));
  const output = options.output || join(host, 'dist/modules', manifest.id, manifest.version);
  const { validateManifest, sanitizeRepository } = await import(pathToFileURL(join(host, 'scripts/module-pack.mjs')).href);
  const { readGitIdentity } = await import(pathToFileURL(join(host, 'scripts/build-version.mjs')).href);
  const { writeImmutableFiles } = await import(pathToFileURL(join(host, 'scripts/distribution.mjs')).href);
  validateManifest(manifest);
  if (manifest.id !== 'nimda-workspace' || manifest.hostApi !== 2 || manifest.webApp !== true) throw new Error('Invalid media-management module identity.');
  const finalFile = join(output, `${manifest.id}-${manifest.version}.ndpkg`);
  if (existsSync(finalFile)) throw new Error('This module version already has an artifact. Preserve it and increment manifest.version for a changed build.');
  const work = join(options.work || join(host, '.cache/media-manager-build'), randomUUID().slice(0, 8));
  const packageRoot = join(work, 'package');
  mkdirSync(packageRoot, { recursive: true });
  const pythonSource = join(work, 'python');
  copyTree(join(integration, 'nimda_opendesk'), join(pythonSource, 'nimda_opendesk'));
  copyFileSync(join(integration, 'worker_entry.py'), join(pythonSource, 'worker_entry.py'));
  writeFileSync(join(pythonSource, 'nimda_opendesk/_build_identity.py'),
    `IDENTITY = ${JSON.stringify({ id: manifest.id, version: manifest.version, hostApi: manifest.hostApi })}\n`);
  const run = (command, args, env = {}) => {
    const result = spawnSync(command, args, { cwd: project, stdio: 'inherit', windowsHide: true, env: { ...process.env, ...env } });
    if (result.error || result.status !== 0) throw result.error || new Error(`${command} failed (${result.status}).`);
  };
  const typecheck = join(work, 'tsconfig.json');
  writeFileSync(typecheck, JSON.stringify({
    compilerOptions: { target: 'ES2022', module: 'ESNext', moduleResolution: 'Bundler',
      lib: ['ES2022', 'DOM', 'DOM.Iterable'], strict: true, noEmit: true, skipLibCheck: true, types: [],
      paths: { '@opendesk/module-sdk': [join(host, 'apps/framework/module-sdk/src/index.ts')] } },
    include: [join(integration, 'frontend/main.ts'), join(host, 'apps/framework/module-sdk/src/env.d.ts')],
  }));
  run(process.execPath, [join(host, 'node_modules/typescript/bin/tsc'), '--project', typecheck]);
  const gitEnv = { ...process.env };
  for (const key of ['GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR']) delete gitEnv[key];
  const origin = spawnSync('git', ['remote', 'get-url', 'origin'], { cwd: host, env: gitEnv, windowsHide: true, encoding: 'utf8', timeout: 5000 });
  const source = readGitIdentity(host);
  manifest.source = { commit: source.commit, dirty: source.dirty !== false,
    repository: origin.status === 0 ? sanitizeRepository(origin.stdout.trim()) : null };
  // The main repository owns code provenance. Real workspace config/data never enter the package.
  const requireHost = createRequire(join(host, 'package.json'));
  const { build } = await import(pathToFileURL(requireHost.resolve('vite')).href);
  await build({
    configFile: false, root: join(integration, 'frontend'), base: './', envDir: false,
    resolve: { alias: { '@opendesk/module-sdk': join(host, 'apps/framework/module-sdk/src/index.ts') } },
    define: { __MODULE_ID__: JSON.stringify(manifest.id), __MODULE_VERSION__: JSON.stringify(manifest.version) },
    build: { outDir: join(packageRoot, 'frontend'), emptyOutDir: true, target: 'es2022', sourcemap: false },
  });
  run(python, ['-X', 'utf8', '-m', 'PyInstaller', '--noconfirm', '--clean', '--distpath', join(work, 'frozen'), '--workpath', join(work, 'pyinstaller'), join(integration, 'module-worker.spec')], {
    MEDIA_MANAGER_MODULE_SOURCE: project, MEDIA_MANAGER_MODULE_INTEGRATION: integration,
    MEDIA_MANAGER_MODULE_PYTHON_SOURCE: pythonSource,
    PYINSTALLER_CONFIG_DIR: join(work, 'cache'), SOURCE_DATE_EPOCH: '1577836800', PYTHONHASHSEED: '0',
  });
  const frozen = join(work, 'frozen/module-worker');
  run(python, ['-X', 'utf8', join(integration, 'tests/frozen_smoke.py'), join(frozen, 'module-worker.exe'), manifest.version]);
  copyTree(frozen, join(packageRoot, 'worker'));
  writeFileSync(join(packageRoot, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
  const archive = join(work, 'artifact.ndpkg');
  run(python, ['-X', 'utf8', join(host, 'scripts/module-pack-archive.py'), packageRoot, archive]);
  const bytes = readFileSync(archive);
  if (bytes.length > 128 * 1024 * 1024) throw new Error('Module exceeds the host package size limit.');
  const digest = createHash('sha256').update(bytes).digest('hex');
  mkdirSync(output, { recursive: true });
  writeImmutableFiles(output, {
    [`${manifest.id}-${manifest.version}.ndpkg`]: bytes,
    'manifest.json': JSON.stringify(manifest, null, 2) + '\n',
    SHA256SUMS: `${digest}  ${manifest.id}-${manifest.version}.ndpkg\n`,
  });
  console.log(JSON.stringify({ file: finalFile, sha256: digest, bytes: bytes.length, work }, null, 2));
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => { console.error('Media manager module build failed: ' + error.message); process.exitCode = 1; });
}
