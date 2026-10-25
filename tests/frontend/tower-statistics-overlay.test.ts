import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import * as Vue from 'vue';
import { createRenderer, h, nextTick, ssrContextKey } from 'vue';
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc';
import ts from 'typescript';
import { afterEach, describe, expect, it, vi } from 'vitest';
import StatisticsOverlay from '../../apps/features/tower_assistant/frontend/StatisticsOverlay.vue';
import RuntimeStatisticsPanel from '../../apps/features/tower_assistant/frontend/RuntimeStatisticsPanel.vue';
import SpriteIcon from '../../apps/features/tower_assistant/frontend/SpriteIcon.vue';
import type { LimitedHost } from '../../apps/framework/module-sdk/src/index';
import type { StatisticsResponse } from '../../apps/features/tower_assistant/frontend/statisticsTypes';
import type { RankingBoardFilter } from '../../apps/features/tower_assistant/frontend/statisticsRanking';

function fixture(count = 2): StatisticsResponse {
  return { schemaVersion: 1, capture: { state: 'connected', message: 'fixture', installed: true, gameRunning: true,
    canInstall: false, canRemove: false, coverage: [], gameDir: 'E:/synthetic-game' }, statistics: {
    schemaVersion: 1, sessionId: 'one', state: 'running', reason: '', elapsedRealSeconds: 10, eventCount: 900,
    retainedEventCount: 0, lastSequence: 900, events: [], totals: [], workCategories: ['建造', '采矿'],
    summary: { work: { amount: 1000000, count: 3 }, damage: { amount: 150, count: 3, perRealSecond: 15 } },
    people: Array.from({ length: count }, (_, index) => ({ personId: `p${index}`, personName: `人物${index}`, personSequence: index + 1,
      person: { profession: index === 0 ? '剑修' : '弓手', attributes: [{ id: 'str', label: '力量', value: 120 }], equipment: [{ slot: '武器', name: '青锋剑' }] },
      work: [{ category: index === 0 ? '建造' : '采矿', amount: index === 0 ? 1000000 : 500, count: 2, averageAmount: 500000, latestAmount: 200, latestSequence: index + 1 }],
      damage: { amount: 100 / (index + 1), count: index + 1, perRealSecond: 99, types: [
        { damageType: 'physical' as const, amount: 100 / (index + 1), count: index + 1, perRealSecond: 99, averageAmount: 99, latestAmount: 1, latestSequence: index + 1 }], attacks: [] },
    })),
  } };
}
function surface(context: { view: string; data: Record<string, unknown> } | null = null) {
  return { context, open: vi.fn(async () => {}), close: vi.fn(async () => {}), startDrag: vi.fn(async () => {}), setAlwaysOnTop: vi.fn(async (_enabled: boolean) => {}) };
}
function filters(values: Partial<RankingBoardFilter> = {}): RankingBoardFilter {
  return { kind: 'damage', query: '', workCategories: null, damageTypes: null, professions: null,
    damageGrouping: 'type', sort: 'amount', limit: 10, hideZero: true, ...values };
}
type TestNode = { tag: string; text: string; props: Record<string, any>; children: TestNode[]; parent: TestNode | null; value: unknown };
function node(tag: string, text = ''): TestNode {
  return { tag, text, props: {}, children: [], parent: null, value: '', style: {}, tagName: tag.toUpperCase(), multiple: false,
    addEventListener() {}, removeEventListener() {}, getRootNode() { return null; },
    get options() { return this.children.filter((entry: TestNode) => entry.tag === 'option'); },
  } as TestNode;
}
function descendants(root: TestNode): TestNode[] { return [root, ...root.children.flatMap(descendants)]; }
function content(root: TestNode): string { return root.tag === '#comment' ? '' : root.text + root.children.map(content).join(''); }
function compile(component: Vue.Component, name: string) {
  const filename = resolve(`apps/features/tower_assistant/frontend/${name}.vue`);
  const { descriptor } = parse(readFileSync(filename, 'utf8'), { filename });
  const bindings = compileScript(descriptor, { id: name }).bindings;
  const template = compileTemplate({ source: descriptor.template!.content, filename, id: name, compilerOptions: { bindingMetadata: bindings } });
  if (template.errors.length) throw new Error(template.errors.join('\n'));
  const code = ts.transpileModule(template.code, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const exports: { render?: unknown } = {};
  new Function('require', 'exports', code)((name: string) => { if (name !== 'vue') throw new Error(name); return Vue; }, exports);
  Object.assign(component, { render: exports.render });
}
async function flush() { for (let index = 0; index < 8; index++) { await Promise.resolve(); await nextTick(); } }
async function mount(call: (method: string, params?: Record<string, unknown>) => Promise<unknown>, options: { main?: boolean; surface?: ReturnType<typeof surface>; props?: Record<string, unknown> } = {}) {
  vi.stubGlobal('document', { activeElement: null }); vi.stubGlobal('Document', class {}); vi.stubGlobal('ShadowRoot', class {});
  const renderer = createRenderer<TestNode, TestNode>({
    createElement: tag => node(tag), createText: text => node('#text', text), createComment: text => node('#comment', text),
    setText: (entry, text) => { entry.text = text; }, setElementText: (entry, text) => { entry.text = text; entry.children = []; },
    parentNode: entry => entry.parent, nextSibling: entry => entry.parent?.children[entry.parent.children.indexOf(entry) + 1] ?? null,
    patchProp: (entry, key, _before, value) => { entry.props[key] = value; if (key === 'value') entry.value = value; },
    insert(entry, parent, anchor) { if (entry.parent) entry.parent.children.splice(entry.parent.children.indexOf(entry), 1); entry.parent = parent; const index = anchor ? parent.children.indexOf(anchor) : -1; if (index < 0) parent.children.push(entry); else parent.children.splice(index, 0, entry); },
    remove(entry) { if (entry.parent) entry.parent.children.splice(entry.parent.children.indexOf(entry), 1); entry.parent = null; },
  });
  const component = options.main ? RuntimeStatisticsPanel : StatisticsOverlay;
  compile(component, options.main ? 'RuntimeStatisticsPanel' : 'StatisticsOverlay');
  compile(SpriteIcon, 'SpriteIcon');
  const host = { available: true, callWorker: vi.fn(call), ...(options.surface ? { surface: options.surface } : {}) } as unknown as LimitedHost;
  const root = node('root'); const app = renderer.createApp({ render: () => h(component, { host, gameDir: 'E:/synthetic-game', ...options.props }) });
  app.provide(ssrContextKey, { modules: new Set() }); app.mount(root); await flush();
  const find = (predicate: (entry: TestNode) => boolean) => { const result = descendants(root).find(predicate); if (!result) throw new Error('Expected element not found'); return result; };
  const label = (name: string) => find(entry => entry.props['aria-label'] === name);
  const button = (name: string) => find(entry => entry.tag === 'button' && content(entry).includes(name));
  return { app, root, host, find, label, button };
}
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('transparent statistics view', () => {
  it('rounds full output displays while preserving character attributes and the underlying aggregates', async () => {
    const state = fixture();
    state.statistics.people[0].damage.amount = 12345.678901;
    state.statistics.people[0].damage.types[0].amount = 12345.678901;
    state.statistics.people[0].person!.attributes![0].value = 120.125;
    state.statistics.people[0].work[0].latestAmount = 45678.9123;
    const view = await mount(async () => state);
    try {
      view.label('排行数值显示').props['onUpdate:modelValue']('raw'); await flush();
      const row = view.find(entry => entry.props['data-person-id'] === 'p0');
      expect(content(row)).toContain('12,346'); expect(content(row)).not.toContain('12,345.678901');
      expect(content(row)).toContain('1,235 / 秒');
      view.label('查看人物0人物快照').props.onClick(); await flush();
      expect(content(view.label('排行榜人物快照'))).toContain('120.125');
      expect(state.statistics.people[0].damage.amount).toBe(12345.678901);
    } finally { view.app.unmount(); }
    const main = await mount(async () => state, { main: true });
    try {
      expect(content(main.label('查看人物0建造最近一次工作详情'))).toContain('45,679');
      main.label('查看人物0人物详情').props.onClick(); await flush();
      expect(content(main.label('固定详情'))).toContain('120.125');
      expect(state.statistics.people[0].work[0].latestAmount).toBe(45678.9123);
    } finally { main.app.unmount(); }
  });
  it('reads existing session aggregates without start, stop or reset; displays compact values and Top 10', async () => {
    const view = await mount(async () => fixture(12), { surface: surface() });
    try {
      expect(descendants(view.root).filter(entry => entry.props['data-person-id'])).toHaveLength(10);
      expect(view.host.callWorker).toHaveBeenCalledWith('tower.capture.status', { gameDir: 'E:/synthetic-game' });
      expect(content(view.root)).toContain('次数为命中，不是技能施放次数');
      view.button('工作量').props.onClick(); await flush();
      expect(content(view.root)).toContain('100 万'); expect(content(view.root)).not.toContain('DPS');
      expect(vi.mocked(view.host.callWorker).mock.calls.some(([method]) => /statistics\.(start|stop)/.test(method))).toBe(false);
    } finally { view.app.unmount(); }
  });
  it('filters output without intersecting dimensions and resets unsupported DPS ordering for work', async () => {
    const view = await mount(async () => fixture());
    try {
      view.label('排行排序').props['onUpdate:modelValue']('dps'); await flush();
      expect(content(view.root)).toContain('DPS');
      view.label('职业：弓手').props.onChange(); await flush();
      const rows = descendants(view.root).filter(entry => entry.props['data-person-id']);
      expect(rows).toHaveLength(1); expect(content(rows[0])).toContain('100.0%');
      view.button('工作量').props.onClick(); await flush();
      expect(content(view.label('排行排序'))).not.toContain('每秒伤害');
      expect(content(view.root)).toContain('Top 10 · 累计工作量');
      expect(descendants(view.root).some(entry => entry.props['aria-label'] === '伤害类型多选')).toBe(false);
    } finally { view.app.unmount(); }
  });
  it('renders one full-row bar board per selected work type with latest actual output and counts', async () => {
    const state = fixture();
    state.statistics.people[0].work.push({ category: '采矿', amount: 900, count: 3, averageAmount: 300, latestAmount: 310.75, latestSequence: 9 });
    const view = await mount(async () => state, { props: { initialRankingFilter: filters({ kind: 'work', workCategories: ['建造', '采矿'] }), initialNumberMode: 'raw' } });
    try {
      const construction = view.label('建造排行榜'); const mining = view.label('采矿排行榜');
      expect(content(construction)).toContain('1,000,000'); expect(content(mining)).toContain('1,400');
      const miningRows = descendants(mining).filter(entry => entry.props['data-person-id']);
      expect(miningRows).toHaveLength(2);
      expect(content(miningRows[0])).toContain('900'); expect(content(miningRows[0])).toContain('64.3%');
      expect(content(miningRows[0])).toContain('最近实际 311'); expect(content(miningRows[0])).toContain('3 次');
      expect(content(miningRows[0])).not.toContain('均次');
      const bar = (row: TestNode) => descendants(row).find(entry => entry.props.class === 'row-bar')!;
      expect(bar(miningRows[0]).props.style.width).toBe('100%');
      expect(parseFloat(bar(miningRows[1]).props.style.width)).toBeCloseTo(500 / 900 * 100);
      view.label('工作类型：建造').props.onChange(); await flush();
      expect(descendants(view.root).filter(entry => entry.props['data-board-key'])).toHaveLength(1);
      expect(content(view.label('采矿排行榜'))).toContain('最近实际');
      view.button('清空工作').props.onClick(); await flush();
      expect(descendants(view.root).filter(entry => entry.props['data-person-id'])).toHaveLength(0);
      expect(state.statistics.people[0].work[1].latestAmount).toBe(310.75);
    } finally { view.app.unmount(); }
  });
  it('combines selected damage attributes, splits by type or profession, and keeps attacks an exclusive dimension', async () => {
    const state = fixture();
    state.statistics.people[0].damage.types.push({ damageType: 'magic', amount: 200, count: 4, perRealSecond: 20, averageAmount: 50, latestAmount: 50, latestSequence: 8 });
    state.statistics.people[0].damage.attacks.push({ attackKind: 'skill', skillId: '7', skillName: '合成技能', actionCount: null, averagePerAction: null,
      amount: 40, count: 2, perRealSecond: 4, averageAmount: 20, latestAmount: 20, latestSequence: 10 });
    const view = await mount(async () => state, { props: { initialRankingFilter: filters({ damageTypes: ['physical', 'magic'], damageGrouping: 'combined' }) } });
    try {
      let rows = descendants(view.root).filter(entry => entry.props['data-person-id']);
      expect(rows).toHaveLength(2); expect(content(rows[0])).toContain('300'); expect(content(rows[0])).toContain('30 / 秒');
      expect(content(rows[0])).toContain('剑修'); expect(content(rows[0])).not.toContain('均次');
      view.label('伤害分榜').props['onUpdate:modelValue']('type'); await flush();
      expect(descendants(view.root).filter(entry => entry.props['data-board-key'])).toHaveLength(2);
      expect(descendants(view.root).map(entry => entry.props['data-board-key']).filter(Boolean)).toEqual(['damage:type:physical', 'damage:type:magic']);
      view.label('伤害分榜').props['onUpdate:modelValue']('profession'); await flush();
      expect(view.label('剑修排行榜')).toBeTruthy(); expect(view.label('弓手排行榜')).toBeTruthy();
      view.label('职业：弓手').props.onChange(); await flush();
      expect(descendants(view.root).filter(entry => entry.props['data-board-key'])).toHaveLength(1);
      view.button('清空伤害类型').props.onClick(); await flush();
      expect(descendants(view.root).filter(entry => entry.props['data-person-id'])).toHaveLength(0);
      view.label('伤害统计维度').props['onUpdate:modelValue']('attack'); await flush();
      view.label('攻击类别').props['onUpdate:modelValue']('skill'); await flush();
      rows = descendants(view.root).filter(entry => entry.props['data-person-id']);
      expect(rows).toHaveLength(1); expect(content(rows[0])).toContain('40'); expect(content(rows[0])).toContain('4 / 秒');
      expect(content(view.root)).toContain('不与伤害属性筛选交叉');
      expect(descendants(view.root).some(entry => entry.props['aria-label'] === '伤害类型多选')).toBe(false);
      view.label('伤害统计维度').props['onUpdate:modelValue']('type'); await flush();
      expect(descendants(view.root).filter(entry => entry.props['data-person-id'])).toHaveLength(0);
    } finally { view.app.unmount(); }
  });
  it('keeps bar scales and percentage denominators independent of sorting and Top N truncation', async () => {
    const view = await mount(async () => fixture(6), { props: { initialRankingFilter: filters({ damageTypes: ['physical'], sort: 'count', limit: 5 }) } });
    try {
      const rows = descendants(view.root).filter(entry => entry.props['data-person-id']);
      expect(rows).toHaveLength(5); expect(rows[0].props['data-person-id']).toBe('p5');
      expect(content(view.root)).toContain('条形相对本榜最大累计量');
      expect(content(view.root)).not.toContain('条形相对榜首');
      expect(content(view.root)).toContain('按命中次数排序');
      const width = descendants(rows[0]).find(entry => entry.props.class === 'row-bar')!.props.style.width;
      expect(parseFloat(width)).toBeCloseTo(100 / 6);
      expect(content(rows[0])).toContain('6.8%');
      expect(rows.some(row => row.props['data-person-id'] === 'p0')).toBe(false);
      view.label('排行人数').props['onUpdate:modelValue'](null); await flush();
      const sameRow = view.find(entry => entry.props['data-person-id'] === 'p5');
      expect(content(sameRow)).toContain('6.8%');
      expect(descendants(sameRow).find(entry => entry.props.class === 'row-bar')!.props.style.width).toBe(width);
    } finally { view.app.unmount(); }
  });
  it.each([
    { kind: 'damage' as const, sort: 'count' as const, label: '命中次数', value: '1 次' },
    { kind: 'damage' as const, sort: 'average' as const, label: '平均单次', value: '100' },
    { kind: 'damage' as const, sort: 'latest' as const, label: '最近单次', value: '1' },
    { kind: 'work' as const, sort: 'count' as const, label: '工作次数', value: '2 次' },
    { kind: 'work' as const, sort: 'average' as const, label: '平均单次', value: '500,000' },
    { kind: 'work' as const, sort: 'latest' as const, label: '最近单次', value: '200' },
  ])('shows just the selected comparison metric for $kind/$sort and removes it for total ordering', async ({ kind, sort, label, value }) => {
    const view = await mount(async () => fixture(), { props: { initialNumberMode: 'raw', initialRankingFilter: filters({ kind, sort, damageTypes: ['physical'], workCategories: ['建造'] }) } });
    try {
      const row = view.find(entry => entry.props['data-person-id'] === 'p0');
      const badges = descendants(row).filter(entry => entry.props['aria-label'] === '排序依据');
      expect(badges).toHaveLength(1); expect(content(badges[0])).toBe(`${label} · ${value}`);
      expect(content(view.root)).toContain(`按${label}排序`);
      const width = descendants(row).find(entry => entry.props.class === 'row-bar')!.props.style.width;
      view.label('排行排序').props['onUpdate:modelValue']('amount'); await flush();
      expect(descendants(view.root).some(entry => entry.props['aria-label'] === '排序依据')).toBe(false);
      const updated = view.find(entry => entry.props['data-person-id'] === 'p0');
      expect(descendants(updated).find(entry => entry.props.class === 'row-bar')!.props.style.width).toBe(width);
    } finally { view.app.unmount(); }
  });
  it('uses the public surface context filter, preserving explicit empty choices across refreshes', async () => {
    vi.useFakeTimers();
    const controls = surface({ view: 'statistics', data: { gameDir: 'E:/synthetic-game', kind: 'work', rankingFilter: filters({ kind: 'work', workCategories: [] }) } });
    const view = await mount(async () => fixture(), { surface: controls });
    try {
      expect(view.button('工作量').props['aria-pressed']).toBe(true);
      expect(descendants(view.root).filter(entry => entry.props['data-board-key'])).toHaveLength(0);
      await vi.advanceTimersByTimeAsync(1000); await flush();
      expect(descendants(view.root).filter(entry => entry.props['data-board-key'])).toHaveLength(0);
      view.button('全部工作').props.onClick(); await flush();
      expect(descendants(view.root).filter(entry => entry.props['data-board-key'])).toHaveLength(2);
    } finally { view.app.unmount(); }
  });
  it.each([
    { ...filters(), extra: true },
    filters({ damageTypes: ['physical', 'physical'] }),
    filters({ professions: Array.from({ length: 513 }, (_, index) => `known:p${index}`) }),
    null,
  ])('makes malformed initial filters explicit without silently displaying unfiltered results (%#)', async initialRankingFilter => {
    const view = await mount(async () => fixture(), { props: { initialRankingFilter } });
    try {
      expect(content(view.root)).toContain('排行筛选无效');
      expect(descendants(view.root).filter(entry => entry.props['data-person-id'])).toHaveLength(0);
      view.button('重置排行筛选').props.onClick(); await flush();
      expect(content(view.root)).not.toContain('排行筛选无效');
      expect(descendants(view.root).filter(entry => entry.props['data-person-id'])).toHaveLength(2);
    } finally { view.app.unmount(); }
  });
  it('rejects oversized complete surface context even when its filter alone is valid', async () => {
    const controls = surface({ view: 'statistics', data: { gameDir: 'E:/synthetic-game', rankingFilter: filters(), padding: '界'.repeat(3000) } });
    const view = await mount(async () => fixture(), { surface: controls });
    try {
      expect(content(view.root)).toContain('超过 8 KiB');
      expect(descendants(view.root).filter(entry => entry.props['data-person-id'])).toHaveLength(0);
      expect(controls.open).not.toHaveBeenCalled();
    } finally { view.app.unmount(); }
  });
  it('preserves a selected person through new sessions and reports refresh errors without advancing stale numbers', async () => {
    vi.useFakeTimers(); let state = fixture(); let broken = false;
    const view = await mount(async () => { if (broken) throw new Error('fixture 断线'); return state; });
    try {
      view.label('查看人物0人物快照').props.onClick(); await flush();
      state = fixture(); state.statistics.sessionId = 'two'; state.statistics.people[0].person!.profession = '变更职业';
      state.statistics.people[0].person!.attributes![0].value = 900;
      await vi.advanceTimersByTimeAsync(1000); await flush();
      const detail = view.label('排行榜人物快照');
      expect(content(detail)).toContain('剑修'); expect(content(detail)).toContain('120'); expect(content(detail)).toContain('青锋剑');
      expect(content(detail)).toContain('上一计时会话'); expect(content(detail)).not.toContain('变更职业');
      expect(content(view.root)).toContain('计时会话已切换');
      broken = true; await vi.advanceTimersByTimeAsync(2000); await flush();
      expect(content(view.root)).toContain('刷新失败 · 保留上次结果'); expect(content(view.root)).toContain('fixture 断线');
      expect(content(view.root)).toContain('00:00:10'); expect(content(detail)).toContain('120');
      expect(vi.mocked(view.host.callWorker).mock.calls.some(([method]) => method === 'tower.inspect')).toBe(false);
      view.label('关闭人物快照').props.onClick(); await flush();
      expect(descendants(view.root).some(entry => entry.props['role'] === 'dialog')).toBe(false);
    } finally { view.app.unmount(); }
  });
  it('forwards window controls and changes the pin state only after a successful host acknowledgement', async () => {
    const controls = surface(); const view = await mount(async () => fixture(), { surface: controls });
    try {
      controls.setAlwaysOnTop.mockRejectedValueOnce(new Error('置顶未成功'));
      await view.label('切换窗口置顶').props.onClick(); await flush();
      expect(view.label('切换窗口置顶').props['aria-pressed']).toBe(true); expect(content(view.root)).toContain('置顶未成功');
      await view.label('切换窗口置顶').props.onClick(); await flush();
      expect(controls.setAlwaysOnTop).toHaveBeenLastCalledWith(false); expect(view.label('切换窗口置顶').props['aria-pressed']).toBe(false);
      await view.label('拖动排行榜窗口').props.onPointerdown({ preventDefault() {} });
      expect(controls.startDrag).toHaveBeenCalledOnce();
      await view.label('关闭排行榜窗口').props.onClick(); expect(controls.close).toHaveBeenCalledOnce();
    } finally { view.app.unmount(); }
  });
  it('loads only actual runtime icon keys in batches no larger than 32, keeping names on icon failure', async () => {
    vi.useFakeTimers();
    const state = fixture(35); state.statistics.people.forEach((person, index) => { person.person!.iconKey = `person:runtime-${index}`; });
    let failIcons = false;
    const view = await mount(async method => {
      if (method === 'tower.visuals') { if (failIcons) throw new Error('图标 fixture 失败'); return { schemaVersion: 1, sheets: {}, sprites: {}, warnings: [] }; }
      return structuredClone(state);
    });
    try {
      view.label('排行人数').props['onUpdate:modelValue'](null); await flush();
      const calls = vi.mocked(view.host.callWorker).mock.calls.filter(([method]) => method === 'tower.visuals');
      expect(calls.every(([, params]) => (params?.iconKeys as string[]).length <= 32)).toBe(true);
      expect(new Set(calls.flatMap(([, params]) => params?.iconKeys as string[])).size).toBe(35);
      failIcons = true; state.statistics.people[0].person!.iconKey = 'person:changed';
      await vi.advanceTimersByTimeAsync(1000); await flush();
      expect(content(view.root)).toContain('图标 fixture 失败');
      expect(content(view.root)).toContain('人物34');
      const failedCount = vi.mocked(view.host.callWorker).mock.calls.filter(([method]) => method === 'tower.visuals').length;
      await vi.advanceTimersByTimeAsync(2000); await flush();
      expect(vi.mocked(view.host.callWorker).mock.calls.filter(([method]) => method === 'tower.visuals')).toHaveLength(failedCount);
      failIcons = false; view.button('重试图标').props.onClick(); await flush();
      expect(content(view.root)).not.toContain('图标 fixture 失败');
    } finally { view.app.unmount(); }
  });
  it('opens a bounded statistics surface from the main panel and makes old-host or open failures explicit', async () => {
    const old = await mount(async () => fixture(), { main: true });
    try { await old.button('透明排行榜').props.onClick(); await flush(); expect(content(old.root)).toContain('需升级宿主以支持透明窗口'); }
    finally { old.app.unmount(); }
    const controls = surface(); const view = await mount(async () => fixture(), { main: true, surface: controls });
    try {
      await view.button('透明排行榜').props.onClick(); await flush();
      expect(controls.open).toHaveBeenCalledWith({ view: 'statistics', title: '工作与战斗排行', width: 440, height: 700,
        alwaysOnTop: true, context: { gameDir: 'E:/synthetic-game', kind: 'work', numberMode: 'raw', rankingFilter: filters({ kind: 'work' }) } });
      controls.open.mockRejectedValueOnce(new Error('fixture 创建失败'));
      await view.button('透明排行榜').props.onClick(); await flush();
      expect(content(view.root)).toContain('透明排行榜未能打开：fixture 创建失败');
    } finally { view.app.unmount(); }
  });
});
