import { spawnSync } from 'node:child_process';
import { root, buildEnvironment } from './environment.mjs';
console.log('OpenDesk workspace: ' + root);
let failed = false;
try {
  const env = buildEnvironment();
  for (const [name, command] of [['Node', process.execPath], ['Python', env.OPENDESK_PYTHON], ['Rust', 'rustc'], ['Cargo', 'cargo']]) {
    const result = spawnSync(command, ['--version'], { env, encoding: 'utf8', windowsHide: true });
    if (result.status !== 0) { failed = true; console.error(name + ': unavailable'); }
    else console.log(name + ': ' + result.stdout.trim());
  }
} catch (error) { failed = true; console.error(error.message); }
console.log('Windows desktop also requires MSVC C++ build tools and WebView2 Runtime.');
process.exitCode = failed ? 1 : 0;
