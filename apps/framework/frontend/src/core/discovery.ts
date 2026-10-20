import { buildModuleRegistry, type ModuleDefinition } from './modules';

// Production packages are discovered by the Rust catalog, never imported here.
const declarations: Record<string, { default: ModuleDefinition }> = {};

// Vite replaces DEV at build time. Example declarations and pages are removed
// from production output, even if the example environment flag is present.
const examples = import.meta.env.DEV && import.meta.env.VITE_ENABLE_EXAMPLE_MODULE === '1'
  ? import.meta.glob<{ default: ModuleDefinition }>(
    '../../../../features/_examples/*/frontend/module.ts',
    { eager: true },
  )
  : {};

export function discoverModules(): ModuleDefinition[] {
  return buildModuleRegistry(Object.values({ ...declarations, ...examples }).map((entry) => entry.default), {
    development: import.meta.env.DEV,
    enableExamples: import.meta.env.VITE_ENABLE_EXAMPLE_MODULE === '1',
  });
}
