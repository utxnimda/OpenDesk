import hljs from 'highlight.js/lib/core';
import bash from 'highlight.js/lib/languages/bash';
import cpp from 'highlight.js/lib/languages/cpp';
import csharp from 'highlight.js/lib/languages/csharp';
import css from 'highlight.js/lib/languages/css';
import diff from 'highlight.js/lib/languages/diff';
import go from 'highlight.js/lib/languages/go';
import ini from 'highlight.js/lib/languages/ini';
import javascript from 'highlight.js/lib/languages/javascript';
import json from 'highlight.js/lib/languages/json';
import powershell from 'highlight.js/lib/languages/powershell';
import python from 'highlight.js/lib/languages/python';
import rust from 'highlight.js/lib/languages/rust';
import sql from 'highlight.js/lib/languages/sql';
import typescript from 'highlight.js/lib/languages/typescript';
import xml from 'highlight.js/lib/languages/xml';
import yaml from 'highlight.js/lib/languages/yaml';

for (const [name, grammar] of Object.entries({ bash, cpp, csharp, css, diff, go, ini, javascript, json, powershell, python, rust, sql, typescript, xml, yaml })) hljs.registerLanguage(name, grammar);
hljs.registerAliases(['vue', 'svg'], { languageName: 'xml' });
hljs.registerAliases(['ps1', 'ps'], { languageName: 'powershell' });
hljs.registerAliases(['shell', 'zsh'], { languageName: 'bash' });

export interface HighlightedCode { html?: string; note?: string }
export function highlightCode(code: string, language: string): HighlightedCode {
  if (!language || !hljs.getLanguage(language)) return {};
  if (code.length > 20_000) return { note: '代码较长，显示完整原文。' };
  try {
    const result = hljs.highlight(code, { language, ignoreIllegals: true });
    return result.errorRaised ? { note: '高亮未完成，已显示原始代码。' } : { html: result.value };
  }
  catch { return { note: '高亮未完成，已显示原始代码。' }; }
}
