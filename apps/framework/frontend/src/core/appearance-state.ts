import type { SurfaceEffects, UIStyle } from '../../../module-sdk/src/index';
export function surfaceEffects(style: UIStyle): SurfaceEffects {
  const material = style === 'glass' ? 'glass' : 'solid';
  return {
    material,
    transparency: 78,
    motion: 'off',
    transparentBackground: material === 'glass',
  };
}
