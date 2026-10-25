import { npm } from './environment.mjs';
for (const script of ['version:check', 'test:versioning', 'typecheck', 'test:architecture', 'test:frontend', 'test:worker', 'test:capture']) {
  console.log('\nChecking ' + script);
  npm(['run', script]);
}
