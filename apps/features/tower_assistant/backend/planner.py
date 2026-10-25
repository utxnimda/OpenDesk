"""Read-only tribulation layout planning; no game or filesystem operations.

The matching problem has an exact aura upper bound. Geometry is searched within
a fixed budget, and a result only claims that bound when its executable sequence
actually reaches it. Entity relocation and item reassignment are separate costs.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import heapq
import itertools
import math
import time
from typing import Any

from .reader import pedestal_qi_units


class PlannerError(ValueError):
    """The supplied snapshot cannot be interpreted safely."""


CORE_OFFSETS = tuple(
    (x, y) for y in range(-3, 4) for x in range(-3, 4)
    if abs(x) + abs(y) <= 3 and max(abs(x), abs(y)) > 1
)
_MAX_ENTITIES = 3000
_MAX_ITEMS = 30000
_BUDGET_SECONDS = 4.5


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or int(value) != value:
        raise PlannerError(f"{label} 必须是有限整数。")
    return int(value)


def _point(entity: dict) -> tuple[int, int]:
    return entity["x"], entity["y"]


def _position(point: tuple[int, int]) -> dict:
    return {"x": point[0], "y": point[1]}


def _cells(entity: dict, point: tuple[int, int] | None = None) -> frozenset[tuple[int, int]]:
    x, y = point if point is not None else _point(entity)
    w, h = entity["width"], entity["height"]
    left, top = x - (w - 1) // 2, y - (h - 1) // 2
    return frozenset((left + dx, top + dy) for dx in range(w) for dy in range(h))


def _qi_units(item: dict | None, pedestal: dict, bonus: Decimal) -> int:
    if item is None:
        return 0
    return pedestal_qi_units(item["qi"], pedestal["tier"], float(bonus))


def _validate(snapshot: dict) -> tuple[list[dict], list[dict], dict, Decimal]:
    if not isinstance(snapshot, dict) or snapshot.get("schemaVersion") != 1:
        raise PlannerError("不支持的存档快照版本。")
    entities, items = snapshot.get("entities"), snapshot.get("items")
    if not isinstance(entities, list) or not isinstance(items, list) or len(entities) > _MAX_ENTITIES or len(items) > _MAX_ITEMS:
        raise PlannerError("快照缺少实体/灵物列表，或数量超过安全分析上限。")
    entities, items = deepcopy(entities), deepcopy(items)
    seen: set[str] = set()
    for entity in entities:
        if not isinstance(entity, dict) or not isinstance(entity.get("id"), str) or not entity["id"] or entity["id"] in seen:
            raise PlannerError("实体编号缺失或重复。")
        seen.add(entity["id"])
        if entity.get("kind") not in {"platform", "pedestal", "building", "resource", "facility", "person"}:
            raise PlannerError("快照包含未知实体类型。")
        entity.setdefault("name", entity["id"])
        for key in ("x", "y", "width", "height"):
            entity[key] = _integer(entity.get(key), f"{entity['name']} 的 {key}")
        if not 1 <= entity["width"] <= 3 or not 1 <= entity["height"] <= 3:
            raise PlannerError("建筑占地尺寸超出支持范围。")
        if not isinstance(entity.get("movable"), bool):
            raise PlannerError("实体缺少明确的可搬迁标志。")
        occupants = entity.get("occupantIds", [])
        if not isinstance(occupants, list) or any(not isinstance(ident, str) for ident in occupants) or len(set(occupants)) != len(occupants):
            raise PlannerError("建筑占用角色列表无效。")
        if entity.get("allowedBounds") is not None:
            if not isinstance(entity["allowedBounds"], dict):
                raise PlannerError("关联设施的移动范围无效。")
            entity["allowedBounds"] = {key: _integer(entity["allowedBounds"].get(key), "设施移动边界") for key in ("minX", "minY", "maxX", "maxY")}
        if entity["kind"] == "pedestal":
            entity["tier"] = _integer(entity.get("tier"), "置物台等级")
            if not 0 <= entity["tier"] <= 100 or (entity["width"], entity["height"]) != (1, 1):
                raise PlannerError("置物台等级或占地不受支持。")
    entity_map = {entity["id"]: entity for entity in entities}
    non_people_occupancy: set[tuple[int, int]] = set()
    for entity in entities:
        if entity["kind"] == "person":
            continue
        cells = _cells(entity)
        if cells & non_people_occupancy:
            raise PlannerError("存档中的建筑、设施或资源占地重叠，不能可靠生成迁移方案。")
        non_people_occupancy.update(cells)
    seen.clear()
    placed: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"] or item["id"] in seen:
            raise PlannerError("灵物编号缺失或重复。")
        seen.add(item["id"])
        if isinstance(item.get("qi"), bool) or not isinstance(item.get("qi"), (int, float)) or not math.isfinite(item["qi"]) or not 0 <= item["qi"] <= 1_000_000:
            raise PlannerError("灵物灵气数值无效。")
        if item.get("location") not in {"warehouse", "placed"}:
            raise PlannerError("灵物位置必须是仓库或已摆放。")
        if item["location"] == "placed":
            pad = entity_map.get(item.get("pedestalId"))
            if not pad or pad["kind"] != "pedestal" or pad["id"] in placed or pad.get("itemId") != item["id"]:
                raise PlannerError("灵物和置物台的绑定关系不一致。")
            placed.add(pad["id"])
    item_map = {item["id"]: item for item in items}
    for entity in entities:
        if entity["kind"] == "pedestal" and entity.get("itemId") is not None:
            item = item_map.get(entity["itemId"])
            if not item or item.get("pedestalId") != entity["id"] or item["location"] != "placed":
                raise PlannerError("置物台引用了缺失或不匹配的灵物。")
    bounds = snapshot.get("bounds")
    if not isinstance(bounds, dict):
        raise PlannerError("快照缺少主城可建造范围。")
    bounds = {key: _integer(bounds.get(key), "主城边界") for key in ("minX", "minY", "maxX", "maxY")}
    if not 1 <= bounds["maxX"] - bounds["minX"] + 1 <= 200 or not 1 <= bounds["maxY"] - bounds["minY"] + 1 <= 200:
        raise PlannerError("主城可建造范围无效或过大。")
    rules = snapshot.get("rules", {})
    if (rules.get("auraRadius") != 3 or rules.get("auraMetric") != "manhattan"
            or rules.get("qiRounding") != "js-one-decimal" or rules.get("allowItemReassignment") is not True
            or rules.get("coordinateAnchor") != "game-grid"):
        raise PlannerError("当前游戏灵气或坐标规则尚未经过支持性校验。")
    try:
        bonus = Decimal(str(rules.get("pedestalTierBonus")))
    except InvalidOperation as error:
        raise PlannerError("置物台等级加成规则无效。") from error
    if bonus != Decimal("0.15"):
        raise PlannerError("不支持当前置物台等级加成规则。")
    return entities, items, bounds, bonus


@dataclass
class _Edge:
    target: int
    reverse: int
    capacity: int
    cost: int


def _match(pads: list[dict], items: list[dict], slots: set[tuple[int, int]], bonus: Decimal,
           required: set[str], deadline: float) -> tuple[dict[str, str | None], int]:
    """Maximum aura matching, then core retention, then item retention.

    Taking the best N warehouse items is sufficient for the aura bound: every
    pedestal's rounded contribution is monotone in raw aura. All placed items
    stay in the candidate set to preserve already correct assignments on ties.
    """
    count = min(len(pads), len(slots))
    if not count:
        return {}, 0
    pool = [item for item in items if item["location"] == "placed"]
    pool += sorted((item for item in items if item["location"] == "warehouse"), key=lambda item: (-item["qi"], item["id"]))[:count]
    pool.sort(key=lambda item: item["id"])
    pool += [None] * count
    pads = sorted(pads, key=lambda entity: entity["id"])
    if len(pads) > 128 or len(pool) > 256:
        raise PlannerError("参与匹配的置物台超过 128 个或候选灵物超过 256 件；本版为保证限时响应暂不支持此规模。")
    source, item_start, pad_start = 0, 1, 1 + len(pool)
    sink = pad_start + len(pads)
    graph: list[list[_Edge]] = [[] for _ in range(sink + 1)]

    def edge(a: int, b: int, cost: int) -> _Edge:
        forward = _Edge(b, len(graph[b]), 1, cost)
        backward = _Edge(a, len(graph[a]), 0, -cost)
        graph[a].append(forward)
        graph[b].append(backward)
        return forward

    # The weights separate objectives even at maximum accepted entity counts.
    aura_weight, movement_weight = 10**12, 10**6
    max_unit = max((_qi_units(item, pad, bonus) for item in pool if item is not None for pad in pads), default=0)
    force_weight = (max_unit * count + 1) * aura_weight
    edges: list[tuple[int, int, _Edge]] = []
    potentials = [0] * len(graph)
    for i, item in enumerate(pool):
        if time.monotonic() > deadline:
            raise PlannerError("灵物匹配超过本次计算预算。")
        edge(source, item_start + i, 0)
        for j, pad in enumerate(pads):
            equip_cost = 0 if pad.get("itemId") == (item["id"] if item else None) else 1
            cost = -_qi_units(item, pad, bonus) * aura_weight + equip_cost
            edges.append((i, j, edge(item_start + i, pad_start + j, cost)))
            potentials[pad_start + j] = min(potentials[pad_start + j], cost)
    for j, pad in enumerate(pads):
        move_cost = -1 if _point(pad) in slots else 1 + len(pad.get("occupantIds", []))
        cost = move_cost * movement_weight - (force_weight if pad["id"] in required else 0)
        edge(pad_start + j, sink, cost)
        if j == 0:
            potentials[sink] = potentials[pad_start + j] + cost
        else:
            potentials[sink] = min(potentials[sink], potentials[pad_start + j] + cost)
    infinity = 10**100
    for _ in range(count):
        if time.monotonic() > deadline:
            raise PlannerError("规划超过本次计算预算；请减少参与分析的实体数量后重试。")
        distance = [infinity] * len(graph)
        previous: list[tuple[int, int] | None] = [None] * len(graph)
        distance[source] = 0
        queue = [(0, source)]
        while queue:
            dist, node = heapq.heappop(queue)
            if dist != distance[node]:
                continue
            for number, current in enumerate(graph[node]):
                if current.capacity:
                    candidate = dist + current.cost + potentials[node] - potentials[current.target]
                    if candidate < distance[current.target]:
                        distance[current.target] = candidate
                        previous[current.target] = (node, number)
                        heapq.heappush(queue, (candidate, current.target))
        if previous[sink] is None:
            raise PlannerError("无法完成灵物与置物台匹配。")
        for node, value in enumerate(distance):
            if value != infinity:
                potentials[node] += value
        node = sink
        while node != source:
            parent, number = previous[node]  # type: ignore[misc]
            current = graph[parent][number]
            current.capacity -= 1
            graph[node][current.reverse].capacity += 1
            node = parent
    result: dict[str, str | None] = {}
    total = 0
    for i, j, current in edges:
        if current.capacity == 0:
            item, pad = pool[i], pads[j]
            contribution = _qi_units(item, pad, bonus)
            if contribution or _point(pad) in slots or pad["id"] in required:
                result[pad["id"]] = item["id"] if item else None
                total += contribution
    if not required.issubset(result):
        raise PlannerError("不可搬动的核心置物台没有被保留。")
    return result, total


def _occupancy(entities: list[dict]) -> dict[tuple[int, int], set[str]]:
    result: dict[tuple[int, int], set[str]] = {}
    for entity in entities:
        for cell in _cells(entity):
            result.setdefault(cell, set()).add(entity["id"])
    return result


def _in_bounds(cells: frozenset[tuple[int, int]], bounds: dict) -> bool:
    return all(bounds["minX"] <= x <= bounds["maxX"] and bounds["minY"] <= y <= bounds["maxY"] for x, y in cells)


def _associated(entity: dict, entity_map: dict[str, dict]) -> set[str]:
    result = set(entity.get("occupantIds", []))
    result.update(person["id"] for person in entity_map.values()
                  if person["kind"] == "person" and (person.get("workingBuildingId") == entity["id"] or person.get("parentId") == entity["id"]))
    return result


def _movable(entity: dict, entity_map: dict[str, dict]) -> bool:
    facility_valid = (entity["kind"] != "facility" or
                      (entity.get("parentId") in entity_map and isinstance(entity.get("allowedBounds"), dict)
                       and not entity_map[entity["parentId"]]["movable"]))
    return (entity["movable"] and entity["kind"] not in {"platform", "resource"} and facility_valid
            and all(ident in entity_map and entity_map[ident]["kind"] == "person" and entity_map[ident]["movable"] for ident in _associated(entity, entity_map)))


def _nearest_positions(entity: dict, bounds: dict, current_occupied: set | None = None) -> list[tuple[int, int]]:
    x, y = _point(entity)
    allowed = entity.get("allowedBounds", bounds)
    positions = [(px, py) for py in range(max(bounds["minY"], allowed["minY"]), min(bounds["maxY"], allowed["maxY"]) + 1)
                 for px in range(max(bounds["minX"], allowed["minX"]), min(bounds["maxX"], allowed["maxX"]) + 1)]
    positions.sort(key=lambda point: ((bool(_cells(entity, point) & current_occupied) if current_occupied is not None else False),
                                      abs(point[0] - x) + abs(point[1] - y), point[1], point[0]))
    return positions


def _empty_destination(entity: dict, bounds: dict, occupied: set, deadline: float,
                       current_occupied: set | None = None) -> tuple[int, int] | None:
    for index, point in enumerate(_nearest_positions(entity, bounds, current_occupied)):
        if index % 32 == 0 and time.monotonic() > deadline:
            return None
        cells = _cells(entity, point)
        if point != _point(entity) and _in_bounds(cells, bounds) and not cells & occupied:
            return point
    return None


def _layout(entities: list[dict], selected: dict[str, str | None], slots: set[tuple[int, int]],
            bounds: dict, deadline: float) -> tuple[dict[str, tuple[int, int]], int] | None:
    entity_map = {entity["id"]: entity for entity in entities}
    occupancy = _occupancy(entities)
    preserved = {ident: _point(entity_map[ident]) for ident in selected if _point(entity_map[ident]) in slots}
    arrivals = sorted((ident for ident in selected if ident not in preserved),
                      key=lambda ident: (entity_map[ident]["y"], entity_map[ident]["x"], ident))
    free_choices = sorted(slots - set(preserved.values()), key=lambda point: (point[1], point[0]))
    alternatives = []
    for choice in itertools.combinations(free_choices, len(arrivals)):
        if time.monotonic() > deadline:
            break
        blockers = set().union(*(occupancy.get(point, set()) for point in choice)) if choice else set()
        blockers -= set(selected)
        for ident in tuple(blockers) + tuple(arrivals):
            blockers.update(_associated(entity_map[ident], entity_map))
        if any(not _movable(entity_map[ident], entity_map) for ident in blockers):
            continue
        score = (len(blockers) + len(arrivals), sum(entity_map[ident]["kind"] != "person" for ident in blockers), choice)
        alternatives.append((score, choice, blockers))
    alternatives.sort(key=lambda value: value[0])
    # Different equal-cost footprints can have different packing feasibility.
    # Explore a bounded set; report movement as heuristic unless a lower bound
    # proves the selected result optimal.
    for _, choice, blockers in alternatives[:48]:
        if time.monotonic() > deadline:
            break
        # Minimum-distance assignment is only a tie breaker for entity count.
        available = set(choice)
        positions = dict(preserved)
        for ident in arrivals:
            original = _point(entity_map[ident])
            target = min(available, key=lambda point: (abs(point[0] - original[0]) + abs(point[1] - original[1]), point[1], point[0]))
            available.remove(target)
            positions[ident] = target
        moving = set(arrivals) | blockers
        static = set().union(*(_cells(entity) for entity in entities if entity["id"] not in moving)) if entities else set()
        reserved = set(positions.values())
        order = sorted(blockers, key=lambda ident: (-entity_map[ident]["width"] * entity_map[ident]["height"], entity_map[ident]["kind"] == "person", ident))
        # Large obstructions go first. Three deterministic orderings reduce
        # greedy packing failures without an unbounded combinatorial search.
        for attempt in range(3):
            placed = dict(positions)
            occupied = static | reserved
            trial_order = order if attempt == 0 else list(reversed(order)) if attempt == 1 else sorted(order)
            success = True
            for ident in trial_order:
                entity = entity_map[ident]
                target = _empty_destination(entity, bounds, occupied, deadline, set(occupancy))
                if target is None:
                    success = False
                    break
                placed[ident] = target
                occupied.update(_cells(entity, target))
            if success:
                return {ident: point for ident, point in placed.items() if point != _point(entity_map[ident])}, alternatives[0][0][0]
    return None


def _schedule(entities: list[dict], destinations: dict[str, tuple[int, int]], bounds: dict,
              deadline: float) -> tuple[list[dict], list[dict]] | None:
    state = {entity["id"]: deepcopy(entity) for entity in entities}
    pending = dict(destinations)
    steps: list[dict] = []
    temporary: set[str] = set()
    final_cells = set().union(*(_cells(state[ident], point) for ident, point in destinations.items())) if destinations else set()

    def move(ident: str, target: tuple[int, int], temporary_move: bool = False) -> None:
        entity = state[ident]
        steps.append({"kind": "move", "entityId": ident, "name": entity["name"],
                      "from": _position(_point(entity)), "to": _position(target),
                      "temporary": temporary_move,
                      "reason": "先移到临时空地，解除互相占位" if temporary_move else "为最高灵气方案腾出必要位置" if entity["kind"] != "pedestal" else "将选定置物台移到渡劫台有效范围"})
        entity["x"], entity["y"] = target
        if entity["kind"] == "person":
            entity.pop("parentId", None)
            entity.pop("workingBuildingId", None)
            # Native worker relocation clears its building occupation link.
            for building in state.values():
                if ident in building.get("occupantIds", []):
                    building["occupantIds"] = [value for value in building["occupantIds"] if value != ident]

    while pending:
        if time.monotonic() > deadline:
            return None
        occupancy = _occupancy(list(state.values()))
        progressed = False
        for ident, target in list(pending.items()):
            blockers = set().union(*(occupancy.get(cell, set()) for cell in _cells(state[ident], target))) - {ident}
            active_workers = _associated(state[ident], state)
            if not blockers and not active_workers:
                move(ident, target)
                del pending[ident]
                progressed = True
        if progressed:
            continue
        # A dependency cycle requires a real, currently empty temporary spot.
        # Do not output a simultaneous swap as a playable movement sequence.
        for ident in sorted(pending, key=lambda key: (state[key]["kind"] != "person", state[key]["width"] * state[key]["height"], key)):
            if ident in temporary or _associated(state[ident], state):
                continue
            occupied = set(occupancy) | final_cells
            target = _empty_destination(state[ident], bounds, occupied, deadline)
            if target is not None:
                move(ident, target, True)
                temporary.add(ident)
                progressed = True
                break
        if not progressed:
            return None
    return steps, list(state.values())


def analyze(snapshot: dict, platform_id: str | None = None) -> dict:
    """Return a complete read-only comparison, or a blocked result with no steps."""
    deadline = time.monotonic() + _BUDGET_SECONDS
    entities, items, bounds, bonus = _validate(snapshot)
    if platform_id is not None and not isinstance(platform_id, str):
        raise PlannerError("渡劫台编号必须是字符串。")
    entity_map = {entity["id"]: entity for entity in entities}
    item_map = {item["id"]: item for item in items}
    platforms = [entity for entity in entities if entity["kind"] == "platform"]
    if platform_id is None:
        if len(platforms) != 1:
            raise PlannerError("没有唯一的渡劫台，请先选择要分析的渡劫台。")
        platform = platforms[0]
    else:
        platform = entity_map.get(platform_id)
        if not platform or platform["kind"] != "platform":
            raise PlannerError("所选渡劫台不存在。")
    if (platform["width"], platform["height"]) != (3, 3):
        raise PlannerError("当前渡劫台占地并非已校验的 3×3。")
    center = _point(platform)
    core = {(center[0] + x, center[1] + y) for x, y in CORE_OFFSETS}
    occupancy = _occupancy(entities)
    slots: set[tuple[int, int]] = set()
    blocked: list[dict] = []
    for point in sorted(core):
        hard = [entity_map[ident] for ident in occupancy.get(point, ())
                if not _movable(entity_map[ident], entity_map) and entity_map[ident]["kind"] != "pedestal"]
        if not _in_bounds(frozenset([point]), bounds) or hard:
            blocked.append({**_position(point), "reason": "超出主城建造范围" if not _in_bounds(frozenset([point]), bounds) else "、".join(entity["name"] for entity in hard) + "不能直接搬迁"})
        else:
            slots.add(point)
    pads = [entity for entity in entities if entity["kind"] == "pedestal"]
    eligible = [entity for entity in pads if _movable(entity, entity_map) or _point(entity) in slots]
    required = {entity["id"] for entity in eligible if not _movable(entity, entity_map)}
    current = sum(_qi_units(item_map.get(pad.get("itemId")), pad, bonus) for pad in pads if _point(pad) in core)
    warnings = list(snapshot.get("warnings", []))
    if blocked:
        warnings.append(f"渡劫台有效范围内有 {len(blocked)} 格越界或受固定设施约束；本方案保持这些约束。")
    if any(entity["kind"] in {"building", "resource"} and not entity["movable"] for entity in entities):
        warnings.append("资源点及带关联设施的主体建筑保持原位；设施只在原属建筑的合法范围内让位。")
    result = {"schemaVersion": 1, "status": "ready", "source": deepcopy(snapshot.get("source", {})),
              "bounds": bounds, "platformId": platform["id"], "platforms": [{key: entity[key] for key in ("id", "name", "x", "y")} for entity in platforms],
              "metrics": {"currentQi": current / 10, "targetQi": current / 10, "gain": 0,
                          "movedPeople": 0, "movedBuildings": 0, "movedFacilities": 0, "itemChanges": 0,
                          "availableItemCount": len(items), "warehouseItemCount": sum(item["location"] == "warehouse" for item in items),
                          "usableCoreSlots": len(slots), "coreSlots": 16},
              "entitiesBefore": entities, "entitiesAfter": deepcopy(entities), "items": items,
              "steps": [], "warnings": warnings, "blockedSlots": blocked,
              "optimality": {"qi": "bounded", "movement": "heuristic", "explanation": "尚未找到经过占位与操作顺序验证的最高灵气方案。"}}
    selected, upper = _match(eligible, items, slots, bonus, required, deadline)
    result["metrics"]["qiUpperBound"] = upper / 10
    layout = _layout(entities, selected, slots, bounds, deadline)
    if layout is None:
        result["status"] = "blocked"
        warnings.append("现有空地不足以安放需要让位的实体，或在计算预算内无法找到可行布局；未输出搬迁步骤。")
        return result
    destinations, _ = layout
    scheduled = _schedule(entities, destinations, bounds, deadline)
    if scheduled is None:
        result["status"] = "blocked"
        warnings.append("最终摆放存在互相占位，且没有找到合法临时位置完成顺序搬迁；未输出不可执行的交换步骤。")
        return result
    move_steps, after = scheduled
    after_map = {entity["id"]: entity for entity in after}
    final_binding = {pad["id"]: pad.get("itemId") for pad in pads}
    wanted_items = {item_id for item_id in selected.values() if item_id is not None}
    for ident, item_id in tuple(final_binding.items()):
        if item_id in wanted_items and selected.get(ident) != item_id:
            final_binding[ident] = None
    for ident, item_id in selected.items():
        final_binding[ident] = item_id
    # Keep unused zero-aura items on their existing pedestals when they neither
    # conflict with a chosen item nor alter the optimum.
    for pad in pads:
        if final_binding[pad["id"]] is None and pad.get("itemId") not in wanted_items:
            final_binding[pad["id"]] = pad.get("itemId")
    unequip, equip = [], []
    changed = []
    for pad in pads:
        ident, old, new = pad["id"], pad.get("itemId"), final_binding[pad["id"]]
        after_map[ident]["itemId"] = new
        if old == new:
            continue
        changed.append(ident)
        if old is not None:
            unequip.append({"kind": "equip", "operation": "unequip", "entityId": ident, "name": pad["name"],
                            "itemId": None, "previousItemId": old,
                            "reason": f"取下{item_map[old].get('name', old)}（基础灵气 {item_map[old]['qi']:g}），先放回仓库以完成后续换装"})
        if new is not None:
            item = item_map[new]
            origin = "仓库"
            if item["location"] == "placed":
                original_pad = entity_map[item["pedestalId"]]
                origin = f"原台 ({original_pad['x']}, {original_pad['y']})"
            equip.append({"kind": "equip", "operation": "equip", "entityId": ident, "name": pad["name"],
                          "itemId": new,
                          "reason": f"放置{item.get('name', new)}（基础灵气 {item['qi']:g}；原来源：{origin}），按此台等级获得 {_qi_units(item, pad, bonus) / 10:g} 灵气"})
    free_slots = snapshot["rules"].get("warehouseFreeSlots")
    if free_slots is not None:
        free_slots = _integer(free_slots, "灵物仓库空位")
        if free_slots < 0:
            raise PlannerError("灵物仓库空位不能为负数。")
        if len(unequip) > free_slots:
            result["status"] = "blocked"
            warnings.append(f"本方案先取下 {len(unequip)} 件灵物，但仓库仅有 {free_slots} 个空位；请先整理仓库再保存分析，未输出可能丢失灵物的步骤。")
            return result
    elif unequip:
        warnings.append("快照未提供灵物仓库空位，换装前需确认至少可暂存所有取下的灵物。")
    target = sum(_qi_units(item_map.get(pad.get("itemId")), pad, bonus) for pad in after if pad["kind"] == "pedestal" and _point(pad) in core)
    if target != upper:
        raise PlannerError("规划结果未通过灵气上界一致性复核。")
    steps = unequip + move_steps + equip
    for number, step in enumerate(steps, 1):
        step["id"] = f"step-{number}"
    for item in items:
        pedestal = next((ident for ident, value in final_binding.items() if value == item["id"]), None)
        item["plannedLocation"] = "placed" if pedestal else "warehouse"
        item["plannedPedestalId"] = pedestal
    moved_people = sum(entity_map[ident]["kind"] == "person" for ident in destinations)
    moved_facilities = sum(entity_map[ident]["kind"] == "facility" for ident in destinations)
    moved_buildings = len(destinations) - moved_people - moved_facilities
    # Zero moves is an unconditional minimum. Full core occupancy also has a
    # useful bound, but without searching every aura-optimal matching we do not
    # claim a global movement proof merely from a single placement search.
    occupied_core_pads = {pad["id"] for pad in pads if _point(pad) in slots}
    existing_pad_points = {_point(pad) for pad in pads if pad["id"] in occupied_core_pads}
    must_fill = slots - existing_pad_points
    mandatory_blockers = set().union(*(occupancy.get(point, set()) for point in must_fill)) if must_fill else set()
    # With N positive available items, a matching using fewer than N pedestals
    # can always be improved by an unused item and pedestal. Therefore an aura
    # optimum filling N slots must bring in N-C pedestals and clear every
    # non-pedestal obstruction in those slots. Meeting that independent lower
    # bound proves the entity-count minimum, not a shortest travel distance.
    positive_count = sum(_qi_units(item, {"tier": 0}, bonus) > 0 for item in items)
    lower_bound = len(mandatory_blockers) + max(0, len(slots) - len(occupied_core_pads))
    full_core_proof = (len(selected) == len(slots) and len(eligible) >= len(slots)
                       and positive_count >= len(slots) and len(destinations) == lower_bound)
    movement_exact = not destinations or full_core_proof
    result["entitiesAfter"] = after
    result["steps"] = steps
    result["metrics"].update({"targetQi": target / 10, "gain": (target - current) / 10,
                              "movedPeople": moved_people, "movedBuildings": moved_buildings, "movedFacilities": moved_facilities,
                              "itemChanges": len(changed), "moveOperations": len(move_steps),
                              "movementLowerBound": lower_bound if full_core_proof else 0})
    result["optimality"] = {"qi": "exact", "movement": "exact" if movement_exact else "heuristic",
                            "explanation": "已达到现有置物台、可用灵物及保留固定约束下的灵气匹配上界。" +
                            ("只需换装或无需操作，人物和建筑搬动为 0，已是最少。" if not destinations else
                             f"填满有效格至少要迁入 {len(slots) - len(occupied_core_pads)} 台，并让位 {len(mandatory_blockers)} 个占格实体；方案恰好搬动 {lower_bound} 个实体，达到最少。此证明不包含步行距离或换装次数。" if full_core_proof else
                             "在同灵气方案中优先保留原位台、减少让位实体，并验证每步占位；未证明所有等收益布局中的全局最少搬动。")}
    return result
