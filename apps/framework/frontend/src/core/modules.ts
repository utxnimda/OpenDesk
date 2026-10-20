import type { Component } from 'vue';
import type { HostAdapter } from './host';

export interface ModuleServices {
  host: HostAdapter;
}

/** Metadata must be side-effect free. Actual page code belongs in load(). */
export interface ModuleDefinition {
  id: string;
  label: string;
  description?: string;
  order?: number;
  developmentOnly?: boolean;
  load: () => Promise<{ default: Component }>;
}

export interface RegistryOptions {
  development: boolean;
  enableExamples: boolean;
}

const RESERVED_IDS = new Set(['workspace', 'packages', 'diagnostics', 'settings', 'version']);
const ID_PATTERN = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/;

/** Discovering a module must never load its page or start its backend. */
export function buildModuleRegistry(definitions: ModuleDefinition[], options: RegistryOptions): ModuleDefinition[] {
  const seen = new Set<string>();
  const enabled: ModuleDefinition[] = [];
  for (const definition of definitions) {
    if (definition.developmentOnly && !(options.development && options.enableExamples)) continue;
    if (!ID_PATTERN.test(definition.id) || RESERVED_IDS.has(definition.id)) {
      throw new Error(`无效的模块 ID：${definition.id}`);
    }
    if (seen.has(definition.id)) throw new Error(`重复的模块 ID：${definition.id}`);
    if (!definition.label?.trim() || typeof definition.load !== 'function') {
      throw new Error(`模块 ${definition.id} 缺少名称或页面入口`);
    }
    if (definition.order !== undefined && !Number.isFinite(definition.order)) {
      throw new Error(`模块 ${definition.id} 的排序值必须是有限数字`);
    }
    seen.add(definition.id);
    enabled.push(definition);
  }
  return enabled.sort((a, b) => (a.order ?? 100) - (b.order ?? 100) || a.id.localeCompare(b.id));
}
