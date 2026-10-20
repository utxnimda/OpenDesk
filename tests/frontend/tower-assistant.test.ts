import { createSSRApp, h } from 'vue';
import { renderToString } from 'vue/server-renderer';
import { describe, expect, it, vi } from 'vitest';
import { createHostAdapter, type InvokeBridge } from '../../apps/framework/frontend/src/core/host';
import { BROWSER_UNAVAILABLE } from '../../apps/framework/module-sdk/src/index';
import { createTowerClient } from '../../apps/features/tower_assistant/frontend/client';
import { collectIconKeys, comparisonSvg, coreCells, entitiesAtPoint, entityCenter, entityDescription, entityLabel, entitySecondaryLabel, entityShortLabel, focusBounds, groupedStepMarkers, itemForEntity, resolveSprite, stepDescription, visibleEntities } from '../../apps/features/tower_assistant/frontend/map';
import TowerMap from '../../apps/features/tower_assistant/frontend/TowerMap.vue';
import TowerAssistantPage from '../../apps/features/tower_assistant/frontend/TowerAssistantPage.vue';
import EntityDetails from '../../apps/features/tower_assistant/frontend/EntityDetails.vue';
import type { Analysis, Entity, MigrationStep, VisualCatalog } from '../../apps/features/tower_assistant/frontend/types';

const platform: Entity = { id: 'building:14', kind: 'platform', name: '渡劫台', x: 10, y: 10, width: 3, height: 3, movable: true };
const pedestal: Entity = { id: 'building:8', kind: 'pedestal', name: '置物台', x: 20, y: 20, width: 1, height: 1, movable: true, itemId: 'placed:1' };
const move: MigrationStep = { id: 'move:1', kind: 'move', entityId: pedestal.id, name: pedestal.name, from: { x: 20, y: 20 }, to: { x: 10, y: 13 }, reason: '移入有效范围' };
const bounds = { minX: 0, maxX: 30, minY: 0, maxY: 30 };
function exampleAnalysis(): Analysis {
  return {
    schemaVersion: 1, status: 'ready', source: { name: '测试存档', savedAt: '2026-10-18T06:00:00Z', sha256: 'abc' },
    platformId: platform.id, bounds,
    metrics: { currentQi: 0, targetQi: 90, gain: 90, movedPeople: 0, movedBuildings: 1, movedFacilities: 0, itemChanges: 0 },
    entitiesBefore: [platform, pedestal], entitiesAfter: [platform, { ...pedestal, x: 10, y: 13 }],
    items: [{ id: 'placed:1', name: '测试灵物', qi: 60, location: 'placed', pedestalId: pedestal.id }],
    steps: [move], warnings: [], optimality: { qi: 'exact', movement: 'exact', explanation: '已核对灵气与搬动数量。' },
  };
}

describe('tower named read-only client', () => {
  it('rejects browser access before invoking any worker command', async () => {
    const host = createHostAdapter();
    const call = vi.spyOn(host, 'callWorker');
    const client = createTowerClient(host);
    await expect(client.discover()).rejects.toThrow(BROWSER_UNAVAILABLE);
    await expect(client.inspect('D:\\Game')).rejects.toThrow(BROWSER_UNAVAILABLE);
    await expect(client.analyze('D:\\Game')).rejects.toThrow(BROWSER_UNAVAILABLE);
    await expect(client.visuals('D:\\Game', ['person:B6'])).rejects.toThrow(BROWSER_UNAVAILABLE);
    expect(call).not.toHaveBeenCalled();
  });
  it('omits optional selectors so the worker reads the latest save for each analysis', async () => {
    const bridge = vi.fn().mockResolvedValue({ schemaVersion: 1 });
    const client = createTowerClient(createHostAdapter(bridge as InvokeBridge));
    await client.analyze('  D:\\游戏  ');
    expect(bridge).toHaveBeenCalledWith('worker_call', { method: 'tower.analyze', params: { gameDir: 'D:\\游戏' } });
    await client.analyze('D:\\游戏', 'save:3', 'building:14');
    expect(bridge).toHaveBeenLastCalledWith('worker_call', { method: 'tower.analyze', params: { gameDir: 'D:\\游戏', saveId: 'save:3', platformId: 'building:14' } });
  });
  it('does not treat unknown schema or empty paths as a successful analysis', async () => {
    const bridge = vi.fn().mockResolvedValue({ schemaVersion: 2 });
    const client = createTowerClient(createHostAdapter(bridge as InvokeBridge));
    await expect(client.analyze('')).rejects.toThrow('游戏安装目录');
    expect(bridge).not.toHaveBeenCalled();
    await expect(client.analyze('D:\\Game')).rejects.toThrow('不支持的数据版本');
  });
  it('reads the selected save so its own platform choices can be shown', async () => {
    const bridge = vi.fn().mockResolvedValue({ schemaVersion: 1 });
    const client = createTowerClient(createHostAdapter(bridge as InvokeBridge));
    await client.inspect('D:\\Game', 'save:earlier');
    expect(bridge).toHaveBeenCalledWith('worker_call', { method: 'tower.inspect', params: { gameDir: 'D:\\Game', saveId: 'save:earlier' } });
  });
  it('preserves worker failure text for actionable UI feedback', async () => {
    const client = createTowerClient(createHostAdapter(vi.fn().mockRejectedValue('存档读取失败：版本不支持') as InvokeBridge));
    await expect(client.inspect('D:\\Game')).rejects.toBe('存档读取失败：版本不支持');
  });
  it('deduplicates and batches image keys through the named capability', async () => {
    const bridge = vi.fn().mockResolvedValue({ schemaVersion: 1, sheets: {}, sprites: {}, warnings: ['无对应图标'] });
    const client = createTowerClient(createHostAdapter(bridge as InvokeBridge));
    const keys = Array.from({ length: 65 }, (_, index) => `person:B${index}`);
    const result = await client.visuals('D:\\Game', [...keys, keys[0]]);
    expect(bridge).toHaveBeenCalledTimes(3);
    expect(bridge.mock.calls.map((call) => call[1].params.iconKeys.length)).toEqual([32, 32, 1]);
    expect(bridge.mock.calls.every((call) => call[1].method === 'tower.visuals')).toBe(true);
    expect(result.warnings).toEqual(['无对应图标']);
  });
});

describe('tower map semantics', () => {
  it('draws even footprints using game anchors, including their rightmost edge', () => {
    const building = { ...platform, x: 2, y: 2, width: 2, height: 2 };
    expect(entityCenter(building)).toEqual({ x: 2.5, y: 2.5 });
    expect(entityCenter(platform)).toEqual({ x: 10, y: 10 });
    expect(visibleEntities([building], { minX: 3, maxX: 4, minY: 3, maxY: 4 })).toEqual([building]);
    expect(visibleEntities([building], { minX: 4, maxX: 5, minY: 4, maxY: 5 })).toEqual([]);
  });
  it('highlights only the 16 legal adjacent slots, excluding corners and the platform footprint', () => {
    const cells = coreCells(platform);
    expect(cells).toHaveLength(16);
    expect(cells).toContainEqual({ x: 10, y: 13 });
    expect(cells).toContainEqual({ x: 12, y: 11 });
    expect(cells).not.toContainEqual({ x: 12, y: 12 });
    expect(cells).not.toContainEqual({ x: 11, y: 11 });
    expect(coreCells()).toEqual([]);
  });
  it('includes both endpoints when a selected step starts outside the focused area', () => {
    const focused = focusBounds(bounds, platform, move);
    expect(focused.maxX).toBeGreaterThanOrEqual(20);
    expect(focused.maxY).toBeGreaterThanOrEqual(20);
    expect(focused.maxX).toBeLessThanOrEqual(bounds.maxX);
    expect(focusBounds(bounds)).toEqual(bounds);
  });
  it('groups repeated steps at one position and makes the selected number visible', () => {
    const equip: MigrationStep = { id: 'equip:2', kind: 'equip', operation: 'equip', entityId: pedestal.id, name: '新灵物', to: move.to, reason: '更换' };
    const markers = groupedStepMarkers([move, equip], [platform, pedestal], true, equip.id);
    expect(markers).toHaveLength(1);
    expect(markers[0].indices).toEqual([0, 1]);
    expect(markers[0].index).toBe(1);
    expect(markers[0].step.id).toBe(equip.id);
  });
  it('distinguishes an emptied pedestal and unloading from installing', () => {
    expect(entityLabel({ ...pedestal, itemId: null })).toBe('空台');
    expect(stepDescription({ ...move, kind: 'equip', operation: 'unequip', itemId: null })).toContain('卸下灵物');
    expect(stepDescription({ ...move, kind: 'equip', operation: 'equip' })).toContain('装入灵物');
    expect(stepDescription({ id: 'unload', kind: 'equip', operation: 'unequip', name: '置物台', entityId: pedestal.id, reason: '取出' }, pedestal)).toContain('(20, 20)');
  });
  it('makes both occupants selectable while respecting even building anchors', () => {
    const person: Entity = { ...pedestal, id: 'person:1', kind: 'person', name: '研究B3', x: 21, y: 21 };
    const building: Entity = { ...pedestal, width: 2, height: 2 };
    expect(entitiesAtPoint([building, person, platform], person)).toEqual([building, person]);
    expect(entitiesAtPoint([building], { x: 19, y: 20 })).toEqual([]);
  });
  it('resolves the item actually bound on each side instead of its original location', () => {
    const data = exampleAnalysis();
    const replacement = { ...data.items[0], id: 'warehouse:1', name: '麒麟护心珠', qi: 55.3, iconKey: 'item:麒麟' };
    const after = { ...pedestal, itemId: replacement.id };
    expect(itemForEntity(after, [...data.items, replacement])).toBe(replacement);
    expect(entityShortLabel(after, [replacement])).toBe('麒麟护心');
    expect(entitySecondaryLabel(after, [replacement])).toBe('灵 55.3');
    expect(entityShortLabel({ ...after, itemId: null }, [replacement])).toBe('空台');
    expect(entitySecondaryLabel({ ...after, itemId: null }, [replacement])).toBe('');
    expect(collectIconKeys([{ ...after, iconKey: 'person:B6', weapon: { name: '弓', iconKey: 'item:弓' } }], [replacement, replacement])).toEqual(['person:B6', 'item:弓', 'item:麒麟']);
  });
});

describe('tower comparison export and rendering', () => {
  it('keeps before-item attributes visible when the suggested pedestal becomes empty', async () => {
    const data = exampleAnalysis();
    data.items[0] = { ...data.items[0], level: 80, quality: 6, qualityName: '神话', attributes: { 金: 10, 木: 20, 水: 30, 火: 40, 土: 50 } };
    const html = await renderToString(createSSRApp(EntityDetails, {
      before: pedestal, after: { ...pedestal, itemId: null, x: 10, y: 13 }, items: data.items,
      hasPlan: true, selectedSide: 'after', neighbours: [pedestal, { ...pedestal, id: 'person:1', kind: 'person', name: '研究B3' }],
    }));
    expect(html).toContain('当前灵物'); expect(html).toContain('建议灵物');
    expect(html).toContain('测试灵物'); expect(html).toContain('神话');
    expect(html).toContain('空台 · 未放置灵物'); expect(html).toContain('同格对象切换');
    expect(html).toContain('(20, 20)'); expect(html).toContain('(10, 13)');
    expect(html).toContain('需要搬动');
  });
  it('shows details for a stationary person independently of migration steps', async () => {
    const person: Entity = { ...pedestal, kind: 'person', id: 'person:20', name: '研究B3', job: '研究', profession: '鬼谋士', equipment: [{ slot: '武器', name: '羽扇', type: '扇子', level: 75, qualityName: '传说' }] };
    const html = await renderToString(createSSRApp(EntityDetails, { before: person, after: person, items: [], hasPlan: true }));
    expect(html).toContain('位置不变'); expect(html).toContain('鬼谋士');
    expect(html).toContain('羽扇'); expect(html).toContain('扇子'); expect(html).toContain('75 级');
    expect(html).toContain('传说'); expect(html).toContain('关闭对象详情');
  });
  it('embeds only valid PNG icons and preserves character/item details in the exported map', () => {
    const data = exampleAnalysis();
    const person: Entity = { ...pedestal, id: 'person:1', kind: 'person', name: '研究B3', iconKey: 'person:B6', profession: '鬼谋士', job: '研究', weapon: { name: '羽扇', type: '扇子' } };
    const visuals: VisualCatalog = { schemaVersion: 1, sheets: { small: { dataUrl: 'data:image/png;base64,aGVsbG8=', width: 32, height: 32 } }, sprites: { 'person:B6': { sheetId: 'small', x: 0, y: 0, width: 32, height: 32 } } };
    data.entitiesBefore.push(person); data.entitiesAfter.push(person);
    data.items[0].attributes = { 水: 40, 土: 30 };
    const svg = comparisonSvg(data, visuals);
    expect(svg.match(/data:image\/png;base64/g)).toHaveLength(1);
    expect(svg).toContain('鬼谋士'); expect(svg).toContain('羽扇');
    expect(svg).toContain('水 40'); expect(svg).toContain('灵 60');
    expect(entityDescription(person)).toContain('当前工作：研究');
    const unsafe = structuredClone(visuals);
    unsafe.sheets.small.dataUrl = 'https://untrusted.invalid/icon.png';
    expect(resolveSprite('person:B6', unsafe)).toBeUndefined();
    expect(comparisonSvg(data, unsafe)).not.toContain('untrusted.invalid');
    unsafe.sheets.small.dataUrl = 'data:image/svg+xml;base64,aGVsbG8=';
    expect(resolveSprite('person:B6', unsafe)).toBeUndefined();
    unsafe.sheets.small = visuals.sheets.small;
    unsafe.sprites['person:B6'].x = 40;
    expect(resolveSprite('person:B6', unsafe)).toBeUndefined();
  });
  it('exports a self-contained comparison with moves, facility metrics, source and proof limits', () => {
    const data = exampleAnalysis();
    data.metrics.movedFacilities = 2;
    data.optimality.explanation = '建筑搬动已证明，设施仍有优化余地。';
    const svg = comparisonSvg(data);
    expect(svg).toContain('xmlns="http://www.w3.org/2000/svg"');
    expect(svg).toContain('当前摆放');
    expect(svg).toContain('建议摆放');
    expect(svg).toContain('测试存档');
    expect(svg).toContain('(20, 20) → (10, 13)');
    expect(svg).toContain('设施 2');
    expect(svg).toContain('设施仍有优化余地');
    expect(svg).not.toContain('http://127.0.0.1');
  });
  it('escapes hostile names, source strings and reasons rather than embedding executable markup', () => {
    const data = exampleAnalysis();
    const hostile = '</text><script>alert("&")</script><text onclick="attack()">';
    data.source.name = hostile;
    data.steps[0] = { ...move, name: hostile, reason: hostile };
    data.entitiesBefore[0] = { ...platform, name: hostile };
    const svg = comparisonSvg(data);
    expect(svg).not.toContain('<script>');
    expect(svg).not.toContain('<text onclick=');
    expect(svg).toContain('&lt;script&gt;');
    expect(svg).toContain('&quot;');
    expect(svg).toContain('&amp;');
  });
  it('renders native-unavailable state without simulated data or calls', async () => {
    const host = createHostAdapter();
    const call = vi.spyOn(host, 'callWorker');
    const html = await renderToString(createSSRApp(TowerAssistantPage, { services: { host } }));
    expect(html).toContain('浏览器预览无法读取本地游戏存档');
    expect(html).toContain('从你的存档开始');
    expect(html).toMatch(/disabled[^>]*>[^]*?生成迁移方案/);
    expect(call).not.toHaveBeenCalled();
  });
  it('renders map labels and unique per-panel SVG definitions safely', async () => {
    const mapProps = {
      entities: [platform, { ...pedestal, itemId: null }], bounds, platformId: platform.id, steps: [move], label: '测试地图',
    };
    const html = await renderToString(createSSRApp({ render: () => h('div', [h(TowerMap, mapProps), h(TowerMap, mapProps)]) }));
    expect(html).toContain('空台');
    expect(html).toContain('测试地图');
    expect(html).toContain('步骤 1');
    expect(html).toContain('clipPath');
    const patternIds = [...html.matchAll(/<pattern id="([^"]+)"/g)].map((match) => match[1]);
    expect(patternIds).toHaveLength(2);
    expect(new Set(patternIds).size).toBe(2);
  });
});
