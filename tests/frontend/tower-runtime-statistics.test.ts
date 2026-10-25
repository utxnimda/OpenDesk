import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import * as Vue from 'vue';
import { createRenderer, h, nextTick, shallowRef, ssrContextKey } from 'vue';
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc';
import ts from 'typescript';
import { afterEach, describe, expect, it, vi } from 'vitest';
import RuntimeStatisticsPanel from '../../apps/features/tower_assistant/frontend/RuntimeStatisticsPanel.vue';
import TowerAssistantPage from '../../apps/features/tower_assistant/frontend/TowerAssistantPage.vue';
import { createStatisticsClient } from '../../apps/features/tower_assistant/frontend/statisticsClient';
import { useRuntimeStatistics } from '../../apps/features/tower_assistant/frontend/useRuntimeStatistics';
import type { DetailSnapshot, LatestEventSelector, OutputEvent, StatisticsExplanation, StatisticsResponse } from '../../apps/features/tower_assistant/frontend/statisticsTypes';
import type { LimitedHost, ModuleServices } from '../../apps/framework/module-sdk/src/index';

function fixture(): StatisticsResponse {
  return {
    schemaVersion: 1,
    capture: { state: 'connected', message: '测试 fixture：游戏事件采集已连接', installed: true, gameRunning: true, canInstall: false, canRemove: false, coverage: ['测试事件'], gameDir: 'E:/synthetic-game' },
    statistics: {
      schemaVersion: 1, sessionId: 'session-one', state: 'stopped', reason: '', elapsedRealSeconds: 10,
      eventCount: 3, retainedEventCount: 3, lastSequence: 13,
      workCategories: ['建造', '采集'],
      summary: { work: { amount: 1002000, count: 2 }, damage: { amount: 100, perRealSecond: 10, count: 1 } },
      people: [
        { personId: 'p1', personName: '工匠甲', person: { profession: '剑修', job: '建造', appearance: 'A1', attributes: [{ id: 'strength', label: '力量', value: 120 }] },
          work: [{ category: '建造', amount: 1000000, count: 1, latestAmount: 1000000, averageAmount: 1000000, latestSequence: 11 }],
          damage: { amount: 100, count: 1, perRealSecond: 10, types: [{ damageType: 'physical', amount: 100, count: 1, perRealSecond: 10, averageAmount: 100, latestAmount: 100, latestSequence: 13 }], attacks: [{ attackKind: 'basic', skillId: '1', skillName: '普通攻击', amount: 100, count: 1, perRealSecond: 10, averageAmount: 100, latestAmount: 100, latestSequence: 13, actionCount: null, averagePerAction: null }] } },
        { personId: 'p2', personName: '工匠乙', person: null, work: [{ category: '采集', amount: 2000, count: 1, latestAmount: 2000, averageAmount: 2000, latestSequence: 12 }], damage: { amount: 0, count: 0, perRealSecond: 0, types: [], attacks: [] } },
      ],
      totals: [
        { kind: 'work', personId: 'p1', personName: '工匠甲', category: '建造', count: 1, amount: 1000000, effectiveAmount: 10000, effectiveCount: 1, effectiveComplete: true, overflowAmount: 990000, latestAmount: 1000000, averageAmount: 1000000, perRealSecond: 100000 },
        { kind: 'work', personId: 'p2', personName: '工匠乙', category: '采集', count: 1, amount: 2000, effectiveAmount: 0, effectiveCount: 0, effectiveComplete: false, overflowAmount: null, latestAmount: 2000, averageAmount: 2000, perRealSecond: 200 },
        { kind: 'damage', personId: 'p1', personName: '工匠甲', category: '普攻', count: 1, amount: 100, effectiveAmount: 10, effectiveCount: 1, effectiveComplete: true, overflowAmount: 90, latestAmount: 100, averageAmount: 100, perRealSecond: 10 },
      ],
      events: [
        { schemaVersion: 1, sequence: 11, kind: 'work', personId: 'p1', personName: '工匠甲', category: '建造', amount: 1000000, effectiveAmount: 10000, overflowAmount: 990000, observedAfterSeconds: 2, gameTime: 30 },
        { schemaVersion: 1, sequence: 12, kind: 'work', personId: 'p2', personName: '工匠乙', category: '采集', amount: 2000, overflowAmount: null, observedAfterSeconds: 4 },
        { schemaVersion: 1, sequence: 13, kind: 'damage', personId: 'p1', personName: '工匠甲', category: '普攻', amount: 100, effectiveAmount: 10, overflowAmount: 90, observedAfterSeconds: 5, gameTime: 33, targetName: '测试敌人', formulaCoverage: 'main-product', formula: { expression: 5579, inputs: [100, 1, 1, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1], result: 100 } },
      ],
    },
  };
}
function explanation(matches = true): StatisticsExplanation {
  return { schemaVersion: 1, sessionId: 'session-one', sequence: 13, formulaCoverage: 'main-product', message: '', formula: {
    expression: 5579, inputs: Array(14).fill(1), result: 100, calculated: matches ? 100 : 99, matches,
    text: '测试公式：向下取整[面板攻击 × 技能系数]',
    terms: Array.from({ length: 14 }, (_, index) => ({ id: `term${index}`, label: `输入${index + 1}`, value: index === 0 ? 0.00012345 : 1, mode: 'multiplier' as const })),
    sensitivity: [{ id: 'attack', label: '面板攻击', changeLabel: '+1%（相对当前值）', baseline: 100, adjusted: 101, delta: 1, gainPercent: 1 }],
    limitation: '只改变本次命中的一个输入；不包含攻速、冷却或投入成本。',
  } };
}
function latestExplanation(latest: LatestEventSelector, sequence = 614): StatisticsExplanation {
  const event = latest.kind === 'work' ? fixture().statistics.events[0] : fixture().statistics.events[2];
  return { schemaVersion: 1, sessionId: 'session-one', sequence, formulaCoverage: 'unavailable', formula: null, workFormula: null,
    selection: { mode: 'latest', ...latest }, message: '最近事件原始快照',
    event: { ...event, sequence, personId: latest.personId, amount: 123456,
      ...(latest.kind === 'work' ? { category: latest.category } : { attackKind: latest.attackKind, ...(latest.skillId === null ? {} : { skillId: latest.skillId }) }),
      person: { profession: '读取时职业' } } };
}

function receiptFixture(): StatisticsResponse {
  const state = fixture();
  state.statistics.events = state.statistics.events.map(event => ({ ...event,
    detailSnapshot: { schemaVersion: 1, payload: `opaque worker receipt for event ${event.sequence}`, signature: 'a'.repeat(64) } }));
  return state;
}
function receiptExplanation(event: OutputEvent): StatisticsExplanation {
  return { ...(event.kind === 'damage' ? explanation() : { schemaVersion: 1 as const, formula: null, formulaCoverage: 'unavailable', message: '' }),
    sessionId: 'session-one', sequence: event.sequence, event: { ...structuredClone(event), person: { profession: '原次人物职业' } },
    ...(event.kind === 'work' ? { workFormula: { text: '原次工作公式', result: event.amount, calculated: event.amount, matches: true,
      terms: [{ id: 'work', label: '原次工作输入', value: event.amount }], steps: [], limitation: '原次工作输入快照' } } : {}) };
}

type TestNode = { tag: string; text: string; props: Record<string, any>; children: TestNode[]; parent: TestNode | null; value: unknown; options: TestNode[] };
function node(tag: string, text = ''): TestNode {
  return { tag, text, props: {}, children: [], parent: null, value: '', style: {}, tagName: tag.toUpperCase(), multiple: false,
    addEventListener() {}, removeEventListener() {}, getRootNode() { return null; },
    get options() { return this.children.filter((entry: TestNode) => entry.tag === 'option'); },
  } as TestNode;
}
function descendants(root: TestNode): TestNode[] { return [root, ...root.children.flatMap(descendants)]; }
function content(root: TestNode): string { return root.text + root.children.map(content).join(''); }
function compilePanel(component: Vue.Component = RuntimeStatisticsPanel, name = 'RuntimeStatisticsPanel') {
  const filename = resolve(`apps/features/tower_assistant/frontend/${name}.vue`);
  const { descriptor } = parse(readFileSync(filename, 'utf8'), { filename });
  const bindings = compileScript(descriptor, { id: 'runtime-statistics' }).bindings;
  const template = compileTemplate({ source: descriptor.template!.content, filename, id: 'runtime-statistics', compilerOptions: { bindingMetadata: bindings } });
  if (template.errors.length) throw new Error(template.errors.join('\n'));
  const code = ts.transpileModule(template.code, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const exports: { render?: unknown } = {};
  new Function('require', 'exports', code)((name: string) => { if (name !== 'vue') throw new Error(name); return Vue; }, exports);
  Object.assign(component, { render: exports.render });
}
async function flush() { for (let index = 0; index < 5; index++) { await Promise.resolve(); await nextTick(); } }
async function mountPanel(call: (method: string, params?: Record<string, unknown>) => Promise<unknown>, options: { gameDir?: string; page?: boolean; runtime?: (state: ReturnType<typeof useRuntimeStatistics>) => void; surface?: LimitedHost['surface'] } = {}) {
  vi.stubGlobal('document', { activeElement: null });
  vi.stubGlobal('Document', class {}); vi.stubGlobal('ShadowRoot', class {});
  const renderer = createRenderer<TestNode, TestNode>({
    createElement: (tag) => node(tag), createText: (text) => node('#text', text), createComment: (text) => node('#comment', text),
    setText: (entry, text) => { entry.text = text; }, setElementText: (entry, text) => { entry.text = text; entry.children = []; },
    parentNode: (entry) => entry.parent, nextSibling: (entry) => entry.parent?.children[entry.parent.children.indexOf(entry) + 1] ?? null,
    patchProp: (entry, key, _before, value) => { entry.props[key] = value; if (key === 'value') entry.value = value; },
    insert(entry, parent, anchor) { if (entry.parent) entry.parent.children.splice(entry.parent.children.indexOf(entry), 1); entry.parent = parent; const index = anchor ? parent.children.indexOf(anchor) : -1; if (index < 0) parent.children.push(entry); else parent.children.splice(index, 0, entry); },
    remove(entry) { if (entry.parent) entry.parent.children.splice(entry.parent.children.indexOf(entry), 1); entry.parent = null; },
  });
  compilePanel();
  if (options.page) compilePanel(TowerAssistantPage, 'TowerAssistantPage');
  const host = { available: true, callWorker: vi.fn(call), ...(options.surface ? { surface: options.surface } : {}) } as unknown as LimitedHost;
  const directory = shallowRef(options.gameDir ?? 'E:/synthetic-game');
  const root = node('root');
  const harness = { setup() { options.runtime!(useRuntimeStatistics(host, () => directory.value)); return () => h('div'); } };
  const app = renderer.createApp({ render: () => options.runtime ? h(harness) : options.page ? h(TowerAssistantPage, { services: { host } as ModuleServices }) : h(RuntimeStatisticsPanel, { host, gameDir: directory.value }) });
  app.provide(ssrContextKey, { modules: new Set() }); app.mount(root); await flush();
  const find = (predicate: (entry: TestNode) => boolean) => { const result = descendants(root).find(predicate); if (!result) throw new Error('Expected element not found'); return result; };
  const button = (text: string) => find((entry) => entry.tag === 'button' && content(entry).includes(text));
  return { app, root, host, directory, find, button };
}
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('runtime statistics transport', () => {
  it('forwards opaque receipts without parsing them and requires the original session and unchanged receipt', async () => {
    const event = receiptFixture().statistics.events[2];
    const original = receiptExplanation(event);
    const host = { available: true, callWorker: vi.fn(async () => original) } as unknown as LimitedHost;
    const client = createStatisticsClient(host);
    await expect(client.explain('x', event.sequence, undefined, event.detailSnapshot)).rejects.toThrow('原计时会话');
    expect(host.callWorker).not.toHaveBeenCalled();
    await expect(client.explain('x', event.sequence, 'session-one', event.detailSnapshot)).resolves.toMatchObject({ event });
    expect(host.callWorker).toHaveBeenLastCalledWith('tower.statistics.explain', { gameDir: 'x', sequence: 13, sessionId: 'session-one', snapshot: event.detailSnapshot });
    for (const invalid of [
      { ...original, event: undefined },
      { ...original, event: { ...original.event!, detailSnapshot: undefined } },
      { ...original, event: { ...original.event!, detailSnapshot: { ...event.detailSnapshot!, payload: 'a different receipt' } } },
      { ...original, event: { ...original.event!, detailSnapshot: { ...event.detailSnapshot!, signature: 'b'.repeat(64) } } },
      { ...original, sessionId: 'session-two' },
    ]) {
      vi.mocked(host.callWorker).mockResolvedValueOnce(invalid);
      await expect(client.explain('x', event.sequence, 'session-one', event.detailSnapshot)).rejects.toThrow();
    }
  });
  it('rejects malformed receipts instead of silently falling back to the rolling history', async () => {
    const receipt = receiptFixture().statistics.events[0].detailSnapshot!;
    const host = { available: true, callWorker: vi.fn() } as unknown as LimitedHost;
    const client = createStatisticsClient(host);
    for (const invalid of [null, [], {}, { ...receipt, schemaVersion: '1' }, { ...receipt, schemaVersion: 2 },
      { ...receipt, payload: '' }, { ...receipt, payload: '汉'.repeat(4097) }, { ...receipt, signature: 'A'.repeat(64) },
      { ...receipt, signature: 'z'.repeat(64) }, { ...receipt, extra: true }]) {
      await expect(client.explain('x', 11, 'session-one', invalid as DetailSnapshot)).rejects.toThrow('不能降级');
    }
    expect(host.callWorker).not.toHaveBeenCalled();
    const invalidState = fixture();
    Object.assign(invalidState.statistics.events[0], { detailSnapshot: undefined });
    vi.mocked(host.callWorker).mockResolvedValueOnce(invalidState);
    await expect(client.request('statistics.status', 'x')).rejects.toThrow('不能降级');
    expect(host.callWorker).toHaveBeenCalledTimes(1);
  });
  it('requires latest session identity and validates both selector echo and the actual event group', async () => {
    const work: LatestEventSelector = { kind: 'work', personId: 'p1', category: '建造' };
    const damage: LatestEventSelector = { kind: 'damage', personId: 'p1', attackKind: 'skill', skillId: '42' };
    const workResult = latestExplanation(work);
    const damageResult = latestExplanation(damage);
    const host = { available: true, callWorker: vi.fn(async () => workResult) } as unknown as LimitedHost;
    const client = createStatisticsClient(host);
    await expect(client.explainLatest('x', work, '')).rejects.toThrow('计时会话');
    expect(host.callWorker).not.toHaveBeenCalled();
    await client.explainLatest('x', work, 'session-one');
    expect(host.callWorker).toHaveBeenLastCalledWith('tower.statistics.explain', { gameDir: 'x', sessionId: 'session-one', latest: work });
    for (const invalid of [
      { ...workResult, selection: undefined }, { ...workResult, event: undefined },
      { ...workResult, selection: { mode: 'latest', ...work, personId: 'p2' } },
      { ...workResult, selection: { mode: 'latest', ...work, category: '采矿' } },
      { ...workResult, event: { ...workResult.event!, personId: 'p2' } },
      { ...workResult, event: { ...workResult.event!, category: '采矿' } },
      { ...workResult, event: { ...workResult.event!, sequence: 615 } },
    ]) {
      vi.mocked(host.callWorker).mockResolvedValueOnce(invalid);
      await expect(client.explainLatest('x', work, 'session-one')).rejects.toThrow('不一致');
    }
    for (const invalid of [
      { ...damageResult, selection: { mode: 'latest', ...damage, attackKind: 'periodic' } },
      { ...damageResult, selection: { mode: 'latest', ...damage, skillId: '43' } },
      { ...damageResult, event: { ...damageResult.event!, attackKind: 'periodic' } },
      { ...damageResult, event: { ...damageResult.event!, skillId: '43' } },
    ]) {
      vi.mocked(host.callWorker).mockResolvedValueOnce(invalid);
      await expect(client.explainLatest('x', damage, 'session-one')).rejects.toThrow('不一致');
    }
    vi.mocked(host.callWorker).mockResolvedValueOnce({ ...workResult, sessionId: 'new-session' });
    await expect(client.explainLatest('x', work, 'session-one')).rejects.toThrow('计时会话已改变');
    const unknown: LatestEventSelector = { kind: 'damage', personId: 'p1', attackKind: 'unknown', skillId: null };
    vi.mocked(host.callWorker).mockResolvedValueOnce(latestExplanation(unknown));
    await expect(client.explainLatest('x', unknown, 'session-one')).resolves.toMatchObject({ sequence: 614 });
  });
  it('never substitutes a latest response or a different sequence for an exact historical event', async () => {
    const latest = latestExplanation({ kind: 'work', personId: 'p1', category: '建造' }, 11);
    const host = { available: true, callWorker: vi.fn(async () => latest) } as unknown as LimitedHost;
    const client = createStatisticsClient(host);
    await expect(client.explain('x', 11, 'session-one')).rejects.toThrow('不一致');
    vi.mocked(host.callWorker).mockResolvedValueOnce({ ...latest, selection: undefined, sequence: 12, event: { ...latest.event!, sequence: 12 } });
    await expect(client.explain('x', 11, 'session-one')).rejects.toThrow('不一致');
    expect(vi.mocked(host.callWorker).mock.calls.every(([, params]) => params?.sequence === 11 && !('latest' in params!))).toBe(true);
  });
  it('accepts explicit unknown capture facts and rejects missing or non-boolean facts and mutation flags', async () => {
    const state = fixture(); state.capture.installed = null; state.capture.gameRunning = null;
    const host = { available: true, callWorker: vi.fn(async () => state) } as unknown as LimitedHost;
    const client = createStatisticsClient(host);
    expect((await client.request('capture.status', 'E:/synthetic-game')).capture.installed).toBeNull();
    for (const [field, value] of [['installed', undefined], ['gameRunning', 'false'], ['canInstall', 'true'], ['canRemove', null]] as const) {
      vi.mocked(host.callWorker).mockResolvedValueOnce({ ...state, capture: { ...state.capture, [field]: value } });
      await expect(client.request('capture.status', 'E:/synthetic-game')).rejects.toThrow('不支持的数据');
    }
  });
  it('uses named, directory-scoped operations and rejects unavailable or unsupported responses', async () => {
    const host = { available: true, callWorker: vi.fn(async () => fixture()) } as unknown as LimitedHost;
    const client = createStatisticsClient(host);
    for (const operation of ['capture.status', 'capture.install', 'capture.remove', 'statistics.start', 'statistics.stop', 'statistics.status'] as const) {
      await client.request(operation, ' E:/synthetic-game ');
      expect(host.callWorker).toHaveBeenLastCalledWith(`tower.${operation}`, { gameDir: 'E:/synthetic-game' });
    }
    await expect(client.request('capture.status', ' ')).rejects.toThrow('游戏安装目录');
    await expect(createStatisticsClient({ ...host, available: false }).request('capture.status', 'x')).rejects.toThrow('桌面能力');
    vi.mocked(host.callWorker).mockResolvedValueOnce({ ...fixture(), schemaVersion: 2 });
    await expect(client.request('statistics.status', 'x')).rejects.toThrow('不支持的数据');
    vi.mocked(host.callWorker).mockResolvedValueOnce(explanation());
    await client.explain('x', 13);
    expect(host.callWorker).toHaveBeenLastCalledWith('tower.statistics.explain', { gameDir: 'x', sequence: 13 });
  });
});

describe('runtime statistics interactions', () => {
  it.each(['work', 'damage'] as const)('reopens frozen historical %s receipts after eviction and a new session without substituting a recent event', async kind => {
    vi.useFakeTimers();
    let state = receiptFixture();
    const original = structuredClone(state.statistics.events.find(event => event.kind === kind)!);
    let evicted = false;
    const view = await mountPanel(async (method, params) => {
      if (method !== 'tower.statistics.explain') return state;
      expect(evicted).toBe(true);
      if (params?.snapshot !== undefined) {
        expect(params).toEqual({ gameDir: 'E:/synthetic-game', sequence: original.sequence, sessionId: 'session-one', snapshot: original.detailSnapshot });
        return receiptExplanation(original);
      }
      throw new Error('该事件已不在保留明细或分类最近快照中');
    });
    try {
      if (kind === 'damage') { view.button('战斗伤害').props.onClick(); await flush(); }
      view.button('暂停明细刷新').props.onClick(); await flush();
      state = receiptFixture();
      state.statistics.events = Array.from({ length: 500 }, (_, index) => ({ ...state.statistics.events[2], sequence: index + 114 }));
      state.statistics.eventCount = 603; state.statistics.retainedEventCount = 500; state.statistics.lastSequence = 613;
      evicted = true;
      await vi.advanceTimersByTimeAsync(1000); await flush();
      expect(content(view.root)).toContain('后台采集与汇总继续更新');
      expect(content(view.root)).toContain('关闭后重开仍查看同一次事件');
      const clickOriginal = () => view.find(entry => entry.props['aria-label'] === `查看第 ${original.sequence} 次${kind === 'work' ? '工作' : '伤害'}详情`).props.onClick();
      for (let reopen = 0; reopen < 2; reopen++) {
        clickOriginal(); await flush();
        const detail = view.find(entry => entry.props['aria-label'] === '固定详情');
        expect(content(detail)).toContain(`#${original.sequence}`);
        expect(content(detail)).toContain('原次人物职业');
        expect(content(detail)).toContain(kind === 'work' ? '本次工作复算一致' : '本击复算一致');
        expect(content(detail)).not.toContain('#613');
        view.button('关闭详情').props.onClick(); await flush();
      }
      state = receiptFixture(); state.statistics.sessionId = 'session-two'; state.statistics.events = [];
      state.statistics.eventCount = 0; state.statistics.retainedEventCount = 0;
      await vi.advanceTimersByTimeAsync(1000); await flush();
      clickOriginal(); await flush();
      const detail = view.find(entry => entry.props['aria-label'] === '固定详情');
      expect(content(detail)).toContain('来自上一计时会话');
      expect(content(detail)).toContain(kind === 'work' ? '本次工作复算一致' : '本击复算一致');
      expect(content(detail)).not.toContain('已不在保留');
      await vi.advanceTimersByTimeAsync(2000); await flush();
      expect(vi.mocked(view.host.callWorker).mock.calls.filter(([method]) => method === 'tower.statistics.explain')).toHaveLength(3);
    } finally { view.app.unmount(); }
  });
  it('rejects replaced historical identity, amount and raw inputs while retaining the clicked snapshot', async () => {
    const original = receiptFixture().statistics.events[2];
    let result = receiptExplanation(original);
    let runtime!: ReturnType<typeof useRuntimeStatistics>;
    const view = await mountPanel(async method => method === 'tower.statistics.explain' ? result : receiptFixture(), { runtime: value => { runtime = value; } });
    try {
      for (const changed of [
        { sequence: 14 }, { personId: 'other' }, { personName: '替代人物' }, { kind: 'work' as const }, { category: '替代类型' },
        { amount: 101 }, { effectiveAmount: 11 }, { overflowAmount: 91 }, { gameTime: 34 }, { observedAfterSeconds: 6 },
        { formula: { ...original.formula!, inputs: Array(14).fill(2) } },
      ]) {
        result = receiptExplanation(original); Object.assign(result.event!, changed);
        runtime.pinEvent(original); await flush();
        expect(runtime.pinned.value!.event).toEqual(original);
        expect(runtime.pinned.value!.explanation).toBeNull();
        expect(runtime.pinned.value!.error).toContain('不一致');
      }
    } finally { view.app.unmount(); }
  });
  it('ignores receipt replies arriving after a new selection, close, or directory change', async () => {
    const state = receiptFixture();
    const resolvers: ((result: StatisticsExplanation) => void)[] = [];
    let runtime!: ReturnType<typeof useRuntimeStatistics>;
    const view = await mountPanel(async method => method === 'tower.statistics.explain'
      ? new Promise<StatisticsExplanation>(resolve => { resolvers.push(resolve); }) : state, { runtime: value => { runtime = value; } });
    try {
      runtime.pinEvent(state.statistics.events[0]); await flush();
      runtime.pinEvent(state.statistics.events[2]); await flush();
      resolvers[0](receiptExplanation(state.statistics.events[0])); await flush();
      expect(runtime.pinned.value).toMatchObject({ event: { sequence: 13 }, explanation: null, loading: true });
      runtime.closePinned(); resolvers[1](receiptExplanation(state.statistics.events[2])); await flush();
      expect(runtime.pinned.value).toBeNull();
      runtime.pinEvent(state.statistics.events[0]); await flush();
      view.directory.value = 'E:/other-game'; await flush();
      resolvers[2](receiptExplanation(state.statistics.events[0])); await flush();
      expect(runtime.pinned.value).toMatchObject({ event: { sequence: 11 }, explanation: null, loading: false, gameDir: 'E:/synthetic-game' });
      expect(runtime.pinned.value!.error).toContain('游戏目录已改变');
    } finally { view.app.unmount(); }
  });
  it('uses the receipt from a successful latest lookup for later exact retries', async () => {
    const latest: LatestEventSelector = { kind: 'work', personId: 'p1', category: '建造' };
    const original = latestExplanation(latest);
    original.event!.detailSnapshot = receiptFixture().statistics.events[0].detailSnapshot;
    const exact = { ...structuredClone(original), selection: undefined };
    let runtime!: ReturnType<typeof useRuntimeStatistics>;
    const view = await mountPanel(async (method, params) => method === 'tower.statistics.explain'
      ? params?.latest ? original : exact : receiptFixture(), { runtime: value => { runtime = value; } });
    try {
      runtime.pinLatest(latest, { personName: '工匠甲', category: '建造' }); await flush();
      original.event!.amount = 999999;
      await runtime.explainPinned(); await flush();
      expect(view.host.callWorker).toHaveBeenLastCalledWith('tower.statistics.explain', { gameDir: 'E:/synthetic-game', sequence: 614, sessionId: 'session-one', snapshot: exact.event!.detailSnapshot });
      expect(runtime.pinned.value).toMatchObject({ event: { sequence: 614, amount: 123456 }, latest: null, selectionMode: 'latest', error: '' });
    } finally { view.app.unmount(); }
  });
  it('resets hidden sort modes when switching between work and damage rankings', async () => {
    const state = fixture();
    state.statistics.people[0].damage.count = 50;
    state.statistics.people[0].damage.types[0].count = 50;
    state.statistics.people[1].damage = { amount: 300, count: 1, perRealSecond: 30,
      types: [{ damageType: 'physical', amount: 300, count: 1, latestAmount: 300, averageAmount: 300, latestSequence: 14, perRealSecond: 30 }], attacks: [] };
    const open = vi.fn(async () => {});
    const view = await mountPanel(async () => state, { surface: { context: null, open, close: async () => {}, setAlwaysOnTop: async () => {}, startDrag: async () => {} } });
    try {
      view.find(entry => entry.props['aria-label'] === '排行排序').props['onUpdate:modelValue']('count'); await flush();
      view.button('战斗伤害').props.onClick(); await flush();
      const table = view.find(entry => entry.props['aria-label'] === '力 · 物理排行表');
      expect(descendants(table).filter(entry => entry.props['data-person-id']).map(entry => entry.props['data-person-id'])).toEqual(['p2', 'p1']);
      await view.button('透明排行榜').props.onClick(); await flush();
      expect(open).toHaveBeenLastCalledWith(expect.objectContaining({ context: expect.objectContaining({ rankingFilter: expect.objectContaining({ kind: 'damage', sort: 'amount' }) }) }));
      view.find(entry => entry.props['aria-label'] === '排行排序').props['onUpdate:modelValue']('dps'); await flush();
      view.button('工作量').props.onClick(); await flush();
      await view.button('透明排行榜').props.onClick(); await flush();
      expect(open).toHaveBeenLastCalledWith(expect.objectContaining({ context: expect.objectContaining({ rankingFilter: expect.objectContaining({ kind: 'work', sort: 'amount' }) }) }));
    } finally { view.app.unmount(); }
  });
  it('supports work multiselect and sorting without disturbing a fixed person or the independent detail filters', async () => {
    const state = fixture();
    state.statistics.people.push({ ...structuredClone(state.statistics.people[0]), personId: 'p3', personName: '工匠丙',
      work: [{ category: '建造', amount: 2000000, count: 20, latestAmount: 100, averageAmount: 100000, latestSequence: 14 }] });
    const view = await mountPanel(async () => state);
    try {
      const table = view.find(entry => entry.props['aria-label'] === '建造排行表');
      const ids = () => descendants(table).filter(entry => entry.props['data-person-id']).map(entry => entry.props['data-person-id']);
      expect(ids()).toEqual(['p3', 'p1']);
      view.find(entry => entry.props['aria-label'] === '查看工匠甲人物详情').props.onClick(); await flush();
      const detail = view.find(entry => entry.props['aria-label'] === '固定详情');
      view.find(entry => entry.props['aria-label'] === '排行排序').props['onUpdate:modelValue']('latest'); await flush();
      expect(ids()).toEqual(['p1', 'p3']); expect(content(detail)).toContain('工匠甲');
      view.find(entry => entry.props['aria-label'] === '筛选统计人物').props['onUpdate:modelValue']('p2'); await flush();
      expect(ids()).toEqual(['p1', 'p3']);
      expect(descendants(view.root).some(entry => entry.props['aria-label'] === '查看第 11 次工作详情')).toBe(false);
      view.find(entry => entry.props['aria-label'] === '排行工作：建造').props.onChange(); await flush();
      expect(descendants(view.root).filter(entry => entry.props['data-board-key']).map(entry => entry.props['data-board-key'])).toEqual(['work:%E9%87%87%E9%9B%86']);
      expect(content(detail)).toContain('工匠甲');
      expect(view.find(entry => entry.props['aria-label'] === '查看第 12 次工作详情')).toBeTruthy();
      view.button('全选工作').props.onClick(); await flush();
      expect(descendants(view.root).filter(entry => entry.props['data-board-key'])).toHaveLength(2);
    } finally { view.app.unmount(); }
  });
  it('applies Top N and search to each work board, while clearing selection stays empty after new categories arrive', async () => {
    vi.useFakeTimers(); let state = fixture();
    state.statistics.people = Array.from({ length: 12 }, (_, index) => ({ ...structuredClone(fixture().statistics.people[0]), personId: `rank${index}`, personName: `排行人物${index}` }));
    const view = await mountPanel(async () => state);
    try {
      const rows = () => descendants(view.find(entry => entry.props['aria-label'] === '建造排行表')).filter(entry => entry.props['data-person-id']);
      expect(rows()).toHaveLength(10);
      view.find(entry => entry.props['aria-label'] === '每榜显示人数').props['onUpdate:modelValue'](5); await flush(); expect(rows()).toHaveLength(5);
      view.find(entry => entry.props['aria-label'] === '每榜显示人数').props['onUpdate:modelValue'](null); await flush(); expect(rows()).toHaveLength(12);
      view.find(entry => entry.props['aria-label'] === '搜索排行人物').props['onUpdate:modelValue']('排行人物11'); await flush();
      expect(rows()).toHaveLength(1); expect(content(rows()[0])).toContain('排行人物11');
      view.button('清空工作').props.onClick(); await flush();
      state = structuredClone(state); state.statistics.workCategories.push('研究');
      await vi.advanceTimersByTimeAsync(1000); await flush();
      expect(descendants(view.root).filter(entry => entry.props['data-board-key'])).toHaveLength(0);
      expect(view.find(entry => entry.props['aria-label'] === '排行工作：研究').props.checked).toBe(false);
      expect(content(view.root)).toContain('暂无符合排行筛选的榜单');
    } finally { view.app.unmount(); }
  });
  it('combines selected damage types or separates professions including unknown, without changing attack details', async () => {
    const state = fixture();
    const makeType = (damageType: 'physical' | 'magic', amount: number) => ({ damageType, amount, count: 5, perRealSecond: amount / 10, latestAmount: 1, latestSequence: 13, averageAmount: amount / 5 });
    state.statistics.people[0].damage = { ...state.statistics.people[0].damage, amount: 1000, count: 10, types: [makeType('physical', 400), makeType('magic', 600)] };
    state.statistics.people[1].damage = { ...state.statistics.people[1].damage, amount: 1000, count: 10, types: [makeType('physical', 500), makeType('magic', 500)] };
    state.statistics.summary.damage.amount = 2000;
    const view = await mountPanel(async () => state);
    try {
      view.button('战斗伤害').props.onClick(); await flush();
      view.find(entry => entry.props['aria-label'] === '伤害分榜方式').props['onUpdate:modelValue']('combined'); await flush();
      const board = view.find(entry => entry.props['data-board-key'] === 'damage:combined');
      expect(content(board)).toContain('2,000');
      view.find(entry => entry.props['aria-label'] === '排行伤害：智 · 魔法').props.onChange(); await flush();
      expect(content(board)).toContain('900'); expect(content(board)).toContain('55.6%'); expect(content(board)).toContain('44.4%');
      const attacks = view.find(entry => entry.props['aria-label'] === '普攻与技能统计');
      expect(content(attacks)).toContain('普通攻击'); expect(content(attacks)).toContain('100');
      expect(content(view.find(entry => entry.props['aria-label'] === '当前种类全局累计'))).toContain('2,000');
      view.find(entry => entry.props['aria-label'] === '伤害分榜方式').props['onUpdate:modelValue']('profession'); await flush();
      expect(descendants(view.root).filter(entry => entry.props['data-board-key'])).toHaveLength(2);
      expect(content(view.find(entry => entry.props['data-board-key'] === 'damage:profession:unknown'))).toContain('500');
      view.find(entry => entry.props['aria-label'] === '排行职业：剑修').props.onChange(); await flush();
      expect(descendants(view.root).filter(entry => entry.props['data-board-key']).map(entry => entry.props['data-board-key'])).toEqual(['damage:profession:unknown']);
      expect(content(attacks)).toContain('工匠甲');
      view.button('清空伤害').props.onClick(); await flush();
      expect(descendants(view.root).filter(entry => entry.props['data-board-key'])).toHaveLength(0);
    } finally { view.app.unmount(); }
  });
  it('passes the current independent ranking filter to a bounded transparent window context', async () => {
    const open = vi.fn(async () => {});
    const surface = { context: null, open, close: async () => {}, setAlwaysOnTop: async () => {}, startDrag: async () => {} };
    const view = await mountPanel(async () => fixture(), { surface });
    try {
      view.find(entry => entry.props['aria-label'] === '排行工作：采集').props.onChange();
      view.find(entry => entry.props['aria-label'] === '搜索排行人物').props['onUpdate:modelValue']('工匠');
      view.find(entry => entry.props['aria-label'] === '每榜显示人数').props['onUpdate:modelValue'](5);
      view.find(entry => entry.props['aria-label'] === '筛选统计人物').props['onUpdate:modelValue']('p2'); await flush();
      await view.button('透明排行榜').props.onClick(); await flush();
      expect(open).toHaveBeenCalledWith({ view: 'statistics', title: '工作与战斗排行', width: 440, height: 700, alwaysOnTop: true,
        context: { gameDir: 'E:/synthetic-game', kind: 'work', numberMode: 'raw', rankingFilter: { kind: 'work', query: '工匠',
          workCategories: ['建造'], damageTypes: null, professions: null, damageGrouping: 'type', sort: 'amount', limit: 5, hideZero: true } } });
      view.find(entry => entry.props['aria-label'] === '搜索排行人物').props['onUpdate:modelValue']('山'.repeat(3000)); await flush();
      await view.button('透明排行榜').props.onClick(); await flush();
      expect(open).toHaveBeenCalledTimes(1); expect(content(view.root)).toContain('8 KiB 限制');
    } finally { view.app.unmount(); }
  });
  it('switches a successful latest selection to the exact returned sequence for any formula retry', async () => {
    const latest: LatestEventSelector = { kind: 'work', personId: 'p1', category: '建造' };
    const original = latestExplanation(latest);
    let runtime!: ReturnType<typeof useRuntimeStatistics>;
    const view = await mountPanel(async (method, params) => {
      if (method !== 'tower.statistics.explain') return fixture();
      return params?.latest ? original : { ...latestExplanation(latest), selection: undefined };
    }, { runtime: value => { runtime = value; } });
    try {
      runtime.pinLatest(latest, { personName: '工匠甲', category: '建造' }); await flush();
      expect(runtime.pinned.value).toMatchObject({ event: { sequence: 614, amount: 123456 }, latest: null, selectionMode: 'latest' });
      original.event!.amount = 999999;
      expect(runtime.pinned.value!.event!.amount).toBe(123456);
      await runtime.explainPinned(); await flush();
      expect(view.host.callWorker).toHaveBeenLastCalledWith('tower.statistics.explain', { gameDir: 'E:/synthetic-game', sequence: 614, sessionId: 'session-one' });
      expect(runtime.pinned.value).toMatchObject({ event: { sequence: 614, amount: 123456 }, latest: null, selectionMode: 'latest' });
    } finally { view.app.unmount(); }
  });
  it('rejects an unrelated latest group without displaying stale output or retrying on polling', async () => {
    vi.useFakeTimers();
    const invalid = latestExplanation({ kind: 'work', personId: 'p1', category: '建造' });
    invalid.event!.category = '采矿';
    const view = await mountPanel(async method => method === 'tower.statistics.explain' ? invalid : fixture());
    try {
      view.find(entry => entry.props['aria-label'] === '查看工匠甲建造最近一次工作详情').props.onClick(); await flush();
      const detail = view.find(entry => entry.props['aria-label'] === '固定详情');
      expect(content(detail)).toContain('所选人物或类型不一致');
      expect(content(detail)).not.toContain('1,000,000'); expect(content(detail)).not.toContain('123,456');
      expect(content(detail)).not.toContain('#614'); expect(content(detail)).not.toContain('读取时职业');
      await vi.advanceTimersByTimeAsync(2000); await flush();
      expect(vi.mocked(view.host.callWorker).mock.calls.filter(([method]) => method === 'tower.statistics.explain')).toHaveLength(1);
    } finally { view.app.unmount(); }
  });
  it('ignores a late latest reply after a different group is selected or the game directory changes', async () => {
    const resolvers: ((value: StatisticsExplanation) => void)[] = [];
    let runtime!: ReturnType<typeof useRuntimeStatistics>;
    const view = await mountPanel(async method => method === 'tower.statistics.explain'
      ? new Promise<StatisticsExplanation>(resolve => { resolvers.push(resolve); }) : fixture(), { runtime: value => { runtime = value; } });
    try {
      const first: LatestEventSelector = { kind: 'work', personId: 'p1', category: '建造' };
      const second: LatestEventSelector = { kind: 'work', personId: 'p2', category: '采集' };
      runtime.pinLatest(first, { personName: '工匠甲', category: '建造' }); await flush();
      runtime.pinLatest(second, { personName: '工匠乙', category: '采集' }); await flush();
      resolvers[0](latestExplanation(first)); await flush();
      expect(runtime.pinned.value).toMatchObject({ summary: { personId: 'p2' }, event: null, loading: true });
      resolvers[1](latestExplanation(second, 615)); await flush();
      expect(runtime.pinned.value).toMatchObject({ event: { personId: 'p2', sequence: 615 } });
      runtime.pinLatest(first, { personName: '工匠甲', category: '建造' }); await flush();
      view.directory.value = 'E:/other-game'; await flush();
      resolvers[2](latestExplanation(first, 616)); await flush();
      expect(runtime.pinned.value!.event).toBeNull();
      expect(runtime.pinned.value!.loading).toBe(false);
      expect(runtime.pinned.value!.gameDir).toBe('E:/synthetic-game');
      expect(runtime.pinned.value!.error).toContain('游戏目录已改变');
    } finally { view.app.unmount(); }
  });
  it.each(['work', 'damage'] as const)('opens the current latest %s group atomically after 600-event eviction and same-group replacement', async (kind) => {
    vi.useFakeTimers();
    const state = fixture();
    state.statistics.events = Array.from({ length: 500 }, (_, index) => ({ ...fixture().statistics.events[2], sequence: index + 114, personId: 'burst-person', personName: '其他战斗人物' }));
    state.statistics.eventCount = 603; state.statistics.retainedEventCount = 500; state.statistics.lastSequence = 613;
    let resolveDetail!: (value: StatisticsExplanation) => void;
    const view = await mountPanel(async method => method === 'tower.statistics.explain' ? new Promise<StatisticsExplanation>(resolve => { resolveDetail = resolve; }) : state);
    try {
      if (kind === 'work') {
        const cell = view.find(entry => entry.props['aria-label'] === '查看工匠甲建造最近一次工作详情');
        expect(cell.props.disabled).not.toBe(true); cell.props.onClick();
      } else {
        view.button('战斗伤害').props.onClick(); await flush();
        const section = view.find(entry => entry.props['aria-label'] === '普攻与技能统计');
        const cell = descendants(section).find(entry => entry.tag === 'button')!;
        expect(cell.props.disabled).not.toBe(true); cell.props.onClick();
      }
      await flush();
      const oldSequence = kind === 'work' ? 11 : 13;
      const latest: LatestEventSelector = kind === 'work' ? { kind: 'work', personId: 'p1', category: '建造' }
        : { kind: 'damage', personId: 'p1', attackKind: 'basic', skillId: '1' };
      expect(view.host.callWorker).toHaveBeenLastCalledWith('tower.statistics.explain', { gameDir: 'E:/synthetic-game', latest, sessionId: 'session-one' });
      const detail = view.find(entry => entry.props['aria-label'] === '固定详情');
      expect(content(detail)).toContain('正在读取该人物/类型最近一次事件');
      expect(content(detail)).not.toContain('剑修');
      expect(content(detail)).not.toContain('人物资料来自采集事件');
      expect(content(detail)).not.toContain(`#${oldSequence}`);
      expect(content(detail)).not.toContain(kind === 'work' ? '1,000,000' : '100');
      const sequence = 614;
      resolveDetail({ schemaVersion: 1, sessionId: 'session-one', sequence, formulaCoverage: 'unavailable', formula: null, workFormula: null,
        selection: { mode: 'latest', ...latest },
        event: { ...fixture().statistics.events.find(event => event.sequence === oldSequence)!, sequence, amount: 123456,
          ...(kind === 'damage' ? { attackKind: 'basic', skillId: '1' } : {}), person: { profession: '读取时最新事件职业' } }, message: '原子读取的最近事件快照' });
      await flush();
      expect(content(detail)).toContain('读取时最新事件职业'); expect(content(detail)).toContain('123,456');
      expect(content(detail)).toContain(`人物资料来自采集事件 #${sequence}`);
      expect(content(detail)).toContain('已固定读取时最近一次事件');
      state.statistics.people[0].work[0].latestSequence = 615; state.statistics.people[0].work[0].latestAmount = 999999;
      await vi.advanceTimersByTimeAsync(2000); await flush();
      expect(content(detail)).toContain('#614'); expect(content(detail)).toContain('123,456'); expect(content(detail)).not.toContain('999,999');
      expect(vi.mocked(view.host.callWorker).mock.calls.filter(([method]) => method === 'tower.statistics.explain')).toHaveLength(1);
    } finally { view.app.unmount(); }
  });
  it('renders one work board per category and keeps actual recent values distinct from cumulative output', async () => {
    const state = fixture();
    state.statistics.people[0].work[0] = { category: '建造', amount: 1234567, count: 7, latestAmount: 250000, averageAmount: 176366.7, latestSequence: 11 };
    const view = await mountPanel(async () => state);
    try {
      const table = view.find(entry => entry.props['aria-label'] === '建造排行表');
      const heads = descendants(table).filter(entry => entry.tag === 'th').map(content);
      expect(heads).toEqual(['人物', '最近实际量', '工作次数', '累计工作量']);
      expect(descendants(view.root).filter(entry => entry.props['data-board-key'])).toHaveLength(2);
      const cell = view.find(entry => entry.props['aria-label'] === '查看工匠甲建造最近一次工作详情');
      expect(content(cell)).toContain('250,000');
      const columns = descendants(table).filter(entry => entry.tag === 'td').map(content);
      expect(columns.slice(1)).toEqual(['250,000', '7', '1,234,567']);
      expect(content(table)).not.toContain('1,750,000');
      view.find(entry => entry.props['aria-label'] === '数值显示模式').props['onUpdate:modelValue']('compact'); await flush();
      expect(content(cell)).toContain('25 万');
      expect(content(table)).toContain('123.5 万');
      view.find(entry => entry.props['aria-label'] === '数值显示模式').props['onUpdate:modelValue']('raw'); await flush();
      expect(content(table)).toContain('1,234,567');
    } finally { view.app.unmount(); }
  });
  it('renders separate damage boards with core columns and keeps the truthful hit-count detail table', async () => {
    const state = fixture();
    const view = await mountPanel(async () => state);
    try {
      view.button('战斗伤害').props.onClick(); await flush();
      const table = view.find(entry => entry.props['aria-label'] === '力 · 物理排行表');
      expect(descendants(table).filter(entry => entry.tag === 'th').map(content)).toEqual(['人物', '完整伤害', 'DPS', '占比']);
      expect(content(table)).toContain('剑修');
      const attacks = view.find(entry => entry.props['aria-label'] === '普攻与技能统计');
      expect(content(attacks)).toContain('命中次数');
      expect(content(attacks)).toContain('平均每次命中');
      expect(descendants(attacks).filter(entry => entry.tag === 'th').map(content)).not.toContain('出手次数');
      expect(content(view.root)).not.toContain('实际扣血');
      expect(content(view.root)).not.toContain('过量伤害');
    } finally { view.app.unmount(); }
  });
  it('pins an event while a status request is pending and retains it after 600 newer events and a session change', async () => {
    vi.useFakeTimers(); let resolveStatus!: (value: StatisticsResponse) => void;
    const view = await mountPanel(async method => {
      if (method === 'tower.statistics.explain') return { ...explanation(), event: { ...fixture().statistics.events[2], person: { profession: '命中时职业' } } };
      if (method === 'tower.statistics.status') return new Promise<StatisticsResponse>(resolve => { resolveStatus = resolve; });
      return fixture();
    });
    try {
      view.button('战斗伤害').props.onClick(); await flush();
      await vi.advanceTimersByTimeAsync(1000); await flush();
      view.find(entry => entry.props['aria-label'] === '查看第 13 次伤害详情').props.onClick(); await flush();
      expect(view.host.callWorker).toHaveBeenLastCalledWith('tower.statistics.explain', { gameDir: 'E:/synthetic-game', sequence: 13, sessionId: 'session-one' });
      expect(content(view.root)).toContain('本击复算一致');
      const newer = fixture(); newer.statistics.sessionId = 'session-two'; newer.statistics.eventCount = 603;
      newer.statistics.summary.damage.amount = 987654321;
      newer.statistics.events = Array.from({ length: 500 }, (_, i) => ({ ...fixture().statistics.events[2], sequence: i + 114 }));
      resolveStatus(newer); await flush();
      const detail = view.find(entry => entry.props['aria-label'] === '固定详情');
      expect(content(detail)).toContain('#13');
      expect(content(detail)).toContain('命中时职业');
      expect(content(detail)).toContain('本击复算一致');
      expect(content(detail)).toContain('来自上一计时会话');
      expect(content(view.root)).toContain('987,654,321');
      expect(content(view.root)).toContain('明细显示已暂停');
      view.button('工作量').props.onClick(); await flush();
      expect(content(detail)).toContain('本击复算一致');
      view.button('关闭详情').props.onClick(); await flush();
      expect(descendants(view.root).some(entry => entry.props['aria-label'] === '固定详情')).toBe(false);
    } finally { view.app.unmount(); }
  });
  it('explains a clicked work cell with frozen input terms and does not substitute current person metadata', async () => {
    const result: StatisticsExplanation = { schemaVersion: 1, sessionId: 'session-one', sequence: 11, formulaCoverage: 'unavailable', formula: null, message: '',
      selection: { mode: 'latest', kind: 'work', personId: 'p1', category: '建造' },
      event: { ...fixture().statistics.events[0], person: { profession: '当时职业', job: '建造' } },
      workFormula: { text: '面板工作 × 任务系数 + 固定加值', result: 1000000, calculated: 1000000, matches: true,
        terms: [{ id: 'work', label: '面板工作', value: 123456.789 }, { id: 'factor', label: '任务系数', value: 2 }],
        steps: [{ label: '固定增加量', value: 1 }], limitation: '本次实际记录的工作输入。' } };
    const view = await mountPanel(async method => method === 'tower.statistics.explain' ? result : fixture());
    try {
      view.find(entry => entry.props['aria-label'] === '查看工匠甲建造最近一次工作详情').props.onClick(); await flush();
      const detail = view.find(entry => entry.props['aria-label'] === '固定详情');
      expect(content(detail)).toContain('当时职业'); expect(content(detail)).not.toContain('剑修');
      expect(content(detail)).toContain('本次工作复算一致');
      expect(content(detail)).toContain('固定增加量');
      view.find(entry => entry.props['aria-label'] === '数值显示模式').props['onUpdate:modelValue']('compact'); await flush();
      expect(content(detail)).toContain('123456.789');
    } finally { view.app.unmount(); }
  });
  it('loads captured portraits in bounded batches and freezes the person details on avatar click', async () => {
    vi.useFakeTimers(); let state = fixture();
    state.statistics.people = Array.from({ length: 35 }, (_, index) => ({ ...fixture().statistics.people[0], personId: `person:${index}`, personName: `角色${index}`,
      personSequence: 10 + index, person: { profession: '点击前职业', appearance: `A${index}`, iconKey: `person:A${index}`, attributes: [{ id: 'power', label: '力量', value: 100 }] } }));
    const view = await mountPanel(async method => method === 'tower.visuals' ? { schemaVersion: 1, sheets: {}, sprites: {}, warnings: [] } : state);
    try {
      await flush();
      const calls = vi.mocked(view.host.callWorker).mock.calls.filter(([method]) => method === 'tower.visuals');
      expect(calls).toHaveLength(2);
      expect((calls[0][1]?.iconKeys as string[])).toHaveLength(32);
      expect((calls[1][1]?.iconKeys as string[])).toHaveLength(3);
      view.find(entry => entry.props['aria-label'] === '查看角色0人物详情').props.onClick(); await flush();
      const detail = view.find(entry => entry.props['aria-label'] === '固定详情');
      expect(content(detail)).toContain('点击前职业');
      expect(content(detail)).toContain('人物资料来自采集事件 #10');
      state = fixture(); state.statistics.people[0].person = { profession: '后续职业' };
      await vi.advanceTimersByTimeAsync(1000); await flush();
      expect(content(detail)).toContain('点击前职业'); expect(content(detail)).not.toContain('后续职业');
      expect(vi.mocked(view.host.callWorker).mock.calls.some(([method]) => method === 'tower.inspect')).toBe(false);
    } finally { view.app.unmount(); }
  });
  it('retains an evicted event with a visible formula error and no automatic retry storm', async () => {
    vi.useFakeTimers();
    const view = await mountPanel(async method => { if (method === 'tower.statistics.explain') throw new Error('该事件已被明细容量淘汰'); return fixture(); });
    try {
      view.find(entry => entry.props['aria-label'] === '查看第 11 次工作详情').props.onClick(); await flush();
      await vi.advanceTimersByTimeAsync(3000); await flush();
      const detail = view.find(entry => entry.props['aria-label'] === '固定详情');
      expect(content(detail)).toContain('1,000,000'); expect(content(detail)).toContain('明细容量淘汰');
      expect(vi.mocked(view.host.callWorker).mock.calls.filter(([method]) => method === 'tower.statistics.explain')).toHaveLength(1);
    } finally { view.app.unmount(); }
  });
  it('shows empty directory and initial loading as unknown without offering installation', async () => {
    const view = await mountPanel(() => new Promise(() => {}), { gameDir: '' });
    try {
      expect(view.host.callWorker).not.toHaveBeenCalled();
      expect(content(view.root)).toContain('尚未指定游戏目录');
      expect(content(view.root)).not.toContain('未检测到游戏进程');
      expect(descendants(view.root).some((entry) => entry.tag === 'button' && /安装采集入口|移除采集入口/.test(content(entry)))).toBe(false);
      view.directory.value = 'E:/synthetic-game'; await flush();
      expect(view.host.callWorker).toHaveBeenCalledTimes(1);
      expect(content(view.root)).toContain('游戏进程状态待确认');
      expect(content(view.root)).toContain('采集入口状态待确认');
      expect(content(view.root)).not.toContain('未检测到游戏进程');
      expect(descendants(view.root).some((entry) => entry.tag === 'button' && /安装采集入口|移除采集入口/.test(content(entry)))).toBe(false);
    } finally { view.app.unmount(); }
  });
  it('expands the empty directory input when switching from a collapsed source panel to statistics', async () => {
    const view = await mountPanel(async (method) => method === 'tower.discover' ? { schemaVersion: 1, gameDirs: [] } : fixture(), { page: true });
    try {
      view.button('选择游戏目录').props.onClick(); await flush();
      expect(descendants(view.root).some((entry) => entry.tag === 'input')).toBe(false);
      view.button('工作与战斗').props.onClick(); await flush();
      expect(view.button('请先填写游戏安装目录').props['aria-expanded']).toBe(true);
      expect(view.find((entry) => entry.tag === 'input').props.placeholder).toContain('game.exe');
      expect(content(view.root)).toContain('未指定目录时不会检测游戏');
      expect(content(view.root)).not.toContain('未检测到游戏进程');
      expect(view.host.callWorker).toHaveBeenCalledTimes(1);
      view.button('请先填写游戏安装目录').props.onClick(); await flush();
      view.find((entry) => entry.tag === 'input').props['onUpdate:modelValue']('E'); await flush();
      expect(view.find((entry) => entry.tag === 'input').value).toBe('E');
    } finally { view.app.unmount(); }
  });
  it('keeps unknown backend facts distinct from false and blocks mutations until process detection succeeds', async () => {
    vi.useFakeTimers();
    let state = fixture(); state.capture = { ...state.capture, state: 'error', installed: null, gameRunning: null, canInstall: false, canRemove: false };
    const view = await mountPanel(async () => state);
    try {
      expect(content(view.root)).toContain('游戏进程状态待确认');
      expect(content(view.root)).toContain('采集入口状态待确认');
      expect(content(view.root)).not.toContain('未检测到游戏进程');
      state = { ...state, capture: { ...state.capture, installed: false, canInstall: true } };
      await vi.advanceTimersByTimeAsync(1000); await flush();
      expect(view.button('安装采集入口').props.disabled).toBe(true);
      view.button('安装采集入口').props.onClick(); await flush();
      state = { ...state, capture: { ...state.capture, installed: true, canInstall: false, canRemove: true } };
      await vi.advanceTimersByTimeAsync(1000); await flush();
      expect(content(view.root)).toContain('采集入口已安装，无需重复安装');
      expect(view.button('移除采集入口').props.disabled).toBe(true);
      view.button('移除采集入口').props.onClick(); await flush();
      expect(vi.mocked(view.host.callWorker).mock.calls.every(([method]) => method.endsWith('.status'))).toBe(true);
    } finally { view.app.unmount(); }
  });
  it.each([false, true])('does not offer a mutation based on a stale installed=%s snapshot after refresh failure', async (installed) => {
    vi.useFakeTimers(); let calls = 0;
    const state = fixture(); state.capture = { ...state.capture, installed, gameRunning: false, canInstall: !installed, canRemove: installed, state: installed ? 'waiting' : 'not-installed' };
    const view = await mountPanel(async () => { if (++calls > 1) throw new Error('状态读取失败'); return state; });
    try {
      const oldAction = view.button(installed ? '移除采集入口' : '安装采集入口');
      expect(oldAction.props.disabled).toBe(false);
      await vi.advanceTimersByTimeAsync(1000); await flush();
      expect(content(view.root)).toContain('游戏进程状态待确认');
      expect(content(view.root)).toContain('采集入口状态待确认');
      expect(content(view.root)).not.toContain('未检测到游戏进程');
      expect(descendants(view.root).some((entry) => entry.tag === 'button' && /安装采集入口|移除采集入口/.test(content(entry)))).toBe(false);
      oldAction.props.onClick(); await flush();
      expect(view.host.callWorker).toHaveBeenCalledTimes(2);
    } finally { view.app.unmount(); }
  });
  it('polls without overlap, ignores a previous directory response and unmounts without stopping the worker', async () => {
    vi.useFakeTimers();
    let resolveRequest!: (value: StatisticsResponse) => void;
    const view = await mountPanel(() => new Promise((resolve) => { resolveRequest = resolve; }));
    try {
      await vi.advanceTimersByTimeAsync(5000); expect(view.host.callWorker).toHaveBeenCalledTimes(1);
      view.directory.value = 'E:/other-game'; await flush();
      resolveRequest(fixture()); await flush();
      expect(content(view.root)).not.toContain('1,002,000');
      await vi.advanceTimersByTimeAsync(0);
      expect(view.host.callWorker).toHaveBeenLastCalledWith('tower.statistics.status', { gameDir: 'E:/other-game' });
      view.app.unmount(); resolveRequest(fixture()); await flush(); await vi.advanceTimersByTimeAsync(5000);
      expect(view.host.callWorker).toHaveBeenCalledTimes(2);
      expect(vi.mocked(view.host.callWorker).mock.calls.some(([method]) => method === 'tower.statistics.stop')).toBe(false);
    } finally { view.app.unmount(); }
  });
  it('retains full output independently of effective output and filters without changing global totals', async () => {
    const view = await mountPanel(async () => fixture());
    try {
      const cards = view.find((entry) => entry.props['aria-label'] === '当前种类全局累计');
      expect(content(cards)).toContain('1,002,000'); expect(content(cards)).toContain('工作次数');
      view.find((entry) => entry.props['aria-label'] === '筛选统计人物').props['onUpdate:modelValue']('p1'); await flush();
      expect(content(cards)).toContain('1,002,000');
      expect(content(view.root)).toContain('1,000,000'); expect(content(view.root)).not.toContain('有效工作量'); expect(content(view.root)).not.toContain('溢出工作量');
      expect(descendants(view.root).some((entry) => entry.props['aria-label'] === '查看第 12 次工作详情')).toBe(false);
      view.button('战斗伤害').props.onClick(); await flush();
      expect(content(cards)).toContain('完整伤害'); expect(content(cards)).toContain('每秒伤害'); expect(content(cards)).not.toContain('实际扣血'); expect(content(cards)).not.toContain('过量伤害');
      expect(content(cards)).toContain('100'); expect(content(cards)).toContain('10');
    } finally { view.app.unmount(); }
  });
  it('requires a connected capture to start, prevents installation while the game runs, and preserves stopped results', async () => {
    vi.useFakeTimers(); let state = fixture(); state.capture = { ...state.capture, state: 'not-installed', installed: false, canInstall: true };
    const view = await mountPanel(async (method) => {
      if (method === 'tower.statistics.start') state = { ...fixture(), statistics: { ...fixture().statistics, state: 'running', sessionId: 'new-session', eventCount: 0, retainedEventCount: 0, totals: [], events: [], people: [], workCategories: [], summary: { work: { amount: 0, count: 0 }, damage: { amount: 0, count: 0, perRealSecond: null } } } };
      if (method === 'tower.statistics.stop') state = { ...state, statistics: { ...state.statistics, state: 'stopped' } };
      return state;
    });
    try {
      expect(view.button('开始计时').props.disabled).toBe(true); expect(view.button('安装采集入口').props.disabled).toBe(true);
      view.button('安装采集入口').props.onClick(); await flush(); expect(view.host.callWorker).toHaveBeenCalledTimes(1);
      state = fixture(); await vi.advanceTimersByTimeAsync(1000); await flush();
      expect(view.button('开始计时').props.disabled).toBe(false);
      view.button('开始计时').props.onClick(); await flush();
      expect(content(view.root)).toContain('正在计时'); expect(content(view.root)).not.toContain('1,002,000');
      state = { ...fixture(), statistics: { ...fixture().statistics, state: 'running', sessionId: 'new-session' } };
      await vi.advanceTimersByTimeAsync(1000); await flush();
      view.button('停止计时').props.onClick(); await flush();
      expect(content(view.root)).toContain('已停止'); expect(content(view.root)).toContain('1,002,000');
    } finally { view.app.unmount(); }
  });
  it('marks failed refreshes as stale without clearing the last result or pretending recording is current', async () => {
    vi.useFakeTimers(); let calls = 0;
    const view = await mountPanel(async () => { if (++calls > 1) throw new Error('测试连接中断'); const data = fixture(); data.statistics.state = 'running'; return data; });
    try {
      await vi.advanceTimersByTimeAsync(1000); await flush();
      expect(content(view.root)).toContain('刷新失败，显示上次成功结果'); expect(content(view.root)).toContain('连接状态待确认');
      expect(content(view.root)).toContain('1,002,000'); expect(content(view.root)).not.toContain('正在计时');
      expect(view.button('开始计时').props.disabled).toBe(true);
    } finally { view.app.unmount(); }
  });
  it('explains the package change inline and installs or restores only when the game is closed', async () => {
    let state = fixture(); state.capture = { ...state.capture, state: 'not-installed', installed: false, gameRunning: false, canInstall: true };
    const view = await mountPanel(async (method) => {
      if (method === 'tower.capture.install') state = { ...state, capture: { ...state.capture, state: 'waiting', installed: true, canInstall: false, canRemove: true } };
      if (method === 'tower.capture.remove') state = { ...state, capture: { ...state.capture, state: 'not-installed', installed: false, canInstall: true, canRemove: false } };
      return state;
    });
    try {
      expect(content(view.root)).toContain('安装会先备份当前游戏包'); expect(content(view.root)).toContain('package.nw');
      expect(view.button('安装采集入口').props.disabled).toBe(false);
      view.button('安装采集入口').props.onClick(); await flush();
      expect(view.host.callWorker).toHaveBeenLastCalledWith('tower.capture.install', { gameDir: 'E:/synthetic-game' });
      expect(view.button('开始计时').props.disabled).toBe(true);
      expect(view.button('移除采集入口并恢复原包').props.disabled).toBe(false);
      view.button('移除采集入口并恢复原包').props.onClick(); await flush();
      expect(view.host.callWorker).toHaveBeenLastCalledWith('tower.capture.remove', { gameDir: 'E:/synthetic-game' });
      expect(view.button('安装采集入口').props.disabled).toBe(false);
    } finally { view.app.unmount(); }
  });
  it('keeps a failed mutation visible after later status polling succeeds', async () => {
    vi.useFakeTimers();
    const view = await mountPanel(async (method) => { if (method === 'tower.statistics.start') throw new Error('计时启动失败，请检查采集入口'); return fixture(); });
    try {
      view.button('开始计时').props.onClick(); await flush(); await vi.advanceTimersByTimeAsync(1000); await flush();
      expect(content(view.root)).toContain('计时启动失败，请检查采集入口'); expect(content(view.root)).toContain('操作未确认成功');
      expect(content(view.root)).not.toContain('正在计时'); expect(content(view.root)).toContain('1,002,000');
    } finally { view.app.unmount(); }
  });
  it('loads selected damage formulas once per session and hides sensitivity when a hit cannot be recomputed', async () => {
    vi.useFakeTimers(); let state = fixture(); let detail = explanation();
    const view = await mountPanel(async (method) => method === 'tower.statistics.explain' ? detail : state);
    try {
      view.button('战斗伤害').props.onClick(); await flush();
      view.find((entry) => entry.props['aria-label'] === '查看第 13 次伤害详情').props.onClick(); await flush();
      expect(content(view.root)).toContain('本击复算一致'); expect(content(view.root)).toContain('并非相同投入成本的比较');
      expect(content(view.root)).toContain('原始项（14 项）');
      expect(content(view.root)).toContain('0.00012345');
      await vi.advanceTimersByTimeAsync(3000); await flush();
      expect(vi.mocked(view.host.callWorker).mock.calls.filter(([method]) => method === 'tower.statistics.explain')).toHaveLength(1);
      state = { ...fixture(), statistics: { ...fixture().statistics, sessionId: 'session-two' } }; detail = { ...explanation(false), sessionId: 'session-two' };
      await vi.advanceTimersByTimeAsync(1000); await flush();
      expect(content(view.root)).toContain('本击复算一致');
      expect(content(view.root)).toContain('来自上一计时会话');
      view.button('恢复明细刷新').props.onClick(); await flush();
      view.find((entry) => entry.props['aria-label'] === '查看第 13 次伤害详情').props.onClick(); await flush();
      expect(content(view.root)).toContain('本击无法复算'); expect(content(view.root)).not.toContain('调整后输出');
      expect(vi.mocked(view.host.callWorker).mock.calls.filter(([method]) => method === 'tower.statistics.explain')).toHaveLength(2);
    } finally { view.app.unmount(); }
  });
  it('rejects a formula from another session and does not silently retry it on every poll', async () => {
    vi.useFakeTimers();
    const view = await mountPanel(async (method) => method === 'tower.statistics.explain' ? { ...explanation(), sessionId: 'another-session' } : fixture());
    try {
      view.button('战斗伤害').props.onClick(); await flush();
      view.find((entry) => entry.props['aria-label'] === '查看第 13 次伤害详情').props.onClick(); await flush();
      expect(content(view.root)).toContain('计时会话已改变'); expect(content(view.root)).not.toContain('本击复算一致');
      await vi.advanceTimersByTimeAsync(3000); await flush();
      expect(vi.mocked(view.host.callWorker).mock.calls.filter(([method]) => method === 'tower.statistics.explain')).toHaveLength(1);
    } finally { view.app.unmount(); }
  });
});
