import { BROWSER_UNAVAILABLE, type LimitedHost } from '../../../framework/module-sdk/src/index';
import type { DetailSnapshot, LatestEventSelector, StatisticsExplanation, StatisticsOperation, StatisticsResponse } from './statisticsTypes';

function validateDetailSnapshot(value: unknown): asserts value is DetailSnapshot {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('明细公式快照格式无效，不能降级读取其他事件。');
  const snapshot = value as Record<string, unknown>;
  if (Object.keys(snapshot).length !== 3 || !Object.keys(snapshot).every(key => ['schemaVersion', 'payload', 'signature'].includes(key))
    || snapshot.schemaVersion !== 1 || typeof snapshot.payload !== 'string' || !snapshot.payload.length
    || new TextEncoder().encode(snapshot.payload).byteLength > 12 * 1024
    || typeof snapshot.signature !== 'string' || !/^[a-f0-9]{64}$/.test(snapshot.signature)) {
    throw new Error('明细公式快照格式无效，不能降级读取其他事件。');
  }
}

function validateExplanation(response: StatisticsExplanation, sessionId?: string): void {
  if (!response || response.schemaVersion !== 1 || typeof response.sessionId !== 'string' || !Number.isSafeInteger(response.sequence) || response.sequence < 1 || typeof response.message !== 'string'
    || (response.formula !== null && (!response.formula || !Array.isArray(response.formula.terms) || !Array.isArray(response.formula.sensitivity)))
    || (response.workFormula != null && (!Array.isArray(response.workFormula.terms) || typeof response.workFormula.text !== 'string'))) {
    throw new Error('公式服务返回了不支持的数据，请更新页签后重试。');
  }
  if (sessionId && response.sessionId !== sessionId) throw new Error('计时会话已改变，无法读取这条旧明细的公式；已选内容仍保留。');
  if (response.event && Object.hasOwn(response.event, 'detailSnapshot')) validateDetailSnapshot(response.event.detailSnapshot);
}

function matchesLatest(actual: LatestEventSelector | undefined, expected: LatestEventSelector): boolean {
  if (!actual || actual.kind !== expected.kind || actual.personId !== expected.personId) return false;
  return actual.kind === 'work' && expected.kind === 'work' ? actual.category === expected.category
    : actual.kind === 'damage' && expected.kind === 'damage' && actual.attackKind === expected.attackKind && actual.skillId === expected.skillId;
}

/** Display snapshots only: capture state, timers, formulas and accumulated output belong to the worker. */
export function createStatisticsClient(host: LimitedHost) {
  async function call<T>(operation: string, gameDir: string, fields: Record<string, unknown> = {}): Promise<T> {
    if (!host.available) throw new Error(BROWSER_UNAVAILABLE);
    const directory = gameDir.trim();
    if (!directory) throw new Error('请先填写游戏安装目录。');
    return host.callWorker<T>(`tower.${operation}`, { gameDir: directory, ...fields });
  }
  return {
  async request(operation: StatisticsOperation, gameDir: string): Promise<StatisticsResponse> {
    const response = await call<StatisticsResponse>(operation, gameDir);
    if (!response || response.schemaVersion !== 1 || response.statistics?.schemaVersion !== 1 || typeof response.statistics.sessionId !== 'string'
      || !['not-installed', 'waiting', 'connected', 'stale', 'error'].includes(response.capture?.state)
      || !(response.capture.installed === null || typeof response.capture.installed === 'boolean')
      || !(response.capture.gameRunning === null || typeof response.capture.gameRunning === 'boolean')
      || typeof response.capture.canInstall !== 'boolean' || typeof response.capture.canRemove !== 'boolean'
      || !(response.capture.updateRequired === undefined || response.capture.updateRequired === null || typeof response.capture.updateRequired === 'boolean')
      || !['idle', 'running', 'stopped', 'interrupted'].includes(response.statistics.state)
      || !Array.isArray(response.capture.coverage) || !Array.isArray(response.statistics.totals) || !Array.isArray(response.statistics.events)
      || !Array.isArray(response.statistics.people) || !Array.isArray(response.statistics.workCategories)
      || response.statistics.people.some(person => !person || typeof person.personId !== 'string' || typeof person.personName !== 'string'
        || !Array.isArray(person.work) || !person.damage || !Array.isArray(person.damage.types) || !Array.isArray(person.damage.attacks))
      || !response.statistics.summary?.work || !response.statistics.summary?.damage) {
      throw new Error('采集服务返回了不支持的数据，请更新页签后重试。');
    }
    for (const event of response.statistics.events) {
      if (event && Object.hasOwn(event, 'detailSnapshot')) validateDetailSnapshot(event.detailSnapshot);
    }
    return response;
  },
  async explain(gameDir: string, sequence: number, sessionId?: string, snapshot?: DetailSnapshot): Promise<StatisticsExplanation> {
    if (snapshot !== undefined) {
      validateDetailSnapshot(snapshot);
      if (!sessionId?.trim()) throw new Error('读取明细公式快照需要原计时会话，请重新选择明细。');
    }
    const response = await call<StatisticsExplanation>('statistics.explain', gameDir, { sequence, ...(sessionId ? { sessionId } : {}), ...(snapshot !== undefined ? { snapshot } : {}) });
    validateExplanation(response, sessionId);
    if (response.sequence !== sequence || response.selection !== undefined || (snapshot !== undefined && !response.event)
      || (response.event && (response.event.sequence !== sequence || !['work', 'damage'].includes(response.event.kind)))) {
      throw new Error('公式服务返回的事件与已选明细不一致，请重新选择。');
    }
    if (snapshot !== undefined && (response.event?.detailSnapshot?.payload !== snapshot.payload
      || response.event.detailSnapshot.signature !== snapshot.signature || response.event.detailSnapshot.schemaVersion !== snapshot.schemaVersion)) {
      throw new Error('公式服务返回的快照与已选明细不一致，请重新选择。');
    }
    return response;
  },
  async explainLatest(gameDir: string, latest: LatestEventSelector, sessionId: string): Promise<StatisticsExplanation> {
    if (!sessionId.trim()) throw new Error('读取最近一次事件需要明确的计时会话，请刷新后重试。');
    const response = await call<StatisticsExplanation>('statistics.explain', gameDir, { latest, sessionId });
    validateExplanation(response, sessionId);
    const event = response.event;
    const eventSelection: LatestEventSelector | undefined = event?.kind === 'work'
      ? { kind: 'work', personId: event.personId, category: event.category }
      : event?.kind === 'damage' && event.attackKind
        ? { kind: 'damage', personId: event.personId, attackKind: event.attackKind, skillId: event.skillId ?? null } : undefined;
    if (response.selection?.mode !== 'latest' || !matchesLatest(response.selection, latest)
      || !event || event.schemaVersion !== 1 || event.sequence !== response.sequence || typeof event.personName !== 'string'
      || typeof event.category !== 'string' || !Number.isFinite(event.amount) || event.amount < 0 || !matchesLatest(eventSelection, latest)) {
      throw new Error('公式服务返回的最近事件与所选人物或类型不一致，请重新选择。');
    }
    return response;
  },
  };
}
