import { renderToString } from '@vue/server-renderer';
import { createRenderer, createSSRApp, h, nextTick } from 'vue';
import { expect, it } from 'vitest';
import PartnerJournal from '../../apps/features/virtual_partner/frontend/PartnerJournal.vue';
import type { Experience, Snapshot } from '../../apps/features/virtual_partner/frontend/types';
import { usePartner } from '../../apps/features/virtual_partner/frontend/usePartner';
import type { LimitedHost } from '../../apps/framework/module-sdk/src';

it('reconciles lost share acknowledgements without duplicating an entry or reviving a removed one', async () => {
  const state: Snapshot = {
    currentTopicId: 'paper', persona: {id: 'x', name: '小满', personality: '温和'},
    connection: {baseUrl: '', model: '', hasKey: false}, topics: [], messages: [], memories: [], run: null,
  };
  const requests: Record<string, unknown>[] = [];
  const host = {
    available: true,
    async callWorker(method: string, args: Record<string, unknown>) {
      if (method === 'partner.state') return state;
      expect(method).toBe('partner.share');
      requests.push({ ...args });
      if ([1, 3].includes(requests.length)) throw new Error('保存响应中断');
      if (requests.length === 4) return {id: 'removed-after-write', removed: true};
      return {id: 'shared'};
    },
  } as LimitedHost;
  const renderer = createRenderer<{}, {}>({
    createElement: () => ({}), createText: () => ({}), createComment: () => ({}), parentNode: () => null,
    nextSibling: () => null, patchProp() {}, insert() {}, remove() {}, setText() {}, setElementText() {},
  });
  let view!: ReturnType<typeof usePartner>;
  const app = renderer.createApp({ setup() { view = usePartner(host); return () => h('div'); } });
  app.mount({});
  try {
    await nextTick();
    const fields = { title: '小纸船', url: '', excerpt: '', impression: '' };
    expect(await view.share(fields)).toBe(false);
    expect(view.error.value).toBe('保存响应中断');
    expect(await view.share(fields)).toBe(true);
    expect(requests[0].requestId).toBeTruthy();
    expect(requests[1]).toEqual(requests[0]);
    const next = {...fields, title: '另一艘船'};
    expect(await view.share(next)).toBe(false);
    expect(requests[2].requestId).not.toBe(requests[0].requestId);
    expect(await view.share(next)).toBe(false);
    expect(requests[3]).toEqual(requests[2]);
    expect(view.error.value).toContain('已经移除');
    expect(await view.share(next)).toBe(true);
    expect(requests[4].requestId).not.toBe(requests[3].requestId);
  } finally { app.unmount(); }
});

it('labels title-only evidence and renders shared URLs and excerpts as inert source text', async () => {
  const common = {url: 'https://example.invalid/notes', impression: '', source: 'user-share', scope: 'private', createdAt: 1, updatedAt: 1} as const;
  const entries: Experience[] = [
    {id: 'url', title: '只有标题', excerpt: '', evidence: 'title', ...common},
    {id: 'piece', title: '附上摘录', excerpt: '<img src=x onerror=alert(1)>', evidence: 'excerpt', ...common},
  ];
  const html = await renderToString(createSSRApp(PartnerJournal, {entries, busy:false, share:async () => false, save:async () => false}));
  expect(html).toContain('仅有标题');
  expect(html).toContain('附原文摘录');
  expect(html).toContain('&lt;img src=x onerror=alert(1)&gt;');
  expect(html).toContain(common.url);
  expect(html).not.toMatch(/<(?:img|a|iframe)\b/i);
});
