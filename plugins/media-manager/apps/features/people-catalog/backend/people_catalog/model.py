"""Pure, bounded validation for independent people and reference-only relations."""
from __future__ import annotations

from copy import deepcopy
import json
import math
import re
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from .taxonomy import validate as validate_taxonomy

ENTITY_ID = re.compile(r"person_[0-9a-f]{32}\Z")
MAX_ENTITY_BYTES = 512 * 1024
MAX_REFERENCES = 1000
AVATAR_ASSET_ID = re.compile(r"[0-9a-f]{64}\.(?:jpg|png|gif|webp)\Z")


def text(value, label, maximum=1000, *, required=False):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ValueError(f"{label} 必须为{'非空' if required else ''}字符串，最多 {maximum} 字符")
    return value


def entity_id(value):
    if not isinstance(value, str) or not ENTITY_ID.fullmatch(value):
        raise ValueError("人物标识必须为 person_ 加 32 位十六进制，不能使用路径")
    return value


def validate_avatar_reference(value):
    """Only local, content-addressed image identifiers can be browser sources.

    The URL is provenance, never a browser URL or a fetch instruction. Provider
    adapters apply their stricter host/path allowlist before any network access.
    """
    if value == {}:
        return {}
    if not isinstance(value, dict) or set(value) != {"asset_id", "source_url"}:
        raise ValueError("人物图片引用只能包含 asset_id 与 source_url")
    filename = value["asset_id"]
    if not isinstance(filename, str) or not AVATAR_ASSET_ID.fullmatch(filename):
        raise ValueError("人物图片资源必须为内容校验值加安全图片扩展名，不能使用路径或网址")
    url = text(value["source_url"], "人物图片来源网址", 2048, required=True)
    if url == "local:" + filename:
        return deepcopy(value)
    try:
        parsed = urlsplit(url)
        valid = (not any(ord(char) < 33 for char in url) and parsed.scheme == "https" and parsed.hostname
                 and parsed.username is None and parsed.password is None and not parsed.fragment
                 and parsed.port in {None, 443})
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("人物图片来源必须为不含身份信息的 HTTPS 网址或与图片标识一致的本地上传引用")
    return deepcopy(value)


def source_identity(provider, kind, external_id):
    text(provider, "来源名称", 64, required=True)
    if kind not in {"character", "person"}:
        raise ValueError("人物类型必须为 character 或 person")
    text(str(external_id), "来源人物 ID", 200, required=True)
    key = json.dumps(["nimda:people", provider, kind, str(external_id)], ensure_ascii=False, separators=(",", ":"))
    return "person_" + uuid5(NAMESPACE_URL, key).hex


def scope(value):
    if not isinstance(value, dict) or not {"provider", "subject_id"}.issubset(value) or set(value) - {"provider", "subject_id", "season_number", "episode_range"}:
        raise ValueError("人物关系来源必须包含 provider 与 subject_id")
    result = {"provider": text(value["provider"], "关系来源", 64, required=True),
              "subject_id": text(value["subject_id"], "关系作品 ID", 200, required=True)}
    if "season_number" in value:
        if value["provider"] != "tmdb" or type(value["season_number"]) is not int or not 0 <= value["season_number"] <= 999:
            raise ValueError("人物来源季编号必须为 TMDB 的 0～999 整数")
        result["season_number"] = value["season_number"]
    if "episode_range" in value:
        interval = value["episode_range"]
        if ("season_number" not in result or not isinstance(interval, dict) or set(interval) != {"start", "end"} or
                any(type(number) not in {int, float} or not math.isfinite(number) or not 0 <= number <= 99999 for number in interval.values()) or interval["start"] > interval["end"]):
            raise ValueError("人物来源章节范围无效")
        result["episode_range"] = deepcopy(interval)
    return result


def validate_people_refs(value):
    if not isinstance(value, list) or len(value) > MAX_REFERENCES:
        raise ValueError(f"people_refs 必须为最多 {MAX_REFERENCES} 项的数组")
    seen = set()
    result = []
    for row in value:
        if not isinstance(row, dict) or set(row) - {"id", "type", "role", "source", "eps", "character_name", "character_name_cn", "character_id", "credit_id", "source_values", "qualifier", "role_group"}:
            raise ValueError("人物引用包含不支持的字段")
        entity_id(row.get("id"))
        if row.get("type") not in {"character", "person"}:
            raise ValueError("人物引用 type 必须为 character 或 person")
        item = {"id": row["id"], "type": row["type"], "role": text(row.get("role", ""), "人物职责", 200)}
        if "source" in row:
            item["source"] = scope(row["source"])
        if "eps" in row:
            item["eps"] = text(row["eps"], "参与章节", 2000)
        if "character_name" in row:
            if row["type"] != "person":
                raise ValueError("饰演角色名只能关联现实演员")
            item["character_name"] = text(row["character_name"], "饰演角色", 1000)
        if "character_name_cn" in row:
            if "character_name" not in item:
                raise ValueError("饰演角色中文名必须与现实演员的原角色名一起保存")
            item["character_name_cn"] = text(row["character_name_cn"], "饰演角色中文名", 1000)
        if "character_id" in row:
            if row["type"] != "person" or "character_name" not in item:
                raise ValueError("独立饰演角色 ID 必须与现实演员的原角色名一起保存")
            item["character_id"] = entity_id(row["character_id"])
            if item["character_id"] == item["id"]:
                raise ValueError("演员与饰演角色不能使用同一实体 ID")
        if "credit_id" in row:
            if row["type"] != "person" or item.get("source", {}).get("provider") != "tmdb":
                raise ValueError("credit_id 只能用于 TMDB 现实人物关联")
            item["credit_id"] = text(row["credit_id"], "TMDB 演职员关系 ID", 200, required=True)
        if "source_values" in row:
            values = row["source_values"]
            if "character_name" not in item or not isinstance(values, dict) or set(values) - {"character_name", "character_name_cn"}:
                raise ValueError("饰演角色来源字段快照无效")
            item["source_values"] = {key: text(value, "饰演角色来源字段", 1000) for key, value in values.items()}
        if "qualifier" in row or "role_group" in row:
            from .portrayal import validate_qualifier, validate_role_group
            if row["type"] != "person" or "character_name" not in item or "source" not in item:
                raise ValueError("饰演阶段与角色分组必须关联有来源的演员角色关系")
            if "qualifier" in row:
                item["qualifier"] = validate_qualifier(row["qualifier"])
            if "role_group" in row:
                item["role_group"] = validate_role_group(row["role_group"])
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if key in seen:
            raise ValueError("人物引用重复")
        seen.add(key)
        result.append(item)
    return result


def validate_entity(value):
    if not isinstance(value, dict):
        raise ValueError("人物记录必须为对象")
    # YAML aliases can represent a huge/cyclic graph in a tiny input file.
    # Bound expansion before JSON serialization or deepcopy traverses it.
    pending = [(value, 0)]
    visited = 0
    while pending:
        node, depth = pending.pop()
        visited += 1
        if visited > 20000 or depth > 32:
            raise ValueError("人物记录嵌套过深、循环引用或字段数超过安全限制")
        if isinstance(node, dict):
            if any(not isinstance(key, str) for key in node):
                raise ValueError("人物记录对象键必须为字符串")
            pending.extend((entry, depth + 1) for entry in node.values())
        elif isinstance(node, list):
            pending.extend((entry, depth + 1) for entry in node)
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, RecursionError):
        raise ValueError("人物记录必须为无循环的有限 JSON 兼容数据") from None
    if len(encoded) > MAX_ENTITY_BYTES:
        raise ValueError("人物记录超过安全大小限制")
    entity_id(value.get("id"))
    if value.get("schema_version") != 1 or type(value.get("schema_version")) is not int:
        raise ValueError("人物记录 schema_version 必须为 1")
    if value.get("type") not in {"character", "person"}:
        raise ValueError("人物记录 type 必须为 character 或 person")
    if "taxonomy" in value:
        validate_taxonomy(value["taxonomy"])
    if "localized" in value:
        from .localization import validate as validate_localized
        validate_localized(value["localized"])
    if "profile_variants" in value or "profile_preferences" in value:
        from .profile_variants import validate_exports
        validate_exports(value)
    text(value.get("name"), "人物名称", required=True)
    if "avatar" in value:
        validate_avatar_reference(value["avatar"])
    for key, maximum in (("name_cn", 1000), ("summary", 100000), ("kind", 100)):
        if key in value:
            text(value[key], key, maximum)
    for key in ("aliases", "career"):
        rows = value.get(key, [])
        if not isinstance(rows, list) or len(rows) > 200:
            raise ValueError(f"{key} 必须为最多 200 项数组")
        for row in rows:
            text(row, key, 1000)
    infobox = value.get("infobox", [])
    if not isinstance(infobox, list) or len(infobox) > 200 or any(not isinstance(row, dict) for row in infobox):
        raise ValueError("infobox 必须为最多 200 项对象数组")
    sources = value.get("source_refs", [])
    if not isinstance(sources, list) or len(sources) > 100:
        raise ValueError("人物 source_refs 格式无效")
    if "merged_into" in value:
        target = entity_id(value["merged_into"])
        if target == value["id"] or sources:
            raise ValueError("已合并人物不能指向自身或继续持有来源身份")
    for row in sources:
        if not isinstance(row, dict):
            raise ValueError("人物来源引用必须为对象")
        text(row.get("provider"), "来源名称", 64, required=True)
        text(row.get("external_id"), "来源 ID", 200, required=True)
        if row.get("entity_type") != value["type"]:
            raise ValueError("人物来源实体类型不一致")
    actors = value.get("voice_actors", [])
    if not isinstance(actors, list) or len(actors) > 2000:
        raise ValueError("声优关系必须为最多 2000 项数组")
    if value["type"] == "person" and actors:
        raise ValueError("只有虚拟角色可以设置声优关系")
    for row in actors:
        if not isinstance(row, dict) or set(row) - {"person_id", "scope", "role"}:
            raise ValueError("声优关系格式无效")
        entity_id(row.get("person_id"))
        scope(row.get("scope"))
        text(row.get("role", "CV"), "声优关系职责", 200)
    imported = value.get("source_values", {})
    if not isinstance(imported, dict):
        raise ValueError("人物来源字段快照必须为对象")
    origins = value.get("role_origins", [])
    if not isinstance(origins, list) or len(origins) > 1000 or (origins and value["type"] != "character"):
        raise ValueError("角色来源证据只允许角色实体保存，最多 1000 项")
    seen_origins = set()
    for origin in origins:
        if (not isinstance(origin, dict) or set(origin) - {"work_key", "actor_id", "scope", "external_id", "original_name", "credit_id", "qualifier", "role_group"}
                or not {"work_key", "actor_id", "scope", "external_id", "original_name"}.issubset(origin)):
            raise ValueError("角色来源证据字段无效")
        text(origin["work_key"], "角色来源作品身份", 300, required=True)
        entity_id(origin["actor_id"])
        origin_scope = scope(origin["scope"])
        if origin_scope["provider"] not in {"tmdb", "douban", "wikipedia"}:
            raise ValueError("演员饰演角色来源只支持 TMDB、维基百科或豆瓣")
        text(origin["external_id"], "角色来源身份", 200, required=True)
        text(origin["original_name"], "角色来源原名", 1000, required=True)
        if "credit_id" in origin:
            text(origin["credit_id"], "角色演职员关系身份", 200, required=True)
        if "qualifier" in origin or "role_group" in origin:
            from .portrayal import validate_qualifier, validate_role_group
            if "qualifier" in origin:
                validate_qualifier(origin["qualifier"])
            if "role_group" in origin:
                validate_role_group(origin["role_group"])
        key = json.dumps(origin, ensure_ascii=False, sort_keys=True)
        if key in seen_origins:
            raise ValueError("角色来源证据不能重复")
        seen_origins.add(key)
    return deepcopy(value)
