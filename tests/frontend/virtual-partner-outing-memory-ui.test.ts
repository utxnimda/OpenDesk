import { expect, it, vi } from 'vitest';
import { mountPage, settle } from './virtual-partner-page-harness';

const older = {id: 'old', bvid: 'BV1xx411c7mD', title: '纸鹤的翅膀', author: '示例作者', description: '<img src=x> 实际读到的文字',
  url: 'https://www.bilibili.com/video/BV1xx411c7mD/', readAt: 1770000000000, reason: '匹配兴趣', draft: '纸鹤要怎么做？',
  discussion: 'draft', discussionNote: '本地草稿', replyNotes: [
    {id: 'n1', incoming: '如何对齐翅膀？', draft: '我想试试从中心折起。', status: 'draft', note: ''}]};
const newer = {...older, id: 'latest', title: '刚读到的曲谱', replyNotes: []};

it('opens an old chat citation directly and distinguishes observed text, incoming material and local drafts', async () => {
  const worker = vi.fn((_method: string, args: Record<string, unknown>) =>
    args.readId === 'old' ? {entries: [older], nextBeforeId: null} : {entries: [newer], nextBeforeId: 'latest'});
  const page = await mountPage(undefined, {outingWorker: worker, chat: () => ({
    messages: [{id: 'm', role: 'assistant', text: '我记得那只纸鹤。', status: 'completed', turnId: 't1', memoryIds: [],
      outingSources: [{id: 'old', title: older.title}]}], run: null})});
  await page.click('查看这条经历');
  expect(worker).toHaveBeenLastCalledWith('partner.outing.memories', {readId: 'old'});
  expect(page.root.label).toContain('阅读证据与来源');
  expect(page.root.label).toContain(older.description);
  expect(page.root.label).toContain('用户带回的评论 · 未经站点核实');
  expect(page.root.label).toContain('评论草稿 · 未发布');
  expect(page.root.label).toContain('回复草稿 · 未发布');
  expect(page.root.label).toContain(older.replyNotes[0].draft);
  expect(page.root.nodes.some(el => el.tag === 'img' || el.tag === 'a')).toBe(false);
  await page.click('查看全部经历');
  expect(worker).toHaveBeenLastCalledWith('partner.outing.memories', {});
  expect(page.root.label).toContain(newer.title);
});

it('pages and forgets an old read with confirmation, independently from manual memories', async () => {
  let forgotten = false;
  const worker = vi.fn((method: string, args: Record<string, unknown>) => {
    if (method === 'partner.outing.forget') {
      expect(args.id).toBe('old');
      forgotten = true;
      return {forgotten: true};
    }
    expect(method).toBe('partner.outing.memories');
    return args.beforeId === 'latest' ? {entries: forgotten ? [] : [older], nextBeforeId: null}
      : {entries: [newer], nextBeforeId: forgotten ? null : 'latest'};
  });
  const page = await mountPage(undefined, {outingWorker: worker});
  await page.click('打开记忆簿');
  await page.click('更早的外出经历');
  expect(worker).toHaveBeenLastCalledWith('partner.outing.memories', {beforeId: 'latest'});
  const item = page.find(el => el.tag === 'li' && el.label.includes(older.title));
  item.nodes.find(el => el.tag === 'button' && el.label === '忘记经历')!.click();
  await settle();
  expect(forgotten).toBe(false);
  await page.click('确认忘记经历');
  expect(forgotten).toBe(true);
  expect(page.root.label).not.toContain(older.title);
  expect(page.root.label).toContain(newer.title);
  await page.click('你教我的事');
  expect(page.root.label).toContain('还没有记下你教我的事');
  await page.click('外出经历');
  expect(page.root.label).not.toContain(older.title);
});
