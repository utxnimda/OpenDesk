import { copyFileSync, mkdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { root, worker, workerComposition, desktop, pythonExecutable, run } from './environment.mjs';

const python = pythonExecutable();
const mode = process.argv[2];
if (mode === 'test') {
  run(python, ['-X', 'utf8', '-m', 'unittest', 'discover', '-s', join(root, 'tests/worker'), '-v'], {
    env: { ...process.env, PYTHONPATH: worker, PYTHONDONTWRITEBYTECODE: '1' },
  });
} else if (mode === 'bundle') {
  const target = 'x86_64-pc-windows-msvc';
  if (process.platform !== 'win32' || process.arch !== 'x64' ||
      (process.env.OPENDESK_TARGET && process.env.OPENDESK_TARGET !== target)) {
    throw new Error('The initial worker bundle supports Windows x64 only; cross-compilation is not supported.');
  }
  run(python, ['-X', 'utf8', '-c', [
    'import platform, struct, sys',
    "if sys.platform != 'win32' or struct.calcsize('P') != 8 or platform.machine().lower() not in ('amd64', 'x86_64') or sys.version_info < (3, 11):",
    "    raise SystemExit('Building the worker requires Windows x64 Python 3.11 or newer.')",
  ].join('\n')]);
  const config = join(root, '.cache/pyinstaller/config');
  mkdirSync(config, { recursive: true });
  run(python, ['-X', 'utf8', '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--contents-directory', '_internal',
    '--name', 'opendesk-worker', '--distpath', join(root, 'dist/worker'),
    '--workpath', join(root, '.cache/pyinstaller/work'), '--specpath', join(root, '.cache/pyinstaller/spec'),
    '--paths', root, '--paths', worker, join(workerComposition, 'freeze_entry.py')], {
    env: { ...process.env, PYINSTALLER_CONFIG_DIR: config },
  });
  const bundle = join(root, 'dist/worker/opendesk-worker');
  if (!statSync(join(bundle, 'opendesk-worker.exe')).isFile() || !statSync(join(bundle, '_internal')).isDirectory()) {
    throw new Error('The worker bundle is incomplete: expected an executable and _internal runtime directory.');
  }
  const output = join(desktop, 'src-tauri/binaries');
  mkdirSync(output, { recursive: true });
  copyFileSync(join(bundle, 'opendesk-worker.exe'), join(output, 'opendesk-worker-' + target + '.exe'));
} else {
  throw new Error('Expected test or bundle');
}
