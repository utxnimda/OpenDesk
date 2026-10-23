import { describe, expect, it, vi } from 'vitest';
import { clonePersonalization } from '../../apps/framework/frontend/src/core/personalization';
import { createHostAdapter } from '../../apps/framework/frontend/src/core/host';
import { companionMenu, companionMenuChange, type CompanionMenuChoice, type CompanionMenuEntry } from '../../apps/framework/frontend/src/companions/menu';
import type { CompanionModel } from '../../apps/framework/frontend/src/companions/contracts';

const base: CompanionModel = { id: 'sample', label: 'Sample', description: '', assets: { idle: 'idle.png', still: 'still.png' } };
const thinking: CompanionModel = { ...base, assets: { ...base.assets, thinking: 'thinking.webm' }, actionDurationsMs: { thinking: 6042 }, actionMediaTypes: { thinking: 'video' } };
const flatten = (items: CompanionMenuEntry[]): Extract<CompanionMenuEntry, { kind: 'item' }>[] => items.flatMap((item) => item.kind === 'item' ? [item] : item.kind === 'group' ? flatten(item.items) : []);

describe('companion menu choices', () => {
  it('offers only existing actions and marks the effective mode when switching to a simpler model', () => {
    const pet = { ...clonePersonalization().pet, actionMode: 'thinking' as const };
    const rich = flatten(companionMenu(thinking, pet, true));
    expect(rich.find((item) => item.id === 'action:thinking')?.checked).toBe(true);
    const simple = flatten(companionMenu(base, pet, true));
    expect(simple.some((item) => item.id === 'action:thinking')).toBe(false);
    expect(simple.find((item) => item.id === 'action:idle')?.checked).toBe(true);
    expect(companionMenuChange('action:thinking', pet, base)).toBeUndefined();
  });
  it('can resume a saved pause, but respects system motion and desktop-only placement', () => {
    const pet = { ...clonePersonalization().pet, animated: false, mode: 'in-app' as const };
    const resumed = flatten(companionMenu(thinking, pet, true));
    expect(resumed.find((item) => item.id === 'toggle-animation')).toMatchObject({ enabled: true, label: '继续动画' });
    expect(resumed.find((item) => item.id === 'toggle-top')?.enabled).toBe(false);
    expect(flatten(companionMenu(thinking, pet, false)).find((item) => item.id === 'toggle-animation')?.enabled).toBe(false);
    expect(companionMenuChange('toggle-top', pet, thinking)).toBeUndefined();
  });
  it('does not turn malformed or unavailable selections into writes', () => {
    const pet = clonePersonalization().pet;
    for (const choice of ['size:0', 'size:257', 'size:NaN', 'action:unknown', 'mode:other', 'model:other']) {
      expect(companionMenuChange(choice as CompanionMenuChoice, pet, thinking)).toBeUndefined();
    }
  });
  it('sends appearance edits without a stale pet snapshot and propagates pet write failures', async () => {
    const bridge = vi.fn().mockResolvedValue({});
    const host = createHostAdapter(bridge);
    await host.saveAppearance({ schemaVersion: 1, theme: 'dark', uiStyle: 'codex', closeToTray: true, personalization: clonePersonalization() });
    expect(bridge.mock.calls[0][1]).toEqual({ theme: 'dark', uiStyle: 'codex', closeToTray: true });
    bridge.mockRejectedValueOnce('Settings locked');
    await expect(host.updateCompanion({ type: 'size', value: 200 })).rejects.toBe('Settings locked');
  });
});
