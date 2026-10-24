"""Snapshot-only identity, field and relationship import for all sources.

Source identities never follow names. Previews can cache public evidence but do
not write entities or works. Safe new identities are selected by default; existing
empty profile fields are safely filled; conflicting values require confirmation.
"""
from copy import deepcopy
import hashlib
import re
import time
import unicodedata

from collection_detail.save import catalog_write_transaction
from people_catalog.model import entity_id, source_identity, validate_entity, validate_people_refs
from people_catalog import localization, profile_variants
from people_catalog.repository import PeopleRepository, safe_path
from people_catalog.source_links import build_source_index
from work_catalog_yaml.persistence import directory_write_transaction
from work_catalog_yaml.operation_progress import report_progress, operation_context

from . import providers, service, people_infobox
from .people_field_merge import adopted_chinese_mirror, field_merge, has_value, profile_field_merge
from .role_catalog import _inventory, _revision

MODE = "people-import"
MAX_ENTRIES = 1000
FIELDS = ("name", "name_cn", "summary", "aliases", "infobox", "career", "gender", "birth_date", "avatar", "taxonomy")
LABELS = dict(zip(FIELDS, ("名称", "中文名", "简介", "别名", "详细资料", "职业", "性别", "生日", "头像", "分类")))
LOCALIZED_COMPAT_FIELDS = frozenset(("name", "name_cn", "summary", "aliases"))


def _hash(value):
    return hashlib.sha256(providers._json_bytes(value)).hexdigest()


def _taxonomy(row):
    from people_catalog.taxonomy import project
    return project(row)


def _taxonomy_locked(row):
    sources = row.get("field_sources", {})
    origin = sources.get("taxonomy", {}) if isinstance(sources, dict) else {}
    manual = isinstance(origin, dict) and (origin.get("provider") == "manual" or origin.get("manually_edited"))
    return has_value("taxonomy", _taxonomy(row)) or bool(manual)


def _names(row):
    localized = localization.project(row)
    return {unicodedata.normalize("NFKC", name).casefold().strip()
            for name in [row.get("name", ""), row.get("name_cn", ""), *row.get("aliases", []),
                         *localized["name"].values(), *(value for values in localized["aliases"].values() for value in values)]
            if isinstance(name, str) and name.strip()}


def _empty(field):
    return [] if field in {"aliases", "infobox", "career"} or field.startswith("localized.aliases.") else {} if field in {"birth_date", "avatar", "taxonomy"} else ""


def _fields(candidate, target=None):
    details = people_infobox.field_paths(target, candidate)
    if "localized" in candidate or "localized" in (target or {}):
        return tuple(localization.field_paths(candidate, target or {})) + tuple(field for field in FIELDS if field not in LOCALIZED_COMPAT_FIELDS and field != "infobox") + details
    return tuple(field for field in FIELDS if field != "infobox") + details


def _value(entity, field):
    if people_infobox.is_field(field):
        return people_infobox.field_value(entity, field)
    return localization.field_value(entity, field) if localization.is_field(field) else deepcopy(entity.get(field, _empty(field)))


def _required_fields(candidate):
    names = [field for field in _fields(candidate) if field.startswith("localized.name.") and has_value(field, _value(candidate, field))]
    if not names:
        return ["name"]
    preferred = next((field for field in names if _value(candidate, field) == candidate.get("name")), names[0])
    return [preferred]


def _card(row):
    return {"id": row["id"], "type": row["type"], "name": row["name"], "name_cn": row.get("name_cn", ""),
            "aliases": deepcopy(row.get("aliases", [])), "taxonomy": _taxonomy(row),
            **({"localized": localization.project(row)} if "localized" in row else {})}


def _sources(candidate):
    return candidate.get("source_refs") or [candidate["source"]]


def _stamp_sources(candidate, snapshot_id):
    """A provider's snapshot proves its own references, not another provider's."""
    provider = candidate["source"]["provider"]
    for source in [candidate["source"], *_sources(candidate)]:
        if source["provider"] == provider:
            source["snapshot_id"] = snapshot_id


def _owner(candidate, index):
    owners = {owner for source in _sources(candidate)
              if (owner := index.find(source["provider"], source["entity_type"], source["external_id"]))}
    if len(owners) > 1:
        raise ValueError("来源及官方重定向身份分别归属多个档案，须先人工处理身份冲突")
    return next(iter(owners), None)


def _cached_repository(repository, versions=None):
    """Immutable parsed rows keyed by checked bytes; no mtime shortcuts."""
    if hasattr(repository, "import_rows"):
        return repository
    from .people_library import _cached_entity
    versions = _inventory(repository) if versions is None else versions
    rows = {}
    for identity, digest in versions.items():
        data = repository._bytes(identity)
        if data is None or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("人物数据库在读取缓存期间已变化")
        rows[identity] = _cached_entity(repository, identity, data)

    class Snapshot:
        root = repository.root
        import_rows = rows
        import_versions = versions

        def read(self, identity):
            return deepcopy(rows.get(identity))

        def _bytes(self, identity):
            return repository._bytes(identity)

    return Snapshot()


def _legacy_role_reference(record, ref, actor_owner, character, repository, index):
    """Recover a pre-UUID role only from its exact, still-owned source evidence.

    Clearing a work does not clear shared people cards. Its next import may have
    a work UUID that the original role did not, so its deterministic key changes.
    Never use names, another UUID's role, or an already-bound current identity as
    migration authority; an explicit work relationship remains authoritative.
    """
    from . import role_catalog

    provider = ref["source"]["provider"]
    source = next((row for row in character["source_refs"] if row["provider"] == provider), None)
    if not source or source.get("evidence_kind") != "work_actor_credit" or index.find(provider, "character", source["external_id"]):
        return []
    legacy = role_catalog._origin({}, {**ref, "id": actor_owner})
    if role_catalog._work_key(record, ref["source"]) == legacy["work_key"]:
        return []
    owner = index.find(provider, "character", legacy["external_id"])
    previous = repository.read(owner) if owner else None
    if not previous or not any(row["provider"] == provider and row["entity_type"] == "character"
                               and row["external_id"] == legacy["external_id"]
                               and row.get("evidence_kind") == "work_actor_credit"
                               for row in previous.get("source_refs", [])):
        return []
    origins = [origin for origin in previous.get("role_origins", [])
               if origin["external_id"] == legacy["external_id"]
               and origin["work_key"] == legacy["work_key"]
               and origin["actor_id"] == actor_owner
               and role_catalog._identity_scope(origin["scope"]) == legacy["scope"]
               and (origin.get("credit_id") == legacy["credit_id"] if legacy.get("credit_id")
                    else not origin.get("credit_id") and origin["original_name"] == legacy["original_name"])]
    return [{**ref, "id": actor_owner, "character_id": owner,
             "_legacy_role_external_id": legacy["external_id"]}] if len(origins) == 1 else []


def _translated_role_reference(record, ref, actor_owner, character, repository, index, *, source_names=()):
    """Recover a cleared creditless link from a unique, evidenced translation.

    A unique actor alone is insufficient: the incoming explicit Chinese name
    (or its explicitly retained original Wiki cell) must equal the old source
    role name and the new name must be Korean. This
    permits Wiki's Chinese-only -> bilingual enrichment, not arbitrary renames.
    """
    from . import role_catalog
    if ref.get("credit_id") or not re.search(r"[\uac00-\ud7af]", ref.get("character_name", "")):
        return []
    chinese = {name.strip() for name in [ref.get("character_name_cn", ""), *source_names]
               if isinstance(name, str) and re.search(r"[\u3400-\u9fff]", name)}
    if not chinese:
        return []
    provider, scope = ref["source"]["provider"], role_catalog._identity_scope(ref["source"])
    source = next((row for row in character["source_refs"] if row["provider"] == provider), None)
    if not source or index.find(provider, "character", source["external_id"]):
        return []
    work_keys = {role_catalog._work_key(record, scope), role_catalog._work_key({}, scope)}
    matches, performances = [], set()
    for previous in repository.import_rows.values():
        if previous.get("type") != "character" or previous.get("merged_into"):
            continue
        for origin in previous.get("role_origins", []):
            if (origin.get("actor_id") != actor_owner or origin.get("work_key") not in work_keys
                    or role_catalog._identity_scope(origin.get("scope", {})) != scope):
                continue
            if not any(row.get("evidence_kind") == "work_actor_credit"
                       and row["provider"] == provider and row["external_id"] == origin["external_id"]
                       for row in previous.get("source_refs", [])):
                continue
            if index.find(provider, "character", origin["external_id"]) != previous["id"]:
                continue
            performances.add((previous["id"], origin["external_id"]))
            if not origin.get("credit_id") and origin.get("original_name", "").strip() in chinese:
                matches.append({**ref, "id": actor_owner, "character_id": previous["id"],
                                "_legacy_role_external_id": origin["external_id"]})
    return matches if len(matches) == 1 and len(performances) == 1 else []


def _role_source_names(snapshot, ref, actor):
    """Use the exact actor/role cell kept before Wiki's simplified conversion."""
    if snapshot.get("provider") != "wikipedia":
        return []
    source = next((row for row in actor.get("source_refs", []) if row["provider"] == "wikipedia"), None)
    if source is None:
        return []
    relations = [row for row in snapshot.get("raw", {}).get("relations", [])
                 if str(row.get("external_id")) == source["external_id"]
                 and row.get("character_name") == ref.get("character_name")
                 and row.get("character_name_cn", "") == ref.get("character_name_cn", "")]
    if len(relations) != 1:
        return []
    from .wikipedia_cast import names
    return names(relations[0].get("evidence", {}).get("role_cell", ""))["names"]


def _role_evidence_matches(entry, entries, entities, index, record):
    """Find work/verified-actor/stage evidence, never a bare role-name join."""
    from . import role_catalog
    from people_catalog import portrayal
    candidate = entry["candidate"]
    if candidate["type"] != "character" or not record.get("id"):
        return set()
    incoming = entry.get("role_origins", [])
    if not incoming:
        return set()
    work_key = role_catalog._work_key(record, incoming[0]["scope"])
    from .source_bindings import normalized_refs
    bindings = normalized_refs(record)

    def belongs_to_work(old):
        if old.get("work_key") == work_key:
            return True
        scope = old.get("scope", {})
        if old.get("work_key") != role_catalog._work_key({}, scope):
            return False
        return any(binding.get("enabled") and binding.get("provider") == scope.get("provider")
                   and str(binding.get("external_id")) == str(scope.get("subject_id"))
                   and (scope.get("season_number") is None
                        or binding.get("season_number") == scope["season_number"]
                        or binding.get("scope") == f"season:{scope['season_number']}") for binding in bindings)
    matches = set()
    for origin in incoming:
        actor = next((row["candidate"] for row in entries if row["candidate"]["id"] == origin["actor_id"]), None)
        actor_owner = _owner(actor, index) if actor else None
        if not actor_owner:
            continue
        peers = {row["external_id"] for row in entries for other in row.get("role_origins", [])
                 if other.get("actor_id") == origin["actor_id"] and other.get("scope") == origin.get("scope")
                 and portrayal.compatible_stage(other, origin)}
        if len(peers) != 1:
            return set()
        for previous in entities.values():
            if previous.get("type") != "character" or previous.get("merged_into"):
                continue
            for old in previous.get("role_origins", []):
                scope = old.get("scope", {})
                if (not belongs_to_work(old) or old.get("actor_id") != actor_owner
                        or scope.get("provider") == origin["scope"].get("provider")
                        or not portrayal.compatible_stage(old, origin)):
                    continue
                if any(source.get("evidence_kind") == "work_actor_credit" and source["external_id"] == old["external_id"]
                       and source["provider"] == scope.get("provider") for source in previous.get("source_refs", [])):
                    if index.find(scope["provider"], "character", old["external_id"]) == previous["id"]:
                        matches.add(previous["id"])
    return matches


def _role_name_candidate_in_work(person, record, entry):
    """Name-only suggestions for derived roles stay inside their work."""
    from . import role_catalog
    if any(row.get("character_id") == person["id"] for row in record.get("people_refs", [])):
        return True
    origins = person.get("role_origins", [])
    if record.get("id") and any(origin.get("work_key") == role_catalog._work_key(record, origin["scope"]) for origin in origins):
        return True
    # Old cards may have lost actor provenance but still retain their exact
    # source work. This only permits a manual suggestion, never auto-reuse.
    # An explicit different local work ID takes priority over source scope.
    if any(origin.get("work_key", "").startswith("work:") for origin in origins):
        return False
    scopes = [role_catalog._identity_scope(origin["scope"]) for origin in entry.get("role_origins", [])]
    return any(source.get("evidence_kind") == "work_actor_credit"
               and source.get("import_scope") and role_catalog._identity_scope(source["import_scope"]) in scopes
               for source in person.get("source_refs", []))


def _role_evidence_scope_unique(entry, matches, entities, record):
    """A work UUID does not prove which of multiple seasons a Wiki row means."""
    from .source_bindings import normalized_refs
    scopes = {}
    for binding in normalized_refs(record):
        if binding.get("enabled"):
            scopes.setdefault(binding.get("provider"), set()).add(_hash({
                key: binding.get(key) for key in ("external_id", "scope", "season_number", "episode_range")}))
    providers_involved = {origin["scope"]["provider"] for origin in entry.get("role_origins", [])}
    providers_involved.update(origin["scope"]["provider"] for identity in matches
                              for origin in entities[identity].get("role_origins", []))
    return all(len(scopes.get(provider, ())) <= 1 for provider in providers_involved)


def _verified_actor_aliases(snapshot, entities, index):
    """Keep a verified Wiki actor identity in the TMDB-first import direction."""
    if snapshot.get("provider") != "tmdb":
        return
    pairs = snapshot.get("raw", {}).get("wikipedia_people", {}).get("actor_matches", [])
    forward, reverse = {}, {}
    for pair in pairs:
        tmdb, wiki = str(pair.get("tmdb_person_id", "")), str(pair.get("wikipedia_pageid", ""))
        if not tmdb.isascii() or not tmdb.isdigit() or not wiki.isascii() or not wiki.isdigit():
            continue
        forward.setdefault(tmdb, set()).add(wiki)
        reverse.setdefault(wiki, set()).add(tmdb)
    for entity in entities:
        if entity["type"] != "person":
            continue
        source = next((row for row in entity.get("source_refs", []) if row["provider"] == "tmdb"), None)
        identities = forward.get(source["external_id"], set()) if source else set()
        if len(identities) != 1:
            continue
        wiki = next(iter(identities))
        if len(reverse[wiki]) != 1:
            continue
        owner, other = _owner(entity, index), index.find("wikipedia", "person", wiki)
        if owner and other and owner != other:
            snapshot.setdefault("warnings", []).append(
                f"TMDB 人物 {source['external_id']} 与维基人物 {wiki} 已分别关联不同资料卡；请人工核对是否合并，未自动转移身份")
            continue
        alias = {"provider": "wikipedia", "entity_type": "person", "external_id": wiki}
        if not any(all(row.get(key) == value for key, value in alias.items()) for row in entity.get("source_refs", [])):
            entity.setdefault("source_refs", []).append(alias)


def normalize(snapshot, record, repository):
    """Project existing provider parsers against an empty, read-only repository."""
    from . import people, role_catalog
    repository = _cached_repository(repository)
    identity_index = build_source_index(repository)

    class Empty:
        root = repository.root
        # Grouping needs read-only source ownership to avoid implicitly joining
        # two already-existing cards, but candidate profiles must stay fresh.
        group_identity_repository = repository

        def group_role_owners(self, ref, actor, origin, incoming_refs):
            """Reuse existing recovery evidence before grouping fresh profiles."""
            if not actor:
                return set()
            actor_owner = _owner(actor, identity_index) or actor["id"]
            previous = [old for old in record.get("people_refs", []) if old["id"] == actor_owner and old.get("character_id")
                        and role_catalog._identity_scope(old.get("source", {})) == role_catalog._identity_scope(ref["source"])
                        and (old.get("credit_id") == ref.get("credit_id") if ref.get("credit_id")
                             else not old.get("credit_id") and old.get("character_name") == ref.get("character_name"))]
            candidate = {"source_refs": [{"provider": ref["source"]["provider"], "external_id": origin["external_id"],
                                          "evidence_kind": "work_actor_credit"}]}
            if not previous:
                previous = _legacy_role_reference(record, ref, actor_owner, candidate, repository, identity_index)
            if not previous and sum(row["id"] == ref["id"] and role_catalog._identity_scope(row["source"])
                                    == role_catalog._identity_scope(ref["source"]) for row in incoming_refs) == 1:
                previous = _translated_role_reference(record, ref, actor_owner, candidate, repository, identity_index,
                                                      source_names=_role_source_names(snapshot, ref, actor))
            return {identity_index.retired.get(old["character_id"], old["character_id"]) for old in previous}

        def read(self, _identity):
            return None

    seed = deepcopy(record)
    seed["people_refs"] = []
    provider = snapshot["provider"]
    if provider == "wikipedia":
        entities, refs, name_evidence = [], [], {}
        for value in snapshot.get("raw", {}).get("profiles", []):
            identity = source_identity("wikipedia", "person", value["external_id"])
            source = {"provider": "wikipedia", "entity_type": "person", "external_id": value["external_id"],
                      "source_url": value["source_url"], "fetched_at": value["fetched_at"],
                      **deepcopy(value.get("source_metadata", {}))}
            row = {"id": identity, "schema_version": 1, "type": "person", "kind": "individual", "avatar": {},
                   **{field: deepcopy(value[field]) for field in (*FIELDS, "localized") if field in value},
                   "source_refs": [source, *[deepcopy(ref) for ref in value.get("source_refs", [])
                       if ref.get("provider") != "wikipedia"]]}
            entities.append(validate_entity(row))
        for relation in snapshot.get("raw", {}).get("relations", []):
            refs.append({"id": source_identity("wikipedia", "person", relation["external_id"]), "type": "person",
                         "role": relation.get("role", "演员"), "character_name": relation.get("character_name", ""),
                         "character_name_cn": relation.get("character_name_cn", ""),
                         "source": {"provider": "wikipedia", "subject_id": str(snapshot["subject_id"])}})
            for field in ("qualifier", "role_group"):
                if field in relation:
                    refs[-1][field] = deepcopy(relation[field])
            if relation.get("localized"):
                ref = refs[-1]
                key = (ref["id"], ref["character_name"], ref["character_name_cn"])
                name_evidence[key] = localization.validate(relation["localized"])
        seed["people_refs"] = validate_people_refs(refs)
        updated, entities, _changes = role_catalog.materialize(seed, entities, Empty(), snapshot.setdefault("warnings", []),
                                                             _name_evidence=name_evidence)
    else:
        updated, entities, _changes = people._build(snapshot, "normalized-public-evidence", seed, Empty(),
                                                    warnings=snapshot.setdefault("warnings", []), report=False)
    # Existing explicit work relationships preserve a role's exact identity
    # when a legacy work gains a UUID or an imported role name is translated.
    # This is not a global name match: actor source ownership and work scope
    # must agree, and ambiguous multiple performances remain separate.
    index = build_source_index(repository)
    _verified_actor_aliases(snapshot, entities, index)
    by_entity = {row["id"]: row for row in entities}
    recovered_legacy_roles = 0
    for ref in updated.get("people_refs", []):
        if not ref.get("character_id"):
            continue
        actor = by_entity[ref["id"]]
        actor_owner = _owner(actor, index) or actor["id"]
        previous_refs = [old for old in record.get("people_refs", []) if old["id"] == actor_owner and old.get("character_id")
                         and role_catalog._identity_scope(old.get("source", {})) == role_catalog._identity_scope(ref["source"])
                         and (old.get("credit_id") == ref.get("credit_id") if ref.get("credit_id") else not old.get("credit_id"))]
        recovered_legacy = not previous_refs
        if recovered_legacy:
            previous_refs = _legacy_role_reference(record, ref, actor_owner, by_entity[ref["character_id"]], repository, index)
            unique_performance = sum(row["id"] == ref["id"] and bool(row.get("character_id"))
                                     and role_catalog._identity_scope(row.get("source", {})) == role_catalog._identity_scope(ref["source"])
                                     for row in updated["people_refs"]) == 1
            if not previous_refs and unique_performance:
                previous_refs = _translated_role_reference(record, ref, actor_owner, by_entity[ref["character_id"]], repository, index,
                                                          source_names=_role_source_names(snapshot, ref, actor))
        exact = [old for old in previous_refs if old.get("source_values", {}).get("character_name", old.get("character_name")) == ref["character_name"]]
        if not exact and not ref.get("credit_id"):
            peers = [row for row in updated["people_refs"] if row["id"] == ref["id"] and row.get("character_id")
                     and role_catalog._identity_scope(row.get("source", {})) == role_catalog._identity_scope(ref["source"])
                     and not row.get("credit_id")]
            if len(peers) != 1:
                continue
        previous_refs = exact or previous_refs
        if len(previous_refs) != 1:
            continue
        previous = repository.read(previous_refs[0]["character_id"])
        if not previous:
            continue
        origins = [origin for origin in previous.get("role_origins", []) if origin["actor_id"] == actor_owner
                   and role_catalog._identity_scope(origin["scope"]) == role_catalog._identity_scope(ref["source"])
                   and (not recovered_legacy or origin["external_id"] == previous_refs[0]["_legacy_role_external_id"])
                   and (origin.get("credit_id") == ref.get("credit_id") if ref.get("credit_id") else not origin.get("credit_id"))]
        exact = [origin for origin in origins if origin["original_name"] == ref["character_name"]]
        origins = exact or origins
        if len(origins) != 1:
            continue
        origin = deepcopy(origins[0])
        if index.find(provider, "character", origin["external_id"]) != previous["id"]:
            raise ValueError("既有饰演关系缺少对应角色来源所有者，请先修复身份关联")
        character = by_entity[ref["character_id"]]
        old_identity = character["id"]
        previous_origins = character.get("role_origins", [])
        replaced = [row for row in previous_origins if row["actor_id"] == ref["id"]
                    and role_catalog._identity_scope(row["scope"]) == role_catalog._identity_scope(ref["source"])
                    and (row.get("credit_id") == ref.get("credit_id") if ref.get("credit_id") else not row.get("credit_id"))]
        if len(replaced) != 1:
            continue
        for source in character["source_refs"]:
            if source["provider"] == provider and source["external_id"] == replaced[0]["external_id"]:
                source["external_id"] = origin["external_id"]
        character["id"] = source_identity(provider, "character", origin["external_id"])
        origin["actor_id"] = actor["id"]
        for field in ("qualifier", "role_group"):
            # Keep source identity stable, but this is still a fresh proposal:
            # new stage evidence must reach the relationship conflict preview.
            if field in replaced[0]:
                origin[field] = deepcopy(replaced[0][field])
        character["role_origins"] = [origin if row is replaced[0] else row for row in previous_origins]
        by_entity[character["id"]] = character
        for related in updated["people_refs"]:
            if related.get("character_id") == old_identity:
                related["character_id"] = character["id"]
        recovered_legacy_roles += int(recovered_legacy)
    if recovered_legacy_roles:
        report_progress(f"已按原作品来源、演员和演职员身份找回 {recovered_legacy_roles} 项既有角色；仅恢复预览对应，未新增或写入资料卡",
                        context={"stage": "人物身份核对", "action": "恢复旧版角色身份", "count": recovered_legacy_roles})
    if len(entities) > MAX_ENTRIES:
        raise ValueError("来源人物与角色超过统一导入安全上限")
    by_id, entries = {}, []
    for entity in entities:
        sources = [source for source in entity.get("source_refs", []) if source["provider"] == provider]
        if not sources:
            raise ValueError("来源候选必须具有唯一明确的平台身份")
        sources = deepcopy(sources)
        for source in sources:
            source.pop("snapshot_id", None)
        source = next((ref for ref in sources if source_identity(ref["provider"], ref["entity_type"], ref["external_id"]) == entity["id"]), sources[0])
        if not source.get("source_url") and not source["external_id"].startswith("role:"):
            source["source_url"] = (f"https://bgm.tv/{entity['type']}/{source['external_id']}" if provider == "bangumi" else
                                    f"https://www.themoviedb.org/person/{source['external_id']}" if provider == "tmdb" else "")
        external = entity["type"] + ":" + source["external_id"]
        by_id[entity["id"]] = external
        candidate = {"id": entity["id"], "type": entity["type"], "kind": entity.get("kind", ""),
                     **{field: deepcopy(entity[field]) for field in (*FIELDS, "localized", "profile_variants", "field_sources") if field in entity},
                     "taxonomy": _taxonomy(entity), "source": source,
                     "source_refs": [*sources, *[deepcopy(ref) for ref in entity.get("source_refs", []) if ref["provider"] != provider]],
                     "source_url": source.get("source_url", ""), "evidence_kind": source.get("evidence_kind", "")}
        if not candidate.get("name"):
            raise ValueError("未确认姓名的来源证据不能创建空白人物候选")
        entries.append({"external_id": external, "candidate": candidate, "relations": [],
                        "role_origins": deepcopy(entity.get("role_origins", []))})
        if entity["type"] == "character":
            evidence_names = {name for ref in updated.get("people_refs", []) if ref.get("character_id") == entity["id"]
                              for name in _role_source_names(snapshot, ref, by_entity[ref["id"]])}
            if evidence_names:
                # This is identity-review evidence, not an adopted profile
                # alias. A card created without an actor link may be suggested
                # by the unchanged source cell, but never auto-bound from it.
                entries[-1]["identity_name_evidence"] = sorted(evidence_names)
    lookup = {entry["external_id"]: entry for entry in entries}
    for ref in validate_people_refs(updated.get("people_refs", [])):
        key = by_id[ref["id"]]
        dependencies = [by_id[ref["character_id"]]] if ref.get("character_id") else []
        relation = {"kind": "work", "ref": deepcopy(ref), "required_entity_keys": dependencies,
                    "role": ref["role"], "character_name": ref.get("character_name", ""),
                    "character_name_cn": ref.get("character_name_cn", ""), "source": deepcopy(ref.get("source", {}))}
        relation["id"] = "relation:" + _hash([key, relation])
        lookup[key]["relations"].append(relation)
        candidate = lookup[key]["candidate"]
        for field, value in (("roles", ref["role"]), ("character_names", ref.get("character_name", ""))):
            if value and value not in candidate.setdefault(field, []):
                candidate[field].append(value)
    for entity in entities:
        for actor in entity.get("voice_actors", []):
            relation = {"kind": "voice_actor", "actor": deepcopy(actor), "role": actor.get("role", "CV"),
                        "required_entity_keys": [by_id[actor["person_id"]]]}
            relation["id"] = "relation:" + _hash([by_id[entity["id"]], relation])
            lookup[by_id[entity["id"]]]["relations"].append(relation)
    snapshot["import_entries"] = entries
    return snapshot


def _entries(snapshot):
    entries = snapshot.get("import_entries")
    if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
        raise ValueError("旧人物预览缺少统一选择快照，请重新预览")
    if len({entry["external_id"] for entry in entries}) != len(entries):
        raise ValueError("来源人物候选身份重复")
    return entries


def _blocked(candidate, target, index):
    source = candidate["source"]
    if target.get("type") != candidate["type"] or target.get("merged_into"):
        return "只能选择同类型的有效既有人物或角色"
    owner = _owner(candidate, index)
    if owner and owner != target["id"]:
        return "此来源身份已有本地所有者，不能转移身份；请另行明确合并档案"
    from people_catalog.taxonomy import is_real_person
    from .people_identity import identity_conflict_reason
    incoming_taxonomy, target_taxonomy = _taxonomy(candidate), _taxonomy(target)
    if any(incoming_taxonomy[key] != "unknown" and target_taxonomy[key] != "unknown"
           and incoming_taxonomy[key] != target_taxonomy[key] for key in ("category", "reality")):
        return "来源与既有档案的实体分类或现实性冲突，不能按同名绑定"
    if not owner and candidate["type"] == "person" and is_real_person(candidate):
        reason = identity_conflict_reason(target, candidate)
        if reason:
            return reason
    return ""


def _diff(candidate, target, *, creating=False):
    result = []
    required = _required_fields(candidate) if creating else []
    for field in _fields(candidate, target):
        before, after = _value(target, field), _value(candidate, field)
        if field == "taxonomy":
            before, after = _taxonomy(target) if target else {}, _taxonomy(candidate)
        decision = profile_field_merge(field, before, after, target, candidate, candidate.get("source", {}))
        available = has_value(field, after)
        editable = field != "avatar"
        # Fill unresolved classification, but never silently reclassify a known
        # or explicitly manually cleared taxonomy.
        taxonomy_locked = field == "taxonomy" and not creating and _taxonomy_locked(target)
        selectable = available and not taxonomy_locked
        decision["default_selected"] = decision["default_selected"] and selectable
        if taxonomy_locked:
            decision["requires_confirmation"] = decision["changed"]
        result.append({"field": field, "label": LABELS[field] if field in LABELS else people_infobox.label(field) if people_infobox.is_field(field) else localization.label(field), "before": before, "after": after,
            **decision, "selectable": selectable or editable, "editable": editable,
            "must_override": editable and (not available or taxonomy_locked),
            "edit_type": people_infobox.edit_type(before, after) if people_infobox.is_field(field) else "json" if field in {"infobox", "birth_date"} else "list" if field in {"aliases", "career"} or field.startswith("localized.aliases.")
                else "textarea" if field == "summary" or field.startswith("localized.summary.") else "taxonomy" if field == "taxonomy" else "text",
            "selected": decision["default_selected"], "required": field in required,
            "reason": "已有手工分类保持不变" if taxonomy_locked else decision.get("reason", "")})
    return result


def _target(entry, target, index):
    reason = _blocked(entry["candidate"], target, index)
    diff = _diff(entry["candidate"], target)
    return {**_card(target), "blocked": bool(reason), "reason": reason,
            "matched": bool(_names(entry["candidate"]) & _names(target)),
            "exact": _owner(entry["candidate"], index) == target["id"],
            "diff": diff, "selected_fields": [row["field"] for row in diff if row["default_selected"] and not reason]}


def _same_work_relation(old, ref):
    """Compare scoped relation identity, not translated labels or profile fields."""
    from people_catalog import portrayal
    return (old["id"] == ref["id"] and old["type"] == ref["type"]
            and not portrayal.conflicting_stage(old, ref)
            and old.get("source") == ref.get("source") and old["role"] == ref["role"]
            and old.get("credit_id") == ref.get("credit_id")
            and (bool(ref.get("credit_id"))
                 or bool(ref.get("character_id") and old.get("character_id") == ref["character_id"])
                 or old.get("source_values", {}).get("character_name", old.get("character_name")) == ref.get("character_name")))


def _legacy_work_relation(old, ref, *, include_ambiguous=False):
    """Compare legacy evidence; sparse roles count only when warning of ambiguity."""
    from people_catalog import portrayal
    if (bool(old.get("credit_id")) == bool(ref.get("credit_id"))
            or old["id"] != ref["id"] or old["type"] != ref["type"]
            or old.get("source") != ref.get("source") or old["role"] != ref["role"]
            or portrayal.conflicting_stage(old, ref)):
        return False
    if old.get("character_id") and ref.get("character_id"):
        return old["character_id"] == ref["character_id"]
    old_name = old.get("source_values", {}).get("character_name", old.get("character_name", ""))
    new_name = ref.get("source_values", {}).get("character_name", ref.get("character_name", ""))
    if old_name and new_name:
        return old_name == new_name
    if (old.get("character_id") or ref.get("character_id")
            or "character_name" in old or "character_name" in ref):
        # Empty labels cannot bridge a known character to an unknown one.
        # Only a pure crew link can be identified without character evidence.
        return include_ambiguous
    return True


def _find_work_relation(existing, ref, incoming):
    same = next((old for old in existing if _same_work_relation(old, ref)), None)
    if same is not None:
        return same
    legacy = [old for old in existing if _legacy_work_relation(old, ref, include_ambiguous=True)]
    # Match in both directions: two new credits must not both claim one old
    # credit-less link, and one new credit must not guess between old roles.
    if (len(legacy) == 1 and _legacy_work_relation(legacy[0], ref)
            and sum(_legacy_work_relation(legacy[0], row, include_ambiguous=True) for row in incoming) == 1):
        return legacy[0]
    return None


def _mapped_work_refs(entries, identities):
    result = []
    for entry in entries:
        for relation in entry["relations"]:
            if relation["kind"] != "work":
                continue
            ref = deepcopy(relation["ref"])
            ref["id"] = identities.get(ref["id"], ref["id"])
            if ref.get("character_id"):
                ref["character_id"] = identities.get(ref["character_id"], ref["character_id"])
            result.append(ref)
    return result


def _default_relations(rows, entries, entities, record):
    """Recommend new links and missing work links for confirmed source owners.

    Profile existence does not imply participation in this particular work.
    Restorations remain explicit, cancellable selections in the save preview.
    """
    from people_catalog.taxonomy import is_fictional_person, is_real_person
    resolved = {row["external_id"]: (row["candidate"] if row["action"] == "create" else entities[row["target_id"]])
                for row in rows if row["action"] in {"create", "reuse"}}
    canonical = {entry["candidate"]["id"]: entry["external_id"] for entry in entries}
    originals = {entry["external_id"]: {relation["id"]: relation for relation in entry["relations"]}
                 for entry in entries}
    incoming = _mapped_work_refs(entries, {entry["candidate"]["id"]: resolved[entry["external_id"]]["id"]
        for entry in entries if entry["external_id"] in resolved})
    for row in rows:
        for relation in row["relations"]:
            reuse = row["action"] == "reuse" and row["target_id"] == row["identity_owner_id"]
            restore = reuse and relation["kind"] == "work"
            safe = (relation.get("selectable") is not False and (row["creation"]["allowed"] or restore)
                    and all(key in resolved for key in relation["required_entity_keys"]))
            original = originals[row["external_id"]][relation["id"]]
            if safe and relation["kind"] == "voice_actor":
                actor = resolved.get(canonical.get(original["actor"]["person_id"]))
                safe = is_fictional_person(row["candidate"]) and bool(actor and is_real_person(actor))
            elif safe:
                ref = original["ref"]
                if ref.get("character_name"):
                    safe = is_real_person(resolved.get(row["external_id"], row["candidate"]))
                if ref.get("character_id"):
                    character = resolved.get(canonical.get(ref["character_id"]))
                    safe = safe and bool(character and is_fictional_person(character))
            if relation["kind"] == "work":
                ref = deepcopy(original["ref"])
                target = resolved.get(row["external_id"])
                if target:
                    ref["id"] = target["id"]
                character = resolved.get(canonical.get(ref.get("character_id")))
                if character:
                    ref["character_id"] = character["id"]
                existing = record.get("people_refs", [])
                present = _find_work_relation(existing, ref, incoming) is not None
                # A reused credit cannot silently change its established job.
                conflict = not present and any(_legacy_work_relation(old, ref, include_ambiguous=True) or (
                    bool(ref.get("credit_id")) and old["id"] == ref["id"]
                    and old.get("source") == ref.get("source") and old.get("credit_id") == ref["credit_id"])
                    for old in existing)
                if ref.get("character_id") and ref.get("character_name"):
                    from . import role_catalog
                    from people_catalog import portrayal
                    origin = role_catalog._origin(record, ref)
                    character = entities.get(ref["character_id"], {})
                    same_origins = [old for old in character.get("role_origins", []) if role_catalog._same_origin(old, origin)]
                    stage_conflicts = [old for old in same_origins if portrayal.conflicting_stage(old, origin)]
                    if stage_conflicts and not any(portrayal.compatible_stage(old, origin) for old in same_origins):
                        conflict = True
                        relation["stage_conflict"] = {"before": [portrayal.qualifier_for(old) for old in stage_conflicts],
                                                      "after": portrayal.qualifier_for(origin)}
                        relation["reason"] = "来源饰演阶段与既有资料不同；默认保留原关联，明确勾选后另存新阶段，不覆盖原阶段。"
                        safe = False
                relation["work_relation_status"] = "existing" if present else "conflict" if conflict else "missing"
                relation["restore_missing"] = bool(restore and not present and not conflict and safe)
                if restore:
                    safe = safe and not present and not conflict
            relation["selected"] = bool(safe)
            if safe and (row["action"] == "create" or restore):
                row["selected_relations"].append(relation["id"])


def preview(snapshot, snapshot_id, record, repository):
    from . import people_import_batch
    repository = _cached_repository(repository)
    versions, entities = repository.import_versions, repository.import_rows
    index = build_source_index(repository)
    cards = [_card(row) for row in entities.values() if not row.get("merged_into")]
    names = {}
    for row in entities.values():
        if not row.get("merged_into"):
            for name in _names(row):
                names.setdefault((row["type"], name), set()).add(row["id"])
    rows, entries = [], _entries(snapshot)
    for entry in entries:
        candidate, source = entry["candidate"], entry["candidate"]["source"]
        owner = _owner(candidate, index)
        matching_ids = {owner} if owner else set()
        role_matches = _role_evidence_matches(entry, entries, entities, index, record) if not owner else set()
        unique_role_scope = _role_evidence_scope_unique(entry, role_matches, entities, record)
        matching_ids.update(role_matches)
        if candidate["id"] in entities:
            matching_ids.add(candidate["id"])
        for name in _names(candidate) | _names({"aliases": entry.get("identity_name_evidence", [])}):
            same_names = names.get((candidate["type"], name), ())
            if candidate.get("evidence_kind") == "work_actor_credit":
                same_names = [identity for identity in same_names if _role_name_candidate_in_work(entities[identity], record, entry)]
            matching_ids.update(same_names)
        matches = [entities[identity] for identity in matching_ids if not entities[identity].get("merged_into")]
        matches.sort(key=lambda row: (row["id"] != owner, row["name"], row["id"]))
        reason = "来源身份已有既有档案，请选择该档案；不得重复新增" if owner else ""
        if not owner and candidate["id"] in entities:
            reason = "规范档案已存在或已退休；请选择有效既有档案，不得重复创建"
        targets = [_target(entry, row, index) for row in matches[:40]]
        creation_diff = _diff(candidate, {}, creating=True)
        creation_fields = [row["field"] for row in creation_diff if row["default_selected"]]
        bound = next((row for row in targets if row["id"] == owner), None)
        action, target_id, selected_fields = "skip", "", []
        if bound and not bound["blocked"]:
            action, target_id = "reuse", owner
            selected_fields = list(bound["selected_fields"])
            match_status = "source_bound"
            default_reason = "已按来源身份找到人物卡并默认复用；空缺资料自动补齐，双方都有且不同的字段请勾选是否采用新值"
        elif owner or reason or any(row["blocked"] for row in targets):
            match_status = "conflict"
            default_reason = "已找到可能相关的档案，但身份或分类存在冲突；请核对后决定，未自动新增"
        elif len(role_matches) == 1 and len(matches) == 1 and unique_role_scope:
            action, target_id = "reuse", next(iter(role_matches))
            selected_fields = list(targets[0]["selected_fields"])
            match_status = "role_evidence"
            default_reason = "同一作品、已确认演员及饰演阶段唯一对应，默认复用角色卡；不同演员的明确年龄分支保留各自演员和阶段。"
        elif matches:
            match_status = "ambiguous" if len(matches) > 1 else "role_evidence" if role_matches else "name_match"
            default_reason = ("同一作品、已确认演员的其他来源存在对应角色；请确认是否复用，不会自动合并已有档案"
                              if role_matches else "已按名称或别名找到人物卡；请核对是否同一人物，再选择需要更新的字段")
        else:
            action, selected_fields = "create", list(creation_fields)
            match_status = "new"
            default_reason = "已搜索全库来源身份、名称和别名，未找到人物卡；默认新增并采用有效来源资料"
        rows.append({"external_id": entry["external_id"], "entity_type": candidate["type"],
            "candidate": deepcopy(candidate), "identity_owner_id": owner or "",
            "recommended_target_id": owner or (next(iter(role_matches)) if len(role_matches) == 1 and len(matches) == 1 and unique_role_scope else ""), "targets": targets,
            "match_status": match_status, "default_reason": default_reason,
            "creation": {"id": candidate["id"], "allowed": not reason, "blocked": bool(reason), "reason": reason,
                         "required_fields": _required_fields(candidate), "diff": creation_diff, "selected_fields": creation_fields},
            "relations": [{key: deepcopy(value) for key, value in relation.items() if key not in {"ref", "actor"}}
                          for relation in entry["relations"]],
            "action": action, "target_id": target_id, "selected_fields": selected_fields,
            "selected_relations": [], "overrides": {}})
    _default_relations(rows, entries, entities, record)
    if _inventory(repository) != versions:
        raise ValueError("人物数据库在统一预览期间发生变化，请重试")
    display = {"mode": MODE, "provider": snapshot["provider"], "subject_id": snapshot["subject_id"],
            "snapshot_id": snapshot_id, "entries": rows, "targets": cards,
            "unresolved": deepcopy(snapshot.get("raw", {}).get("unresolved", [])),
            "warnings": list(snapshot.get("warnings", [])),
            "retry_failures": deepcopy(snapshot.get("retry_failures", []))}
    return people_import_batch.annotate(display, repository), versions


def prepare(snapshot, item, *, settings, repository):
    from . import people
    repository = _cached_repository(repository)
    original_warnings = set(snapshot.get("warnings", []))
    snapshot = normalize(snapshot, item["record"], repository)
    snapshot["normalization_warnings"] = [warning for warning in snapshot.get("warnings", []) if warning not in original_warnings]
    for warning in snapshot.get("warnings", []):
        report_progress(warning, context={"stage": "人物来源警告", "status": "warning"})
    snapshot_id, _digest = providers._save_snapshot(snapshot, safe_path(repository.root / "sources" / snapshot["provider"]))
    display, versions = preview(snapshot, snapshot_id, item["record"], repository)
    proposal = {"ref": deepcopy(item["ref"]), "provider": snapshot["provider"], "subject_id": snapshot["subject_id"],
                "snapshot_id": snapshot_id, "scope": providers._catalog_scope(settings), "people_scope": people._root_scope(repository),
                "selection_only": True, "inventory_sha256": _hash(versions), "selection_plan_sha256": _hash(display)}
    if snapshot["provider"] == "tmdb":
        proposal.update(season_number=snapshot.get("season_number"), episode_range=snapshot.get("episode_range"))
    display["selection_token"] = providers._sign({**proposal, "kind": MODE, "expires": int(time.time()) + providers.PREVIEW_LIFETIME})
    new_count = sum(row["action"] == "create" for row in display["entries"])
    report_progress(f"人物统一预览完成：{len(display['entries'])} 项来源候选；已搜索全库，{new_count} 项默认新增，已有档案仅更新勾选字段",
                    context={"stage": "人物统一预览", "action": "只缓存来源证据", "count": len(display["entries"])})
    return {"proposal": proposal, "preview": display}


def wikipedia_preview(body, *, settings, people_root=None, data_root=None):
    """Explicit page identifiers only; never search a name to infer identity."""
    from datetime import datetime, timezone
    from . import people, wikipedia_people
    body = providers._body(body, {"ref", "pages"})
    item = providers._catalog_item(body.get("ref"), settings)
    providers._check_version(item, body["ref"])
    pages = body.get("pages")
    if not isinstance(pages, list) or not 1 <= len(pages) <= 20:
        raise ValueError("请明确选择 1～20 个维基人物页面")
    profiles, relations, warnings = [], [], []
    deadline = time.monotonic() + 90
    for page in pages:
        if (not isinstance(page, dict) or set(page) - {"page", "role", "character_name", "character_name_cn"}
                or "page" not in page):
            raise ValueError("维基独立人物必须提供明确页面及可选作品关系")
        try:
            with operation_context(stage="维基人物独立预览", object=str(page["page"])[:200], action="读取明确人物页面"):
                profile = wikipedia_people.fetch_person_profile(page["page"], deadline=deadline)
        except (ValueError, OSError, RuntimeError) as error:
            warnings.append("维基人物页面 " + str(page["page"])[:200] + " 未获取：" + str(error)[:500])
            continue
        if not any(row["external_id"] == profile["external_id"] for row in profiles):
            profiles.append(profile)
        if page.get("role"):
            relations.append({"external_id": profile["external_id"], "role": providers._text(page["role"], 200),
                "character_name": providers._text(page.get("character_name", ""), 1000),
                "character_name_cn": providers._text(page.get("character_name_cn", ""), 1000)})
    if not profiles:
        raise ValueError("所有明确选择的维基人物页面均未获取：" + "；".join(warnings)[:1000])
    snapshot = {"schema_version": 1, "provider": "wikipedia", "kind": "people", "subject_id": int(profiles[0]["external_id"]),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "warnings": warnings,
        "raw": {"profiles": profiles, "relations": relations}}
    result = prepare(snapshot, item, settings=settings, repository=PeopleRepository(people_root))
    result["proposal"]["independent_source"] = True
    payload = {**result["proposal"], "kind": "people", "expires": int(time.time()) + providers.PREVIEW_LIFETIME}
    return {"ok": True, "ref": deepcopy(item["ref"]), **result["preview"], "preview_token": providers._sign(payload)}


def _snapshot(proposal, repository):
    result = providers._read_snapshot(proposal["snapshot_id"], proposal["snapshot_id"],
        safe_path(repository.root / "sources" / proposal["provider"]), provider=proposal["provider"])
    result["_snapshot_id"] = proposal["snapshot_id"]
    return result


def target_preview(body, *, settings, people_root=None, data_root=None):
    from . import people, people_import_batch
    body = providers._body(body, {"snapshot_id", "preview_token", "external_id", "target_id"})
    token = providers._verify_token(body.get("preview_token"))
    repository = PeopleRepository(people_root)
    if (token.get("kind") != MODE or token.get("snapshot_id") != body.get("snapshot_id")
            or token.get("people_scope") != people._root_scope(repository) or token.get("scope") != providers._catalog_scope(settings)):
        raise ValueError("目标差异凭据与人物来源不一致")
    versions = _inventory(repository)
    if token.get("inventory_sha256") != _hash(versions):
        raise ValueError("人物库已变化，请重新预览")
    repository = _cached_repository(repository, versions)
    repository = people_import_batch.repository_for(token, repository)
    snapshot = _snapshot(token, repository)
    entry = next((row for row in _entries(snapshot) if row["external_id"] == body.get("external_id")), None)
    target = repository.read(entity_id(body.get("target_id")))
    if entry is None or not target:
        raise ValueError("人物候选或所选全库目标不存在")
    result = _target(entry, target, build_source_index(repository))
    people_import_batch.annotate({"targets": [result]}, repository)
    if _inventory(repository) != versions:
        raise ValueError("人物库在读取目标差异期间已变化")
    return {"ok": True, "target": result}


def _selected(entry, choice, previous, repository):
    fields, overrides = choice.get("fields", []), choice.get("overrides", {})
    creating = previous is None
    candidate, source = entry["candidate"], entry["candidate"]["source"]
    allowed = _fields(candidate, previous or {})
    if (not isinstance(fields, list) or len(fields) > len(allowed)
            or any(not isinstance(field, str) or field not in allowed for field in fields) or len(set(fields)) != len(fields)
            or not isinstance(overrides, dict) or set(overrides) - set(fields) or "avatar" in overrides):
        raise ValueError("采用字段或手工覆盖字段无效；只有明确勾选的可编辑字段可覆盖")
    if previous is not None and not fields and not choice.get("relations") and all(
            any(all(existing.get(key) == incoming.get(key) for key in ("provider", "entity_type", "external_id"))
                for existing in previous.get("source_refs", [])) for incoming in _sources(candidate)):
        # A confirmed identity can retain source versions without changing
        # displayed fields. Skip persistence when even that evidence is equal.
        captured = deepcopy(previous)
        profile_variants.capture(captured, candidate, source)
        profile_variants.collect_aliases(captured)
        profile_variants.refresh_projection_provenance(captured)
        if captured == previous:
            return deepcopy(previous)
    updated = deepcopy(previous) if previous else {"id": candidate["id"], "schema_version": 1,
        "type": candidate["type"], "kind": candidate.get("kind", ""), "name": "", "avatar": {}, "taxonomy": _taxonomy(candidate)}
    required = _required_fields(candidate)
    if creating and any(field not in fields for field in required):
        raise ValueError("创建人物必须明确确认非空名称；不能创建空白档案")
    if creating:
        updated["name"] = candidate["name"]
    profile_variants.capture(updated, candidate, source, seed_current=not creating)
    if overrides:
        from .people_library import _manual_changes
        legacy = {field: value for field, value in overrides.items() if not localization.is_field(field) and not people_infobox.is_field(field)}
        cleaned = _manual_changes(updated, legacy) if legacy else {}
        for field, value in overrides.items():
            if localization.is_field(field):
                checked = deepcopy(updated)
                localization.set_field(checked, field, value)
                cleaned[field] = _value(checked, field)
            elif people_infobox.is_field(field):
                checked = deepcopy(updated)
                people_infobox.set_field(checked, field, value)
                validate_entity(checked)
                cleaned[field] = _value(checked, field)
        overrides = cleaned
    for field in fields:
        if field == "taxonomy" and previous is not None and field not in overrides:
            if _taxonomy_locked(previous) and (choice["action"] != "create" or _taxonomy(previous) != _taxonomy(candidate)):
                raise ValueError("来源导入不能覆盖既有人物的手工分类")
        incoming = _value(candidate, field)
        value = deepcopy(overrides[field] if field in overrides else profile_field_merge(
            field, _value(updated, field), incoming, updated, candidate, source)["merged_value"])
        if field not in overrides and not has_value(field, incoming):
            raise ValueError("来源缺失字段不能清空本地资料；如确需清空须明确编辑该字段")
        if field == "avatar" and value:
            from .people_assets import read_avatar
            read_avatar(value["asset_id"], root=repository.root)
        if localization.is_field(field):
            localization.set_field(updated, field, value)
        elif people_infobox.is_field(field):
            people_infobox.set_field(updated, field, value)
        else:
            updated[field] = value
        if field in overrides:
            profile_variants.mark_manual(updated, field, previous)
    chinese_mirror = adopted_chinese_mirror(updated, candidate, fields)
    if chinese_mirror:
        updated["name_cn"] = chinese_mirror
    if creating and "localized" in updated:
        name = _value(updated, required[0])
        if not has_value(required[0], name):
            raise ValueError("创建人物必须明确确认非空名称；不能创建空白档案")
        updated["name"] = name
        for field, value in localization.compatibility(updated).items():
            if not updated.get(field) and value:
                updated[field] = deepcopy(value)
    updated.setdefault("source_refs", [])
    for source_ref in _sources(candidate):
        existing = next((row for row in updated["source_refs"] if all(row.get(key) == source_ref.get(key)
                         for key in ("provider", "entity_type", "external_id"))), None)
        if existing and source_ref["provider"] != source["provider"]:
            # Cross-provider identity evidence may link cards, but this refresh
            # did not fetch the other provider's profile/snapshot. Keep its
            # original revision, URL, timestamps and provenance byte-for-byte.
            continue
        updated["source_refs"] = [row for row in updated["source_refs"] if not all(row.get(key) == source_ref.get(key)
                                  for key in ("provider", "entity_type", "external_id"))] + [deepcopy(source_ref)]
    baseline = updated.setdefault("source_values", {}).setdefault(source["provider"], {})
    if not isinstance(baseline, dict):
        raise ValueError("已有来源字段基线无效，不能覆盖")
    for field in fields:
        baseline[field] = _value(candidate, field)
        slot = profile_variants.slot(updated, field)
        if (field not in overrides and slot and profile_variants.effective_preferences(updated)
                .get(slot[0], {}).get(slot[1], {}).get("locked")):
            continue
        evidence = candidate.get("field_sources", {}).get(field, source)
        updated.setdefault("field_sources", {})[field] = {**deepcopy(evidence), "manually_edited": field in overrides}
    profile_variants.collect_aliases(updated)
    profile_variants.refresh_projection_provenance(updated)
    return validate_entity(updated)


def compose(snapshot, choices, record, repository, pending=None):
    """Apply explicitly selected fields and additive links to an in-memory plan."""
    pending = {} if pending is None else pending
    entries = {entry["external_id"]: entry for entry in _entries(snapshot)}
    if not isinstance(choices, list) or len(choices) > MAX_ENTRIES:
        raise ValueError("人物选择必须为有界数组")
    index = build_source_index(repository)
    chosen, identities, changes = {}, {}, []
    for entry in entries.values():
        source = entry["candidate"]["source"]
        owner = _owner(entry["candidate"], index)
        if owner:
            identities[entry["external_id"]] = owner
    for choice in choices:
        if (not isinstance(choice, dict) or set(choice) - {"external_id", "action", "target_id", "fields", "overrides", "relations"}
                or not isinstance(choice.get("external_id"), str) or choice["external_id"] not in entries
                or not isinstance(choice.get("action"), str) or choice["action"] not in {"skip", "reuse", "create"}):
            raise ValueError("人物选择必须包含本次来源候选和明确动作")
        key = choice["external_id"]
        if key in chosen:
            raise ValueError("来源人物不能重复选择")
        chosen[key] = choice
        if choice["action"] == "skip":
            if choice.get("fields") or choice.get("overrides") or choice.get("relations"):
                raise ValueError("跳过人物不能同时采用字段或关系")
            continue
        entry, owner = entries[key], identities.get(key)
        if choice["action"] == "create":
            identity = entry["candidate"]["id"]
            if owner or repository.read(identity):
                raise ValueError("来源身份已有所有者或规范卡已存在，不能重复创建")
            previous = pending.get(identity)
            if previous and not any(all(ref.get(field) == entry["candidate"]["source"].get(field)
                                        for field in ("provider", "entity_type", "external_id"))
                                    for ref in previous.get("source_refs", [])):
                raise ValueError("本次候选规范身份冲突，不能重复创建")
        else:
            identity = entity_id(choice.get("target_id"))
            previous = pending.get(identity) or repository.read(identity)
            if not previous:
                raise ValueError("复用只能选择全库既有档案")
            reason = _blocked(entry["candidate"], previous, index)
            if reason:
                raise ValueError(reason)
        # In-batch bindings obey the same exclusive source ownership rule.
        source = entry["candidate"]["source"]
        for source in _sources(entry["candidate"]):
            for row in pending.values():
                if row["id"] != identity and any(all(ref.get(key) == source.get(key) for key in ("provider", "entity_type", "external_id"))
                                                  for ref in row.get("source_refs", [])):
                    raise ValueError("本次选择将同一来源身份绑定到不同档案，不能保存")
        entry = deepcopy(entry)
        if snapshot.get("_snapshot_id"):
            _stamp_sources(entry["candidate"], snapshot["_snapshot_id"])
        candidate = _selected(entry, choice, previous, repository)
        pending[identity] = candidate
        identities[key] = identity
        changes.append({"id": identity, "external_id": key, "type": candidate["type"], "name": candidate["name"],
                        "action": "create" if previous is None else "update", "changed_fields": list(choice.get("fields", []))})
        report_progress(f"已规划 {candidate['name']}：{len(choice.get('fields', []))} 个确认字段、{len(choice.get('relations', []))} 项确认关系",
                        context={"stage": "人物统一确认", "object": identity, "action": choice["action"]})
    canonical = {entry["candidate"]["id"]: key for key, entry in entries.items()}
    incoming = _mapped_work_refs(entries.values(), {identity: identities[key] for identity, key in canonical.items()
        if key in identities})

    def resolved(identity):
        key = canonical.get(identity)
        result = identities.get(key)
        if not result:
            raise ValueError("所选关系依赖的人物或角色尚未明确创建/复用：" + str(key))
        return result

    work = deepcopy(record)
    refs = validate_people_refs(work.get("people_refs", []))
    for key, choice in chosen.items():
        if choice["action"] == "skip":
            continue
        entry, identity = entries[key], identities[key]
        relations = choice.get("relations", [])
        available = {row["id"]: row for row in entry["relations"]}
        if (not isinstance(relations, list) or any(not isinstance(value, str) for value in relations)
                or len(set(relations)) != len(relations) or set(relations) - available.keys()):
            raise ValueError("只能明确选择本次预览中的作品或声优关系")
        entity = pending[identity]
        for relation_id in relations:
            relation = available[relation_id]
            for dependency in relation["required_entity_keys"]:
                if dependency not in identities:
                    raise ValueError("关系依赖尚未选择创建/复用：" + dependency)
            if relation["kind"] == "voice_actor":
                actor = deepcopy(relation["actor"])
                actor["person_id"] = resolved(actor["person_id"])
                from people_catalog.taxonomy import is_real_person
                if not is_real_person(pending.get(actor["person_id"]) or repository.read(actor["person_id"])):
                    raise ValueError("声优关系必须指向已明确分类的现实人物，不能指向组织或物件")
                if actor not in entity.setdefault("voice_actors", []):
                    entity["voice_actors"].append(actor)
            else:
                ref = deepcopy(relation["ref"])
                ref["id"] = identity
                if ref.get("character_name"):
                    from people_catalog.taxonomy import is_real_person
                    if not is_real_person(entity):
                        raise ValueError("饰演关系只能关联已明确分类的现实人物")
                if ref.get("character_id"):
                    original_character = ref["character_id"]
                    ref["character_id"] = resolved(original_character)
                    # Role provenance links an actor to a character. Persist it
                    # only when that relationship was explicitly confirmed,
                    # never when importing the character's profile alone.
                    character = deepcopy(pending.get(ref["character_id"]) or repository.read(ref["character_id"]))
                    from people_catalog.taxonomy import is_fictional_person
                    if not is_fictional_person(character):
                        raise ValueError("饰演关系必须指向虚构人物角色，不能是组织、物件或现实人物")
                    role_entry = entries[canonical[original_character]]
                    for origin in role_entry.get("role_origins", []):
                        if origin["actor_id"] != entry["candidate"]["id"]:
                            continue
                        from people_catalog import portrayal
                        if portrayal.stage_for(ref) and not portrayal.compatible_stage(origin, ref):
                            continue
                        origin = {**deepcopy(origin), "actor_id": identity}
                        from .role_catalog import retain_origin
                        retain_origin(character.setdefault("role_origins", []), origin)
                    pending[character["id"]] = validate_entity(character)
                same = _find_work_relation(refs, ref, incoming)
                if same is None:
                    if ref not in refs:
                        refs.append(ref)
                else:
                    baseline = same.get("source_values", {})
                    for field, value in ref.items():
                        if field in {"id", "type", "source", "source_values", "character_id"}:
                            continue
                        manual = field in same and (same[field] != baseline[field] if field in baseline else same[field] == "")
                        if not manual:
                            same[field] = deepcopy(value)
                        if field in {"character_name", "character_name_cn"}:
                            same.setdefault("source_values", {})[field] = deepcopy(value)
                    if ref.get("character_id"):
                        same["character_id"] = ref["character_id"]
        pending[identity] = validate_entity(entity)
    if refs or "people_refs" in work:
        work["people_refs"] = validate_people_refs(refs)
    return work, pending, changes


def apply_proposals(proposals, item, final_record, *, settings, people_root=None, data_root=None):
    from . import people, people_import_batch
    repository = PeopleRepository(people_root)
    repository.root.mkdir(parents=True, exist_ok=True)
    with directory_write_transaction(repository.root), catalog_write_transaction(safe_path(settings.filesystem_root)):
        versions = _inventory(repository)
        captured = _cached_repository(repository, versions)
        pending, combined, changes, warnings = {}, deepcopy(final_record), [], []
        for proposal in proposals:
            if (proposal.get("selection_only") is not True or proposal.get("ref") != item["ref"]
                    or proposal.get("scope") != providers._catalog_scope(settings)
                    or proposal.get("people_scope") != people._root_scope(repository)
                    or proposal.get("inventory_sha256") != _hash(versions)):
                raise ValueError("统一人物预览或全库版本已变化，请重新预览")
            people._proposal_source(proposal, final_record)
            snapshot = _snapshot(proposal, repository)
            display_repository = people_import_batch.repository_for(proposal, captured)
            display, _ = preview(snapshot, proposal["snapshot_id"], item["record"], display_repository)
            if proposal.get("selection_plan_sha256") != _hash(display):
                raise ValueError("人物候选或差异已经变化，请重新预览")
            people_import_batch.validate_choices(proposal, snapshot, proposal.get("_people_selections", []), captured, pending)
            combined, pending, added = compose(snapshot, proposal.get("_people_selections", []), combined, captured, pending)
            changes.extend(added)
            warnings.extend(snapshot.get("warnings", []))
        if _inventory(repository) != versions:
            raise ValueError("人物数据库在规划期间发生并发变化")
        writes = [row for row in pending.values() if row != captured.read(row["id"])]
        if not writes and combined == item["record"]:
            return {"ok": True, "records": [], "unchanged": True, "people_updated": False,
                    "people_changes": changes, "warnings": list(dict.fromkeys(warnings)),
                    "people": people.project(combined, people_root=people_root)}

        def save_work():
            current = _inventory(repository)
            if ({key: value for key, value in current.items() if key not in pending}
                    != {key: value for key, value in versions.items() if key not in pending}):
                raise ValueError("保存期间出现新的来源所有者或人物变更")
            saved = _cached_repository(repository, current)
            index = build_source_index(saved)
            for row in pending.values():
                if saved.read(row["id"]) != row:
                    raise ValueError("选中人物在保存期间已变化")
                for source in row.get("source_refs", []):
                    if index.find(source["provider"], source["entity_type"], source["external_id"]) != row["id"]:
                        raise ValueError("来源身份不再唯一归属于选中人物")
            if _inventory(repository) != current:
                raise ValueError("人物数据库在最终来源归属校验期间已变化")
            return service.apply_edits({"edits": [{"ref": item["ref"], "record": combined}]}, settings=settings, data_root=data_root)

        result = repository.apply_batch(writes, expected_revision=_revision(versions, list(pending)),
                                        revision_ids=list(pending), after_write=save_work)
    report_progress(f"统一人物导入已保存：{len(writes)} 项档案、{len(changes)} 项确认；作品与人物一次事务提交",
                    context={"stage": "人物统一保存", "action": "事务完成", "count": len(writes)})
    return {**result, "people_updated": bool(writes or combined.get("people_refs") != item["record"].get("people_refs")),
            "people_changes": changes, "warnings": list(dict.fromkeys(warnings)),
            "people": people.project(combined, people_root=people_root)}
