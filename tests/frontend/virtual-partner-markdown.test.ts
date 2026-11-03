import { afterEach, expect, it, vi } from 'vitest';
import { renderPartnerMarkdown } from '../../apps/features/virtual_partner/frontend/markdown';
import type { Message, Run } from '../../apps/features/virtual_partner/frontend/types';
import { mountPage, settle } from './virtual-partner-page-harness';

const screenshot = '这次“外出”记录里，我读到的是一条 B 站视频的**公开标题和简介**，没有播放视频，也没有发表评论。';
const strong = '<strong>公开标题和简介</strong>';
afterEach(() => vi.useRealTimers());

it('formats assistant history and the live stream while leaving the user’s literal text intact', async () => {
  vi.useFakeTimers({toFake: ['setTimeout', 'clearTimeout']});
  const user: Message = {id: 'a', turnId: 'turn', role: 'user', status: 'completed', text: '**照原样保留**', memoryIds: []};
  const assistant: Message = {...user, id: 'b', role: 'assistant', text: screenshot};
  let messages = [user, assistant];
  let run: Run = {id: 'current', topicId: 't', status: 'running', text: '正在看**公开标'};
  const page = await mountPage(undefined, {chat: () => ({messages, run})});
  const replies = () => page.root.nodes.filter(el => el.props.class === 'vp-markdown').map(el => el.props.innerHTML);
  expect(page.root.label).toContain(user.text);
  expect(replies()).toHaveLength(2);
  expect(replies()[0]).toContain(strong);
  expect(replies()[1]).toContain('正在看**公开标');
  run = {...run, text: '正在看**公开标题和简介**。'};
  await vi.advanceTimersByTimeAsync(400); await settle();
  const live = replies()[1];
  expect(live).toContain(strong);
  messages = [...messages, {...assistant, id: 'c', text: run.text}];
  run = {...run, status: 'completed'};
  await vi.advanceTimersByTimeAsync(400); await settle();
  expect(replies()).toEqual([replies()[0], live]);
  expect(page.root.label).not.toContain('正在回复');
});

it('renders lists, quotes, tables and code and preserves literal markdown escapes', () => {
  const html = renderPartnerMarkdown('# 标题\n\n- 一级\n  - 二级\n\n> *引述*\n\n| 项 | 值 |\n| --- | --- |\n| A | 2 |\n\n`**字面值**` 与 \\*\\*星号\\*\\*');
  expect(html).toContain('<h1>标题</h1>');
  expect(html.match(/<ul>/g)).toHaveLength(2);
  expect(html).toContain('<blockquote>');
  expect(html).toContain('<em>引述</em>');
  expect(html).toContain('<table>');
  expect(html).toContain('<code>**字面值**</code> 与 **星号**');
});

it('highlights streamed fences without a closing marker and escapes source even for unknown languages', () => {
  const partial = '~~~js\nconst tag = "<script>";\n';
  const html = renderPartnerMarkdown(partial);
  expect(html).toContain('hljs-keyword');
  expect(html).toContain('&lt;script&gt;');
  expect(renderPartnerMarkdown(partial + '~~~')).toBe(html);
  const plain = renderPartnerMarkdown('~~~unknown\n<img src=x onerror=run()>\n~~~');
  expect(plain).not.toContain('<img');
  expect(plain).toContain('&lt;img');
});

it('shows HTTP(S) targets only on request without navigations or remote image loads', () => {
  const html = renderPartnerMarkdown('<img src=x onerror=run()> ![像素](https://track.invalid/x)\n\n[**站点**](https://example.org/?x=1&y=2)\n\n[JS](jav&#x61;script:alert(1)) [相对](../admin) [登录](https://user:secret@example.org/)');
  expect(html).toContain('&lt;img');
  expect(html).toContain('[图片：像素]');
  expect(html).not.toMatch(/<(?:img|iframe|script|a)\b|\bhref=/i);
  expect(html).toContain('data-address="https://example.org/?x=1&amp;y=2"');
  expect(html).toContain('<strong>站点</strong></button>');
  expect(html).not.toMatch(/data-address="(?:javascript:|\.\.|https:\/\/user:)/i);
});

it('reveals a readonly URL for manual copying when the label inside a link button is clicked', async () => {
  const text = '[**来源**](https://example.org/watch)';
  const message: Message = {id: 'one', turnId: 't', role: 'assistant', status: 'completed', text, memoryIds: []};
  const page = await mountPage(undefined, {chat: () => ({messages: [message], run: null})});
  const body = page.find(el => el.props.class === 'vp-markdown');
  const address = body.props.innerHTML.match(/data-address="([^"]+)"/)?.[1];
  expect(address).toBe('https://example.org/watch');
  class LinkLabel { closest(selector: string) { return selector === 'button.vp-md-link' ? {dataset: {address}} : null; } }
  vi.stubGlobal('Element', LinkLabel);
  expect(page.root.label).not.toContain('按 Ctrl+C');
  await body.props.onClick({target: new LinkLabel()});
  expect(page.root.label).toContain('链接地址 · 按 Ctrl+C 复制');
  const field = page.find(el => el.tag === 'input' && el.value === address);
  expect(field.props).toHaveProperty('readonly');
});
