import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { errorMessage, type LimitedHost } from '../../../framework/module-sdk/src/index';
import { isActive, type ExperienceInput, type Persona, type Run, type Save, type Share, type Snapshot } from './types';

export function createRequestId() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
  return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join('-');
}

export function usePartner(host: LimitedHost) {
  const state = ref<Snapshot>();
  const run = ref<Run | null>(null);
  const working = ref(false);
  const loading = ref(false);
  const loadingEarlier = ref(false);
  const stale = ref(false);
  const error = ref('');
  const drafts = ref<Record<string, string>>({});
  const selectedTopic = ref('');
  let mounted = true;
  let revision = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let retry: { topicId: string; text: string; requestId: string } | undefined;
  let shareRetry: (ExperienceInput & {requestId: string}) | undefined;
  const active = computed(() => isActive(run.value));
  const draft = computed({
    get: () => drafts.value[selectedTopic.value] ?? '',
    set: (value: string) => { drafts.value[selectedTopic.value] = value; },
  });

  function monitor(next: Run | null) {
    clearTimeout(timer);
    run.value = next;
    if (mounted && next && isActive(next)) {
      timer = setTimeout(() => { void poll(next.id); }, 400);
    }
  }

  async function poll(id: string) {
    const ticket = revision;
    try {
      const update = await host.callWorker<Run>('partner.run', { id });
      if (!mounted || ticket !== revision || run.value?.id !== id) return;
      if (isActive(update)) monitor(update);
      else {
        run.value = update;
        await load();
        if (update.status === 'failed' && !stale.value) error.value = update.error || '回复失败，请检查连接设置后再试。';
      }
    } catch (cause) {
      if (!mounted || ticket !== revision) return;
      stale.value = true;
      error.value = '暂时无法读取回复进度。' + errorMessage(cause);
    }
  }

  async function load(topicId = selectedTopic.value) {
    const ticket = ++revision;
    loading.value = true;
    loadingEarlier.value = false;
    clearTimeout(timer);
    try {
      const snapshot = await host.callWorker<Snapshot>('partner.state', topicId ? { topicId } : {});
      if (!mounted || ticket !== revision) return false;
      state.value = snapshot;
      selectedTopic.value = snapshot.currentTopicId;
      stale.value = false;
      error.value = '';
      monitor(snapshot.run ?? null);
      return true;
    } catch (cause) {
      if (mounted && ticket === revision) {
        stale.value = true;
        error.value = errorMessage(cause);
      }
      return false;
    } finally { if (ticket === revision) loading.value = false; }
  }

  async function earlier() {
    const page = state.value;
    if (!page?.hasOlderMessages || !page.messages.length || loadingEarlier.value || loading.value) return false;
    const ticket = revision;
    const first = page.messages[0].id;
    loadingEarlier.value = true;
    try {
      const previous = await host.callWorker<Snapshot>('partner.state', { topicId: page.currentTopicId, beforeMessageId: first });
      if (!mounted || ticket !== revision || previous.currentTopicId !== page.currentTopicId) return false;
      const loaded = new Set(page.messages.map(message => message.id));
      const extra = previous.messages.filter(message => !loaded.has(message.id));
      page.messages = [...extra, ...page.messages];
      page.hasOlderMessages = previous.hasOlderMessages && extra.length > 0;
      return true;
    } catch (cause) {
      if (mounted && ticket === revision) error.value = errorMessage(cause);
      return false;
    } finally { if (ticket === revision) loadingEarlier.value = false; }
  }

  async function change<T>(method: string, params: Record<string, unknown>, after?: (result: T) => string | undefined) {
    if (working.value) return false;
    working.value = true;
    error.value = '';
    try {
      const response = await host.callWorker<T>('partner.' + method, params);
      const topicId = after?.(response) ?? selectedTopic.value;
      await load(topicId);
      // The write already succeeded even if a following snapshot could not be loaded.
      return true;
    } catch (cause) {
      // The worker may have switched the active profile before its reply was lost.
      if (method === 'connection.select' || method === 'connection.upsert') await load();
      error.value = errorMessage(cause);
      return false;
    } finally { working.value = false; }
  }

  const save: Save = (method, values) => {
    if (method === 'profile') return change<Persona>(method, values, persona => {
      // The worker returns the committed profile even if refreshing the conversation subsequently fails.
      if (state.value) state.value.persona = persona;
      return undefined;
    });
    return change(method, values);
  };
  const share: Share = async ({title, excerpt, url, impression}) => {
    if (working.value) return false;
    if (!shareRetry || shareRetry.title !== title || shareRetry.excerpt !== excerpt ||
        shareRetry.url !== url || shareRetry.impression !== impression) {
      shareRetry = { title, excerpt, url, impression, requestId: createRequestId() };
    }
    const saved = await change<{id: string; removed?: boolean}>('share', { ...shareRetry }, receipt => {
      if (receipt.removed) {
        shareRetry = undefined;
        throw new Error('上次分享的近况已经移除。如需重新添加，请再次保存。');
      }
      return undefined;
    });
    if (saved) shareRetry = undefined;
    return saved;
  };
  const createTopic = (title: string) => change<{id: string}>('topic', { title }, result => result.id);
  async function send() {
    const topicId = selectedTopic.value;
    const text = draft.value.trim();
    if (!text || !topicId || active.value || working.value || loading.value || stale.value) return;
    if (!retry || retry.topicId !== topicId || retry.text !== text) {
      retry = { topicId, text, requestId: createRequestId() };
    }
    await change<{id: string}>('send', retry, () => {
      if (drafts.value[topicId]?.trim() === text) drafts.value[topicId] = '';
      retry = undefined;
      return topicId;
    });
  }
  async function cancel() {
    if (run.value && isActive(run.value)) await change('cancel', { id: run.value.id });
  }

  onMounted(() => { void load(); });
  onBeforeUnmount(() => { mounted = false; revision++; clearTimeout(timer); });
  return { state, run, active, working, loading, loadingEarlier, stale, error, draft, load, earlier, save, share, createTopic, send, cancel };
}
