import MarkdownIt from 'markdown-it';
import hljs from 'highlight.js/lib/common';
import powershell from 'highlight.js/lib/languages/powershell';

hljs.registerLanguage('powershell', powershell);
hljs.registerAliases(['ps1', 'ps'], {languageName: 'powershell'});
hljs.registerAliases(['vue', 'svg'], {languageName: 'xml'});

const md = new MarkdownIt({
  html: false, breaks: true, linkify: true, maxNesting: 20,
  highlight(code, language) {
    if (!language || !hljs.getLanguage(language) || code.length > 20_000) return '';
    return hljs.highlight(code, {language, ignoreIllegals: true}).value;
  },
});
const escape = md.utils.escapeHtml;

md.validateLink = (value) => {
  if (value.length > 4096 || !/^https?:\/\//i.test(value) || /[\u0000-\u0020\u007f\\]/.test(value)) return false;
  try {
    const url = new URL(value);
    return !url.username && !url.password;
  } catch { return false; }
};
// API 1 frames cannot open a new tab. Show a selectable address on demand.
md.renderer.rules.link_open = (tokens, index) => {
  const address = escape(String(tokens[index].attrGet('href')));
  return `<button type="button" class="vp-md-link" data-address="${address}" title="查看链接地址 · ${address}">`;
};
md.renderer.rules.link_close = () => '</button>';
md.renderer.rules.image = (tokens, index) => `<span class="vp-md-image">[图片：${escape(tokens[index].content || '未提供描述')}]</span>`;
md.renderer.rules.table_open = () => '<div class="vp-md-table" tabindex="0" role="region" aria-label="表格"><table>';
md.renderer.rules.table_close = () => '</table></div>';

/** Only parser-generated markup enters the reply view. Model HTML is always escaped. */
export function renderPartnerMarkdown(source: string): string {
  return md.render(source);
}
