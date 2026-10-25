import type { AttackKind, DamageType, OutputKind, PersonStatistics, StatisticsSnapshot, WorkCell } from './statisticsTypes';

/** One damage dimension at a time: the worker exposes marginal aggregates,
 * not intersections of damage type and attack kind. */
export type DamageScope = 'all' | `type:${DamageType}` | `attack:${AttackKind}`;
export type RankingSort = 'amount' | 'dps' | 'count' | 'average';
export interface RankingFilter {
  kind: OutputKind;
  damageScope: DamageScope;
  workCategory: string;
  query: string;
  profession: string;
  sort: RankingSort;
  limit: number | null;
  hideZero: boolean;
}
export interface RankingRow {
  person: PersonStatistics;
  amount: number;
  count: number;
  average: number;
  dps: number | null;
  share: number;
  rank: number;
}
export const damageScopes: { value: DamageScope; label: string }[] = [
  { value: 'all', label: '全部伤害' },
  { value: 'type:physical', label: '力 · 物理' },
  { value: 'type:magic', label: '智 · 魔法' },
  { value: 'type:piercing', label: '敏 · 穿刺' },
  { value: 'type:unknown', label: '其他 / 未知伤害' },
  { value: 'attack:basic', label: '普攻命中' },
  { value: 'attack:skill', label: '技能命中' },
  { value: 'attack:periodic', label: '持续伤害' },
  { value: 'attack:unknown', label: '未分类攻击' },
];

export function professionKey(person: PersonStatistics): string {
  const name = person.person?.profession?.trim();
  return name ? `known:${name}` : 'unknown';
}

export function rankingProfessions(people: PersonStatistics[]): { value: string; label: string }[] {
  const values = new Map(people.map(person => [professionKey(person), person.person?.profession?.trim() || '职业未知']));
  return [...values].map(([value, label]) => ({ value, label })).sort((a, b) => a.label.localeCompare(b.label, 'zh-CN'));
}

export function rankStatistics(snapshot: StatisticsSnapshot | null | undefined, filter: RankingFilter) {
  const elapsed = snapshot?.elapsedRealSeconds ?? 0;
  const query = filter.query.trim().toLocaleLowerCase();
  const rows: RankingRow[] = [];
  for (const person of snapshot?.people ?? []) {
    if (query && !`${person.personName}\n${person.personId}`.toLocaleLowerCase().includes(query)) continue;
    if (filter.profession && professionKey(person) !== filter.profession) continue;
    let amount = 0;
    let count = 0;
    if (filter.kind === 'work') {
      for (const cell of person.work) if (!filter.workCategory || cell.category === filter.workCategory) {
        amount += cell.amount; count += cell.count;
      }
    } else if (filter.damageScope === 'all') {
      amount = person.damage.amount; count = person.damage.count;
    } else {
      const [dimension, value] = filter.damageScope.split(':');
      const cells = dimension === 'type'
        ? person.damage.types.filter(cell => cell.damageType === value)
        : person.damage.attacks.filter(cell => cell.attackKind === value);
      for (const cell of cells) { amount += cell.amount; count += cell.count; }
    }
    if (filter.hideZero && amount === 0) continue;
    rows.push({ person, amount, count, average: count > 0 ? amount / count : 0,
      dps: filter.kind === 'damage' && elapsed > 0 ? amount / elapsed : null, share: 0, rank: 0 });
  }
  // Shares use the complete filtered session aggregate, before Top N truncation.
  const amount = rows.reduce((total, row) => total + row.amount, 0);
  const count = rows.reduce((total, row) => total + row.count, 0);
  const sort = filter.kind === 'work' && filter.sort === 'dps' ? 'amount' : filter.sort;
  rows.sort((a, b) => ((b[sort] ?? 0) - (a[sort] ?? 0)) || a.person.personId.localeCompare(b.person.personId, 'en', { numeric: true }));
  rows.forEach((row, index) => { row.rank = index + 1; row.share = amount > 0 ? row.amount / amount : 0; });
  return { rows: filter.limit === null ? rows : rows.slice(0, filter.limit), peopleCount: rows.length,
    amount, count, dps: filter.kind === 'damage' && elapsed > 0 ? amount / elapsed : null };
}

export interface RankingBoardFilter {
  kind: OutputKind;
  query: string;
  personId?: string;
  /** null follows all current/future categories; [] explicitly selects none. */
  workCategories: string[] | null;
  damageTypes: DamageType[] | null;
  professions: string[] | null;
  damageGrouping: 'combined' | 'type' | 'profession';
  sort: RankingSort | 'latest';
  limit: number | null;
  hideZero: boolean;
  /** Attack and damage-type aggregates are marginal, never an intersection. */
  attackKind?: AttackKind | null;
}
export interface RankingBoardRow extends RankingRow {
  latestAmount: number | null;
  latestSequence: number | null;
  /** Source work cell, for opening its atomic latest explanation. Read-only. */
  workCell?: WorkCell;
}
export interface RankingBoard {
  key: string;
  label: string;
  kind: OutputKind;
  rows: RankingBoardRow[];
  amount: number;
  count: number;
  dps: number | null;
  peopleCount: number;
  maximumAmount: number;
  workCategory?: string;
  damageType?: DamageType;
  profession?: string;
}

const damageTypeOrder: DamageType[] = ['physical', 'magic', 'piercing', 'unknown'];
const damageTypeLabels: Record<DamageType, string> = { physical: '力 · 物理', magic: '智 · 魔法', piercing: '敏 · 穿刺', unknown: '其他 / 未知伤害' };
const attackKindLabels: Record<AttackKind, string> = { basic: '普攻命中', skill: '技能命中', periodic: '持续伤害', unknown: '未分类攻击' };

/** Parse transport context only; callers must report null as an invalid filter. */
export function parseRankingBoardFilter(value: unknown): RankingBoardFilter | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const source = value as Record<string, unknown>;
  const required = ['kind', 'query', 'workCategories', 'damageTypes', 'professions', 'damageGrouping', 'sort', 'limit', 'hideZero'];
  const allowed = new Set([...required, 'personId', 'attackKind']);
  if (required.some(key => !Object.hasOwn(source, key)) || Object.keys(source).some(key => !allowed.has(key))) return null;
  try { if (new TextEncoder().encode(JSON.stringify(value)).byteLength > 8192) return null; } catch { return null; }
  const printable = (text: unknown, maximum: number, empty = false): text is string => typeof text === 'string'
    && [...text].length <= maximum && (empty || Boolean(text.trim())) && !/[\p{Cc}\p{Cs}]/u.test(text);
  const list = (items: unknown, maximum: number): items is string[] | null => items === null || (Array.isArray(items)
    && items.length <= 512 && items.every(item => printable(item, maximum)) && new Set(items).size === items.length);
  if ((source.kind !== 'work' && source.kind !== 'damage') || !printable(source.query, 200, true)
    || !list(source.workCategories, 100) || !list(source.professions, 106)
    || (source.professions !== null && !source.professions.every(key => key === 'unknown' || (key.startsWith('known:') && Boolean(key.slice(6).trim()))))
    || !(source.damageTypes === null || (Array.isArray(source.damageTypes) && source.damageTypes.length <= 4
      && source.damageTypes.every(type => damageTypeOrder.includes(type as DamageType)) && new Set(source.damageTypes).size === source.damageTypes.length))
    || !['combined', 'type', 'profession'].includes(source.damageGrouping as string)
    || !['amount', 'dps', 'count', 'average', 'latest'].includes(source.sort as string)
    || !(source.limit === null || source.limit === 5 || source.limit === 10 || source.limit === 20)
    || typeof source.hideZero !== 'boolean'
    || (Object.hasOwn(source, 'personId') && !printable(source.personId, 100, true))
    || (Object.hasOwn(source, 'attackKind') && source.attackKind !== null
      && (typeof source.attackKind !== 'string' || !Object.hasOwn(attackKindLabels, source.attackKind)))) return null;
  return {
    kind: source.kind, query: source.query, workCategories: source.workCategories === null ? null : [...source.workCategories],
    damageTypes: source.damageTypes === null ? null : [...source.damageTypes as DamageType[]],
    professions: source.professions === null ? null : [...source.professions],
    damageGrouping: source.damageGrouping as RankingBoardFilter['damageGrouping'], sort: source.sort as RankingBoardFilter['sort'],
    limit: source.limit, hideZero: source.hideZero,
    ...(Object.hasOwn(source, 'personId') ? { personId: source.personId as string } : {}),
    ...(Object.hasOwn(source, 'attackKind') ? { attackKind: source.attackKind as AttackKind | null } : {}),
  };
}

type MetricCell = { amount: number; count: number; latestAmount: number; latestSequence: number };
function latestOf(cells: MetricCell[]): { latestAmount: number | null; latestSequence: number | null } {
  let latest: MetricCell | undefined;
  for (const cell of cells) if (Number.isSafeInteger(cell.latestSequence) && cell.latestSequence > 0 && Number.isFinite(cell.latestAmount)
    && (!latest || cell.latestSequence > latest.latestSequence)) latest = cell;
  return { latestAmount: latest?.latestAmount ?? null, latestSequence: latest?.latestSequence ?? null };
}

/** Read-only projections of worker aggregates; never recompute from rolling events. */
export function buildRankingBoards(snapshot: StatisticsSnapshot | null | undefined, filter: RankingBoardFilter): RankingBoard[] {
  if (!snapshot || filter.professions?.length === 0) return [];
  const elapsed = snapshot.elapsedRealSeconds;
  const query = filter.query.trim().toLocaleLowerCase();
  const professions = filter.professions === null ? null : new Set(filter.professions);
  const people = snapshot.people.filter(person => (!filter.personId || person.personId === filter.personId)
    && (!query || `${person.personName}\n${person.personId}`.toLocaleLowerCase().includes(query))
    && (professions === null || professions.has(professionKey(person))));
  function row(person: PersonStatistics, amount: number, count: number, cells: MetricCell[]): RankingBoardRow {
    return { person, amount, count, average: count > 0 ? amount / count : 0,
      dps: filter.kind === 'damage' && elapsed > 0 ? amount / elapsed : null, share: 0, rank: 0, ...latestOf(cells) };
  }
  function board(key: string, label: string, source: RankingBoardRow[], scope: Pick<RankingBoard, 'workCategory' | 'damageType' | 'profession'> = {}): RankingBoard {
    const rows = filter.hideZero ? source.filter(item => item.amount !== 0) : source;
    const amount = rows.reduce((sum, item) => sum + item.amount, 0);
    const count = rows.reduce((sum, item) => sum + item.count, 0);
    const maximumAmount = rows.reduce((maximum, item) => Math.max(maximum, item.amount), 0);
    const sort = filter.kind === 'work' && filter.sort === 'dps' ? 'amount' : filter.sort;
    const score = (item: RankingBoardRow) => (sort === 'latest' ? item.latestAmount : item[sort]) ?? -Infinity;
    rows.sort((a, b) => (score(a) === score(b) ? 0 : score(a) > score(b) ? -1 : 1)
      || a.person.personId.localeCompare(b.person.personId, 'en', { numeric: true })
      || a.person.personId.localeCompare(b.person.personId, 'en'));
    rows.forEach((item, index) => { item.rank = index + 1; item.share = amount > 0 ? item.amount / amount : 0; });
    return { key, label, kind: filter.kind, ...scope, amount, count, maximumAmount, peopleCount: rows.length,
      dps: filter.kind === 'damage' && elapsed > 0 ? amount / elapsed : null,
      rows: filter.limit === null ? rows : rows.slice(0, filter.limit) };
  }
  if (filter.kind === 'work') {
    const available = [...new Set([...snapshot.workCategories,
      ...snapshot.people.flatMap(person => person.work.map(cell => cell.category)).sort((a, b) => a.localeCompare(b, 'zh-CN'))])];
    const selected = filter.workCategories === null ? null : new Set(filter.workCategories);
    return available.filter(category => selected === null || selected.has(category)).map(category => {
      const rows: RankingBoardRow[] = [];
      for (const person of people) {
        const cell = person.work.find(item => item.category === category);
        if (cell) rows.push({ ...row(person, cell.amount, cell.count, [cell]), workCell: cell });
      }
      return board(`work:${encodeURIComponent(category)}`, category, rows, { workCategory: category });
    });
  }
  const attack = filter.attackKind ?? null;
  const types = filter.damageTypes === null ? null : new Set(filter.damageTypes);
  const selectedTypes = types === null ? damageTypeOrder : damageTypeOrder.filter(type => types.has(type));
  if (attack === null && selectedTypes.length === 0) return [];
  function damageRow(person: PersonStatistics, types: DamageType[]): RankingBoardRow {
    const cells = attack === null ? person.damage.types.filter(cell => types.includes(cell.damageType))
      : person.damage.attacks.filter(cell => cell.attackKind === attack);
    // The unfiltered total is authoritative even when reading an older snapshot
    // without type details. Explicit type selection uses only observed cells.
    const complete = attack === null && filter.damageTypes === null && types.length === damageTypeOrder.length;
    return row(person, complete ? person.damage.amount : cells.reduce((sum, cell) => sum + cell.amount, 0),
      complete ? person.damage.count : cells.reduce((sum, cell) => sum + cell.count, 0), cells);
  }
  if (filter.damageGrouping === 'type' && attack === null) return selectedTypes.map(type =>
    board(`damage:type:${type}`, damageTypeLabels[type], people.map(person => damageRow(person, [type])), { damageType: type }));
  if (filter.damageGrouping === 'profession') return rankingProfessions(people).map(profession =>
    board(`damage:profession:${encodeURIComponent(profession.value)}`, profession.label,
      people.filter(person => professionKey(person) === profession.value).map(person => damageRow(person, selectedTypes)), { profession: profession.value }));
  const label = attack !== null ? attackKindLabels[attack] : selectedTypes.length === damageTypeOrder.length ? '全部伤害'
    : selectedTypes.map(type => damageTypeLabels[type]).join(' + ');
  return [board('damage:combined', label, people.map(person => damageRow(person, selectedTypes)))];
}
