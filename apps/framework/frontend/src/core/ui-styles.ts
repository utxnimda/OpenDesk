import type { Theme, UIStyle } from './host';

export interface UIStylePreset {
  id: UIStyle;
  label: string;
  caption: string;
  description: string;
  theme: Theme;
}

export const UI_STYLE_PRESETS: readonly UIStylePreset[] = [
  { id: 'simple', label: '简洁', caption: 'CLASSIC', description: '熟悉的侧栏与清晰分区', theme: 'system' },
  { id: 'codex', label: 'Codex', caption: 'FOCUS', description: '轻量导航，留白与专注内容', theme: 'light' },
  { id: 'cursor', label: 'Cursor', caption: 'STUDIO', description: '活动栏、工作区标签与紧凑布局', theme: 'graphite' },
  { id: 'web', label: '现代 Web', caption: 'WORKSPACE', description: '悬浮导航、卡片与宽阔画布', theme: 'ocean' },
  { id: 'anime', label: '二次元', caption: 'DAYDREAM', description: '柔彩星光、顶部导航与圆润面板', theme: 'sakura' },
  { id: 'glass', label: '深海透明', caption: 'ABYSS', description: '青色细边、半透明分组与紧凑导航', theme: 'dark' },
];

export function uiStylePreset(style: UIStyle): UIStylePreset {
  return UI_STYLE_PRESETS.find((preset) => preset.id === style) ?? UI_STYLE_PRESETS[0];
}
