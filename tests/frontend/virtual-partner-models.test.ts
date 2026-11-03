import { effectScope, ref } from 'vue';
import { afterEach, expect, it, vi } from 'vitest';
import { useModelPicker } from '../../apps/features/virtual_partner/frontend/useModelPicker';
import type { LimitedHost } from '../../apps/framework/module-sdk/src';

const endpoint = 'https://provider.invalid/v1';

function picker(initialModel = '') {
  const rpc = vi.fn<LimitedHost['callWorker']>();
  const host = { available: true, callWorker: rpc } as LimitedHost;
  const baseUrl = ref(endpoint);
  const apiKey = ref('');
  const model = ref(initialModel);
  const profileId = ref<string | null>('saved-profile');
  const scope = effectScope();
  const view = scope.run(() => useModelPicker(host, profileId, baseUrl, apiKey, model))!;
  return { ...view, baseUrl, apiKey, model, profileId, rpc, close: () => scope.stop() };
}

afterEach(() => { vi.useRealTimers(); });

it('loads with a draft key before saving, polls by ID and changes the model only on user input', async () => {
  vi.useFakeTimers();
  const view = picker();
  try {
    view.profileId.value = null;
    view.apiKey.value = 'synthetic-key';
    view.rpc.mockResolvedValueOnce({id: 'lookup'}).mockResolvedValueOnce({status: 'loading'})
      .mockResolvedValueOnce({status: 'ready', models: ['chat-small', 'image-model']});
    await view.refresh();
    expect(view.catalog.value.status).toBe('loading');
    view.selection.value = view.customOption;
    view.model.value = 'draft-deployment';
    await vi.advanceTimersByTimeAsync(400);
    expect(view.rpc.mock.calls).toEqual([
      ['partner.models', {baseUrl: endpoint, profileId: null, apiKey: 'synthetic-key'}],
      ['partner.models.result', {id: 'lookup'}],
      ['partner.models.result', {id: 'lookup'}],
    ]);
    expect(view.models.value).toEqual(['chat-small', 'image-model']);
    expect(view.model.value).toBe('draft-deployment');
    expect(view.custom.value).toBe(true);
    view.selection.value = 'chat-small';
    expect(view.custom.value).toBe(false);
    expect(view.model.value).toBe('chat-small');
    view.selection.value = view.customOption;
    expect(view.model.value).toBe('chat-small');
    expect(vi.getTimerCount()).toBe(0);
  } finally { view.close(); }
});

it('isolates results and draft credentials when the user switches providers mid-request', async () => {
  const view = picker('keep-my-model');
  let finish!: (result: unknown) => void;
  try {
    view.apiKey.value = 'first-draft';
    view.rpc.mockResolvedValueOnce({id: 'first'}).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    const old = view.refresh();
    await Promise.resolve();
    view.baseUrl.value = 'https://second.invalid/v1';
    expect(view.apiKey.value).toBe('');
    expect(view.catalog.value.status).toBe('idle');
    view.apiKey.value = 'second-draft';
    view.rpc.mockResolvedValueOnce({id: 'second'}).mockResolvedValueOnce({status: 'ready', models: ['second-chat']});
    await view.refresh();
    expect(view.rpc.mock.calls[2]).toEqual(['partner.models', {baseUrl: view.baseUrl.value, profileId: 'saved-profile', apiKey: 'second-draft'}]);
    finish({status: 'ready', models: ['obsolete']});
    await old;
    expect(view.models.value).toEqual(['second-chat']);
    expect(view.selection.value).toBe('keep-my-model');
    view.apiKey.value = 'new-draft';
    expect(view.models.value).toEqual([]);
  } finally { view.close(); }
});

it('omits a blank key, allows retry after failure, and distinguishes a successful empty list', async () => {
  const view = picker('old-name');
  try {
    view.rpc.mockResolvedValueOnce({id: 'one'}).mockResolvedValueOnce({status: 'failed', error: 'HTTP 403'});
    await view.refresh();
    expect(view.rpc.mock.calls[0]).toEqual(['partner.models', {baseUrl: endpoint, profileId: 'saved-profile'}]);
    expect(view.catalog.value).toEqual({status: 'failed', error: 'HTTP 403'});
    view.rpc.mockResolvedValueOnce({id: 'two'}).mockResolvedValueOnce({status: 'ready', models: []});
    await view.refresh();
    expect(view.catalog.value).toEqual({status: 'ready', models: []});
    expect(view.model.value).toBe('old-name');
  } finally { view.close(); }
});

it('times out while waiting for a receipt and ignores receipts delivered after timeout or closing', async () => {
  vi.useFakeTimers();
  const view = picker();
  let finish!: (result: unknown) => void;
  const delayReceipt = () => new Promise(resolve => { finish = resolve; });
  try {
    view.rpc.mockImplementationOnce(delayReceipt);
    const old = view.refresh();
    await vi.advanceTimersByTimeAsync(35_000);
    expect(view.catalog.value).toMatchObject({status: 'failed', error: expect.stringContaining('超时')});
    finish({id: 'expired'});
    await old;
    view.rpc.mockImplementationOnce(delayReceipt);
    const closing = view.refresh();
    view.close();
    finish({id: 'closed'});
    await closing;
    expect(view.rpc.mock.calls.map(([method]) => method)).toEqual(['partner.models', 'partner.models']);
    expect(vi.getTimerCount()).toBe(0);
  } finally { view.close(); }
});
