import { join, delimiter } from 'node:path';
import { root, pythonExecutable, run } from '../../scripts/environment.mjs';
run(process.execPath, [join(root, 'node_modules/vue-tsc/bin/vue-tsc.js'), '--noEmit', '-p', join(root, 'plugins/ai-workbench/tsconfig.json')]);
run(process.execPath, [join(root, 'node_modules/vitest/vitest.mjs'), 'run', '--config', join(root, 'plugins/ai-workbench/vitest.config.ts')]);
run(pythonExecutable(), ['-X', 'utf8', '-m', 'unittest', 'discover', '-s', join(root, 'plugins/ai-workbench/tests'), '-p', 'test_*.py', '-v'], {
  env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1', PYTHONPATH: [join(root,'plugins/ai-workbench/backend'),join(root,'apps/framework/worker')].join(delimiter) },
});
