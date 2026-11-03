import { computed, onMounted, onScopeDispose, ref, shallowRef } from 'vue';
import { errorMessage, type LimitedHost } from '../../../framework/module-sdk/src/index';
import { createRequestId } from './usePartner';

export interface OutingRead {
  id: string; bvid: string; title: string; author: string; description: string; url: string; readAt: number;
  reason: string; draft: string; discussion: 'pending' | 'draft' | 'skipped' | 'failed'; discussionNote: string;
}
export interface OutingRun {
  id: string; status: 'running' | 'completed' | 'skipped' | 'failed' | 'interrupted'; message: string; read: OutingRead | null;
}
interface OutingState { interests: string[]; latest: OutingRun | null; reads: OutingRead[] }

export function useOuting(host: LimitedHost) {
  const state = shallowRef<OutingState>();
  const interests = ref<string[]>([]);
  const latest = shallowRef<OutingRun | null>(null);
  const loading = ref(false);
  const changing = ref(false);
  const stale = ref(false);
  const error = ref('');
  const dirty = computed(() => JSON.stringify(interests.value) !== JSON.stringify(state.value?.interests ?? []));
  const locked = computed(() => loading.value || changing.value || stale.value || latest.value?.status === 'running' || !state.value);
  const reads = computed(() => {
    const current = latest.value?.read;
    const history = state.value?.reads ?? [];
    return current ? [current, ...history.filter(item => item.id !== current.id)] : history;
  });
  let ticket = 0;
  let disposed = false;
  let pendingId: string | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;

  function monitor(run: OutingRun | null, version: number) {
    latest.value = run;
    if (run?.id === pendingId) pendingId = null;
    if (run?.status === 'running') {
      timer = setTimeout(() => { void poll(run.id, version); }, 600);
    }
  }

  function readFailed(cause: unknown) {
    stale.value = true;
    error.value = '暂时无法核实外出状态。' + errorMessage(cause);
  }

  async function poll(id: string, version: number) {
    try {
      const run = await host.callWorker<OutingRun>('partner.outing.run', {id});
      if (version !== ticket) return;
      if (!run || run.id !== id) throw new Error('外出进度响应缺少对应的任务。');
      monitor(run, version);
      if (run.status !== 'running') await refresh();
    } catch (cause) { if (version === ticket) readFailed(cause); }
  }

  async function refresh() {
    if (disposed) return;
    const version = ++ticket;
    clearTimeout(timer);
    loading.value = true;
    try {
      const next = await host.callWorker<OutingState>('partner.outing.state', {});
      if (version !== ticket) return;
      if (!next || !Array.isArray(next.reads) || !Array.isArray(next.interests) || next.latest === undefined) {
        throw new Error('外出记录响应格式无效。');
      }
      if (!state.value || !dirty.value) interests.value = [...next.interests];
      state.value = next;
      monitor(next.latest, version);
      stale.value = false;
      error.value = '';
    } catch (cause) { if (version === ticket) readFailed(cause); }
    finally { if (version === ticket) loading.value = false; }
  }

  async function writeInterests() {
    const saved = await host.callWorker<{interests: string[]}>('partner.outing.interests', {interests: [...interests.value]});
    if (disposed) return;
    if (!Array.isArray(saved?.interests)) throw new Error('保存兴趣时没有收到已保存的列表。');
    interests.value = [...saved.interests];
    state.value = {...state.value!, interests: saved.interests};
    pendingId = null;
  }

  async function saveInterests() {
    if (locked.value || !dirty.value) return;
    changing.value = true;
    error.value = '';
    try { await writeInterests(); }
    catch (cause) { if (!disposed) error.value = errorMessage(cause); }
    finally { changing.value = false; }
  }

  async function start() {
    if (locked.value) return;
    changing.value = true;
    error.value = '';
    let requestId: string | null = null;
    try {
      if (dirty.value) await writeInterests();
      if (disposed) return;
      requestId = pendingId ?? createRequestId();
      pendingId = requestId;
      const receipt = await host.callWorker<{id: string}>('partner.outing.start', {requestId});
      if (disposed) return;
      if (receipt?.id !== requestId) throw new Error('外出请求没有收到对应的任务编号。');
      await refresh();
    } catch (cause) {
      if (disposed) return;
      // An acknowledgement can be lost after the worker has already started.
      if (requestId) await refresh();
      if (!disposed && (!requestId || latest.value?.id !== requestId)) {
        error.value = [errorMessage(cause), stale.value ? error.value : ''].filter(Boolean).join(' ');
      }
    } finally { changing.value = false; }
  }

  onMounted(() => { void refresh(); });
  onScopeDispose(() => { disposed = true; ticket++; clearTimeout(timer); });
  return {state, interests, latest, reads, loading, changing, stale, locked, dirty, error, refresh, start, saveInterests};
}
