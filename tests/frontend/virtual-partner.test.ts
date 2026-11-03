import { createRenderer, h, nextTick } from 'vue';
import { expect, it } from 'vitest';
import type { LimitedHost } from '../../apps/framework/module-sdk/src';
import type { Snapshot } from '../../apps/features/virtual_partner/frontend/types';
import { usePartner } from '../../apps/features/virtual_partner/frontend/usePartner';

function snapshot(topicId: string, messageId: string): Snapshot {
  return {
    persona: { id: 'persona', name: '小满', personality: '温和' },
    connection: { baseUrl: 'https://example.invalid/v1', model: '', hasKey: false },
    topics: [{ id: topicId, title: topicId }], currentTopicId: topicId, memories: [],
    messages: [{ id: messageId, role: 'user', text: messageId, status: 'completed', turnId: 'turn', memoryIds: [] }],
    run: null, hasOlderMessages: true,
  };
}

async function mount(call: (name: string, params: Record<string, unknown>) => Promise<unknown>) {
  const renderer = createRenderer<{}, {}>({
    createElement: () => ({}), createText: () => ({}), createComment: () => ({}), parentNode: () => null,
    nextSibling: () => null, patchProp() {}, insert() {}, remove() {}, setText() {}, setElementText() {},
  });
  let model!: ReturnType<typeof usePartner>;
  const app = renderer.createApp({ setup() {
    model = usePartner({ available: true, callWorker: (name, params = {}) => call(name, params) } as LimitedHost);
    return () => h('div');
  } });
  app.mount({});
  await nextTick();
  return { model, unmount: () => app.unmount() };
}

it('retains an offline draft and the same request identity when the first response is lost', async () => {
  const sends: Record<string, unknown>[] = [];
  const view = await mount(async (name, params) => {
    if (name === 'partner.state') return snapshot('one', 'latest');
    expect(name).toBe('partner.send');
    sends.push({ ...params });
    if (sends.length === 1) throw new Error('响应中断');
    return { id: 'existing-turn' };
  });
  try {
    view.model.draft.value = '记住：喜欢喝茶';
    await view.model.send();
    expect(view.model.draft.value).toBe('记住：喜欢喝茶');
    expect(view.model.error.value).toContain('响应中断');
    await view.model.send();
    expect(sends).toHaveLength(2);
    expect(sends[1]).toEqual(sends[0]);
    expect(view.model.draft.value).toBe('');
  } finally { view.unmount(); }
});

it('does not insert an older page from the previous topic after switching', async () => {
  let deliver!: (snapshot: Snapshot) => void;
  const view = await mount(async (_name, params) => {
    if (params.beforeMessageId) return new Promise<Snapshot>(resolve => { deliver = resolve; });
    return snapshot(String(params.topicId || 'one'), 'latest');
  });
  try {
    const older = view.model.earlier();
    await view.model.load('two');
    deliver(snapshot('one', 'old'));
    expect(await older).toBe(false);
    expect(view.model.state.value?.currentTopicId).toBe('two');
    expect(view.model.state.value?.messages.map(message => message.id)).toEqual(['latest']);
  } finally { view.unmount(); }
});
