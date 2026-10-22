import type { PetSettings } from '../core/personalization';

/** The composition root supplies assets; the framework has no character imports. */
export interface CompanionModel {
  readonly id: string;
  readonly label: string;
  readonly description: string;
  readonly assets: Readonly<{
    idle: string;
    still: string;
    walk?: string;
    wave?: string;
    sleep?: string;
  }>;
}

export type CompanionAction = 'idle' | 'walk' | 'wave' | 'sleep';
export type CompanionExtraAction = Exclude<CompanionAction, 'idle'>;
const EXTRA_ACTIONS: readonly CompanionExtraAction[] = ['walk', 'wave', 'sleep'];

/** An extra action needs its own asset, rather than an alias of another pose. */
export function companionExtraActions(model: CompanionModel): CompanionExtraAction[] {
  const sources = Object.values(model.assets);
  return EXTRA_ACTIONS.filter((action) => {
    const source = model.assets[action];
    return typeof source === 'string' && source.trim().length > 0 && sources.filter((value) => value === source).length === 1;
  });
}

/** Pause always wins over an action; an unavailable action resumes the real idle. */
export function companionFrame(model: CompanionModel, requested: CompanionAction, animate: boolean): { action: CompanionAction | 'still'; source: string } {
  if (!animate) return { action: 'still', source: model.assets.still };
  if (requested !== 'idle' && companionExtraActions(model).includes(requested)) return { action: requested, source: model.assets[requested]! };
  return { action: 'idle', source: model.assets.idle };
}

export interface CompanionCatalog {
  readonly defaultId: string | null;
  readonly models: readonly CompanionModel[];
}

export interface CompanionPoint { x: number; y: number }
export interface CompanionBox { width: number; height: number }

export function findCompanion(catalog: CompanionCatalog | undefined, id: string | null): CompanionModel | undefined {
  return catalog?.models.find((model) => model.id === (id ?? catalog.defaultId));
}

export function canDisplayCompanion(catalog: CompanionCatalog | undefined, settings: PetSettings, mode: PetSettings['mode']): boolean {
  return settings.enabled && settings.mode === mode && Boolean(findCompanion(catalog, settings.modelId));
}

export function companionSize(size: number): number {
  return Number.isFinite(size) ? Math.min(256, Math.max(96, Math.round(size))) : 160;
}

export function companionBox(size: number): CompanionBox {
  const pixels = companionSize(size);
  return { width: Math.max(240, pixels + 80), height: pixels + 100 };
}

/** A small viewport may leave no margin; never put the control outside it. */
export function clampCompanionPosition(point: CompanionPoint, viewport: CompanionBox, box: CompanionBox): CompanionPoint {
  const maxX = Math.max(0, viewport.width - box.width);
  const maxY = Math.max(0, viewport.height - box.height);
  return {
    x: Math.min(maxX, Math.max(0, Number.isFinite(point.x) ? point.x : maxX)),
    y: Math.min(maxY, Math.max(0, Number.isFinite(point.y) ? point.y : maxY)),
  };
}
