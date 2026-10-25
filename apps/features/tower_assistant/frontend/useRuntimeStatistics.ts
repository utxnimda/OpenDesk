import { onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue';
import { errorMessage, type LimitedHost } from '../../../framework/module-sdk/src/index';
import { createStatisticsClient } from './statisticsClient';
import type { LatestEventSelector, OutputEvent, StatisticsExplanation, StatisticsOperation, StatisticsResponse } from './statisticsTypes';

export interface PinnedEvent {
  event: OutputEvent | null;
  summary: Pick<OutputEvent, 'kind' | 'personId' | 'personName' | 'category'>;
  latest: LatestEventSelector | null;
  selectionMode: 'sequence' | 'latest';
  sessionId: string;
  gameDir: string;
  explanation: StatisticsExplanation | null;
  error: string;
  loading: boolean;
}

function copy<T>(value: T): T { return JSON.parse(JSON.stringify(value)) as T; }

function sameValue(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (!left || !right || typeof left !== 'object' || typeof right !== 'object') return false;
  if (Array.isArray(left) || Array.isArray(right)) return Array.isArray(left) && Array.isArray(right)
    && left.length === right.length && left.every((value, index) => sameValue(value, right[index]));
  const source = left as Record<string, unknown>;
  const target = right as Record<string, unknown>;
  const keys = Object.keys(source);
  return keys.length === Object.keys(target).length && keys.every(key => Object.hasOwn(target, key) && sameValue(source[key], target[key]));
}

function matchesObservedEvent(observed: OutputEvent, returned: OutputEvent): boolean {
  // An explanation may add metadata omitted from the lightweight list, but it
  // must not change any observed amount, identity, time or raw formula input.
  return Object.keys(observed).filter(key => key !== 'detailSnapshot').every(key =>
    sameValue(observed[key as keyof OutputEvent], returned[key as keyof OutputEvent]));
}

/** Poll serially, discard responses for a previous directory, and never stop the worker on unmount. */
export function useRuntimeStatistics(host: LimitedHost, gameDir: () => string) {
  const response = shallowRef<StatisticsResponse | null>(null);
  const pending = ref<StatisticsOperation | ''>('');
  const failure = ref('');
  const actionFailure = ref('');
  const updatedAt = ref('');
  // One explicitly selected, frozen display snapshot. Polls never replace it,
  // even after ring eviction or a new recording interval.
  const pinned = shallowRef<PinnedEvent | null>(null);
  let pinGeneration = 0;
  const call = createStatisticsClient(host);
  let active = false;
  let generation = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;

  function clearTimer() { if (timer !== undefined) clearTimeout(timer); timer = undefined; }
  function schedule(delay = 1000) {
    clearTimer();
    if (active && host.available && gameDir().trim()) timer = setTimeout(() => { void request('statistics.status'); }, delay);
  }
  async function request(operation: StatisticsOperation): Promise<boolean> {
    if (!active || pending.value || !host.available || !gameDir().trim()) return false;
    clearTimer();
    const requestedGeneration = generation;
    const directory = gameDir();
    const mutation = !operation.endsWith('.status');
    if (mutation) actionFailure.value = '';
    pending.value = operation;
    try {
      const result = await call.request(operation, directory);
      if (!active || requestedGeneration !== generation) return false;
      response.value = result;
      failure.value = '';
      updatedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false });
      return true;
    } catch (error) {
      if (active && requestedGeneration === generation) {
        if (mutation) actionFailure.value = errorMessage(error);
        else failure.value = errorMessage(error);
      }
      return false;
    } finally {
      pending.value = '';
      // A directory changed while the previous request was in flight: read the new one immediately.
      schedule(requestedGeneration === generation ? 1000 : 0);
    }
  }
  async function explainPinned(): Promise<void> {
    const selection = pinned.value;
    if (!active || !selection || selection.loading) return;
    const requestedPin = pinGeneration;
    pinned.value = { ...selection, loading: true, error: '' };
    try {
      const result = selection.latest
        ? await call.explainLatest(selection.gameDir, selection.latest, selection.sessionId)
        : await call.explain(selection.gameDir, selection.event!.sequence, selection.sessionId, selection.event!.detailSnapshot);
      if (selection.event && result.event && !matchesObservedEvent(selection.event, result.event)) {
        throw new Error('公式服务返回的事件身份或原始数值与已固定明细不一致，请重新选择。');
      }
      if (active && requestedPin === pinGeneration) {
        // A successful latest lookup becomes an exact historical selection.
        // Retrying its explanation must never jump to a later event.
        pinned.value = { ...selection, event: copy(result.event ?? selection.event), latest: null, explanation: copy(result), error: '', loading: false };
      }
    } catch (error) {
      if (active && requestedPin === pinGeneration) {
        pinned.value = { ...selection, loading: false, error: errorMessage(error) };
      }
    }
  }
  function pinEvent(event: OutputEvent, context?: { sessionId: string; gameDir: string }) {
    if (!response.value) return;
    pinGeneration++;
    pinned.value = { event: copy(event), summary: { kind: event.kind, personId: event.personId, personName: event.personName, category: event.category }, latest: null, selectionMode: 'sequence', sessionId: context?.sessionId ?? response.value.statistics.sessionId,
      gameDir: context?.gameDir ?? gameDir().trim(), explanation: null, error: '', loading: false };
    void explainPinned();
  }
  function pinLatest(latest: LatestEventSelector, label: { personName: string; category: string }) {
    if (!response.value) return;
    pinGeneration++;
    pinned.value = { event: null, summary: { kind: latest.kind, personId: latest.personId, ...label },
      latest: copy(latest), selectionMode: 'latest', sessionId: response.value.statistics.sessionId,
      gameDir: gameDir().trim(), explanation: null, error: '', loading: false };
    void explainPinned();
  }
  function closePinned() {
    pinGeneration++;
    pinned.value = null;
  }
  function resetDirectory() {
    generation++;
    if (pinned.value?.loading) {
      pinGeneration++;
      pinned.value = { ...pinned.value, loading: false, error: '游戏目录已改变，本次公式读取已取消；已选内容保留，请重新选择或重试。' };
    }
    clearTimer();
    response.value = null;
    failure.value = '';
    actionFailure.value = '';
    updatedAt.value = '';
    if (!pending.value) void request('capture.status');
  }
  watch(gameDir, resetDirectory);
  onMounted(() => { active = true; resetDirectory(); });
  onBeforeUnmount(() => { active = false; generation++; pinGeneration++; clearTimer(); });
  return { response, pending, failure, actionFailure, updatedAt, request, pinned, pinEvent, pinLatest, closePinned, explainPinned };
}
