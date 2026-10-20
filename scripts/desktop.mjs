import { join } from 'node:path';
import { root, desktop, buildEnvironment, run } from './environment.mjs';

const mode = process.argv[2] || 'dev';
const env = buildEnvironment();
if (mode === 'check' || mode === 'test') {
  run('cargo', [mode, '--locked', '--manifest-path', join(desktop, 'src-tauri/Cargo.toml')], { env });
} else if (mode === 'dev' || mode === 'build') {
  run(process.execPath, [join(root, 'node_modules/@tauri-apps/cli/tauri.js'), mode, ...process.argv.slice(3), '--', '--locked'], {
    cwd: desktop, env,
  });
  if (mode === 'build') run(process.execPath, [join(root, 'scripts/package-portable.mjs')], { env });
} else {
  throw new Error('Unknown desktop operation: ' + mode);
}
