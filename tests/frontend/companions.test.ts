import { describe, expect, it } from 'vitest';
import { createSSRApp } from 'vue';
import { renderToString } from '@vue/server-renderer';
import { canDisplayCompanion, clampCompanionPosition, companionBox, companionExtraActions, companionFrame, companionSize, findCompanion, type CompanionCatalog, type CompanionModel } from '../../apps/framework/frontend/src/companions/contracts';
import CompanionOverlay from '../../apps/framework/frontend/src/companions/CompanionOverlay.vue';
import CompanionPet from '../../apps/framework/frontend/src/companions/CompanionPet.vue';
import CompanionSettings from '../../apps/framework/frontend/src/components/CompanionSettings.vue';
import { createHostAdapter } from '../../apps/framework/frontend/src/core/host';
import { clonePersonalization } from '../../apps/framework/frontend/src/core/personalization';

const catalog: CompanionCatalog = {
  defaultId: 'demo',
  models: [{ id: 'demo', label: 'Demo', description: 'Fixture only', assets: { idle: '/idle.png', walk: '/walk.png', still: '/still.png', wave: '/wave.png', sleep: '/sleep.png' } }],
};
const emptyCatalog: CompanionCatalog = { defaultId: null, models: [] };
const loopModel: CompanionModel = {
  id: 'video-fixture', label: 'Video fixture', description: 'A single real loop, fixture only',
  assets: { idle: '/loop.apng', still: '/still.png' },
};

describe('companion selection and visible bounds', () => {
  it('supports an explicitly empty catalog until authorized resources are added', () => {
    expect(findCompanion(emptyCatalog, null)).toBeUndefined();
    expect(findCompanion(undefined, null)).toBeUndefined();
  });

  it.each(['desktop', 'in-app'] as const)('does not revive a saved enabled %s companion without its resources', (mode) => {
    const saved = { ...clonePersonalization().pet, enabled: true, mode, modelId: 'removed-model' };
    expect(canDisplayCompanion(emptyCatalog, saved, mode)).toBe(false);
    expect(canDisplayCompanion(catalog, saved, mode)).toBe(false);
    expect(canDisplayCompanion(emptyCatalog, { ...saved, modelId: null }, mode)).toBe(false);
    expect(canDisplayCompanion(catalog, { ...saved, modelId: 'demo' }, mode)).toBe(true);
    expect(canDisplayCompanion(catalog, { ...saved, enabled: false, modelId: 'demo' }, mode)).toBe(false);
  });

  it('renders a clear empty settings state without enable or show controls', async () => {
    const saved = { ...clonePersonalization().pet, enabled: true, modelId: 'removed-model' };
    const html = await renderToString(createSSRApp(CompanionSettings, {
      host: createHostAdapter(), catalog: emptyCatalog, modelValue: saved, savedMode: saved, disabled: false,
    }));
    expect(html).toContain('暂无可用宠物资源，等待添加已授权资源');
    expect(html).not.toContain('<input');
    expect(html).not.toContain('<button');
    expect(html).not.toContain('<img');
  });

  it('renders no in-app bubble or recall control for an obsolete enabled model', async () => {
    const saved = { ...clonePersonalization().pet, enabled: true, mode: 'in-app' as const, modelId: 'removed-model' };
    const html = await renderToString(createSSRApp(CompanionOverlay, { catalog: emptyCatalog, settings: saved }));
    expect(html).not.toContain('companion-overlay');
    expect(html).not.toContain('role="alert"');
    expect(html).not.toContain('<button');
  });

  it('uses the default only for an unselected model, not for a missing explicit model', () => {
    expect(findCompanion(catalog, null)).toBe(catalog.models[0]);
    expect(findCompanion(catalog, 'demo')).toBe(catalog.models[0]);
    expect(findCompanion(catalog, 'removed-model')).toBeUndefined();
    expect(findCompanion({ defaultId: 'demo', models: [] }, null)).toBeUndefined();
  });
  it('keeps all available sizes bounded and reserves room for the interaction controls', () => {
    expect(companionSize(Number.NaN)).toBe(160);
    expect(companionSize(1)).toBe(96);
    expect(companionSize(1000)).toBe(256);
    expect(companionBox(96)).toEqual({ width: 240, height: 196 });
    expect(companionBox(256)).toEqual({ width: 336, height: 356 });
  });
  it('clamps a dragged companion when the host shrinks without persisting a stale position', () => {
    const box = companionBox(256);
    expect(clampCompanionPosition({ x: 1100, y: 700 }, { width: 860, height: 620 }, box)).toEqual({ x: 524, y: 264 });
    expect(clampCompanionPosition({ x: -40, y: -20 }, { width: 860, height: 620 }, box)).toEqual({ x: 0, y: 0 });
  });
  it('does not create negative or non-finite coordinates in a very small viewport', () => {
    expect(clampCompanionPosition({ x: Number.NaN, y: Infinity }, { width: 200, height: 100 }, companionBox(256))).toEqual({ x: 0, y: 0 });
  });
});

describe('companion assets and action availability', () => {
  it('accepts a single loop with a static frame without inventing extra actions', () => {
    expect(companionExtraActions(loopModel)).toEqual([]);
    expect(companionFrame(loopModel, 'idle', true)).toEqual({ action: 'idle', source: '/loop.apng' });
    for (const action of ['walk', 'wave', 'sleep'] as const) {
      expect(companionFrame(loopModel, action, true)).toEqual({ action: 'idle', source: '/loop.apng' });
    }
  });

  it('keeps legacy five-asset models compatible while pause overrides every action', () => {
    const model = catalog.models[0];
    expect(companionExtraActions(model)).toEqual(['walk', 'wave', 'sleep']);
    for (const action of ['idle', 'walk', 'wave', 'sleep'] as const) {
      expect(companionFrame(model, action, true)).toEqual({ action, source: model.assets[action] });
      expect(companionFrame(model, action, false)).toEqual({ action: 'still', source: model.assets.still });
    }
  });

  it('does not expose blank resources or duplicate poses as separate actions', () => {
    const aliased: CompanionModel = { ...loopModel, assets: { ...loopModel.assets, walk: '/loop.apng', wave: '/still.png', sleep: ' ' } };
    expect(companionExtraActions(aliased)).toEqual([]);
    const duplicates: CompanionModel = { ...loopModel, assets: { ...loopModel.assets, walk: '/shared.apng', wave: '/shared.apng', sleep: '/rest.png' } };
    expect(companionExtraActions(duplicates)).toEqual(['sleep']);
    expect(companionFrame(duplicates, 'wave', true)).toEqual({ action: 'idle', source: '/loop.apng' });
  });

  it('renders only real controls and a defined image source for a single-loop pet', async () => {
    const html = await renderToString(createSSRApp(CompanionPet, { model: loopModel, size: 160, animated: true }));
    expect(html).toContain('src="/loop.apng"');
    expect(html).toContain('aria-label="暂停宠物动画"');
    expect(html).toContain('aria-label="暂时收起宠物"');
    for (const unavailable of ['宠物挥手', '宠物活动一下', '让宠物休息', '点击挥手']) expect(html).not.toContain(unavailable);
    expect(html).not.toContain('undefined');
  });

  it.each([{ animated: false }, { animated: true, active: false }, { animated: true, reducedMotion: true }])('renders the static image when animation is suppressed: %j', async (preferences) => {
    const html = await renderToString(createSSRApp(CompanionPet, { model: loopModel, size: 160, ...preferences }));
    expect(html).toContain('src="/still.png"');
    expect(html).toContain('data-action="still"');
    expect(html).not.toContain('src="/loop.apng"');
  });

  it('describes only the supplied actions in pet settings', async () => {
    const settings = { ...clonePersonalization().pet, enabled: true, modelId: loopModel.id };
    const html = await renderToString(createSSRApp(CompanionSettings, {
      host: createHostAdapter(), catalog: { defaultId: loopModel.id, models: [loopModel] },
      modelValue: settings, savedMode: settings, disabled: false,
    }));
    expect(html).toContain('循环动画；暂停时使用静态形象。');
    expect(html).not.toContain('走动');
    expect(html).not.toContain('挥手');
  });
});
