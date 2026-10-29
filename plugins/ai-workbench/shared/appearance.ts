/** The wrapper relays the host's appearance; the inner Web App never owns it. */
import {
  SUPPORTED_APPEARANCES, SUPPORTED_UI_STYLES, resolveMessageSurfaceEffects,
  type Appearance, type SurfaceEffects, type UIStyle,
} from '../../../apps/framework/module-sdk/src/index';

export interface WorkbenchAppearance {
  theme: Appearance;
  uiStyle: UIStyle;
  surfaceEffects: SurfaceEffects;
}

export const APPEARANCE_ATTRIBUTES = [
  'data-theme', 'data-ui-style', 'data-material', 'data-motion', 'data-shell-background', 'style',
];

const theme = (value: unknown): Appearance => SUPPORTED_APPEARANCES.find(item => item === value) ?? 'light';
const uiStyle = (value: unknown): UIStyle => SUPPORTED_UI_STYLES.find(item => item === value) ?? 'simple';

export function readHostAppearance(root: HTMLElement): WorkbenchAppearance {
  return {
    theme: theme(root.dataset.theme),
    uiStyle: uiStyle(root.dataset.uiStyle),
    surfaceEffects: resolveMessageSurfaceEffects({
      material: root.dataset.material,
      motion: root.dataset.motion,
      transparency: Number.parseFloat(root.style.getPropertyValue('--glass-opacity')),
      transparentBackground: root.dataset.shellBackground === 'true',
    }),
  };
}

export function writeAppearanceFragment(fragment: URLSearchParams, value: WorkbenchAppearance): void {
  fragment.set('appearance', value.theme);
  fragment.set('uiStyle', value.uiStyle);
  fragment.set('material', value.surfaceEffects.material);
  fragment.set('transparency', String(value.surfaceEffects.transparency));
  fragment.set('motion', value.surfaceEffects.motion);
  fragment.set('transparentBackground', String(value.surfaceEffects.transparentBackground));
}

export function readAppearanceFragment(fragment: URLSearchParams): WorkbenchAppearance {
  return {
    theme: theme(fragment.get('appearance')),
    uiStyle: uiStyle(fragment.get('uiStyle')),
    surfaceEffects: resolveMessageSurfaceEffects({
      material: fragment.get('material'), motion: fragment.get('motion'),
      transparency: fragment.has('transparency') ? Number(fragment.get('transparency')) : undefined,
      transparentBackground: fragment.get('transparentBackground') === 'true',
    }),
  };
}

/** Reject malformed live messages instead of replacing a valid current style. */
export function parseAppearanceMessage(value: unknown): WorkbenchAppearance | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const data = value as Record<string, unknown>;
  if (data.type !== 'appearance' || data.appearanceVersion !== 1
      || !SUPPORTED_APPEARANCES.some(item => item === data.theme)
      || !SUPPORTED_UI_STYLES.some(item => item === data.uiStyle)) return null;
  const e = data.surfaceEffects;
  if (!e || typeof e !== 'object' || Array.isArray(e)) return null;
  const effects = e as Record<string, unknown>;
  if ((effects.material !== 'solid' && effects.material !== 'glass')
      || (effects.motion !== 'off' && effects.motion !== 'subtle' && effects.motion !== 'full')
      || typeof effects.transparency !== 'number' || !Number.isFinite(effects.transparency)
      || effects.transparency < 40 || effects.transparency > 100
      || typeof effects.transparentBackground !== 'boolean') return null;
  return { theme: data.theme as Appearance, uiStyle: data.uiStyle as UIStyle,
    surfaceEffects: resolveMessageSurfaceEffects(effects) };
}

export function applyAppearance(root: HTMLElement, value: WorkbenchAppearance): void {
  root.dataset.theme = value.theme;
  root.dataset.uiStyle = value.uiStyle;
  root.dataset.material = value.surfaceEffects.material;
  root.dataset.motion = value.surfaceEffects.motion;
  root.dataset.shellBackground = String(value.surfaceEffects.transparentBackground);
  root.style.setProperty('--glass-opacity', `${value.surfaceEffects.transparency}%`);
}

export function observeHostAppearance(root: HTMLElement, publish: () => void): MutationObserver {
  const observer = new MutationObserver(publish);
  observer.observe(root, { attributes: true, attributeFilter: APPEARANCE_ATTRIBUTES });
  return observer;
}
