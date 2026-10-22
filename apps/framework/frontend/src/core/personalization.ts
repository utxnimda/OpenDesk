import frameworkDefaults from '../../../../../config/framework/defaults.json';

export interface PetSettings {
  enabled: boolean;
  mode: 'desktop' | 'in-app';
  modelId: string | null;
  size: number;
  alwaysOnTop: boolean;
  animated: boolean;
}

export interface Personalization {
  pet: PetSettings;
}

export const DEFAULT_PERSONALIZATION: Readonly<Personalization> = Object.freeze({
  pet: Object.freeze({ ...frameworkDefaults.personalization.pet }),
} as Personalization);

export function clonePersonalization(value: Readonly<Personalization> = DEFAULT_PERSONALIZATION): Personalization {
  return { pet: { ...value.pet } };
}
