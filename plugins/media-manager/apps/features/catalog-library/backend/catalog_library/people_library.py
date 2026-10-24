"""Independent, preview-first people library with per-entity CAS and history.

Normal browsing, manual edits and clear/apply are offline. Only source_preview
performs explicit provider reads. Work links, voice actor links and source
ownership are never cleared by metadata operations; IDs and types are stable.
"""
from __future__ import annotations

from copy import deepcopy
from collections import OrderedDict
from datetime import date, datetime, timezone
import hashlib
from itertools import islice
import time
import threading
import unicodedata

from people_catalog.model import entity_id, validate_entity, validate_people_refs
from people_catalog.repository import PeopleRepository, safe_path
from people_catalog.source_links import build_source_index
from people_catalog import taxonomy, localization, profile_variants
from ruamel.yaml.error import YAMLError
from work_catalog_yaml.operation_progress import operation_context, report_progress
from work_catalog_yaml.persistence import atomic_write_bytes
from work_catalog_yaml.yaml_io import load_yaml_string

from . import people_inventory, people, people_assets, providers, service, tmdb_people, tmdb_source, wikipedia_people, people_infobox
from .people_field_merge import adopted_chinese_mirror, field_merge, profile_field_merge

MAX_PEOPLE = 10000
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_PLAN_BYTES = 16 * 1024 * 1024
_ENTITY_CACHE = OrderedDict()
_ENTITY_CACHE_LOCK = threading.RLock()
_ENTITY_CACHE_BYTES = 0
_MAX_CACHE_BYTES = 32 * 1024 * 1024
_FIELDS = {
    "name": ("原名 / 名称", "text"), "name_cn": ("中文名", "text"),
    "aliases": ("别名", "list"), "kind": ("来源细分类别", "select"),
    "taxonomy": ("实体分类", "taxonomy"),
    "gender": ("性别", "select"), "birth_date": ("出生日期", "date-parts"),
    "career": ("职业", "list"), "summary": ("简介", "textarea"),
    "infobox": ("详细资料", "json"), "avatar": ("头像", "avatar"),
    "profile_preferences": ("名称与简介版本", "json"),
}
_CLEAR_GROUPS = [
    {"id": "basic", "label": "补充身份资料", "fields": ["name_cn", "aliases", "gender", "birth_date"]},
    {"id": "profile", "label": "职业与简介", "fields": ["career", "summary", "infobox"]},
    {"id": "avatar", "label": "头像引用（保留图片文件）", "fields": ["avatar"]},
]


def _field_definitions():
    result = dict(_FIELDS)
    for kind, field_type in (("name", "text"), ("summary", "textarea"), ("aliases", "list")):
        for language in localization.LANGUAGES:
            path = f"localized.{kind}.{language}"
            result[path] = (localization.label(path), field_type)
    return result


def _field_value(person, field):
    if people_infobox.is_field(field):
        return people_infobox.field_value(person, field)
    return localization.field_value(person, field) if localization.is_field(field) else deepcopy(person.get(field, _empty(field)))


def _write_field(person, field, value):
    if field == "profile_preferences":
        profile_variants.apply_preferences(person, value)
    elif people_infobox.is_field(field):
        people_infobox.set_field(person, field, value)
    elif localization.is_field(field):
        if "localized" not in person:
            person["localized"] = localization.project(person)
        localization.set_field(person, field, deepcopy(value))
    else:
        person[field] = deepcopy(value)


def _source_changes(incoming, target=None):
    unavailable = incoming.get("_unavailable_fields", [])
    localized = isinstance(incoming.get("localized"), dict)
    changes = {field: deepcopy(incoming[field]) for field in people._PROFILE_FIELDS
               if field in incoming and field not in unavailable and field in _FIELDS
               and not (localized and field in {"name", "name_cn", "summary", "aliases"})}
    if localized:
        changes.update({field: localization.field_value(incoming, field) for field in localization.field_paths(incoming)})
    return people_infobox.source_changes(changes, target)


def _hash(value):
    return hashlib.sha256(providers._json_bytes(value)).hexdigest()


def _cached_entity(repository, identity, data):
    """Key by full content, not mtime/size, so external edits are never stale."""
    global _ENTITY_CACHE_BYTES
    key = (str(repository.root), identity, hashlib.sha256(data).hexdigest())
    with _ENTITY_CACHE_LOCK:
        if key in _ENTITY_CACHE:
            _ENTITY_CACHE.move_to_end(key)
            return deepcopy(_ENTITY_CACHE[key][0])
    try:
        row = validate_entity(load_yaml_string(data.decode("utf-8")))
    except (ValueError, UnicodeError, RecursionError, YAMLError) as error:
        raise ValueError(f"人物记录无法读取（{identity}）：{error}") from error
    if row["id"] != identity:
        raise ValueError("人物文件名与记录 ID 不一致")
    weight = len(data) + len(providers._json_bytes(row))
    with _ENTITY_CACHE_LOCK:
        if key not in _ENTITY_CACHE:
            _ENTITY_CACHE[key] = (deepcopy(row), weight)
            _ENTITY_CACHE_BYTES += weight
        _ENTITY_CACHE.move_to_end(key)
        while len(_ENTITY_CACHE) > MAX_PEOPLE or _ENTITY_CACHE_BYTES > _MAX_CACHE_BYTES:
            _old, (_row, old_weight) = _ENTITY_CACHE.popitem(last=False)
            _ENTITY_CACHE_BYTES -= old_weight
    return row


def _read(repository, identity):
    identity = entity_id(identity)
    data = repository._bytes(identity)
    person = _cached_entity(repository, identity, data) if data is not None else None
    if not person or person.get("merged_into"):
        raise ValueError("人物不存在或已合并，请重新打开人员库")
    return person


def _read_state(repository, identity):
    identity = entity_id(identity)
    revision = repository.revision([identity])
    person = _read(repository, identity)
    if repository.revision([identity]) != revision:
        raise ValueError("人物资料读取期间已变化，请重新打开详情")
    return person, revision


def _inventory(repository):
    paths = list(islice(safe_path(repository.root / "db").glob("*.yaml"), MAX_PEOPLE + 1))
    if len(paths) > MAX_PEOPLE:
        raise ValueError("人员库记录数量超过安全扫描上限")
    report_progress(f"正在校验人员库当前文件内容：共 {len(paths)} 项；缓存命中仍会检查文件内容变化",
        completed=0, total=len(paths), unit="项", context={"stage": "人员库只读扫描", "action": "校验实时文件内容"})
    total, rows = 0, []
    for position, path in enumerate(sorted(paths), 1):
        identity = entity_id(path.stem)
        content = repository._bytes(identity)
        if content is None:
            raise ValueError("人员库扫描期间文件已变化，请刷新")
        total += len(content)
        if total > MAX_TOTAL_BYTES:
            raise ValueError("人员库资料超过安全扫描大小上限")
        row = _cached_entity(repository, identity, content)
        if row and not row.get("merged_into"):
            rows.append(row)
        if position % 200 == 0:
            report_progress(f"人员库校验进度：{position}/{len(paths)} 项",
                completed=position, total=len(paths), unit="项", context={"stage": "人员库只读扫描", "action": "解析或复用内容哈希缓存"})
    report_progress(f"人员库扫描完成：{len(rows)} 个有效人物与角色，已读取 {total // 1024} KiB 当前内容",
        completed=len(paths), total=len(paths), unit="项", context={"stage": "人员库只读扫描完成"})
    return rows


def _normal(value):
    return unicodedata.normalize("NFKC", value).casefold().strip()


def _projection(person, *, card=False):
    if card:
        result = {key: deepcopy(person.get(key, [] if key in {"aliases", "career"} else ""))
                  for key in ("id", "type", "kind", "name", "name_cn", "aliases", "career", "gender")}
        result["summary"] = person.get("summary", "")[:500]
        result["providers"] = sorted({row["provider"] for row in person.get("source_refs", [])})
    else:
        result = deepcopy(person)
        result["profile_preferences"] = profile_variants.effective_preferences(person)
    result["localized"] = localization.project(person)
    if card:
        result["localized"]["summary"] = {key: value[:500] for key, value in result["localized"].get("summary", {}).items()}
    result["avatar_url"] = people_assets.avatar_url(person.get("avatar"))
    if not card:
        result["avatar_image_source"] = deepcopy(person.get("field_sources", {}).get("avatar", {}).get("image_source", {}))
    result["taxonomy"] = taxonomy.project(person)
    result["taxonomy_label"] = taxonomy.label(person)
    result["taxonomy_origin"] = "stored" if "taxonomy" in person else "legacy" if result["taxonomy"]["category"] != "unknown" else "unresolved"
    return result


def _fields(person):
    result = []
    for field, (label, field_type) in _field_definitions().items():
        if field == "profile_preferences":
            continue
        if isinstance(person.get("localized"), dict) and field in {"name", "name_cn", "summary", "aliases"}:
            continue
        if field == "kind" or field in {"gender", "birth_date"} and taxonomy.project(person)["category"] != "person":
            continue
        row = {"field": field, "label": label, "type": field_type, "required": field == "name",
               "editable": field != "avatar", "value": _field_value(person, field)}
        if localization.is_field(field):
            row["group"], row["language"] = "localized", field.rsplit(".", 1)[-1]
        if field == "taxonomy":
            row["options"] = taxonomy.options()
        elif field == "gender":
            row["options"] = [{"value": value, "label": value or "未设置"} for value in ("", "男性", "女性", "非二元", "其他")]
        result.append(row)
    return result


def _version(person, repository, revision):
    return providers._sign({"kind": "people-library-version", "person_id": person["id"],
        "people_scope": people._root_scope(repository), "revision": revision,
        "expires": int(time.time()) + providers.PREVIEW_LIFETIME})


def _verify_version(body, repository):
    payload = providers._verify_token(body.get("version"))
    person, revision = _read_state(repository, body.get("person_id"))
    if (payload.get("kind") != "people-library-version" or payload.get("person_id") != person["id"]
            or payload.get("people_scope") != people._root_scope(repository)
            or payload.get("revision") != revision):
        raise ValueError("人物资料版本已变化或不属于当前人员库，请重新打开详情")
    return person, payload["revision"]


def _relations(identity, settings, repository):
    # Reuse the repair scan's bounded catalog preflight; no media traversal.
    report_progress("正在检查此人物的关联作品与声优关系；只读作品数据库，不扫描媒体目录",
        context={"stage": "人物关联只读扫描", "object": identity, "action": "校验作品数据库"})
    people_inventory._work_versions(settings)
    works = service._records(settings)
    if len(works) > people_inventory.MAX_WORKS:
        raise ValueError("人物关联作品数量超过安全扫描限制")
    # Only characters referenced by a work can contribute a work-scoped voice
    # actor relation. Reading every unrelated real-person profile here caused
    # a second full-library cold parse when opening a direct detail link.
    character_ids = set()
    for item in works:
        character_ids.update(ref["id"] for ref in validate_people_refs(item["record"].get("people_refs", []))
                             if ref["type"] == "character")
        if len(character_ids) > MAX_PEOPLE:
            raise ValueError("关联角色数量超过安全扫描上限")
    characters, total_bytes = {}, 0
    report_progress(f"正在读取作品实际引用的 {len(character_ids)} 个角色以核对声优关系",
        completed=0, total=len(character_ids), unit="个", context={"stage": "人物声优关系只读扫描", "object": identity})
    for position, character_id in enumerate(sorted(character_ids), 1):
        content = repository._bytes(character_id)
        if content is not None:
            total_bytes += len(content)
            if total_bytes > MAX_TOTAL_BYTES:
                raise ValueError("关联角色资料超过安全扫描大小限制")
            row = _cached_entity(repository, character_id, content)
            if row["type"] == "character" and not row.get("merged_into") and any(
                    actor["person_id"] == identity for actor in row.get("voice_actors", [])):
                characters[character_id] = row
        if position % 200 == 0:
            report_progress(f"角色声优核对进度：{position}/{len(character_ids)} 个",
                completed=position, total=len(character_ids), unit="个", context={"stage": "人物声优关系只读扫描", "object": identity})
    report_progress(f"角色声优核对完成：{len(character_ids)} 个作品关联角色，其中 {len(characters)} 个引用当前人物",
        completed=len(character_ids), total=len(character_ids), unit="个", context={"stage": "人物声优关系只读扫描完成", "object": identity})
    result = []
    report_progress(f"正在逐条核对 {len(works)} 部作品的关联关系", completed=0, total=len(works), unit="部",
        context={"stage": "人物关联只读扫描", "object": identity})
    for position, item in enumerate(works, 1):
        for ref in validate_people_refs(item["record"].get("people_refs", [])):
            if ref["id"] == identity:
                result.append({"ref": deepcopy(item["ref"]), "name": item.get("name", ""),
                               "relation": deepcopy(ref), "role": ref["role"], "kind": "direct"})
            if ref.get("character_id") == identity:
                actor = _read(repository, ref["id"])
                result.append({"ref": deepcopy(item["ref"]), "name": item.get("name", ""),
                               "relation": deepcopy(ref), "role": "饰演角色", "kind": "portrayed_character",
                               "actor": _projection(actor), "character_name": ref.get("character_name", "")})
            if ref["id"] in characters:
                character = characters[ref["id"]]
                for actor in character.get("voice_actors", []):
                    if actor["person_id"] == identity and actor["scope"] == ref.get("source"):
                        result.append({"ref": deepcopy(item["ref"]), "name": item.get("name", ""),
                            "role": actor.get("role", "CV"), "kind": "voice_actor", "character_id": character["id"],
                            "character_name": character["name"], "relation": deepcopy(actor)})
            if len(result) > 10000:
                raise ValueError("人物关联总数超过安全数量上限")
        if position % 200 == 0:
            report_progress(f"人物关联核对进度：{position}/{len(works)} 部作品，已找到 {len(result)} 条关系",
                completed=position, total=len(works), unit="部", context={"stage": "人物关联只读扫描", "object": identity})
    report_progress(f"人物关联扫描完成：已核对 {len(works)} 部作品，找到 {len(result)} 条关系",
        completed=len(works), total=len(works), unit="部", context={"stage": "人物关联只读扫描完成", "object": identity})
    return result


def browse(body=None, *, settings=None, people_root=None, data_root=None):
    # Browse filters are not provider mutations: an empty provider means all
    # sources, and existing custom provider names must remain searchable.
    body = {} if body is None else body
    if not isinstance(body, dict) or set(body) - {"query", "type", "kind", "category", "reality", "subtype", "provider", "page", "page_size"}:
        raise ValueError("人员库查询必须为仅包含有效筛选和分页参数的对象")
    query = providers._text(body.get("query", ""), 1000)
    kind, subtype, provider = body.get("type", ""), body.get("kind", ""), body.get("provider", "")
    if not isinstance(kind, str) or kind not in {"", "person", "character"} or not isinstance(subtype, str) or len(subtype) > 100:
        raise ValueError("人员库类型筛选无效")
    if not isinstance(provider, str) or len(provider) > 64:
        raise ValueError("人员库来源筛选无效")
    category, reality, classification_subtype = (body.get(key, "") for key in ("category", "reality", "subtype"))
    all_subtypes = {value for values in taxonomy.SUBTYPES.values() for value in values}
    for field, value, allowed in (("category", category, taxonomy.CATEGORIES), ("reality", reality, taxonomy.REALITIES),
                                  ("subtype", classification_subtype, all_subtypes)):
        if not isinstance(value, str) or value and value not in allowed:
            raise ValueError("人物与组织分类筛选无效：" + field)
    page, page_size = body.get("page", 1), body.get("page_size", 36)
    if type(page) is not int or not 1 <= page <= 10000 or type(page_size) is not int or not 1 <= page_size <= 100:
        raise ValueError("人员库分页参数无效")
    all_rows = _inventory(PeopleRepository(people_root))
    projected = {row["id"]: taxonomy.project(row) for row in all_rows}
    rows = [row for row in all_rows if (not kind or row["type"] == kind)
            and (not subtype or row.get("kind") == subtype)
            and (not category or projected[row["id"]]["category"] == category)
            and (not reality or projected[row["id"]]["reality"] == reality)
            and (not classification_subtype or projected[row["id"]]["subtype"] == classification_subtype)
            and (not provider or any(source["provider"] == provider for source in row.get("source_refs", [])))
            and (not query or _normal(query) in _normal("\n".join([row["name"], row.get("name_cn", ""), *row.get("aliases", []),
                 *localization.project(row).get("name", {}).values(),
                 *(alias for values in localization.project(row).get("aliases", {}).values() for alias in values)])))]
    rows.sort(key=lambda row: (_normal(row.get("name_cn") or row["name"]), row["id"]))
    start = (page - 1) * page_size
    return {"ok": True, "items": [_projection(row, card=True) for row in rows[start:start + page_size]],
        "total": len(rows), "page": page, "page_size": page_size,
        "taxonomy_options": taxonomy.options(),
        "facets": {"categories": [{"value": value, "label": label, "count": sum(row["category"] == value for row in projected.values())}
                                     for value, label in taxonomy.CATEGORIES.items()],
                   "realities": [{"value": value, "label": label, "count": sum(row["reality"] == value for row in projected.values())}
                                    for value, label in taxonomy.REALITIES.items()],
                   "types": [{"value": value, "label": label, "count": sum(row["type"] == value for row in all_rows)}
                               for value, label in (("person", "来源人员条目"), ("character", "来源角色条目"))],
                   "providers": sorted({source["provider"] for row in all_rows for source in row.get("source_refs", [])})}}


def detail(body, *, settings, people_root=None, data_root=None):
    body = providers._body(body, {"person_id"})
    repository = PeopleRepository(people_root)
    person, revision = _read_state(repository, body.get("person_id"))
    relations = _relations(person["id"], settings, repository)
    if repository.revision([person["id"]]) != revision:
        raise ValueError("人物资料读取期间已变化，请重新打开")
    return {"ok": True, "person": _projection(person), "version": _version(person, repository, revision),
        "relations": relations, "sources": deepcopy(person.get("source_refs", [])), "edit_fields": _fields(person),
        "clear_groups": deepcopy(_CLEAR_GROUPS), "taxonomy_options": taxonomy.options(),
        "type_options": [{"value": "person", "label": "来源人员条目"}, {"value": "character", "label": "来源角色条目"}],
        "type_editable": False, "warnings": ["人物是全库共享记录；修改资料会同时影响所有关联作品，ID 与关联关系保持不变。"]}


def _empty(field):
    return [] if field in {"aliases", "career", "infobox"} or field.startswith("localized.aliases.") else {} if field in {"birth_date", "avatar", "taxonomy", "profile_preferences"} else ""


def _manual_changes(person, value):
    if not isinstance(value, dict) or not value or set(value) - (set(_field_definitions()) - {"avatar", "kind"}):
        raise ValueError("手工修改只能包含明确选择的资料字段；ID、类型、来源、头像及关系不可直接改写")
    changes = deepcopy(value)
    if "profile_preferences" in changes:
        changes["profile_preferences"] = profile_variants.validate_preferences(
            changes["profile_preferences"], person.get("profile_variants", {}))
    if "taxonomy" in changes:
        changes["taxonomy"] = taxonomy.validate(changes["taxonomy"])
    if taxonomy.project({**person, **changes})["category"] != "person" and set(changes) & {"gender", "birth_date"}:
        raise ValueError("组织或其他实体不能编辑人物性别、出生日期；已有资料会保留")
    if "gender" in changes:
        synonyms = {"男": "男性", "male": "男性", "女": "女性", "female": "女性", "nonbinary": "非二元", "non-binary": "非二元"}
        gender = changes["gender"]
        if not isinstance(gender, str):
            raise ValueError("性别必须使用人员库枚举")
        changes["gender"] = synonyms.get(gender, gender)
        if changes["gender"] not in {"", "男性", "女性", "非二元", "其他"}:
            raise ValueError("性别必须使用人员库枚举")
    if "birth_date" in changes:
        birth = changes["birth_date"]
        if not isinstance(birth, dict) or set(birth) - {"year", "month", "mon", "day"} or ("mon" in birth and "month" in birth):
            raise ValueError("出生日期只能包含 year、month/mon、day 分量")
        if any(type(number) is not int or not 1 <= number <= (9999 if key == "year" else 31 if key == "day" else 12)
               for key, number in birth.items()):
            raise ValueError("出生日期分量超出范围")
        month = birth.get("month", birth.get("mon"))
        if month and birth.get("day"):
            try:
                date(birth.get("year", 2000), month, birth["day"])
            except ValueError:
                raise ValueError("出生日期不是有效日历日期") from None
    candidate = deepcopy(person)
    for field, field_value in changes.items():
        _write_field(candidate, field, field_value)
    validate_entity(candidate)
    return changes


def _plan_root(repository, source_root=None):
    return safe_path(source_root if source_root is not None else repository.root / "sources" / "people-library")


def _save_plan(plan, repository, source_root=None):
    content = providers._json_bytes(plan)
    if len(content) > MAX_PLAN_BYTES:
        raise ValueError("人物编辑预览超过安全大小限制")
    identity = hashlib.sha256(content).hexdigest()
    path = safe_path(_plan_root(repository, source_root) / (identity + ".json"))
    if path.exists():
        if not path.is_file() or path.stat().st_size > MAX_PLAN_BYTES:
            raise ValueError("人物预览快照目标不是安全普通文件")
        with path.open("rb") as stream:
            existing = stream.read(MAX_PLAN_BYTES + 1)
        if existing != content:
            raise ValueError("人物预览快照同名内容不一致")
    else:
        atomic_write_bytes(path, content)
    return identity


def _read_plan(identity, repository, source_root=None):
    if not isinstance(identity, str) or not providers._HASH.fullmatch(identity):
        raise ValueError("人物预览快照标识无效")
    path = safe_path(_plan_root(repository, source_root) / (identity + ".json"))
    if not path.is_file() or path.stat().st_size > MAX_PLAN_BYTES:
        raise ValueError("人物预览快照不存在或超过安全大小限制")
    with path.open("rb") as stream:
        content = stream.read(MAX_PLAN_BYTES + 1)
    if len(content) > MAX_PLAN_BYTES or hashlib.sha256(content).hexdigest() != identity:
        raise ValueError("人物预览快照内容已变化")
    plan = providers._load_json(content)
    if not isinstance(plan, dict) or plan.get("kind") != "people-library-plan" or plan.get("schema_version") != 1:
        raise ValueError("人物预览快照类型无效")
    return plan


def _diff(person, changes, *, selected=(), supplement=False, candidate=None, source=None, image_source=None):
    result = []
    for field, value in changes.items():
        before = (taxonomy.project(person) if field == "taxonomy" else profile_variants.effective_preferences(person)
                  if field == "profile_preferences" else _field_value(person, field))
        changed = before != value
        if field == "avatar" and image_source is not None:
            changed = changed or person.get("field_sources", {}).get("avatar") != {
                "provider": "manual", "action": "image", "image_source": image_source}
        dynamic = people_infobox.is_field(field)
        row = {"field": field, "label": people_infobox.label(field) if dynamic else _field_definitions()[field][0],
               "edit_type": people_infobox.edit_type(before, value) if dynamic else _field_definitions()[field][1], "before": before,
               "after": deepcopy(value), "changed": changed, "selectable": changed,
               "selected": field in selected and changed}
        if field == "profile_preferences":
            projected = deepcopy(person)
            profile_variants.apply_preferences(projected, value)
            row["display_diff"] = [{"field": path, "label": localization.label(path),
                "before": localization.field_value(person, path), "after": localization.field_value(projected, path)}
                for path in localization.field_paths(projected, person)
                if path.startswith(("localized.name.", "localized.summary."))
                and localization.field_value(person, path) != localization.field_value(projected, path)]
        if supplement:
            # Source refreshes share the same field-level policy as work
            # imports; manual edit/clear deliberately retain their semantics.
            merge = (profile_field_merge(field, before, value, person, candidate, source or {})
                     if candidate is not None else field_merge(field, before, value))
            row.update(merge)
            row.update(selectable=merge["changed"], selected=merge["default_selected"])
        result.append(row)
    return result


def _preview(person, revision, mode, changes, repository, *, selected=(), warnings=(), source=None, raw=None, source_root=None, candidate=None):
    plan = {"schema_version": 1, "kind": "people-library-plan", "mode": mode, "person_id": person["id"],
            "changes": changes, "source": source, "raw": raw, "warnings": list(warnings)}
    if candidate is not None:
        plan["profile_candidate"] = deepcopy(candidate)
    if repository.revision([person["id"]]) != revision:
        raise ValueError("人物在预览期间已变化，请重新打开详情")
    identity = _save_plan(plan, repository, source_root)
    diff = _diff(person, changes, selected=selected, supplement=mode == "source", candidate=candidate, source=source,
                 image_source=source if mode == "image" else None)
    profile_versions_changed = False
    if mode == "source" and candidate is not None:
        captured = deepcopy(person)
        profile_variants.capture(captured, candidate, source or {})
        profile_variants.collect_aliases(captured)
        profile_variants.refresh_projection_provenance(captured)
        profile_versions_changed = captured != person
    payload = {"kind": "people-library-" + mode, "person_id": person["id"], "people_scope": people._root_scope(repository),
               "revision": revision, "snapshot_id": identity, "expires": int(time.time()) + providers.PREVIEW_LIFETIME}
    return {"ok": True, "mode": "people-library-" + mode, "person_id": person["id"], "person": _projection(person),
        "diff": diff, "selected_fields": [row["field"] for row in diff if row["selected"]],
        "profile_versions_changed": profile_versions_changed,
        "preview_token": providers._sign(payload), "source": deepcopy(source), "warnings": list(warnings),
        "groups": deepcopy(_CLEAR_GROUPS) if mode == "clear" else []}


def edit_preview(body, *, settings=None, people_root=None, data_root=None, source_root=None):
    body = providers._body(body, {"person_id", "version", "changes"})
    repository = PeopleRepository(people_root)
    person, revision = _verify_version(body, repository)
    changes = _manual_changes(person, body.get("changes"))
    return _preview(person, revision, "edit", changes, repository, selected=changes,
        warnings=["仅保存明确选中的资料字段；所有关联作品会显示同一人物的新资料，不修改作品关联、头像或来源身份。"], source_root=source_root)


def clear_preview(body, *, settings=None, people_root=None, data_root=None, source_root=None):
    body = providers._body(body, {"person_id", "version"})
    repository = PeopleRepository(people_root)
    person, revision = _verify_version(body, repository)
    changes = {field: _empty(field) for group in _CLEAR_GROUPS for field in group["fields"]}
    if isinstance(person.get("localized"), dict):
        for field in ("name_cn", "summary", "aliases"):
            changes.pop(field, None)
        for field in localization.field_paths(person):
            if not field.startswith("localized.name."):
                changes[field] = _empty(field)
    return _preview(person, revision, "clear", changes, repository,
        warnings=["清空的是全库共享人物的资料，所有关联作品都会受影响。姓名、ID、类型、来源身份和作品/声优关系保留。",
                  "头像仅清空当前引用，图片文件和历史备份不会删除；默认不选择任何清空字段。"], source_root=source_root)


def _source_identity(person, provider, external_id, repository):
    if not isinstance(provider, str) or provider not in {"bangumi", "tmdb", "wikipedia"} or provider != "bangumi" and person["type"] != "person":
        raise ValueError("此人物类型只支持 Bangumi 人物/角色、TMDB 或维基百科现实人物来源")
    if provider in {"tmdb", "wikipedia"} and taxonomy.project(person)["category"] in {"organization", "object"}:
        raise ValueError("组织或其他实体不能绑定现实人物专用来源；请使用对应实体的资料来源")
    sources = [row for row in person.get("source_refs", []) if row["provider"] == provider]
    if external_id is None:
        identities = {row["external_id"] for row in sources}
        if len(identities) != 1:
            raise ValueError("请明确选择一个来源人物 ID")
        external_id = next(iter(identities))
    external_id = str(providers._positive_id(external_id, "external_id"))
    if sources and external_id not in {row["external_id"] for row in sources}:
        raise ValueError("此人物已有不同的同平台身份；不能用刷新自动替换或合并身份")
    owner = build_source_index(repository).find(provider, person["type"], external_id)
    if owner is not None and owner != person["id"]:
        raise ValueError("来源身份已由其他独立人物持有，不能按同名自动合并")
    return external_id


def _wikipedia_preview(body, person, revision, repository, *, source_root=None):
    if person["type"] != "person" or taxonomy.project(person)["category"] in {"organization", "object"}:
        raise ValueError("维基百科人物来源只用于现实人物，不能将人物条目绑定为虚拟角色")
    selected = body.get("external_id")
    if selected is None:
        selected = _source_identity(person, "wikipedia", None, repository)
    elif type(selected) is int or isinstance(selected, str) and selected.isdecimal():
        # A known conflicting page ID should fail before any provider request.
        selected = _source_identity(person, "wikipedia", selected, repository)
    report_progress("正在读取明确选择的维基百科人物条目并准备简体显示；不会搜索同名人物或改动作品关系",
        context={"stage": "独立人物来源预览", "provider": "wikipedia", "object": person["id"]})
    profile = wikipedia_people.fetch_person_profile(selected, deadline=time.monotonic() + 30)
    if not isinstance(profile, dict) or profile.get("provider") != "wikipedia":
        raise ValueError("维基人物来源响应格式无效")
    external_id = _source_identity(person, "wikipedia", profile.get("external_id"), repository)
    changes = _source_changes(profile, person)
    candidate = deepcopy(person)
    for field, value in changes.items():
        _write_field(candidate, field, value)
    validate_entity(candidate)
    source = {"provider": "wikipedia", "entity_type": "person", "external_id": external_id,
        "source_url": profile["source_url"], "fetched_at": profile["fetched_at"],
        "evidence_kind": "independent_person_article", **deepcopy(profile.get("source_metadata", {}))}
    warnings = [*profile.get("warnings", []), "维基百科为可选人物补充；保留规范页面与原始修订、简体转换证据，仅应用明确选择的字段。"]
    # Keep the full bounded adapter result, including original revision and
    # separate display_conversion evidence. Apply never performs conversion.
    return _preview(person, revision, "source", changes, repository, warnings=warnings,
                    source=source, raw=profile, source_root=source_root, candidate=profile)


def source_preview(body, *, settings=None, people_root=None, data_root=None, source_root=None):
    body = providers._body(body, {"person_id", "version", "provider", "external_id"})
    repository = PeopleRepository(people_root)
    person, revision = _verify_version(body, repository)
    provider = body.get("provider")
    if provider == "wikipedia":
        return _wikipedia_preview(body, person, revision, repository, source_root=source_root)
    external_id = _source_identity(person, provider, body.get("external_id"), repository)
    deadline = time.monotonic() + 60
    warnings, avatar = [], None
    with operation_context(stage="独立人物来源预览", provider=provider, object=person["id"], action="读取明确选择的来源人物"):
        report_progress(f"正在读取 {provider} 人物 {external_id}；不会同步作品或演职员关系")
        if provider == "bangumi":
            path = f"/v0/{'characters' if person['type'] == 'character' else 'persons'}/{external_id}"
            raw = providers._request_json(path, deadline=deadline)
        else:
            raw = tmdb_source._request_json(f"/person/{external_id}", query={"language": "zh-CN"}, deadline=deadline)
        if not isinstance(raw, dict) or type(raw.get("id")) is not int or str(raw["id"]) != external_id:
            raise ValueError("来源人物详情 ID 或结构与明确选择不一致")
        if len(providers._json_bytes(raw)) > providers.MAX_RESPONSE_BYTES:
            raise ValueError("来源人物详情超过安全大小限制")
        has_image = bool(raw.get("images")) if provider == "bangumi" else bool(raw.get("profile_path"))
        if has_image:
            try:
                acquire = people_assets.acquire_avatar if provider == "bangumi" else people_assets.acquire_tmdb_avatar
                avatar = acquire(raw, user_agent=providers.USER_AGENT, root=repository.root, deadline=deadline, previous=person.get("avatar"))
            except (ValueError, OSError, RuntimeError) as error:
                warnings.append("来源头像不可用，保留现有头像：" + str(error)[:1000])
        if provider == "bangumi":
            incoming = people._profile(person["type"], raw, warnings=warnings, subject_id="独立人物", avatar=avatar)
        else:
            incoming = tmdb_people._profile(raw, {"id": int(external_id), "name": raw.get("name", "")}, avatar,
                warnings, {"subject_id": "独立人物", "season_number": None, "raw": {}}, report=True)
    source = {"provider": provider, "entity_type": person["type"], "external_id": external_id,
        "source_url": (f"https://bgm.tv/{person['type']}/{external_id}" if provider == "bangumi" else f"https://www.themoviedb.org/person/{external_id}"),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "evidence_kind": "independent_person_api"}
    changes = _source_changes(incoming, person)
    _source_identity(person, provider, external_id, repository)
    warnings.append("仅补充明确选择的人物字段；缺失来源字段不会清空本地值，不会改动任何作品或声优关系。")
    return _preview(person, revision, "source", changes, repository, warnings=warnings,
                    source=source, raw=raw, source_root=source_root, candidate=incoming)


def _apply(body, mode, *, settings, people_root=None, data_root=None, source_root=None):
    body = providers._body(body, {"person_id", "preview_token", "selected_fields"})
    repository = PeopleRepository(people_root)
    person, revision = _read_state(repository, body.get("person_id"))
    payload = providers._verify_token(body.get("preview_token"))
    if (payload.get("kind") != "people-library-" + mode or payload.get("person_id") != person["id"]
            or payload.get("people_scope") != people._root_scope(repository)
            or payload.get("revision") != revision):
        raise ValueError("人物资料在预览后已变化，或凭据不属于当前人物与操作")
    plan = _read_plan(payload.get("snapshot_id"), repository, source_root)
    if plan.get("mode") != mode or plan.get("person_id") != person["id"]:
        raise ValueError("人物预览快照与操作不一致")
    changes = plan.get("changes")
    if not isinstance(changes, dict) or any(field not in _field_definitions() and not (
            mode == "source" and people_infobox.is_field(field)) for field in changes):
        raise ValueError("人物预览字段无效")
    if mode == "image" and set(changes) != {"avatar"}:
        raise ValueError("图片预览只能更改头像")
    selected = body.get("selected_fields")
    candidate = plan.get("profile_candidate", {})
    source = plan.get("source") or {}
    allowed = {row["field"] for row in _diff(person, changes, supplement=mode == "source",
               candidate=candidate if mode == "source" else None, source=source,
               image_source=source if mode == "image" else None) if row["selectable"]}
    if (not isinstance(selected, list) or len(selected) > len(changes) or any(not isinstance(field, str) for field in selected)
            or len(set(selected)) != len(selected) or set(selected) - allowed):
        raise ValueError("只能保存本次人物预览中明确可选的字段")
    updated = deepcopy(person)
    if mode == "image" and selected:
        from .people_images import validate_candidate
        validate_candidate(changes["avatar"], source, person)
        people_assets.read_avatar(changes["avatar"]["asset_id"], root=repository.root)
    if mode == "source":
        _source_identity(person, source.get("provider"), source.get("external_id"), repository)
        profile_variants.capture(updated, candidate, source)
    for field in selected:
        value = (profile_field_merge(field, _field_value(updated, field), changes[field], updated, candidate, source)["merged_value"]
                 if mode == "source" else changes[field])
        _write_field(updated, field, value)
        if mode != "source" and field != "profile_preferences":
            profile_variants.mark_manual(updated, field, person)
    chinese_mirror = adopted_chinese_mirror(updated, plan.get("raw"), selected) if mode == "source" else ""
    if chinese_mirror:
        updated["name_cn"] = chinese_mirror
    if any(localization.is_field(field) for field in selected):
        for field, value in localization.compatibility(updated).items():
            if not updated.get(field) and value:
                updated[field] = deepcopy(value)
    if mode == "source" and updated != person:
        source = deepcopy(plan.get("source"))
        if not isinstance(source, dict) or source.get("entity_type") != person["type"]:
            raise ValueError("人物来源预览证据无效")
        _source_identity(person, source.get("provider"), source.get("external_id"), repository)
        source["snapshot_id"] = payload["snapshot_id"]
        updated["source_refs"] = [deepcopy(row) for row in person.get("source_refs", []) if not (
            row["provider"] == source["provider"] and row["external_id"] == source["external_id"])] + [source]
        imported = updated.setdefault("source_values", {}).setdefault(source["provider"], {})
        field_sources = updated.setdefault("field_sources", {})
        if not isinstance(imported, dict) or not isinstance(field_sources, dict):
            raise ValueError("已有的人物来源字段证据格式无效")
        for field in selected:
            imported[field] = deepcopy(changes[field])
            field_sources[field] = deepcopy(candidate.get("field_sources", {}).get(field, source))
    elif selected:
        field_sources = updated.setdefault("field_sources", {})
        if not isinstance(field_sources, dict):
            raise ValueError("已有的人物字段证据格式无效")
        # Keep imported values: subsequent refresh can recognize manual clears.
        for field in selected:
            field_sources[field] = {"provider": "manual", "action": mode}
            if mode == "image":
                field_sources[field]["image_source"] = deepcopy(source)
    updated = validate_entity(updated)
    if mode == "source":
        profile_variants.collect_aliases(updated)
        profile_variants.refresh_projection_provenance(updated)
        updated = validate_entity(updated)
    changed = updated != person

    def confirm_identity():
        if mode == "source" and changed:
            source = plan["source"]
            _source_identity(updated, source["provider"], source["external_id"], repository)
        return {"ok": True}

    report_progress(f"正在保存独立人物资料：{person['name']}；明确选择 {len(selected)} 个字段，保持全库关联不变",
        context={"stage": "独立人物资料保存", "object": person["id"], "action": mode})
    repository.apply_batch([updated] if changed else [], expected_revision=payload["revision"],
                           revision_ids=[person["id"]], after_write=confirm_identity)
    current, current_revision = _read_state(repository, person["id"])
    return {"ok": True, "mode": "people-library-" + mode, "person_id": person["id"], "person": _projection(current),
        "version": _version(current, repository, current_revision), "people_updated": changed, "changed_fields": selected,
        "warnings": plan.get("warnings", [])}


def edit_apply(body, *, settings=None, people_root=None, data_root=None, source_root=None):
    return _apply(body, "edit", settings=settings, people_root=people_root, data_root=data_root, source_root=source_root)


def clear_apply(body, *, settings=None, people_root=None, data_root=None, source_root=None):
    return _apply(body, "clear", settings=settings, people_root=people_root, data_root=data_root, source_root=source_root)


def source_apply(body, *, settings=None, people_root=None, data_root=None, source_root=None):
    return _apply(body, "source", settings=settings, people_root=people_root, data_root=data_root, source_root=source_root)


def image_preview(body, **kwargs):
    from .people_images import preview
    return preview(body, **kwargs)


def image_apply(body, **kwargs):
    from .people_images import apply
    return apply(body, **kwargs)


def image_candidates(body, **kwargs):
    from .people_images import candidates
    return candidates(body, **kwargs)
