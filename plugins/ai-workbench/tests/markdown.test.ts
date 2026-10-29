import { afterEach, describe, expect, it, vi } from 'vitest';
import { createSSRApp, h } from 'vue';
import { renderToString } from 'vue/server-renderer';
import MessageBody from '../frontend/MessageBody.vue';
import { markdownNodes, safeMarkdownLink, type MarkdownNode } from '../frontend/markdown';
import { highlightCode } from '../frontend/codeHighlight';
import { copyCode } from '../frontend/copyCode';

const render = (text: string) => renderToString(createSSRApp({ render: () => h(MessageBody, { text }) }));
function codeNodes(nodes: MarkdownNode[]): Extract<MarkdownNode, { kind: 'code' }>[] {
  return nodes.flatMap(node => node.kind === 'code' ? [node] : node.kind === 'element' ? codeNodes(node.children) : []);
}

describe('Markdown conversation content', () => {
  it('renders headings, emphasis, nested lists, quotes and aligned tables as semantic elements', async () => {
    const output = await render('# 标题\n\n**重点**，*斜体* 和 `inline()`。\n\n- 第一项\n  - 子项\n\n3. 有序项\n\n> 引用\n\n| 名称 | 数量 |\n| :--- | ---: |\n| A | 2 |');
    expect(output).toContain('<h1>');
    expect(output).toContain('<strong>');
    expect(output).toContain('<em>');
    expect(output).toContain('<code>');
    expect(output.match(/<ul>/g)).toHaveLength(2);
    expect(output).toContain('<ol start="3">');
    expect(output).toContain('<blockquote>');
    expect(output).toContain('class="markdown-table-scroll"');
    expect(output).toContain('<thead>');
    expect(output).toContain('class="markdown-align-right"');
  });

  it('never turns model HTML, image references or dangerous protocols into active content', async () => {
    const input = '<script>alert(1)</script>\n<img src="https://tracking.invalid/pixel" onerror="alert(1)">\n\n![图](https://tracking.invalid/pixel)\n\n[bad](javascript:alert(1)) [encoded](jav&#x61;script:alert(1)) [data](data:text/html,evil) [relative](../../private)';
    const output = await render(input);
    expect(output).toContain('&lt;script&gt;');
    expect(output).toContain('&lt;img');
    expect(output).toContain('[图片：图]');
    expect(output).not.toMatch(/<(?:script|img|iframe|object|embed)(?:\s|>)/);
    expect(output).not.toContain('href="javascript:');
    expect(output).not.toContain('href="data:');
    expect(output).not.toContain('href="../../private');
    expect(output).not.toContain('src="https://tracking.invalid');
  });

  it('only opens absolute HTTP(S) links without opener or referrer', async () => {
    const output = await render('[文档](https://example.org/docs?q=1)');
    expect(output).toContain('href="https://example.org/docs?q=1"');
    expect(output).toContain('target="_blank"');
    expect(output).toContain('rel="noopener noreferrer"');
    expect(output).toContain('referrerpolicy="no-referrer"');
    for (const invalid of ['javascript:alert(1)', 'file:///C:/secret', 'data:text/html,evil', '//example.org', 'https://user:secret@example.org', 'https://example.org\n/secret']) expect(safeMarkdownLink(invalid)).toBeUndefined();
  });

  it('keeps incomplete streaming fences as code and preserves original text for copying', async () => {
    const source = 'const message = "<tag>";\nconsole.log(message);\n';
    const partial = markdownNodes('说明\n\n```js\n' + source);
    const complete = markdownNodes('说明\n\n```js\n' + source + '```');
    expect(codeNodes(partial)).toEqual([{ kind: 'code', text: source, language: 'js' }]);
    expect(codeNodes(complete)).toEqual(codeNodes(partial));
    const output = await render('```js\n' + source);
    expect(output).toContain('hljs-keyword');
    expect(output).toContain('&lt;tag&gt;');
    expect(output).not.toContain('<tag>');
    expect(output).toContain('aria-label="复制代码"');
  });

  it('supports unknown languages and long blocks without losing or executing text', async () => {
    const unknown = await render('```unknown-lang\n<img src=x onerror=evil()>\n```');
    expect(unknown).toContain('unknown-lang');
    expect(unknown).toContain('&lt;img');
    expect(unknown).not.toContain('<img');
    expect(highlightCode('const x = 1;\n'.repeat(1600), 'javascript')).toEqual({ note: '代码较长，显示完整原文。' });
    const text = 'x'.repeat(300_001);
    const large = markdownNodes(text);
    expect(large[1]).toMatchObject({ kind: 'element', children: [{ kind: 'text', text }] });
  });
});

describe('explicit code copying', () => {
  afterEach(() => vi.unstubAllGlobals());
  it('copies the complete raw source through Clipboard API only when requested', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    await copyCode('const x = "<tag>";\n');
    expect(writeText).toHaveBeenCalledExactlyOnceWith('const x = "<tag>";\n');
  });

  it('cleans up its temporary field and reports failure if both copy mechanisms refuse', async () => {
    const remove = vi.fn(), select = vi.fn(), focus = vi.fn();
    class Element { focus = focus; }
    const field = { value: '', readOnly: false, tabIndex: 0, style: { cssText: '' }, setAttribute: vi.fn(), select, remove };
    const appendChild = vi.fn();
    const execCommand = vi.fn().mockReturnValue(false);
    vi.stubGlobal('HTMLElement', Element);
    vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('Denied')) } });
    vi.stubGlobal('document', { activeElement: new Element(), getSelection: () => null, createElement: () => field, body: { appendChild }, execCommand });
    await expect(copyCode('raw\ncode')).rejects.toThrow('Clipboard permission denied');
    expect(field.value).toBe('raw\ncode');
    expect(appendChild).toHaveBeenCalledWith(field);
    expect(select).toHaveBeenCalledOnce();
    expect(remove).toHaveBeenCalledOnce();
    expect(focus).toHaveBeenCalledWith({ preventScroll: true });
  });
});
