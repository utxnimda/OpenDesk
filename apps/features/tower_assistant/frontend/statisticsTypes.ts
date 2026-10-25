export type OutputKind = 'work' | 'damage';
export type DamageType = 'physical' | 'magic' | 'piercing' | 'unknown';
export type AttackKind = 'basic' | 'skill' | 'periodic' | 'unknown';
export type CaptureState = 'not-installed' | 'waiting' | 'connected' | 'stale' | 'error';
export interface CaptureStatus {
  state: CaptureState;
  message: string;
  installed: boolean | null;
  gameRunning: boolean | null;
  canInstall: boolean;
  canRemove: boolean;
  coverage: string[];
  gameDir: string;
  updateRequired?: boolean | null;
}
export interface PersonSnapshot {
  appearance?: string;
  iconKey?: string;
  professionId?: string | number;
  profession?: string;
  professionDescription?: string;
  job?: string;
  attributes?: { id: string; label: string; value: number }[];
  equipment?: { slot?: string; name: string; type?: string; iconKey?: string; level?: number; qualityName?: string }[];
}
export interface WorkFormula {
  text: string;
  result: number;
  calculated: number;
  matches: boolean;
  terms: { id: string; label: string; value: number | string; mode?: string }[];
  steps?: { label: string; value: number }[];
  limitation: string;
}
export interface DamageFormula {
  expression: 5579 | 5584;
  inputs: number[];
  result: number;
  calculated: number;
  matches: boolean;
  text: string;
  terms: { id: string; label: string; value: number; mode: 'multiplier' | 'additive' | 'defense-rate' }[];
  sensitivity: { id: string; label: string; changeLabel: string; baseline: number; adjusted: number; delta: number; gainPercent: number | null }[];
  limitation: string;
}
/** Opaque worker-issued receipt. The frontend never parses or recomputes payload. */
export interface DetailSnapshot {
  schemaVersion: 1;
  payload: string;
  signature: string;
}
export interface OutputEvent {
  schemaVersion: 1;
  sequence: number;
  kind: OutputKind;
  personId: string;
  personName: string;
  category: string;
  amount: number;
  effectiveAmount?: number;
  overflowAmount?: number | null;
  gameTime?: number;
  targetName?: string;
  observedAfterSeconds?: number;
  formulaCoverage?: 'main-product' | 'special-return' | 'stored-buff-value-only' | 'unavailable';
  formula?: { expression: 5579 | 5584; inputs: number[]; result: number };
  person?: PersonSnapshot;
  damageType?: DamageType;
  attackKind?: AttackKind;
  skillId?: string;
  skillName?: string;
  actionId?: string;
  workFormula?: unknown;
  detailSnapshot?: DetailSnapshot;
}
export interface WorkCell {
  category: string;
  amount: number;
  count: number;
  latestAmount: number;
  averageAmount: number;
  latestSequence: number;
}
export interface DamageCell {
  damageType: DamageType;
  amount: number;
  count: number;
  perRealSecond: number | null;
  averageAmount: number;
  latestAmount: number;
  latestSequence: number;
}
export interface AttackCell extends Omit<DamageCell, 'damageType'> {
  attackKind: AttackKind;
  skillId: string | null;
  skillName: string | null;
  actionCount: number | null;
  averagePerAction: number | null;
}
export interface PersonStatistics {
  personId: string;
  personName: string;
  person: PersonSnapshot | null;
  personSequence?: number | null;
  work: WorkCell[];
  damage: { amount: number; count: number; perRealSecond: number | null; types: DamageCell[]; attacks: AttackCell[] };
}
export interface OutputTotal {
  kind: OutputKind;
  personId: string;
  personName: string;
  category: string;
  count: number;
  amount: number;
  effectiveAmount: number;
  effectiveCount: number;
  effectiveComplete: boolean;
  overflowAmount: number | null;
  latestAmount: number;
  averageAmount: number;
  perRealSecond: number | null;
}
export interface StatisticsSnapshot {
  schemaVersion: 1;
  sessionId: string;
  state: 'idle' | 'running' | 'stopped' | 'interrupted';
  reason: string;
  elapsedRealSeconds: number;
  eventCount: number;
  retainedEventCount: number;
  lastSequence: number;
  totals: OutputTotal[];
  events: OutputEvent[];
  workCategories: string[];
  people: PersonStatistics[];
  summary: { work: { amount: number; count: number }; damage: { amount: number; perRealSecond: number | null; count: number } };
}
export interface StatisticsResponse {
  schemaVersion: 1;
  capture: CaptureStatus;
  statistics: StatisticsSnapshot;
}
export type StatisticsOperation = 'capture.status' | 'capture.install' | 'capture.remove' | 'statistics.start' | 'statistics.stop' | 'statistics.status';
export type LatestEventSelector = { kind: 'work'; personId: string; category: string }
  | { kind: 'damage'; personId: string; attackKind: AttackKind; skillId: string | null };
export interface StatisticsExplanation {
  schemaVersion: 1;
  sessionId: string;
  sequence: number;
  formulaCoverage: OutputEvent['formulaCoverage'];
  formula: DamageFormula | null;
  workFormula?: WorkFormula | null;
  event?: OutputEvent;
  selection?: LatestEventSelector & { mode: 'latest' };
  message: string;
}
