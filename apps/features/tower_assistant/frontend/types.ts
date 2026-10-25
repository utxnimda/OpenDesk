export interface Point { x: number; y: number }
export interface Bounds { minX: number; maxX: number; minY: number; maxY: number }
export interface SaveSource { id?: string; name: string; savedAt: string; sha256: string }
export interface SaveEntry { id: string; name: string; savedAt: string }
export interface SpriteSheet { dataUrl: string; width: number; height: number }
export interface SpriteFrame { sheetId: string; x: number; y: number; width: number; height: number }
export interface VisualCatalog {
  schemaVersion: 1;
  sheets: Record<string, SpriteSheet>;
  sprites: Record<string, SpriteFrame>;
  warnings?: string[];
}
export interface Equipment {
  slot?: string;
  name: string;
  type?: string;
  templateId?: number;
  iconKey?: string;
  level?: number;
  quality?: number;
  qualityName?: string;
}
export type EntityKind = 'platform' | 'pedestal' | 'building' | 'resource' | 'facility' | 'person';
export interface Entity extends Point {
  id: string;
  kind: EntityKind;
  name: string;
  width: number;
  height: number;
  movable: boolean;
  tier?: number;
  itemId?: string | null;
  iconKey?: string;
  job?: string;
  profession?: string;
  professionDescription?: string;
  appearance?: string;
  weapon?: Equipment;
  equipment?: Equipment[];
  parentId?: string;
  allowedBounds?: Bounds;
  lockedReason?: string;
  currentQi?: number;
  detailWarnings?: string[];
}
export interface SpiritItem {
  id: string;
  name: string;
  qi: number;
  location: 'warehouse' | 'placed';
  pedestalId?: string | null;
  iconKey?: string;
  attributes?: Record<string, number>;
  level?: number;
  quality?: number;
  qualityName?: string;
  plannedLocation?: 'warehouse' | 'placed';
  plannedPedestalId?: string | null;
  originalId?: number;
  /** Authoritative per-pedestal emission returned by the worker; absent in storage. */
  currentQi?: number;
  /** Worker-computed contributions to receiving platforms. An omitted platform receives zero. */
  contributions?: { platformId: string; qi: number }[];
}
export interface Snapshot {
  source: SaveSource;
  bounds: Bounds;
  entities: Entity[];
  items: SpiritItem[];
  warnings: string[];
}
export interface Inspection {
  schemaVersion: 1;
  gameDir: string;
  saves: SaveEntry[];
  snapshot: Snapshot;
}
export interface MigrationStep {
  id: string;
  kind: 'move' | 'equip';
  entityId: string;
  name: string;
  from?: Point | null;
  to?: Point | null;
  itemId?: string | null;
  operation?: 'unequip' | 'equip';
  previousItemId?: string | null;
  reason: string;
}
export interface Analysis {
  schemaVersion: 1;
  status: 'ready' | 'blocked';
  source: SaveSource;
  platformId: string;
  bounds: Bounds;
  metrics: {
    currentQi: number;
    targetQi: number;
    gain: number;
    movedPeople: number;
    movedBuildings: number;
    movedFacilities: number;
    itemChanges: number;
    availableItemCount?: number;
    warehouseItemCount?: number;
  };
  entitiesBefore: Entity[];
  entitiesAfter: Entity[];
  items: SpiritItem[];
  steps: MigrationStep[];
  warnings: string[];
  optimality: {
    qi: 'exact' | 'bounded' | 'heuristic';
    movement: 'exact' | 'bounded' | 'heuristic';
    explanation: string;
  };
}
