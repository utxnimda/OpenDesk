import { describe, expect, it, vi } from 'vitest';
import { buildModuleRegistry, type ModuleDefinition } from '../../apps/framework/frontend/src/core/modules';

const defaults = { development: true, enableExamples: false };
function moduleDefinition(id: string, extra: Partial<ModuleDefinition> = {}): ModuleDefinition {
  return { id, label: id, load: vi.fn(async () => ({ default: {} })), ...extra };
}

describe('module registry boundaries', () => {
  it('rejects duplicate module IDs', () => { expect(() => buildModuleRegistry([moduleDefinition('sample'), moduleDefinition('sample')], defaults)).toThrow('重复'); });
  it.each(['', 'Bad Name', '../bad', 'a_b', 'settings', 'workspace', 'diagnostics', 'version'])('rejects invalid or reserved ID %s', (id) => { expect(() => buildModuleRegistry([moduleDefinition(id)], defaults)).toThrow('无效'); });
  it('sorts enabled metadata without loading page code', () => {
    const first = moduleDefinition('first', { order: 1 }); const second = moduleDefinition('second', { order: 2 });
    expect(buildModuleRegistry([second, first], defaults).map((entry) => entry.id)).toEqual(['first', 'second']);
    expect(first.load).not.toHaveBeenCalled(); expect(second.load).not.toHaveBeenCalled();
  });
  it('keeps development examples isolated unless both gates are enabled', () => {
    const example = moduleDefinition('dev-example', { developmentOnly: true });
    for (const options of [defaults, { development: false, enableExamples: true }, { development: false, enableExamples: false }]) {
      expect(buildModuleRegistry([example], options)).toEqual([]);
    }
    expect(buildModuleRegistry([example], { development: true, enableExamples: true })).toEqual([example]);
    expect(example.load).not.toHaveBeenCalled();
  });
});
