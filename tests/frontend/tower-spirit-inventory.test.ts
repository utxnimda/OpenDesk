import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import * as Vue from 'vue';
import { createRenderer, createSSRApp, h, nextTick, shallowRef, ssrContextKey } from 'vue';
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc';
import ts from 'typescript';
import { renderToString } from 'vue/server-renderer';
import { afterEach, describe, expect, it, vi } from 'vitest';
import SpiritInventoryPanel from '../../apps/features/tower_assistant/frontend/SpiritInventoryPanel.vue';
import TowerMap from '../../apps/features/tower_assistant/frontend/TowerMap.vue';
import SpriteIcon from '../../apps/features/tower_assistant/frontend/SpriteIcon.vue';
import { filterSpirits, rankSpirits, spiritContribution, spiritForEntity, spiritMapBounds, spiritPedestal } from '../../apps/features/tower_assistant/frontend/spiritInventory';
import type { Snapshot } from '../../apps/features/tower_assistant/frontend/types';

function inventory(): Snapshot {
  return {
    source: { id: 'save:ea2save.dat', name: '测试槽 2', savedAt: '2026-10-25T01:00:00Z', sha256: 'save-a' },
    bounds: { minX: 0, maxX: 30, minY: 0, maxY: 30 },
    entities: [
      { id: 'platform:1', name: '渡劫台', kind: 'platform', x: 10, y: 10, width: 3, height: 3, movable: false },
      { id: 'pedestal:1', name: '置物台', kind: 'pedestal', x: 10, y: 13, width: 1, height: 1, movable: true, tier: 2, itemId: 'placed:1' },
      { id: 'pedestal:2', name: '置物台', kind: 'pedestal', x: 25, y: 25, width: 1, height: 1, movable: true, tier: 1, itemId: 'placed:2' },
    ],
    items: [
      { id: 'placed:1', name: '青玉', qi: 80, location: 'placed', pedestalId: 'pedestal:1', currentQi: 104, contributions: [{ platformId: 'platform:1', qi: 104 }], level: 4, qualityName: '精致', attributes: { 金: 2, 木: 3 } },
      { id: 'warehouse:1', name: '天珠', qi: 100, location: 'warehouse', level: 5, qualityName: '传说' },
      { id: 'warehouse:2', name: '赤玉', qi: 80, location: 'warehouse' },
      { id: 'placed:2', name: '石钟', qi: 20, location: 'placed', pedestalId: 'pedestal:2', currentQi: 23, contributions: [] },
    ], warnings: [],
  };
}

describe('spirit inventory ranking and presentation contract', () => {
  it('ranks all saved items before filtering, preserves ties and does not mutate the snapshot', () => {
    const snapshot = inventory();
    const original = snapshot.items.map((item) => item.id);
    const ranks = rankSpirits(snapshot.items);
    expect(ranks.map((row) => row.rank)).toEqual([1, 2, 2, 4]);
    expect(ranks.map((row) => row.relativeQi)).toEqual([100, 80, 80, 20]);
    expect(filterSpirits(ranks, 'placed', '').map((row) => row.rank)).toEqual([2, 4]);
    expect(filterSpirits(ranks, 'all', ' 石钟 ')[0].rank).toBe(4);
    expect(snapshot.items.map((item) => item.id)).toEqual(original);
    expect(rankSpirits([{ ...snapshot.items[0], qi: 0 }])[0].relativeQi).toBe(0);
  });
  it('uses authoritative contributions and distinguishes warehouse, outside range and unknown data', () => {
    const [inside, storage, , outside] = inventory().items;
    expect(spiritContribution(inside, 'platform:1')).toBe(104);
    expect(spiritContribution(outside, 'platform:1')).toBe(0);
    expect(spiritContribution(storage, 'platform:1')).toBeUndefined();
    expect(spiritContribution({ ...inside, contributions: undefined }, 'platform:1')).toBeUndefined();
    expect(spiritContribution(inside, '')).toBeUndefined();
  });
  it('uses both directions of the saved binding and never places a warehouse item on the map', () => {
    const snapshot = inventory();
    expect(spiritForEntity('pedestal:1', snapshot)?.id).toBe('placed:1');
    expect(spiritForEntity('platform:1', snapshot)).toBeUndefined();
    expect(spiritPedestal(snapshot.items[1], snapshot.entities)).toBeUndefined();
    expect(spiritPedestal({ ...snapshot.items[0], pedestalId: 'pedestal:2' }, snapshot.entities)).toBeUndefined();
    expect(spiritMapBounds(snapshot).maxX).toBeGreaterThanOrEqual(25);
    expect(spiritMapBounds(snapshot, snapshot.entities[1]).maxX).toBeLessThan(25);
  });
  it('renders every item and global rank without a planning result, including a warehouse-only save', async () => {
    const snapshot = inventory();
    const html = await renderToString(createSSRApp(SpiritInventoryPanel, { snapshot, visuals: null }));
    for (const item of snapshot.items) expect(html).toContain(item.name);
    expect(html).toContain('已摆放 2 件 · 仓库 2 件');
    expect(html).toContain('第4名 石钟');
    expect(html).toContain('全局第 2 名');
    expect(html).toContain('存档快照 · 非实时数据');
    const storageOnly = { ...snapshot, entities: [], items: [snapshot.items[1]] };
    const storageHtml = await renderToString(createSSRApp(SpiritInventoryPanel, { snapshot: storageOnly, visuals: null }));
    expect(storageHtml).toContain('没有城中置物台');
    expect(storageHtml).toContain('第1名 天珠');
    expect(storageHtml).not.toContain('生成迁移方案');
  });
});

// Minimal host renderer exercises Vue state, bindings and event handlers without a browser dependency.
type TestNode = {
  tag: string; children: TestNode[]; parent: TestNode | null; props: Record<string, any>; text: string;
  value: string; selected: boolean; _value?: unknown; clientWidth: number; clientHeight: number;
  options: TestNode[]; tagName: string; multiple: boolean;
  addEventListener: (...args: unknown[]) => void; removeEventListener: (...args: unknown[]) => void;
  scrollIntoView: ReturnType<typeof vi.fn>; setAttribute: (...args: unknown[]) => void;
};
function node(tag: string, text = ''): TestNode {
  const result = {
    tag, children: [], parent: null, props: {}, text, value: '', selected: false, tagName: tag.toUpperCase(), multiple: false,
    clientWidth: 650, clientHeight: 430, addEventListener() {}, removeEventListener() {}, setAttribute() {}, getRootNode() { return null; }, scrollIntoView: vi.fn(),
    get options() { return this.children.filter((child: TestNode) => child.tag === 'option'); },
  };
  return result as TestNode;
}
function descendants(root: TestNode): TestNode[] { return [root, ...root.children.flatMap(descendants)]; }
function addClientRender(component: object, name: string) {
  // Node tests receive SSR SFCs. Compile the actual template for the host renderer as well.
  const filename = resolve('apps/features/tower_assistant/frontend', name);
  const { descriptor } = parse(readFileSync(filename, 'utf8'), { filename });
  const bindings = compileScript(descriptor, { id: name }).bindings;
  const compiled = compileTemplate({ source: descriptor.template!.content, filename, id: name, compilerOptions: { bindingMetadata: bindings } });
  if (compiled.errors.length) throw new Error(compiled.errors.join('\n'));
  const code = ts.transpileModule(compiled.code, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const exports: { render?: unknown } = {};
  new Function('require', 'exports', code)((name: string) => {
    if (name !== 'vue') throw new Error(`Unexpected template import ${name}`);
    return Vue;
  }, exports);
  Object.assign(component, { render: exports.render });
}
async function mountInventory() {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  vi.stubGlobal('document', { activeElement: null });
  vi.stubGlobal('Document', class {});
  vi.stubGlobal('ShadowRoot', class {});
  const renderer = createRenderer<TestNode, TestNode>({
    createElement: (tag) => node(tag), createText: (text) => node('#text', text), createComment: (text) => node('#comment', text),
    setText: (entry, text) => { entry.text = text; }, setElementText: (entry, text) => { entry.text = text; entry.children = []; },
    parentNode: (entry) => entry.parent, nextSibling: (entry) => entry.parent?.children[entry.parent.children.indexOf(entry) + 1] ?? null,
    patchProp: (entry, key, _previous, value) => { entry.props[key] = value; if (key === 'value') { entry.value = value; entry._value = value; } },
    insert(entry, parent, anchor) {
      if (entry.parent) entry.parent.children.splice(entry.parent.children.indexOf(entry), 1);
      entry.parent = parent;
      const at = anchor ? parent.children.indexOf(anchor) : -1;
      if (at >= 0) parent.children.splice(at, 0, entry); else parent.children.push(entry);
    },
    remove(entry) { if (entry.parent) entry.parent.children.splice(entry.parent.children.indexOf(entry), 1); entry.parent = null; },
  });
  const snapshot = shallowRef(inventory());
  const root = node('root');
  addClientRender(SpiritInventoryPanel, 'SpiritInventoryPanel.vue');
  addClientRender(TowerMap, 'TowerMap.vue');
  addClientRender(SpriteIcon, 'SpriteIcon.vue');
  const app = renderer.createApp({ render: () => h(SpiritInventoryPanel, { snapshot: snapshot.value, visuals: null }) });
  app.provide(ssrContextKey, { modules: new Set() });
  app.mount(root);
  await nextTick();
  const find = (predicate: (entry: TestNode) => boolean) => {
    const result = descendants(root).find(predicate);
    if (!result) throw new Error('Expected rendered element was not found');
    return result;
  };
  return { root, app, snapshot, find };
}
afterEach(() => vi.unstubAllGlobals());

describe('spirit inventory interaction', () => {
  it('links list selection to the map, map selection to filtered rows, and warehouse selection to details', async () => {
    const view = await mountInventory();
    try {
      const row = view.find((entry) => entry.tag === 'button' && String(entry.props['aria-label']).startsWith('第4名'));
      row.props.onClick(); await nextTick();
      expect(view.find((entry) => entry.tag === 'g' && String(entry.props['aria-label']).includes('石钟')).props['aria-pressed']).toBe(true);
      const search = view.find((entry) => entry.props['aria-label'] === '搜索灵物名称或编号');
      search.props['onUpdate:modelValue']('天珠'); await nextTick();
      const target = view.find((entry) => entry.tag === 'g' && String(entry.props['aria-label']).includes('石钟'));
      target.props.onClick(); await nextTick(); await nextTick();
      const revealed = view.find((entry) => entry.tag === 'button' && String(entry.props['aria-label']).startsWith('第4名'));
      expect(revealed.props['aria-pressed']).toBe(true);
      expect(revealed.scrollIntoView).toHaveBeenCalled();
      const stored = view.find((entry) => entry.tag === 'button' && String(entry.props['aria-label']).startsWith('第1名'));
      stored.props.onClick(); await nextTick();
      expect(descendants(view.root).some((entry) => entry.tag === 'g' && entry.props['aria-pressed'] === true)).toBe(false);
      expect(descendants(view.root).filter((entry) => entry.tag === 'dd' && entry.text === '未摆放')).toHaveLength(2);
    } finally { view.app.unmount(); }
  });
  it('clears selection and filters when the chosen save changes even if item ids are reused', async () => {
    const view = await mountInventory();
    try {
      view.find((entry) => entry.tag === 'button' && String(entry.props['aria-label']).startsWith('第1名')).props.onClick();
      await nextTick();
      expect(descendants(view.root).some((entry) => entry.props['aria-label'] === '选中灵物详情')).toBe(true);
      const updated = inventory(); updated.source = { ...updated.source, id: 'save:ea1save.dat', sha256: 'save-b' };
      view.snapshot.value = updated; await nextTick();
      expect(descendants(view.root).some((entry) => entry.props['aria-label'] === '选中灵物详情')).toBe(false);
    } finally { view.app.unmount(); }
  });
});
