import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import { fileURLToPath } from 'node:url';
import { buildVersion } from './scripts/build-version.mjs';

const at = (path: string) => fileURLToPath(new URL(path, import.meta.url));

export default defineConfig({
  root: at('./apps/composition/frontend'),
  envDir: at('./'),
  plugins: [vue()],
  define: { __OPENDESK_VERSION__: JSON.stringify(buildVersion(at('./'))) },
  clearScreen: false,
  server: { host: '127.0.0.1', port: 1420, strictPort: true, fs: { allow: [at('./')] } },
  build: { outDir: at('./dist/frontend'), emptyOutDir: true, target: 'es2022' },
});
