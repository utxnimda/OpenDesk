import { afterEach, expect, it, vi } from 'vitest';
import type { OutingRead, OutingRun } from '../../apps/features/virtual_partner/frontend/useOuting';
import { mountPage, settle } from './virtual-partner-page-harness';

const read: OutingRead = {
  id: 'read1', bvid: 'BV1234567890', title: '纸船折痕', author: '纸上画室', description: '<img src=x> 摘录',
  url: 'https://www.bilibili.com/video/BV1234567890/', readAt: 1_770_000_000_000,
  reason: '匹配兴趣「绘画」。', draft: '纸上的折痕也有层次。', discussion: 'draft', discussionNote: '',
};
afterEach(() => vi.useRealTimers());

it('only reads local state when opened, saves user-selected interests, then polls one explicit outing across reopening', async () => {
  vi.useFakeTimers({toFake: ['setTimeout', 'clearTimeout']});
  let interests: string[] = [];
  let latest: OutingRun | null = null;
  let reads: OutingRead[] = [];
  const worker = vi.fn((method: string, args: Record<string, unknown>) => {
    if (method === 'partner.outing.state') return {latest, interests, reads};
    if (method === 'partner.outing.interests') { interests = [...args.interests as string[]]; return {interests}; }
    if (method === 'partner.outing.start') {
      latest = {id: String(args.requestId), status: 'running', message: '正在读取来源。', read: null};
      return {id: latest.id};
    }
    if (method === 'partner.outing.run') {
      expect(args).toEqual({id: latest!.id});
      reads = [read];
      return latest = {...latest!, status: 'completed', message: '已读公开资料。', read};
    }
    throw new Error(method);
  });
  const page = await mountPage(undefined, {outingWorker: worker});
  expect(worker).not.toHaveBeenCalled();
  await page.click('打开伙伴外出');
  expect(worker.mock.calls).toEqual([['partner.outing.state', {}]]);
  await page.click('绘画');
  await page.edit(page.input('自定义兴趣'), '铅笔素描');
  expect(page.button('保存兴趣并外出一次').disabled).toBe(true);
  await page.click('添加');
  await page.click('只保存兴趣');
  expect(interests).toEqual(['绘画', '铅笔素描']);
  expect(worker.mock.calls.some(([method]) => method === 'partner.outing.start')).toBe(false);
  await page.click('外出一次');
  expect(page.button('伙伴正在外出…').disabled).toBe(true);
  await page.click('关闭');
  await vi.advanceTimersByTimeAsync(1000);
  expect(worker.mock.calls.some(([method]) => method === 'partner.outing.run')).toBe(false);
  await page.click('打开伙伴外出');
  expect(page.button('伙伴正在外出…').disabled).toBe(true);
  await vi.advanceTimersByTimeAsync(650); await settle();
  expect(worker.mock.calls.filter(([method]) => method === 'partner.outing.start')).toHaveLength(1);
  expect(page.root.label).toContain('评论草稿 · 未发布');
  const url = page.find(el => el.tag === 'input' && el.parent?.label.trim() === '视频来源地址');
  expect(url.value).toBe(read.url);
  const select = vi.spyOn(url, 'select');
  url.click();
  expect(select).toHaveBeenCalled();
  expect(page.root.label).toContain(read.description);
  expect(page.root.nodes.some(el => el.tag === 'img' || el.tag === 'iframe' || el.tag === 'a')).toBe(false);
});

it('reconciles a lost start acknowledgement, distinguishes provider failure after reading, and never starts twice', async () => {
  let latest: OutingRun | null = null;
  const entry: OutingRead = {...read, draft: '', discussion: 'failed', discussionNote: '模型请求超时。'};
  const worker = vi.fn((method: string, args: Record<string, unknown>) => {
    if (method === 'partner.outing.state') return {interests: [], latest, reads: latest ? [entry] : []};
    if (method === 'partner.outing.start') {
      latest = {id: String(args.requestId), status: 'completed', message: '已读标题和简介。', read: entry};
      throw new Error('启动回执丢失');
    }
    throw new Error(method);
  });
  const page = await mountPage(undefined, {outingWorker: worker});
  await page.click('打开伙伴外出'); await page.click('外出一次');
  expect(page.root.label).toContain(entry.title);
  expect(page.root.label).toContain('草稿生成失败');
  expect(page.root.label).toContain('模型请求超时');
  expect(page.root.label).not.toContain('启动回执丢失');
  expect(worker.mock.calls.filter(([method]) => method === 'partner.outing.start')).toHaveLength(1);
});

it.each([
  ['skipped', '没有匹配兴趣的未读候选。', '这次略过'],
  ['failed', 'B 站拒绝了公开资料请求。', '外出失败'],
  ['interrupted', 'worker 已停止。', '外出已中断'],
] as const)('labels a %s outing without inventing a read', async (status, message, label) => {
  const page = await mountPage(undefined, {outingWorker: () => ({
    interests: [], latest: {id: 'last', status, message, read: null}, reads: [],
  })});
  await page.click('打开伙伴外出');
  expect(page.root.label).toContain(label + message);
  expect(page.root.label).toContain('0 条');
  expect(page.root.label).not.toContain('评论草稿 · 未发布');
});

it('offers a manual copy when the host clipboard policy rejects writing and emits no public action', async () => {
  const worker = vi.fn(() => ({interests: [], latest: null, reads: [read]}));
  const page = await mountPage(undefined, {outingWorker: worker});
  const writeText = vi.fn().mockRejectedValue(new Error('clipboard denied'));
  vi.stubGlobal('navigator', {clipboard: {writeText}});
  await page.click('打开伙伴外出'); await page.click('复制草稿');
  expect(page.root.label).toContain('按 Ctrl+C');
  expect(page.find(el => el.tag === 'textarea' && el.props.readonly !== undefined).value).toBe(read.draft);
  expect(writeText).toHaveBeenCalledWith(read.draft);
  expect(worker.mock.calls).toEqual([['partner.outing.state', {}]]);
});

it('blocks a second launch after losing progress and refreshes the same outing without issuing another start', async () => {
  vi.useFakeTimers({toFake: ['setTimeout', 'clearTimeout']});
  let latest: OutingRun = {id: 'existing', status: 'running', message: '', read: null};
  const worker = vi.fn((method: string) => {
    if (method === 'partner.outing.state') return {interests: [], latest, reads: latest.read ? [latest.read] : []};
    throw new Error('连接已中断');
  });
  const page = await mountPage(undefined, {outingWorker: worker});
  await page.click('打开伙伴外出');
  await vi.advanceTimersByTimeAsync(650); await settle();
  expect(page.root.label).toContain('暂时无法核实外出状态');
  expect(page.button('请先核实外出状态').disabled).toBe(true);
  latest = {...latest, status: 'completed', message: '已读资料。', read: {...read, draft: '', discussion: 'skipped'}};
  await page.click('重新读取外出状态');
  expect(page.root.label).toContain('这条略过评论');
  expect(page.root.label).not.toContain('暂时无法核实外出状态');
  expect(worker.mock.calls.map(([method]) => method)).toEqual(['partner.outing.state', 'partner.outing.run', 'partner.outing.state']);
});

it('requires saved interests to enable automatic outings and still lets the user switch them off with unsaved edits', async () => {
  const schedule = {enabled: false, nextAt: null, intervalMinutes: 60, dailyLimit: 3, attemptsToday: 0};
  const worker = vi.fn((method: string, args: Record<string, unknown>) => {
    if (method === 'partner.outing.state') return {interests: [], reads: [], latest: null};
    if (method === 'partner.outing.interests') return {interests: args.interests};
    if (method === 'partner.outing.schedule') return {...schedule, enabled: args.enabled};
    throw new Error(method);
  });
  const page = await mountPage(undefined, {outingWorker: worker, outingSchedule: schedule});
  await page.click('打开伙伴外出');
  const toggle = page.find(el => el.tag === 'input' && el.type === 'checkbox');
  const change = async (checked: boolean) => { toggle.props.onChange({currentTarget: {checked}}); await settle(); };
  await page.edit(page.input('自定义兴趣'), '模型制作');
  expect(toggle.disabled).toBe(true);
  await change(true);
  expect(worker.mock.calls.some(([method]) => method === 'partner.outing.schedule')).toBe(false);
  await page.click('添加');
  expect(toggle.disabled).toBe(true);
  await page.click('只保存兴趣');
  expect(toggle.disabled).toBe(false);
  await change(true);
  expect(toggle.props.checked).toBe(true);
  await page.click('音乐');
  expect(toggle.disabled).toBe(false);
  await change(false);
  expect(toggle.props.checked).toBe(false);
  expect(toggle.disabled).toBe(true);
  expect(worker.mock.calls.filter(([method]) => method === 'partner.outing.schedule')).toEqual([
    ['partner.outing.schedule', {enabled: true}], ['partner.outing.schedule', {enabled: false}],
  ]);
});
