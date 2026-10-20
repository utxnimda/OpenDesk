import { existsSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

export const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
export const desktop = join(root, 'apps/framework/desktop');
export const worker = join(root, 'apps/framework/worker');
export const workerComposition = join(root, 'apps/composition/worker');

export function localConfig() {
  const path = join(root, '.local/dev.json');
  return existsSync(path) ? JSON.parse(readFileSync(path, 'utf8')) : {};
}

export function pythonExecutable() {
  const configured = process.env.OPENDESK_PYTHON || localConfig().python;
  if (configured) {
    if (!existsSync(configured)) throw new Error('Configured Python executable does not exist.');
    return configured;
  }
  throw new Error('Python 3.11+ is required. Set OPENDESK_PYTHON or .local/dev.json { "python": "absolute path" }.');
}

export function buildEnvironment() {
  const env = { ...process.env };
  const cargoHome = join(root, '.tools/cargo');
  if (existsSync(join(cargoHome, 'bin/cargo.exe'))) {
    env.CARGO_HOME = cargoHome;
    env.RUSTUP_HOME = join(root, '.tools/rustup');
    const pathKey = Object.keys(env).find((key) => key.toLowerCase() === 'path') || 'PATH';
    env[pathKey] = join(cargoHome, 'bin') + ';' + (env[pathKey] || '');
  }
  env.OPENDESK_PYTHON = pythonExecutable();
  env.OPENDESK_WORKER_SOURCE = workerComposition;
  return env;
}

export function run(command, args, options = {}) {
  const result = spawnSync(command, args, { cwd: root, stdio: 'inherit', windowsHide: true, ...options });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(command + ' failed with exit code ' + result.status);
}

export function npm(args, options = {}) {
  // npm_execpath is supplied by npm scripts; invoke its JS entry without a shell.
  const cli = process.env.npm_execpath;
  if (!cli) throw new Error('Run this entry through npm.');
  run(process.execPath, [cli, ...args], options);
}
