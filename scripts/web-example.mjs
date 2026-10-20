import { npm } from './environment.mjs';
npm(['run', 'dev:web'], { env: { ...process.env, VITE_ENABLE_EXAMPLE_MODULE: '1' } });
