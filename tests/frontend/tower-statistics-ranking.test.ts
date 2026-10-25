import { describe, expect, it } from 'vitest';
import { buildRankingBoards, parseRankingBoardFilter, rankStatistics, rankingProfessions, type RankingBoardFilter, type RankingFilter } from '../../apps/features/tower_assistant/frontend/statisticsRanking';
import type { PersonStatistics, StatisticsSnapshot } from '../../apps/features/tower_assistant/frontend/statisticsTypes';

const filter: RankingFilter = { kind: 'damage', damageScope: 'all', workCategory: '', query: '', profession: '', sort: 'amount', limit: 10, hideZero: true };
function person(id: string, amount: number, count = 1): PersonStatistics {
  return { personId: id, personName: `人物${id}`, person: null, work: [], damage: { amount, count, perRealSecond: 9999, types: [], attacks: [] } };
}
function snapshot(people: PersonStatistics[], elapsed = 10): StatisticsSnapshot {
  return { schemaVersion: 1, sessionId: 'rank-session', state: 'running', reason: '', elapsedRealSeconds: elapsed,
    eventCount: 9999, retainedEventCount: 0, lastSequence: 9999, events: [], totals: [], people, workCategories: [],
    summary: { work: { amount: 0, count: 0 }, damage: { amount: 0, count: 0, perRealSecond: 0 } } };
}
const cell = (amount: number, count: number) => ({ amount, count, perRealSecond: 9999, averageAmount: 9999, latestAmount: 1, latestSequence: 9 });

describe('session statistics ranking', () => {
  it('uses full person aggregates despite empty rolling events, with shares before Top N and session DPS', () => {
    const state = snapshot([person('1', 600), person('2', 300), person('3', 100)]);
    const result = rankStatistics(state, { ...filter, limit: 1 });
    expect(result.rows).toHaveLength(1); expect(result.peopleCount).toBe(3);
    expect(result.amount).toBe(1000); expect(result.dps).toBe(100);
    expect(result.rows[0]).toMatchObject({ amount: 600, dps: 60, share: .6, rank: 1 });
  });
  it('filters profession and person before calculating the denominator, preserving unknown metadata', () => {
    const a = person('1', 600); a.person = { profession: '剑修' };
    const b = person('2', 300); b.person = { profession: '剑修' };
    const c = person('3', 100);
    const state = snapshot([a, b, c]);
    expect(rankStatistics(state, { ...filter, profession: 'known:剑修', limit: 1 }).rows[0].share).toBeCloseTo(2 / 3);
    expect(rankStatistics(state, { ...filter, profession: 'unknown' }).rows[0].person).toBe(c);
    expect(rankStatistics(state, { ...filter, query: ' 人物2 ' }).amount).toBe(300);
    expect(rankingProfessions(state.people)).toEqual(expect.arrayContaining([{ value: 'unknown', label: '职业未知' }, { value: 'known:剑修', label: '剑修' }]));
  });
  it('selects damage type OR attack kind and combines multi-skill means by hit count, never averaging averages', () => {
    const a = person('1', 1000, 10);
    a.damage.types = [{ ...cell(300, 3), damageType: 'physical' }, { ...cell(700, 7), damageType: 'magic' }];
    a.damage.attacks = [{ ...cell(200, 1), attackKind: 'skill', skillId: '1', skillName: 'A', actionCount: null, averagePerAction: null },
      { ...cell(300, 3), attackKind: 'skill', skillId: '2', skillName: 'B', actionCount: null, averagePerAction: null },
      { ...cell(500, 6), attackKind: 'basic', skillId: '3', skillName: 'C', actionCount: null, averagePerAction: null }];
    expect(rankStatistics(snapshot([a]), { ...filter, damageScope: 'type:physical' }).rows[0]).toMatchObject({ amount: 300, count: 3, average: 100 });
    expect(rankStatistics(snapshot([a]), { ...filter, damageScope: 'attack:skill' }).rows[0]).toMatchObject({ amount: 500, count: 4, average: 125 });
  });
  it('uses actual work output, sums counts and amounts only for the selected work type, and never supplies work DPS', () => {
    const a = person('1', 10);
    a.work = [{ category: '建造', ...cell(1000000, 2) }, { category: '采矿', ...cell(500, 5) }];
    expect(rankStatistics(snapshot([a]), { ...filter, kind: 'work', workCategory: '建造', sort: 'dps' }).rows[0])
      .toMatchObject({ amount: 1000000, count: 2, average: 500000, dps: null });
    expect(rankStatistics(snapshot([a]), { ...filter, kind: 'work' }).amount).toBe(1000500);
  });
  it('keeps stable identity order for ties, applies count/average sort, and does not mutate source rows', () => {
    const state = snapshot([person('10', 100, 10), person('2', 100, 2), person('1', 50, 20)]);
    expect(rankStatistics(state, filter).rows.map(row => row.person.personId)).toEqual(['2', '10', '1']);
    expect(rankStatistics(state, { ...filter, sort: 'count' }).rows[0].person.personId).toBe('1');
    expect(rankStatistics(state, { ...filter, sort: 'average' }).rows[0].person.personId).toBe('2');
    expect(state.people.map(row => row.personId)).toEqual(['10', '2', '1']);
  });
  it('handles zero time and empty/zero aggregates without fabricating a rate or a share', () => {
    const state = snapshot([person('1', 0, 0)], 0);
    expect(rankStatistics(state, filter).rows).toEqual([]);
    expect(rankStatistics(state, { ...filter, hideZero: false, limit: null }).rows[0]).toMatchObject({ share: 0, dps: null, average: 0 });
    expect(rankStatistics(null, filter)).toMatchObject({ rows: [], amount: 0, dps: null, peopleCount: 0 });
  });
});

const boardFilter: RankingBoardFilter = { kind: 'damage', query: '', workCategories: null, damageTypes: null, professions: null,
  damageGrouping: 'combined', sort: 'amount', limit: null, hideZero: true };

describe('shared statistics ranking boards', () => {
  it('keeps selected work categories separate, excludes people without that work, and retains the real latest output', () => {
    const a = person('1', 0); a.work = [{ category: '锻造', ...cell(1000000, 2), latestAmount: 1, latestSequence: 11 },
      { category: '采矿', ...cell(900, 3), latestAmount: 400, latestSequence: 12 }];
    const b = person('2', 0); b.work = [{ category: '锻造', ...cell(100, 5), latestAmount: 50, latestSequence: 13 }];
    const state = snapshot([a, b, person('3', 0)]);
    state.workCategories = ['锻造', '采矿'];
    const boards = buildRankingBoards(state, { ...boardFilter, kind: 'work', workCategories: ['采矿', '锻造', '采矿'], sort: 'latest', hideZero: false });
    expect(boards.map(board => board.label)).toEqual(['锻造', '采矿']);
    expect(boards[0]).toMatchObject({ amount: 1000100, count: 7, peopleCount: 2, dps: null, maximumAmount: 1000000, workCategory: '锻造' });
    expect(boards[0].rows.map(row => row.person.personId)).toEqual(['2', '1']);
    expect(boards[0].rows[0]).toMatchObject({ latestAmount: 50, latestSequence: 13, average: 20, dps: null });
    expect(boards[0].rows[0].workCell).toBe(b.work[0]);
    expect(boards[1]).toMatchObject({ amount: 900, peopleCount: 1 });
    expect(boards[1].rows[0]).toMatchObject({ latestAmount: 400, latestSequence: 12, average: 300 });
    expect(buildRankingBoards(state, { ...boardFilter, kind: 'work', workCategories: [] })).toEqual([]);
    expect(buildRankingBoards(state, { ...boardFilter, kind: 'work', workCategories: ['未出现的工作'] })).toEqual([]);
    a.work.push({ category: '炼丹', ...cell(10, 1) });
    expect(buildRankingBoards(state, { ...boardFilter, kind: 'work' }).map(board => board.label)).toContain('炼丹');
  });
  it('sums unique selected damage types including unknown, with each latest value tied to its greatest sequence', () => {
    const a = person('1', 1000, 10);
    a.damage.types = [{ ...cell(300, 3), damageType: 'physical', latestAmount: 3, latestSequence: 10 },
      { ...cell(600, 6), damageType: 'magic', latestAmount: 60, latestSequence: 30 },
      { ...cell(100, 1), damageType: 'unknown', latestAmount: 100, latestSequence: 20 }];
    const state = snapshot([a]);
    const [combined] = buildRankingBoards(state, { ...boardFilter, damageTypes: ['physical', 'unknown', 'physical'] });
    expect(combined).toMatchObject({ amount: 400, count: 4, dps: 40 });
    expect(combined.rows[0]).toMatchObject({ average: 100, latestAmount: 100, latestSequence: 20 });
    const separate = buildRankingBoards(state, { ...boardFilter, damageTypes: ['unknown', 'physical'], damageGrouping: 'type' });
    expect(separate.map(board => board.damageType)).toEqual(['physical', 'unknown']);
    expect(separate.map(board => board.amount)).toEqual([300, 100]);
    expect(buildRankingBoards(state, { ...boardFilter, damageTypes: [] })).toEqual([]);
    expect(buildRankingBoards(state, { ...boardFilter, damageTypes: [], damageGrouping: 'type', hideZero: false })).toEqual([]);
  });
  it('calculates shares and bar maxima separately for every board before Top N even when sorted by latest output', () => {
    const people = [person('1', 900), person('2', 100), person('3', 100)];
    people.forEach((item, index) => { item.work = [
      { category: '锻造', ...cell([600, 300, 100][index], 1), latestAmount: [1, 20, 3][index] },
      { category: '采矿', ...cell([50, 150, 0][index], 1), latestAmount: [2, 8, 0][index] }]; });
    const boards = buildRankingBoards(snapshot(people), { ...boardFilter, kind: 'work', limit: 1, sort: 'latest' });
    const forge = boards.find(board => board.workCategory === '锻造')!;
    const mine = boards.find(board => board.workCategory === '采矿')!;
    expect(forge).toMatchObject({ amount: 1000, maximumAmount: 600, peopleCount: 3 });
    expect(forge.rows).toHaveLength(1);
    expect(forge.rows[0]).toMatchObject({ amount: 300, share: .3, rank: 1 });
    expect(mine).toMatchObject({ amount: 200, maximumAmount: 150, peopleCount: 2 });
    expect(mine.rows[0]).toMatchObject({ amount: 150, share: .75 });
  });
  it('filters profession, name and exact person before profession boards without mixing unknown metadata', () => {
    const a = person('1', 600); a.person = { profession: ' 剑修 ' };
    const b = person('2', 300); b.person = { profession: '剑修' };
    const c = person('3', 100); const d = person('4', 900); d.person = { profession: '医师' };
    const state = snapshot([a, b, c, d]);
    const filter = { ...boardFilter, damageGrouping: 'profession' as const, professions: ['known:剑修', 'unknown', 'known:剑修'], limit: 1 };
    const boards = buildRankingBoards(state, filter);
    expect(boards).toHaveLength(2);
    expect(boards.find(board => board.profession === 'known:剑修')).toMatchObject({ label: '剑修', amount: 900, peopleCount: 2 });
    expect(boards.find(board => board.profession === 'known:剑修')!.rows[0].share).toBeCloseTo(2 / 3);
    expect(boards.find(board => board.profession === 'unknown')!.rows[0].person).toBe(c);
    expect(buildRankingBoards(state, { ...filter, query: ' 人物2 ' })[0].amount).toBe(300);
    expect(buildRankingBoards(state, { ...filter, personId: '1' })[0].amount).toBe(600);
    expect(buildRankingBoards(state, { ...filter, professions: [] })).toEqual([]);
    expect(buildRankingBoards(state, { ...filter, professions: ['known:未出现'] })).toEqual([]);
  });
  it('uses attack-kind marginal aggregates without inventing a damage-type intersection', () => {
    const a = person('1', 1000, 10); a.person = { profession: '剑修' };
    a.damage.types = [{ ...cell(700, 7), damageType: 'physical' }, { ...cell(300, 3), damageType: 'magic' }];
    a.damage.attacks = [{ ...cell(200, 1), latestAmount: 200, latestSequence: 20, attackKind: 'skill', skillId: '1', skillName: 'A', actionCount: null, averagePerAction: null },
      { ...cell(300, 3), latestAmount: 50, latestSequence: 30, attackKind: 'skill', skillId: '2', skillName: 'B', actionCount: null, averagePerAction: null }];
    const state = snapshot([a]);
    const [board] = buildRankingBoards(state, { ...boardFilter, damageTypes: ['magic'], attackKind: 'skill', damageGrouping: 'type' });
    expect(board).toMatchObject({ key: 'damage:combined', label: '技能命中', amount: 500, count: 4 });
    expect(board.rows[0]).toMatchObject({ average: 125, latestAmount: 50, latestSequence: 30 });
    expect(buildRankingBoards(state, { ...boardFilter, damageTypes: [], attackKind: 'skill' })[0].amount).toBe(500);
    expect(buildRankingBoards(state, { ...boardFilter, damageTypes: ['magic'], attackKind: 'skill', damageGrouping: 'profession' })[0])
      .toMatchObject({ profession: 'known:剑修', amount: 500 });
  });
  it('preserves precision and source snapshots, keeps keys stable, and sorts ties by person ID', () => {
    const state = snapshot([person('10', 100.004, 3), person('2', 100.004, 3), person('1', 100.003, 3)]);
    const before = structuredClone(state);
    const [board] = buildRankingBoards(state, boardFilter);
    expect(board.rows.map(row => row.person.personId)).toEqual(['2', '10', '1']);
    expect(board.rows[0].amount).toBe(100.004);
    expect(board.rows[0].average).toBeCloseTo(100.004 / 3);
    expect(board.rows[0].latestAmount).toBeNull();
    expect(board.key).toBe(buildRankingBoards(state, { ...boardFilter, sort: 'count', limit: 1 })[0].key);
    expect(state).toEqual(before);
    const [empty] = buildRankingBoards(snapshot([person('0', 0, 0)], 0), { ...boardFilter, hideZero: false });
    expect(empty).toMatchObject({ amount: 0, maximumAmount: 0, dps: null });
    expect(empty.rows[0]).toMatchObject({ share: 0, dps: null, latestAmount: null, latestSequence: null });
    expect(buildRankingBoards(undefined, boardFilter)).toEqual([]);
  });
});

describe('ranking-board transport context', () => {
  const valid = { ...boardFilter, limit: 10, personId: '', attackKind: null };
  it('copies bounded complete filters and keeps null/all distinct from empty/none', () => {
    expect(parseRankingBoardFilter(valid)).toEqual(valid);
    const input: RankingBoardFilter = { ...valid, workCategories: [], damageTypes: ['physical', 'unknown'], professions: ['known:剑修', 'unknown'] };
    const parsed = parseRankingBoardFilter(input)!;
    expect(parsed).toEqual(input); expect(parsed).not.toBe(input);
    parsed.damageTypes!.push('magic'); expect(input.damageTypes).toEqual(['physical', 'unknown']);
    expect(parseRankingBoardFilter({ ...valid, workCategories: null })!.workCategories).toBeNull();
    expect(parseRankingBoardFilter({ ...valid, workCategories: [] })!.workCategories).toEqual([]);
  });
  it('rejects unknown fields, invalid enums, duplicate lists, missing required fields and oversized context', () => {
    const { query: _query, ...missing } = valid;
    const invalid = [null, [], missing, { ...valid, extra: true }, { ...valid, kind: 'other' }, { ...valid, sort: 'fake' },
      { ...valid, damageGrouping: 'area' }, { ...valid, limit: 1 }, { ...valid, hideZero: 'true' },
      { ...valid, query: 'x'.repeat(201) }, { ...valid, query: 'line\nbreak' }, { ...valid, personId: 'x'.repeat(101) },
      { ...valid, attackKind: 'magic' }, { ...valid, attackKind: ['basic'] }, { ...valid, attackKind: { toString: () => 'basic' } },
      { ...valid, workCategories: ['锻造', '锻造'] }, { ...valid, workCategories: [''] }, { ...valid, workCategories: ['x'.repeat(101)] },
      { ...valid, workCategories: Array.from({ length: 513 }, (_, index) => String(index)) },
      { ...valid, professions: ['unknown', 'unknown'] }, { ...valid, professions: ['剑修'] }, { ...valid, professions: ['known:'] },
      { ...valid, professions: ['known:' + 'x'.repeat(101)] }, { ...valid, damageTypes: ['physical', 'physical'] },
      { ...valid, damageTypes: ['holy'] }, { ...valid, workCategories: Array.from({ length: 100 }, (_, index) => `${index}${'甲'.repeat(90)}`) }];
    for (const input of invalid) expect(parseRankingBoardFilter(input)).toBeNull();
  });
});
