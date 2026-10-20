import type { Analysis, Bounds, Entity, EntityKind, MigrationStep, Point, SpiritItem, VisualCatalog } from './types';

export const ENTITY_STYLE: Record<EntityKind, { fill: string; stroke: string; label: string }> = {
  platform: { fill: '#f2d995', stroke: '#9c752a', label: '渡劫台' },
  pedestal: { fill: '#c6e6db', stroke: '#548e7e', label: '灵台' },
  building: { fill: '#d5deee', stroke: '#8298b7', label: '建筑' },
  resource: { fill: '#e3e8ca', stroke: '#96a378', label: '资源' },
  facility: { fill: '#e6d9ed', stroke: '#aa8eb8', label: '设施' },
  person: { fill: '#e5b99f', stroke: '#a76d4d', label: '人物' },
};

export function formatQi(value: number): string {
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(value);
}

export function coordinates(point?: Point | null): string {
  return point ? `(${point.x}, ${point.y})` : '仓库';
}

export function stepDescription(step: MigrationStep, entity?: Entity): string {
  return step.kind === 'equip'
    ? `${step.name}${step.to || entity ? ` ${coordinates(step.to ?? entity)}` : ''} · ${step.operation === 'unequip' ? '卸下灵物' : '装入灵物'}`
    : `${step.name} · ${coordinates(step.from)} → ${coordinates(step.to)}`;
}

/** Odd footprints are centred on their anchor; even footprints extend one cell further right/down. */
export function entityCenter(entity: Entity): Point {
  return {
    x: entity.x + (entity.width % 2 === 0 ? .5 : 0),
    y: entity.y + (entity.height % 2 === 0 ? .5 : 0),
  };
}

export function coreCells(platform?: Point): Point[] {
  if (!platform) return [];
  const cells: Point[] = [];
  for (let dx = -3; dx <= 3; dx++) for (let dy = -3; dy <= 3; dy++) {
    if (Math.abs(dx) + Math.abs(dy) <= 3 && Math.max(Math.abs(dx), Math.abs(dy)) > 1) {
      cells.push({ x: platform.x + dx, y: platform.y + dy });
    }
  }
  return cells;
}

export function entityLabel(entity: Entity): string {
  if (entity.kind === 'person') return '';
  if (entity.kind === 'pedestal' && !entity.itemId) return '空台';
  return entity.name.slice(0, entity.width > 1 ? 6 : 2);
}

export function itemForEntity(entity: Entity, items: SpiritItem[]): SpiritItem | undefined {
  return entity.kind === 'pedestal' && entity.itemId ? items.find((item) => item.id === entity.itemId) : undefined;
}

export function entityShortLabel(entity: Entity, items: SpiritItem[] = []): string {
  const name = entity.kind === 'pedestal' ? itemForEntity(entity, items)?.name ?? '空台' : entity.name;
  return Array.from(name).slice(0, entity.width > 1 ? 6 : 4).join('');
}

export function entitySecondaryLabel(entity: Entity, items: SpiritItem[] = []): string {
  const item = itemForEntity(entity, items);
  return item ? `灵 ${formatQi(item.qi)}` : '';
}

/** Include both people and the building beneath them, with the game's anchor convention. */
export function entitiesAtPoint(entities: Entity[], point: Point): Entity[] {
  return entities.filter((entity) => {
    const left = entity.x - Math.floor((entity.width - 1) / 2);
    const top = entity.y - Math.floor((entity.height - 1) / 2);
    return point.x >= left && point.x < left + entity.width && point.y >= top && point.y < top + entity.height;
  });
}

export function collectIconKeys(entities: Entity[], items: SpiritItem[]): string[] {
  return [...new Set([
    ...entities.flatMap((entity) => [entity.iconKey, entity.weapon?.iconKey, ...(entity.equipment ?? []).map((item) => item.iconKey)]),
    ...items.map((item) => item.iconKey),
  ].filter((key): key is string => Boolean(key)))];
}

/** Only inert, bounded PNG data from the named image capability can enter an export. */
export function resolveSprite(iconKey: string | undefined, visuals?: VisualCatalog | null) {
  const frame = iconKey ? visuals?.sprites[iconKey] : undefined;
  const sheet = frame ? visuals?.sheets[frame.sheetId] : undefined;
  if (!frame || !sheet || !/^data:image\/png;base64,[A-Za-z0-9+/]+={0,2}$/.test(sheet.dataUrl) || sheet.dataUrl.length > 100_000) return undefined;
  const dimensions = [frame.x, frame.y, frame.width, frame.height, sheet.width, sheet.height];
  if (dimensions.some((value) => !Number.isInteger(value) || value < 0)
    || !frame.width || !frame.height || !sheet.width || !sheet.height
    || sheet.width > 1024 || sheet.height > 1024
    || frame.x + frame.width > sheet.width || frame.y + frame.height > sheet.height) return undefined;
  return { frame, sheet };
}

export function entityDescription(entity: Entity, items: SpiritItem[] = []): string {
  const item = itemForEntity(entity, items);
  const lines = [`${entity.name} ${coordinates(entity)}`];
  if (entity.tier !== undefined) lines.push(`等阶 ${entity.tier}`);
  if (entity.profession) lines.push(`职业：${entity.profession}`);
  if (entity.job) lines.push(`当前工作：${entity.job}`);
  if (entity.equipment?.length) lines.push(`穿戴：${entity.equipment.map((entry) => `${entry.name}${entry.type ? `（${entry.type}）` : ''}`).join('、')}`);
  else if (entity.weapon) lines.push(`武器：${entity.weapon.name}`);
  if (item) {
    lines.push(`${item.name} · 基础灵气 ${formatQi(item.qi)}`);
    if (item.level !== undefined) lines.push(`灵物等级 ${item.level}${item.qualityName ? ` · ${item.qualityName}` : ''}`);
    if (item.attributes) lines.push(Object.entries(item.attributes).map(([name, value]) => `${name} ${formatQi(value)}`).join(' · '));
  } else if (entity.kind === 'pedestal') lines.push('空台');
  if (entity.lockedReason) lines.push(entity.lockedReason);
  return lines.join('\n');
}

export function focusBounds(bounds: Bounds, platform?: Entity, step?: MigrationStep): Bounds {
  if (!platform) return bounds;
  const points: Point[] = [platform];
  if (step?.from) points.push(step.from);
  if (step?.to) points.push(step.to);
  return {
    minX: Math.max(bounds.minX, Math.min(platform.x - 7, ...points.map((point) => point.x - 2))),
    maxX: Math.min(bounds.maxX, Math.max(platform.x + 7, ...points.map((point) => point.x + 2))),
    minY: Math.max(bounds.minY, Math.min(platform.y - 7, ...points.map((point) => point.y - 2))),
    maxY: Math.min(bounds.maxY, Math.max(platform.y + 7, ...points.map((point) => point.y + 2))),
  };
}

export function mapGeometry(bounds: Bounds, cell = 32, padding = 28) {
  return {
    width: (bounds.maxX - bounds.minX + 1) * cell + padding * 2,
    height: (bounds.maxY - bounds.minY + 1) * cell + padding * 2,
    cell,
    padding,
    x: (x: number) => padding + (x - bounds.minX + 0.5) * cell,
    y: (y: number) => padding + (y - bounds.minY + 0.5) * cell,
  };
}

export function visibleEntities(entities: Entity[], bounds: Bounds): Entity[] {
  return entities.filter((entity) => entityCenter(entity).x + entity.width / 2 > bounds.minX - 0.5
    && entityCenter(entity).x - entity.width / 2 < bounds.maxX + 0.5
    && entityCenter(entity).y + entity.height / 2 > bounds.minY - 0.5
    && entityCenter(entity).y - entity.height / 2 < bounds.maxY + 0.5)
    .sort((a, b) => Number(a.kind === 'person') - Number(b.kind === 'person'));
}

export function stepMarker(step: MigrationStep, entities: Entity[], after: boolean): Point | undefined {
  return (after ? step.to : step.from) ?? entities.find((entity) => entity.id === step.entityId);
}

/** Several operations can use the same cell; keep their badges legible instead of stacking them. */
export function groupedStepMarkers(steps: MigrationStep[], entities: Entity[], after: boolean, selectedStep = '') {
  const groups = new globalThis.Map<string, { step: MigrationStep; index: number; point: Point; indices: number[] }>();
  steps.forEach((step, index) => {
    const point = stepMarker(step, entities, after);
    if (!point) return;
    const key = `${point.x}:${point.y}`;
    const group = groups.get(key);
    if (!group) groups.set(key, { step, index, point, indices: [index] });
    else {
      group.indices.push(index);
      if (step.id === selectedStep) { group.step = step; group.index = index; }
    }
  });
  return [...groups.values()];
}

export function escapeXml(value: string): string {
  return value.replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' }[character]!));
}

function exportMap(analysis: Analysis, entities: Entity[], after: boolean, prefix: string, iconIds: globalThis.Map<string, string>, visuals?: VisualCatalog | null): string {
  const bounds = analysis.bounds;
  const geometry = mapGeometry(bounds);
  const { cell, padding, x, y, width, height } = geometry;
  const plotWidth = width - padding * 2;
  const plotHeight = height - padding * 2;
  const platform = entities.find((entity) => entity.id === analysis.platformId);
  const ring = coreCells(platform).map((point) => `<rect x="${x(point.x) - cell / 2}" y="${y(point.y) - cell / 2}" width="${cell}" height="${cell}" fill="#e6bd5738" stroke="#c8a451" stroke-width=".6"/>`).join('');
  const shapes = visibleEntities(entities, bounds).map((entity) => {
    const style = ENTITY_STYLE[entity.kind];
    const moved = analysis.steps.some((step) => step.entityId === entity.id);
    const item = itemForEntity(entity, analysis.items);
    const title = escapeXml(entityDescription(entity, analysis.items));
    const centre = entityCenter(entity);
    const shape = entity.kind === 'person'
      ? `<circle cx="${x(entity.x)}" cy="${y(entity.y)}" r="${cell * .23}" fill="${style.fill}" stroke="${moved ? '#cc6046' : style.stroke}" stroke-width="${moved ? 2.5 : 1}"/>`
      : `<rect x="${x(centre.x) - entity.width * cell / 2 + 1}" y="${y(centre.y) - entity.height * cell / 2 + 1}" width="${entity.width * cell - 2}" height="${entity.height * cell - 2}" rx="3" fill="${style.fill}" stroke="${moved ? '#cc6046' : style.stroke}" stroke-width="${moved ? 2.5 : 1}"/>`;
    const iconKey = entity.kind === 'pedestal' ? item?.iconKey : entity.iconKey;
    const sprite = resolveSprite(iconKey, visuals);
    const iconId = iconKey ? iconIds.get(iconKey) : undefined;
    const iconSize = entity.kind === 'person' ? 20 : 14;
    const icon = sprite && iconId ? `<svg x="${x(centre.x) - iconSize / 2}" y="${y(centre.y) - 14}" width="${iconSize}" height="${iconSize}" viewBox="${sprite.frame.x} ${sprite.frame.y} ${sprite.frame.width} ${sprite.frame.height}" overflow="hidden"><use href="#${iconId}"/></svg>` : '';
    const compact = entity.kind === 'person' || entity.kind === 'pedestal';
    const shortName = entityShortLabel(entity, analysis.items);
    const secondary = entitySecondaryLabel(entity, analysis.items);
    const label = `<text x="${x(centre.x)}" y="${y(centre.y) + (compact ? (entity.kind === 'person' ? 13 : 6) : 3.5)}" text-anchor="middle" font-size="${compact ? 6.4 : 10}" fill="#243149">${escapeXml(shortName)}</text>`;
    const subtitle = secondary ? `<text x="${x(centre.x)}" y="${y(centre.y) + 13}" text-anchor="middle" font-size="5.7" fill="#365a4f">${escapeXml(secondary)}</text>` : '';
    return `<g><title>${title}</title>${shape}${icon}${label}${subtitle}</g>`;
  }).join('');
  const movements = after ? analysis.steps.filter((step) => step.kind === 'move' && step.from && step.to).map((step) => `<path d="M ${x(step.from!.x)} ${y(step.from!.y)} L ${x(step.to!.x)} ${y(step.to!.y)}" fill="none" stroke="#c35c44" opacity=".7" stroke-width="1.8" marker-end="url(#${prefix}-arrow)"/>`).join('') : '';
  const markers = groupedStepMarkers(analysis.steps, entities, after).map(({ index, point, indices }) => {
    return `<g><title>步骤 ${indices.map((value) => value + 1).join('、')}</title><circle cx="${x(point.x) + cell * .32}" cy="${y(point.y) - cell * .32}" r="8" fill="#a64e3a"/><text x="${x(point.x) + cell * .32}" y="${y(point.y) - cell * .32 + 3}" fill="white" text-anchor="middle" font-size="8">${index + 1}${indices.length > 1 ? '+' : ''}</text></g>`;
  }).join('');
  const axes: string[] = [];
  for (let col = bounds.minX; col <= bounds.maxX; col += 2) axes.push(`<text x="${x(col)}" y="17" text-anchor="middle" fill="#748093" font-size="9">${col}</text>`);
  for (let row = bounds.minY; row <= bounds.maxY; row += 2) axes.push(`<text x="15" y="${y(row) + 3}" text-anchor="middle" fill="#748093" font-size="9">${row}</text>`);
  return `<defs><pattern id="${prefix}-grid" width="${cell}" height="${cell}" patternUnits="userSpaceOnUse" x="${padding}" y="${padding}"><path d="M ${cell} 0 L 0 0 0 ${cell}" fill="none" stroke="#dfe5ec" stroke-width="1"/></pattern><marker id="${prefix}-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6" fill="none" stroke="#c35c44"/></marker><clipPath id="${prefix}-clip"><rect x="${padding}" y="${padding}" width="${plotWidth}" height="${plotHeight}"/></clipPath></defs><rect x="${padding}" y="${padding}" width="${plotWidth}" height="${plotHeight}" fill="#f7f9fb" stroke="#dfe5ec"/><g clip-path="url(#${prefix}-clip)">${ring}<rect x="${padding}" y="${padding}" width="${plotWidth}" height="${plotHeight}" fill="url(#${prefix}-grid)"/>${shapes}${movements}${markers}</g>${axes.join('')}`;
}

/** Standalone, inert SVG: names and source strings never become markup or attributes. */
export function comparisonSvg(analysis: Analysis, visuals?: VisualCatalog | null): string {
  const iconIds = new globalThis.Map<string, string>();
  const definitions: string[] = [];
  for (const key of collectIconKeys([...analysis.entitiesBefore, ...analysis.entitiesAfter], analysis.items)) {
    const sprite = resolveSprite(key, visuals);
    if (!sprite) continue;
    const id = `tower-icon-${iconIds.size}`;
    iconIds.set(key, id);
    definitions.push(`<image id="${id}" width="${sprite.sheet.width}" height="${sprite.sheet.height}" href="${sprite.sheet.dataUrl}"/>`);
  }
  const totalWidth = 1480;
  const mapWidth = 704;
  const geometry = mapGeometry(analysis.bounds);
  const scale = mapWidth / geometry.width;
  const mapHeight = geometry.height * scale;
  const listY = 142 + mapHeight + 36;
  const lines = [analysis.optimality.explanation, ...analysis.warnings, ...analysis.steps.map((step, index) => `${index + 1}. ${stepDescription(step, (step.operation === 'unequip' ? analysis.entitiesBefore : analysis.entitiesAfter).find((entity) => entity.id === step.entityId))} — ${step.reason}`)]
    .flatMap((line) => Array.from({ length: Math.max(1, Math.ceil(line.length / 100)) }, (_, index) => line.slice(index * 100, (index + 1) * 100)));
  const totalHeight = Math.ceil(listY + 45 + lines.length * 23);
  const renderText = (text: string, x: number, y: number, size = 13, fill = '#516176') => `<text x="${x}" y="${y}" font-size="${size}" fill="${fill}">${escapeXml(text)}</text>`;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${totalWidth}" height="${totalHeight}" viewBox="0 0 ${totalWidth} ${totalHeight}" role="img" aria-label="渡劫布局迁移对比图"><defs>${definitions.join('')}</defs><rect width="100%" height="100%" fill="#ffffff"/><g font-family="Microsoft YaHei,Segoe UI,sans-serif">${renderText('渡劫布局 · 迁移对比', 28, 38, 24, '#243149')}${renderText(`${analysis.source.name} · ${analysis.source.savedAt} · 只读存档分析`, 28, 65)}${renderText(`灵气 ${formatQi(analysis.metrics.currentQi)} → ${formatQi(analysis.metrics.targetQi)}   搬动建筑 ${analysis.metrics.movedBuildings} / 设施 ${analysis.metrics.movedFacilities} / 人物 ${analysis.metrics.movedPeople}   灵物更换 ${analysis.metrics.itemChanges}`, 28, 90, 14)}${renderText('当前摆放', 28, 124, 16, '#243149')}${renderText('建议摆放 · 编号对应下方步骤', 748, 124, 16, '#243149')}<g transform="translate(28 142) scale(${scale})">${exportMap(analysis, analysis.entitiesBefore, false, 'before', iconIds, visuals)}</g><g transform="translate(748 142) scale(${scale})">${exportMap(analysis, analysis.entitiesAfter, true, 'after', iconIds, visuals)}</g>${renderText('迁移步骤与分析说明', 28, listY, 17, '#243149')}${lines.map((line, index) => renderText(line, 28, listY + 30 + index * 23)).join('')}</g></svg>`;
}
