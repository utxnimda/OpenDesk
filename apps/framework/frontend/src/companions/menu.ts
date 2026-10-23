import type { PetActionMode, PetPreferenceChange, PetSettings } from '../core/personalization';
import { companionExtraActions, type CompanionModel } from './contracts';

export type CompanionMenuChoice = 'toggle-animation' | 'toggle-top' | 'hide' | 'open-app'
  | `mode:${PetSettings['mode']}` | `action:${PetActionMode}` | `size:${number}`;
export type CompanionMenuEntry =
  | { kind: 'separator' }
  | { kind: 'group'; label: string; items: CompanionMenuEntry[] }
  | { kind: 'item'; id: CompanionMenuChoice; label: string; checked?: boolean; enabled?: boolean };
export const PET_SIZES = [96, 128, 160, 200, 256] as const;
const actionNames: Record<PetActionMode, string> = { idle: '默认循环', walk: '走动', wave: '挥手', sleep: '休息', thinking: '托腮思考' };

export function companionMenu(model: CompanionModel, pet: PetSettings, canAnimate: boolean): CompanionMenuEntry[] {
  const actions: PetActionMode[] = ['idle', ...companionExtraActions(model)];
  const selected = actions.includes(pet.actionMode) ? pet.actionMode : 'idle';
  return [
    { kind: 'item', id: 'toggle-animation', label: pet.animated ? '暂停动画' : '继续动画', enabled: canAnimate },
    { kind: 'group', label: '显示位置', items: [
      { kind: 'item', id: 'mode:desktop', label: '桌面悬浮', checked: pet.mode === 'desktop' },
      { kind: 'item', id: 'mode:in-app', label: '应用内陪伴', checked: pet.mode === 'in-app' },
    ] },
    { kind: 'group', label: '动作模式', items: actions.map((action) => ({ kind: 'item', id: `action:${action}` as const, label: actionNames[action], checked: selected === action })) },
    { kind: 'group', label: `大小 · ${pet.size} px`, items: PET_SIZES.map((size) => ({ kind: 'item', id: `size:${size}` as const, label: `${size} px`, checked: pet.size === size })) },
    { kind: 'item', id: 'toggle-top', label: '始终置顶', checked: pet.alwaysOnTop, enabled: pet.mode === 'desktop' },
    { kind: 'separator' },
    { kind: 'item', id: 'hide', label: '暂时收起' },
    { kind: 'item', id: 'open-app', label: '打开 OpenDesk' },
  ];
}

/** Resolve only choices offered by this model; native validation remains authoritative. */
export function companionMenuChange(choice: CompanionMenuChoice, pet: PetSettings, model: CompanionModel): PetPreferenceChange | undefined {
  if (choice === 'toggle-animation') return { type: 'animated', value: !pet.animated };
  if (choice === 'toggle-top' && pet.mode === 'desktop') return { type: 'always-on-top', value: !pet.alwaysOnTop };
  if (choice === 'mode:desktop' || choice === 'mode:in-app') return { type: 'mode', value: choice === 'mode:desktop' ? 'desktop' : 'in-app' };
  if (choice.startsWith('size:')) {
    const size = Number(choice.slice(5));
    if (PET_SIZES.some((value) => value === size)) return { type: 'size', value: size };
  }
  if (choice.startsWith('action:')) {
    const action = choice.slice(7) as PetActionMode;
    if (action === 'idle' || companionExtraActions(model).includes(action)) return { type: 'action-mode', value: action };
  }
}

export function applyPetPreference(pet: PetSettings, change: PetPreferenceChange): PetSettings {
  switch (change.type) {
    case 'mode': return { ...pet, mode: change.value };
    case 'size': return { ...pet, size: change.value };
    case 'always-on-top': return { ...pet, alwaysOnTop: change.value };
    case 'animated': return { ...pet, animated: change.value };
    case 'action-mode': return { ...pet, actionMode: change.value };
  }
}
