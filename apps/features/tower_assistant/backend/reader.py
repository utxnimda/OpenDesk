"""Read game-owned files without executing game code or writing save data.

The format and constants below are audited against complete RULE_PROFILES.
Unknown game revisions deliberately fail instead of producing plausible layouts.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from typing import Any
from zipfile import BadZipFile, ZipFile


class ReaderError(ValueError):
    """A readable, expected rejection of game data."""


RULE_HASHES = {
    "data.json": "9f34cb8a3179504851af003dae84d8d374379e73e2211508010a1c63ffb1e465",
    "scripts/c3runtime.js": "b097d0f36506e4ea3ea4735754f8053e02f7c38a340a3cf3577ab9f7bf46bbc0",
    "jz.json": "4f2e35465ef62b3ead01daf7feef4355f9e5115de3ff471b3d926b98d925c620",
    "ss.json": "4350cb979c4026ce0d8dc25e7807fde3d29a1d42fbd2e499673614a4f67e1bc7",
    "lw.json": "7a4f5a5bfd5967f3a74e36aa9a0c2cda132619b2908bdc39b1406da4b346bde6",
    "wp.json": "14516d34cedf209c497fca10699e179c26c43558c2a3253bc87c8b33493a82c4",
    "zy.json": "89f437020459a63408d88202afa47f0550306034e6d6e75e2315c7122d662748",
}
RULE_PROFILES = {
    "audited-2026-10-18": {
        "hashes": RULE_HASHES,
        "saveDepth": 22,
        "sprites": ((221, "角色形象", "person"), (420, "物品图标", "item")),
    },
    "audited-2026-10-25": {
        "saveDepth": 23,
        "hashes": {
            "data.json": "f6222404d982bdd5cb57d8e206b70ccf5e7c3f7b0f18891ca0d387c68ab92001",
            "scripts/c3runtime.js": "11b5942035ae1fc3185abb5550f97abbe70e1451679698da2e169e9c7ee3753c",
            "jz.json": "3d5da9a57d55f8ec616bf514571bbd06e688ee456d3b3c348a390322de3e3b25",
            "ss.json": "4350cb979c4026ce0d8dc25e7807fde3d29a1d42fbd2e499673614a4f67e1bc7",
            "lw.json": "7a4f5a5bfd5967f3a74e36aa9a0c2cda132619b2908bdc39b1406da4b346bde6",
            "wp.json": "c7b65aad4dc3ee9458221ca8fc16d91a8e6ad7e872686a820e97303de7bb6558",
            "zy.json": "97d59a029aefbfbba315c9cec7b83a168571def79282eec2be9edbfeb3759188",
        },
        "sprites": ((224, "角色形象", "person"), (423, "物品图标", "item")),
    },
    "audited-2026-10-25-r2": {
        "saveDepth": 23,
        "hashes": {
            "data.json": "9e5697adeb11a0c1daf40be6086b56f58755562f3560ad1772d771445e167ed2",
            "scripts/c3runtime.js": "2546ca0823f858f3ce2a2e1dd013ed70f7e4d330e8d6c33d7ce75fb2a75d6936",
            "jz.json": "3d5da9a57d55f8ec616bf514571bbd06e688ee456d3b3c348a390322de3e3b25",
            "ss.json": "4350cb979c4026ce0d8dc25e7807fde3d29a1d42fbd2e499673614a4f67e1bc7",
            "lw.json": "7a4f5a5bfd5967f3a74e36aa9a0c2cda132619b2908bdc39b1406da4b346bde6",
            "wp.json": "c7b65aad4dc3ee9458221ca8fc16d91a8e6ad7e872686a820e97303de7bb6558",
            "zy.json": "97d59a029aefbfbba315c9cec7b83a168571def79282eec2be9edbfeb3759188",
        },
        "sprites": ((224, "角色形象", "person"), (423, "物品图标", "item")),
    },
}
QUALITY_NAMES = {1: "凡物", 2: "优秀", 3: "精致", 4: "绝世", 5: "传说", 6: "神话"}
SAVE_NAME = re.compile(r"ea([1-9][0-9]{0,2})save\.dat\Z")
SAVE_ID = re.compile(r"(save|savedir):(ea[1-9][0-9]{0,2}save\.dat)\Z")
MAX_SAVE_BYTES = 32 * 1024 * 1024
MAX_MEMBER_BYTES = 24 * 1024 * 1024


def _json(raw: bytes, label: str) -> Any:
    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite number")
    try:
        return json.loads(raw.decode("utf-8-sig"), parse_constant=reject_constant)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ReaderError(f"{label}不是完整的游戏 JSON 数据，请等保存结束后重试。") from error


def _root(game_dir: str | Path) -> Path:
    path = Path(game_dir)
    if not path.is_absolute() or str(path).startswith(("\\\\", "//")):
        raise ReaderError("请使用本机游戏安装目录的绝对路径。")
    try:
        root = path.resolve(strict=True)
    except OSError as error:
        raise ReaderError("找不到游戏安装目录。") from error
    if not root.is_dir():
        raise ReaderError("游戏安装目录无效。")
    _inside(root, "package.nw")
    return root


def _inside(root: Path, *parts: str) -> Path:
    # Names come from fixed constants or SAVE_NAME, never a caller-supplied path.
    path = root.joinpath(*parts)
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ReaderError("找不到所选游戏文件，请刷新存档列表。") from error
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ReaderError("游戏数据路径越过安装目录，已拒绝读取。")
    if parts and parts[0] in ("save", "savedir"):
        # Also reject a redirected save directory within the installation root.
        expected_parent = root / parts[0]
        if resolved.parent != expected_parent:
            raise ReaderError("存档目录不能通过链接跳转到其他目录。")
    return resolved


def _read_stable(path: Path, limit: int) -> tuple[bytes, float]:
    try:
        before = path.stat()
        if before.st_size > limit:
            raise ReaderError("游戏数据文件超出支持的大小。")
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
        after = path.stat()
    except OSError as error:
        raise ReaderError("无法读取游戏文件，请检查权限并等保存结束后重试。") from error
    if len(raw) > limit or len(raw) != before.st_size:
        raise ReaderError("读取期间存档发生变化，请等游戏保存结束后重试。")
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
        after.st_size, after.st_mtime_ns, after.st_ino
    ):
        raise ReaderError("读取期间存档发生变化，请等游戏保存结束后重试。")
    return raw, after.st_mtime


def _array(value: Any, label: str, *, save: bool = False, save_depth: int = 22) -> list:
    if not isinstance(value, dict) or value.get("c2array") is not True:
        raise ReaderError(f"{label}不是支持的 Construct 数组格式。")
    size = value.get("size")
    if (not isinstance(size, list) or len(size) != 3
            or any(type(n) is not int or n <= 0 for n in size)):
        raise ReaderError(f"{label}数组尺寸无效。")
    if size[0] > 5001 or size[1] > 100 or size[2] > 30:
        raise ReaderError(f"{label}数组尺寸超出支持范围。")
    if save and (size[0] < 201 or size[1] < 51 or size[2] != save_depth):
        raise ReaderError("存档版本不受支持，无法可靠识别城市和仓库。")
    data = value.get("data")
    if not isinstance(data, list) or len(data) != size[0]:
        raise ReaderError(f"{label}数组内容不完整。")
    for row in data:
        if not isinstance(row, list) or len(row) != size[1]:
            raise ReaderError(f"{label}数组内容不完整。")
        for cell in row:
            if not isinstance(cell, list) or len(cell) != size[2]:
                raise ReaderError(f"{label}数组内容不完整。")
            if any(type(v) not in (str, int, float, type(None)) or (
                isinstance(v, float) and not math.isfinite(v)
            ) for v in cell):
                raise ReaderError(f"{label}包含未知字段类型。")
    return data


def _number(value: Any, label: str, *, integer: bool = False, minimum: float = 0) -> int | float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
        raise ReaderError(f"{label}的数值无效。")
    if integer and value != int(value):
        raise ReaderError(f"{label}必须是整数。")
    return int(value) if integer else value


def _iso(seconds: float) -> str:
    try:
        return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, OSError, OverflowError) as error:
        raise ReaderError("存档保存时间无效。") from error


def _save_entries(root: Path) -> list[dict[str, Any]]:
    entries = []
    for dirname in ("save", "savedir"):
        directory = root / dirname
        if not directory.exists():
            continue
        if directory.resolve() != directory or not directory.is_dir():
            raise ReaderError("存档目录不能通过链接跳转到其他目录。")
        info = None
        if (directory / "saveinfo.dat").exists():
            raw, _ = _read_stable(_inside(root, dirname, "saveinfo.dat"), 1024 * 1024)
            info = _array(_json(raw, "存档索引"), "存档索引")
        for path in directory.iterdir():
            match = SAVE_NAME.fullmatch(path.name)
            if not match:
                continue
            path = _inside(root, dirname, path.name)
            slot = int(match[1])
            timestamp = path.stat().st_mtime
            nickname = ""
            game_time = ""
            date_source = "file"
            if info is not None and slot < len(info) and len(info[slot]) > 7:
                row = info[slot]
                if row[0][0] != 1:
                    continue
                timestamp = _number(row[7][0], "存档索引时间") / 1000
                nickname = str(row[1][0])
                game_time = str(row[5][0])
                date_source = "game-index"
            entries.append({
                "id": f"{dirname}:{path.name}",
                "name": f"存档 {slot}" + (f" · {nickname}" if nickname else "") + f" · {dirname}",
                "savedAt": _iso(timestamp), "gameTime": game_time,
                "_time": timestamp, "_dateSource": date_source,
            })
    if not entries:
        raise ReaderError("没有找到可读取的存档，请先在游戏内保存到存档槽。")
    # save is the active working copy; savedir is the game's synchronized copy.
    entries.sort(key=lambda item: (item["_time"], item["id"].startswith("save:"), item["id"]), reverse=True)
    return entries


def discover_saves(game_dir: str | Path) -> list[dict[str, Any]]:
    """List the two game-owned save directories; never recurse or follow links."""
    return [{k: v for k, v in item.items() if not k.startswith("_")}
            for item in _save_entries(_root(game_dir))]


def _audited_package(package: ZipFile) -> tuple[str, dict[str, bytes]]:
    """Match one whole audited revision; never combine individually known files."""
    manifest = package.getinfo("package.json")
    if manifest.file_size > 65536:
        raise ReaderError("游戏包标识异常。")
    identity = _json(package.read(manifest), "游戏标识")
    if not isinstance(identity, dict) or identity.get("name") != "com.ujwan.tfszmnq":
        raise ReaderError("所选目录不是塔防山贼模拟器。")
    candidates = list(RULE_PROFILES)
    members = {}
    names = package.namelist()
    for member in RULE_HASHES:
        if names.count(member) != 1:
            raise ReaderError("游戏规则文件缺失或重复，无法确认版本。")
        if package.getinfo(member).file_size > MAX_MEMBER_BYTES:
            raise ReaderError("游戏规则文件超出支持大小。")
        raw = package.read(member)
        digest = sha256(raw).hexdigest()
        candidates = [ident for ident in candidates if RULE_PROFILES[ident]["hashes"][member] == digest]
        if not candidates:
            raise ReaderError("游戏版本的完整规则组合尚未核实，已停止读取；需要适配当前版本后再使用。")
        members[member] = raw
    return candidates[0], members


def pedestal_qi_units(qi: int | float, tier: int, bonus: float = 0.15) -> int:
    """Native nonnegative Math.round result, in tenths of qi."""
    # Keep binary arithmetic and the game's operation order. Decimal rounding
    # or applying the factor 10 last changes some half-unit boundaries.
    value = 10 * float(qi) * (1 + tier * bonus)
    return math.floor(value + 0.5)


def _tables(root: Path) -> dict[str, Any]:
    try:
        with ZipFile(_inside(root, "package.nw")) as package:
            profile, members = _audited_package(package)
            tables: dict[str, Any] = {"_ruleProfile": profile}
            for member, raw in members.items():
                if member in ("jz.json", "ss.json", "lw.json", "wp.json", "zy.json"):
                    data = _array(_json(raw, member), member)
                    tables[member] = [[cell[0] for cell in row] for row in data]
            return tables
    except (BadZipFile, KeyError, OSError, AttributeError) as error:
        raise ReaderError("游戏 package.nw 不完整或不受支持。") from error


def _template(table: list, index: Any, label: str) -> list:
    index = _number(index, label, integer=True, minimum=1)
    if index >= len(table) or table[index][0] != index:
        raise ReaderError(f"存档中的{label}不在当前游戏规则表中。")
    return table[index]


def _footprint(entity: dict) -> set[tuple[int, int]]:
    left = entity["x"] - (entity["width"] - 1) // 2
    top = entity["y"] - (entity["height"] - 1) // 2
    return {(x, y) for x in range(left, left + entity["width"])
            for y in range(top, top + entity["height"])}


def _person_details(row: list, slot: int, data: list, tables: dict[str, Any]) -> dict[str, Any]:
    """Optional display fields never alter the placement/qi authority."""
    details: dict[str, Any] = {}
    notices = []
    if isinstance(row[5], str) and row[5]:
        details.update({"appearance": row[5], "iconKey": f"person:{row[5]}"})
    professions = tables.get("zy.json")
    if professions is not None:
        try:
            profession = _template(professions, row[6], "职业模板")
            details["profession"] = str(profession[1])
            details["professionDescription"] = str(profession[5])
        except ReaderError:
            notices.append("当前职业模板无法识别，未推测职业名称。")
    equipment = []
    templates = tables.get("wp.json")
    if templates is not None:
        for field, label in enumerate(("武器", "手套", "腰带", "饰品", "内丹", "宝物"), 16):
            index = row[field]
            if not index:
                continue
            try:
                index = _number(index, "穿戴装备索引", integer=True, minimum=1)
                if index >= len(data):
                    raise ReaderError("穿戴装备索引越界。")
                equipped = [cell[9] for cell in data[index]]
                if not equipped[0] or equipped[7] != slot:
                    raise ReaderError("穿戴装备反向绑定不一致。")
                template = _template(templates, equipped[1], "装备模板")
                if template[6] != field - 15:
                    raise ReaderError("穿戴装备类型与槽位不一致。")
                item = {"slot": label, "name": str(template[1]), "type": str(template[7]),
                        "templateId": equipped[1], "iconKey": f"item:{template[2]}",
                        "level": _number(equipped[6], "装备等级", integer=True),
                        "quality": _number(equipped[5], "装备品质", integer=True)}
                if item["quality"] in QUALITY_NAMES:
                    item["qualityName"] = QUALITY_NAMES[item["quality"]]
                equipment.append(item)
                if field == 16:
                    details["weapon"] = dict(item)
            except ReaderError:
                notices.append(f"{label}记录无法可靠解析，未显示该槽信息。")
        details["equipment"] = equipment
    if notices:
        details["detailWarnings"] = notices
    return details


def _normalize(data: list, tables: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    if data[0][0][1] != "szmnqea":
        raise ReaderError("存档缺少游戏格式标识。")
    level = _number(data[2][1][0], "山寨等级", integer=True, minimum=1)
    if level > 19:
        raise ReaderError("山寨等级超出已核实的地图范围。")
    bounds = {"minX": 21 - ((level + 1) // 2) * 2,
              "maxX": 30 + math.ceil((level + 1) / 2) * 2,
              "minY": 1, "maxY": 10 + 2 * (level + 1)}
    entities: list[dict[str, Any]] = []
    buildings: dict[int, dict[str, Any]] = {}
    warnings = []
    def record(slot: int, layer: int) -> list:
        return [cell[layer] for cell in data[slot]]
    def city(x: Any, y: Any) -> bool:
        return (type(x) in (int, float) and type(y) in (int, float)
                and 10000 < x <= 10050 and 10000 < y <= 10050)
    for slot in range(1, len(data)):
        row = record(slot, 1)
        if not row[0]:
            continue
        _number(row[0], "建筑编号", integer=True, minimum=1)
        _number(row[2], "建筑 X", integer=True)
        _number(row[3], "建筑 Y", integer=True)
        if not city(row[2], row[3]):
            continue
        template = _template(tables["jz.json"], row[1], "建筑模板")
        width = _number(template[8], "建筑宽度", integer=True, minimum=1)
        height = _number(template[9], "建筑高度", integer=True, minimum=1)
        if width not in (1, 2, 3) or width != height:
            raise ReaderError("遇到尚未核实占地规则的建筑。")
        entity = {"id": f"building:{slot}", "kind": "platform" if row[1] == 66 else "pedestal" if row[1] == 67 else "building",
                  "name": str(template[1]), "x": int(_number(row[2], "建筑 X", integer=True)) - 10000,
                  "y": int(_number(row[3], "建筑 Y", integer=True)) - 10000,
                  "width": width, "height": height, "movable": row[1] != 66,
                  "tier": _number(row[5], "建筑等阶", integer=True), "templateId": row[1],
                  "occupantIds": [], "blocksPlacement": True}
        if row[1] == 67 and row[9]:
            entity["itemId"] = f"placed:{_number(row[9], '灵物索引', integer=True, minimum=1)}"
        if row[7]:
            # The native move function rejects this occupancy marker. Its mapping
            # to a character isn't inferred from a cached job or nearby position.
            entity["movable"] = False
            entity["lockedReason"] = "建筑有占用角色标记，需要在游戏内先移除占用角色再保存。"
        buildings[slot] = entity
        entities.append(entity)
    for x in range(bounds["minX"], bounds["maxX"] + 1):
        for y in range(bounds["minY"], bounds["maxY"] + 1):
            for offset, kind in ((0, "terrain"), (50, "resource")):
                index = data[x + offset][y][6]
                if index:
                    template = _template(tables["jz.json"], index, "地块模板")
                    entities.append({"id": f"{kind}:{x}:{y}", "kind": "resource", "name": str(template[1]),
                                     "x": x, "y": y, "width": 1, "height": 1,
                                     "movable": False, "blocksPlacement": True,
                                     "lockedReason": "本版本保留地图原生资源和特殊地形。"})
            slot = data[x + 150][y][6]
            if not slot:
                continue
            slot = _number(slot, "设施索引", integer=True, minimum=1)
            if slot >= len(data):
                raise ReaderError("设施索引超出存档范围。")
            row = record(slot, 21)
            if not row[0]:
                raise ReaderError("设施地图与设施记录不一致。")
            template = _template(tables["ss.json"], row[1], "设施模板")
            parent = buildings.get(row[2])
            if parent is None:
                raise ReaderError("主城设施缺少所属建筑，无法核实搬迁范围。")
            parent["movable"] = False
            parent["lockedReason"] = "保留所属设施的有效范围，当前方案固定主体建筑。"
            width = _number(template[8], "设施宽度", integer=True, minimum=1)
            height = _number(template[9], "设施高度", integer=True, minimum=1)
            movable = width == height == 1 and parent["width"] in (2, 3) and template[3] != "城镇"
            extra = 1 if parent["width"] == 3 else 0
            allowed = {"minX": max(bounds["minX"], parent["x"] - 3 - extra),
                       "maxX": min(bounds["maxX"], parent["x"] + 4),
                       "minY": max(bounds["minY"], parent["y"] - 3 - extra),
                       "maxY": min(bounds["maxY"], parent["y"] + 4)}
            entities.append({"id": f"facility:{slot}", "kind": "facility", "name": str(template[1]),
                             "x": x, "y": y, "width": width, "height": height,
                             "movable": movable, "parentId": parent["id"], "allowedBounds": allowed,
                             "templateId": row[1], "blocksPlacement": True})
    for slot in range(1, len(data)):
        row = record(slot, 2)
        if not row[0]:
            continue
        _number(row[0], "人物编号", integer=True, minimum=1)
        _number(row[7], "人物 X", integer=True)
        _number(row[8], "人物 Y", integer=True)
        if not city(row[7], row[8]):
            continue
        entities.append({"id": f"person:{slot}", "kind": "person", "name": str(row[1]),
                         "x": int(_number(row[7], "人物 X", integer=True)) - 10000,
                         "y": int(_number(row[8], "人物 Y", integer=True)) - 10000,
                         "width": 1, "height": 1, "movable": True, "blocksPlacement": True,
                         "job": str(row[9]), **_person_details(row, slot, data, tables)})
    items = []
    warehouse_free_slots = 0
    for layer, location, prefix in ((18, "warehouse", "warehouse"), (19, "placed", "placed")):
        for slot in range(1, len(data)):
            row = record(slot, layer)
            if not row[0]:
                if layer == 18:
                    if row[1] != 0:
                        raise ReaderError("仓库灵物编号与模板记录不一致，请重新保存后再分析。")
                    warehouse_free_slots += 1
                continue
            template = _template(tables["lw.json"], row[1], "灵物模板")
            item = {"id": f"{prefix}:{slot}", "name": str(template[1]),
                    "iconKey": f"item:{template[2]}",
                    "qi": _number(row[10], "灵物基础灵气"), "location": location,
                    "attributes": {name: _number(row[field], f"灵物{name}灵气")
                                   for field, name in enumerate(("金", "木", "水", "火", "土"), 5)},
                    "originalId": _number(row[0], "灵物编号", integer=True, minimum=1),
                    "level": _number(row[2], "灵物等级", integer=True),
                    "quality": _number(row[4], "灵物品质", integer=True)}
            if item["quality"] in QUALITY_NAMES:
                item["qualityName"] = QUALITY_NAMES[item["quality"]]
            if location == "placed":
                parent = buildings.get(row[12])
                if parent is None or parent["kind"] != "pedestal" or parent.get("itemId") != item["id"]:
                    raise ReaderError("已摆放灵物与置物台的绑定不一致，请重新保存后再分析。")
                item["pedestalId"] = parent["id"]
            items.append(item)
    item_by_id = {item["id"]: item for item in items}
    for entity in entities:
        if not entity.get("itemId"):
            continue
        item = item_by_id.get(entity["itemId"])
        if item is None:
            raise ReaderError("置物台引用了不存在的灵物。")
        if item["location"] != "placed" or item.get("pedestalId") != entity["id"]:
            raise ReaderError("置物台与已摆放灵物的绑定不一致，不能重复引用同一灵物；请重新保存后再分析。")
    if len({e["id"] for e in entities}) != len(entities):
        raise ReaderError("地图重复引用同一个设施，无法可靠分析。")
    if not any(e["kind"] == "platform" for e in entities):
        warnings.append("这个存档的主城没有渡劫台。")
    warnings.append("只读取所选存档保存时的城市；游戏中尚未保存的移动不会出现在图上。")
    warnings.append("固定渡劫台与有附属设施的主体建筑；设施可在原主体有效范围内搬迁，原生资源和特殊地形保持原位。")
    entity_by_id = {entity["id"]: entity for entity in entities}
    platforms = [entity for entity in entities if entity["kind"] == "platform"]
    platform_units = {platform["id"]: 0 for platform in platforms}
    for item in items:
        item["contributions"] = []
        if item["location"] != "placed":
            continue
        pad = entity_by_id[item["pedestalId"]]
        units = pedestal_qi_units(item["qi"], pad["tier"])
        item["currentQi"] = units / 10
        for platform in platforms:
            if abs(pad["x"] - platform["x"]) + abs(pad["y"] - platform["y"]) <= 3:
                item["contributions"].append({"platformId": platform["id"], "qi": units / 10})
                platform_units[platform["id"]] += units
    for platform in platforms:
        platform["currentQi"] = platform_units[platform["id"]] / 10
    profile = tables.get("_ruleProfile", "audited-2026-10-18")
    return {"schemaVersion": 1, "source": source, "bounds": bounds, "entities": entities, "items": items,
            "rules": {"auraRadius": 3, "auraMetric": "manhattan", "pedestalTierBonus": 0.15,
                      "qiRounding": "js-one-decimal", "allowItemReassignment": True,
                      "coordinateAnchor": "game-grid", "boundsInclusive": True,
                      "cityLevel": level, "mapSize": 50, "facilityRange": 3,
                      "warehouseCapacity": len(data) - 1,
                      "warehouseFreeSlots": warehouse_free_slots,
                      "ruleProfile": profile,
                      "ruleHashes": dict(RULE_PROFILES[profile]["hashes"])}, "warnings": warnings}


def load_latest(game_dir: str | Path, save_id: str | None = None) -> dict[str, Any]:
    """Read the newest indexed save, or an opaque ID returned by discover_saves."""
    if save_id is not None and (not isinstance(save_id, str) or not SAVE_ID.fullmatch(save_id)):
        raise ReaderError("存档编号无效，请从存档列表选择。")
    root = _root(game_dir)
    entries = _save_entries(root)
    entry = entries[0] if save_id is None else next((s for s in entries if s["id"] == save_id), None)
    if entry is None:
        raise ReaderError("所选存档已不存在，请刷新存档列表。")
    dirname, filename = entry["id"].split(":")
    tables = _tables(root)
    raw, _mtime = _read_stable(_inside(root, dirname, filename), MAX_SAVE_BYTES)
    profile = tables.get("_ruleProfile", "audited-2026-10-18")
    data = _array(_json(raw, "存档"), "存档", save=True,
                  save_depth=RULE_PROFILES[profile]["saveDepth"])
    source = {k: v for k, v in entry.items() if not k.startswith("_")}
    source.update({"sha256": sha256(raw).hexdigest(), "savedAtSource": entry["_dateSource"]})
    snapshot = _normalize(data, tables, source)
    if entry["_dateSource"] == "file":
        snapshot["warnings"].append("该存档缺少保存时间索引，最近存档排序改用文件修改时间。")
    return snapshot
