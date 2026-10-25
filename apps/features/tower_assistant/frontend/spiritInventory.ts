import type { Bounds, Entity, Snapshot, SpiritItem } from './types';

export interface RankedSpirit { item: SpiritItem; rank: number; relativeQi: number }
export type SpiritLocationFilter = 'all' | 'warehouse' | 'placed';

/** Rank the whole saved inventory before filtering; equal values share competition rank. */
export function rankSpirits(items: readonly SpiritItem[]): RankedSpirit[] {
  const sorted = [...items].sort((a, b) => b.qi - a.qi || a.name.localeCompare(b.name, 'zh-CN') || a.id.localeCompare(b.id));
  const highest = sorted[0]?.qi ?? 0;
  let rank = 0;
  return sorted.map((item, index) => {
    if (!index || item.qi !== sorted[index - 1].qi) rank = index + 1;
    return { item, rank, relativeQi: highest > 0 ? item.qi / highest * 100 : 0 };
  });
}

export function filterSpirits(rows: readonly RankedSpirit[], location: SpiritLocationFilter, search: string): RankedSpirit[] {
  const needle = search.trim().toLocaleLowerCase('zh-CN');
  return rows.filter(({ item }) => (location === 'all' || item.location === location)
    && (!needle || item.name.toLocaleLowerCase('zh-CN').includes(needle) || item.id.toLowerCase().includes(needle)));
}

export function spiritPedestal(item: SpiritItem, entities: readonly Entity[]): Entity | undefined {
  if (item.location !== 'placed') return undefined;
  return entities.find((entry) => entry.kind === 'pedestal' && entry.id === item.pedestalId && entry.itemId === item.id);
}

export function spiritForEntity(entityId: string, snapshot: Snapshot): SpiritItem | undefined {
  const entity = snapshot.entities.find((entry) => entry.id === entityId && entry.kind === 'pedestal');
  if (!entity) return undefined;
  return snapshot.items.find((item) => item.location === 'placed' && item.id === entity.itemId && item.pedestalId === entity.id);
}

/** Presentation lookup only: the worker owns range checks, amplification and rounding. */
export function spiritContribution(item: SpiritItem, platformId: string): number | undefined {
  if (item.location !== 'placed' || !platformId || item.contributions === undefined) return undefined;
  return item.contributions.find((entry) => entry.platformId === platformId)?.qi ?? 0;
}

export function spiritMapBounds(snapshot: Snapshot, focus?: Entity): Bounds {
  const anchors = focus ? [focus] : snapshot.entities.filter((entry) => entry.kind === 'pedestal' || entry.kind === 'platform');
  if (!anchors.length) return snapshot.bounds;
  const margin = focus ? 3 : 1;
  return {
    minX: Math.max(snapshot.bounds.minX, Math.min(...anchors.map((entry) => entry.x - Math.floor((entry.width - 1) / 2))) - margin),
    maxX: Math.min(snapshot.bounds.maxX, Math.max(...anchors.map((entry) => entry.x + Math.ceil((entry.width - 1) / 2))) + margin),
    minY: Math.max(snapshot.bounds.minY, Math.min(...anchors.map((entry) => entry.y - Math.floor((entry.height - 1) / 2))) - margin),
    maxY: Math.min(snapshot.bounds.maxY, Math.max(...anchors.map((entry) => entry.y + Math.ceil((entry.height - 1) / 2))) + margin),
  };
}
