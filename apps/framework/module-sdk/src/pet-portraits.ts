/** Read-only portraits. Asset URLs, settings and full-size media stay in the host. */
export interface PetPortraitSource { id: string; label: string }
export interface PetPortraitPet { id: string; label: string; sources: PetPortraitSource[] }
export interface PetPortraitCatalog { activeId: string | null; pets: PetPortraitPet[] }
export interface PetPortraitFrame {
  width: number;
  height: number;
  rgba: Uint8ClampedArray;
  frameCount: number;
  index: number;
}
export interface PetPortraitsClient {
  list(): Promise<PetPortraitCatalog>;
  frame(petId: string, sourceId: string, index: number): Promise<PetPortraitFrame>;
  onChange(listener: () => void): () => void;
}
export const MAX_PORTRAIT_SIDE = 512;
export const MAX_PORTRAIT_FRAMES = 2048;
export type PetPortraitOperation =
  | { action: 'list'; params: Record<string, never> }
  | { action: 'frame'; params: { petId: string; sourceId: string; index: number } };

function identifier(value: unknown): value is string {
  return typeof value === 'string' && /^[a-z][a-z0-9-]{0,63}$/.test(value);
}

export function parsePetPortraitOperation(action: unknown, value: unknown): PetPortraitOperation {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new Error('桌宠头像请求参数无效。');
  const params = value as Record<string, unknown>;
  const keys = Object.keys(params);
  if (action === 'list' && !keys.length) return { action, params: {} };
  if (action === 'frame' && keys.length === 3 && identifier(params.petId) && identifier(params.sourceId)
    && typeof params.index === 'number' && Number.isInteger(params.index) && params.index >= 0 && params.index < MAX_PORTRAIT_FRAMES) {
    return { action, params: { petId: params.petId, sourceId: params.sourceId, index: params.index } };
  }
  throw new Error('桌宠头像只接受已登记的角色、动作标识与非负整数帧号。');
}
