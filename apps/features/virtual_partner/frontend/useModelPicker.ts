import { computed, onScopeDispose, ref, shallowRef, watch, type Ref } from 'vue';
import { errorMessage, type LimitedHost } from '../../../framework/module-sdk/src/index';

type Catalog = { status: 'idle' | 'loading' } | { status: 'ready'; models: string[] } | { status: 'failed'; error: string };
type ModelResult = { status: 'loading' | 'ready' | 'failed'; models?: string[]; error?: string };
// Provider IDs cannot contain spaces, so this option cannot collide with an ID.
const customOption = '自定义 ID';

export function useModelPicker(host: LimitedHost, profileId: Ref<string | null>, baseUrl: Ref<string>, apiKey: Ref<string>, model: Ref<string>) {
  const catalog = shallowRef<Catalog>({ status: 'idle' });
  const models = computed(() => catalog.value.status === 'ready' ? catalog.value.models : []);
  const custom = ref(false);
  const selection = computed({
    get: () => custom.value ? customOption : model.value,
    set: (option: string) => {
      custom.value = option === customOption;
      if (!custom.value) model.value = option;
    },
  });

  let revision = 0;
  let pollTimer: ReturnType<typeof setTimeout> | undefined;
  let deadline: ReturnType<typeof setTimeout> | undefined;
  function reset() {
    revision++;
    clearTimeout(pollTimer);
    clearTimeout(deadline);
    catalog.value = { status: 'idle' };
  }

  async function refresh() {
    if (catalog.value.status === 'loading') return;
    reset();
    const ticket = revision;
    const params: Record<string, string | null> = { baseUrl: baseUrl.value.trim(), profileId: profileId.value };
    if (apiKey.value.trim()) params.apiKey = apiKey.value.trim();
    catalog.value = { status: 'loading' };

    function fail(cause: unknown) {
      if (ticket !== revision) return;
      reset();
      catalog.value = { status: 'failed', error: errorMessage(cause) };
    }
    async function poll(id: string) {
      const result = await host.callWorker<ModelResult>('partner.models.result', { id });
      if (ticket !== revision) return;
      if (result?.status === 'loading') {
        pollTimer = setTimeout(() => { void poll(id).catch(fail); }, 400);
      } else if (result?.status === 'failed') {
        throw new Error(result.error || '模型列表读取失败。');
      } else if (result?.status === 'ready' && Array.isArray(result.models) && result.models.every(m => typeof m === 'string' && m.length)) {
        clearTimeout(deadline);
        catalog.value = { status: 'ready', models: result.models };
      } else {
        throw new Error('模型列表响应格式无效。');
      }
    }

    deadline = setTimeout(() => fail('读取模型列表超时，请重试。'), 35_000);
    try {
      const job = await host.callWorker<{ id: string }>('partner.models', params);
      if (ticket !== revision) return;
      if (!job || typeof job.id !== 'string' || !job.id) throw new Error('模型列表请求缺少任务 ID。');
      await poll(job.id);
    } catch (cause) { fail(cause); }
  }

  async function readSaved(fetchIfMissing: boolean) {
    reset();
    if (!profileId.value) return;
    const ticket = revision;
    catalog.value = { status: 'loading' };
    try {
      const result = await host.callWorker<{ models: string[] | null }>('partner.catalog', { id: profileId.value });
      if (ticket !== revision) return;
      if (result?.models === null) {
        catalog.value = { status: 'idle' };
        if (fetchIfMissing) await refresh();
      } else if (Array.isArray(result?.models) && result.models.every(m => typeof m === 'string' && m.length)) {
        catalog.value = { status: 'ready', models: result.models };
      } else {
        throw new Error('已保存的模型列表格式无效。');
      }
    } catch (cause) {
      if (ticket === revision) catalog.value = { status: 'failed', error: errorMessage(cause) };
    }
  }

  watch([profileId, baseUrl], () => { apiKey.value = ''; }, { flush: 'sync' });
  watch([profileId, baseUrl, apiKey], reset, { flush: 'sync' });
  onScopeDispose(reset);
  return { catalog, models, custom, customOption, selection, refresh, reset, readSaved };
}
