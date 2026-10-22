import { describe, expect, it } from 'vitest';
import { surfaceEffects } from '../../apps/framework/frontend/src/core/appearance-state';
import { clonePersonalization, DEFAULT_PERSONALIZATION } from '../../apps/framework/frontend/src/core/personalization';
import { UI_STYLE_PRESETS } from '../../apps/framework/frontend/src/core/ui-styles';

describe('personalization behavior', () => {
  it('keeps pet drafts separate from saved preferences and shared defaults', () => {
    const saved = clonePersonalization();
    const draft = clonePersonalization(saved);
    draft.pet.enabled = true;
    draft.pet.size = 240;
    draft.pet.animated = false;
    expect(saved.pet).toEqual(DEFAULT_PERSONALIZATION.pet);
    expect(saved.pet.enabled).toBe(false);
    expect(saved.pet.size).toBe(160);
    expect(saved.pet.animated).toBe(true);
  });

  it('drops obsolete background and effects fields from loaded drafts', () => {
    const legacy = {
      ...clonePersonalization(),
      background: { assetId: 'old-private-image', placement: 'window', opacity: 75, blur: 0, dim: 25 },
      effects: { material: 'glass', transparency: 40, motion: 'full', decoration: 'petals' },
    };
    legacy.pet.enabled = true;
    legacy.pet.mode = 'in-app';
    expect(clonePersonalization(legacy)).toEqual({ pet: legacy.pet });
    expect(Object.keys(DEFAULT_PERSONALIZATION)).toEqual(['pet']);
  });

  it.each(UI_STYLE_PRESETS)('derives fixed, still module surfaces from the $id style', ({ id }) => {
    const glass = id === 'glass';
    expect(surfaceEffects(id)).toEqual({
      material: glass ? 'glass' : 'solid',
      transparency: 78,
      motion: 'off',
      transparentBackground: glass,
    });
  });
});
