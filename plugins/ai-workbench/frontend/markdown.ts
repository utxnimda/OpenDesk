import MarkdownIt, { type Token } from 'markdown-it';
type ElementTag = 'p' | 'h1' | 'h2' | 'h3' | 'h4' | 'h5' | 'h6' | 'ul' | 'ol' | 'li' | 'blockquote' | 'strong' | 'em' | 's' | 'code' | 'table' | 'thead' | 'tbody' | 'tr' | 'th' | 'td' | 'hr' | 'br' | 'a' | 'span';
export type MarkdownNode =
  | { kind: 'text'; text: string }
  | { kind: 'code'; text: string; language: string }
  | { kind: 'element'; tag: ElementTag; attributes: Record<string, string | number>; children: MarkdownNode[] };

const parser = new MarkdownIt({ html: false, linkify: true, typographer: false, breaks: true, maxNesting: 20 });
const elementTags = new Set<string>(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'blockquote', 'strong', 'em', 's', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'hr', 'br', 'a']);
const MAX_FORMATTED_LENGTH = 300_000;
const MAX_TOKENS = 20_000;

export function safeMarkdownLink(value: string | null): string | undefined {
  if (!value || value.length > 4096 || /[\u0000-\u0020\u007f]/.test(value)) return undefined;
  try {
    const url = new URL(value);
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) return undefined;
    return url.href;
  } catch { return undefined; }
}

function text(value: string): MarkdownNode { return { kind: 'text', text: value }; }
function plain(value: string, notice: string): MarkdownNode[] {
  return [{ kind: 'element', tag: 'p', attributes: { class: 'markdown-format-note' }, children: [text(notice)] },
    { kind: 'element', tag: 'p', attributes: { class: 'markdown-plain-fallback' }, children: [text(value)] }];
}

function element(token: Token): Extract<MarkdownNode, { kind: 'element' }> {
  const tag = elementTags.has(token.tag) ? token.tag as ElementTag : 'span';
  const result: Extract<MarkdownNode, { kind: 'element' }> = { kind: 'element', tag, attributes: {}, children: [] };
  if (tag === 'a') {
    const rawHref = token.attrGet('href');
    const href = safeMarkdownLink(typeof rawHref === 'string' ? rawHref : null);
    if (href) result.attributes = { href, target: '_blank', rel: 'noopener noreferrer', referrerpolicy: 'no-referrer' };
    else { result.tag = 'span'; result.attributes = { class: 'markdown-unavailable-link', title: '此链接不能在工作台中打开' }; }
  }
  if (tag === 'ol') {
    const start = Number(token.attrGet('start'));
    if (Number.isSafeInteger(start) && start > 0 && start <= 1_000_000) result.attributes.start = start;
  }
  if (tag === 'th' || tag === 'td') {
    const align = token.attrGet('style');
    if (typeof align === 'string' && /^text-align:(left|center|right)$/.test(align)) result.attributes.class = `markdown-align-${align.slice(11)}`;
  }
  return result;
}

/** Convert parser tokens into a closed set of Vue nodes, never model-provided HTML. */
function nodesFromTokens(tokens: Token[], budget: { remaining: number }): MarkdownNode[] {
  const result: MarkdownNode[] = [];
  const stack: MarkdownNode[][] = [result];
  for (const token of tokens) {
    if (--budget.remaining < 0) throw new Error('MARKDOWN_TOKEN_LIMIT');
    if (token.hidden) continue;
    const current = stack[stack.length - 1]!;
    if (token.type === 'inline') {
      current.push(...nodesFromTokens(token.children ?? [], budget));
    } else if (token.type === 'fence' || token.type === 'code_block') {
      const name = token.info.trim().split(/\s+/)[0] ?? '';
      current.push({ kind: 'code', text: token.content, language: /^[\w+#.-]{1,40}$/.test(name) ? name.toLowerCase() : '' });
    } else if (token.type === 'code_inline') {
      current.push({ kind: 'element', tag: 'code', attributes: {}, children: [text(token.content)] });
    } else if (token.type === 'image') {
      current.push({ kind: 'element', tag: 'span', attributes: { class: 'markdown-image-placeholder', title: '图片不会自动加载' }, children: [text(`[图片：${token.content || '未提供描述'}]`)] });
    } else if (token.type === 'softbreak' || token.type === 'hardbreak') {
      current.push({ kind: 'element', tag: 'br', attributes: {}, children: [] });
    } else if (token.nesting === 1) {
      const node = element(token);
      current.push(node);
      stack.push(node.children);
    } else if (token.nesting === -1) {
      if (stack.length > 1) stack.pop();
    } else if (token.tag && elementTags.has(token.tag)) {
      current.push(element(token));
    } else if (token.content) {
      current.push(text(token.content));
    }
  }
  return result;
}

export function markdownNodes(value: string): MarkdownNode[] {
  if (value.length > MAX_FORMATTED_LENGTH) return plain(value, '内容较长，使用纯文本显示。');
  try { return nodesFromTokens(parser.parse(value, {}), { remaining: MAX_TOKENS }); }
  catch { return plain(value, '格式解析未完成，已保留原文显示。'); }
}
