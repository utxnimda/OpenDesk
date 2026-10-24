"""Compose a selected supplement with existing people sources before confirmation.

The original work remains the CAS/hash baseline. Only the in-memory source
context passed to the shared people importer contains the newly chosen page.
"""
from copy import deepcopy
from .source_bindings import normalized_refs

MAX_PEOPLE_BINDINGS = 4


def _snapshot(proposal, repository):
    from . import providers
    return providers._read_snapshot(proposal["snapshot_id"], proposal["snapshot_id"],
        repository.root / "sources" / proposal["provider"], provider=proposal["provider"])


def _same_tmdb(proposal, binding):
    return (proposal.get("provider") == "tmdb" and str(proposal.get("subject_id")) == str(binding.get("external_id"))
            and proposal.get("season_number") == binding.get("season_number")
            and proposal.get("episode_range") == binding.get("episode_range"))


def _independent_remainder(snapshot, enriched, subject_id, repository):
    """Drop only explicit actor/role evidence already represented by TMDB.

    No name-based person merge is performed here. Additional Wiki-only roles
    retain their own proposal; their actor receives a source identity only when
    the linked Wiki actor article was uniquely verified by the enrichment.
    """
    data = enriched.get("raw", {}).get("wikipedia_people", {}) if enriched else {}
    covered = set()
    for relation in data.get("relations", []):
        evidence = relation.get("evidence", {})
        for row in [{"source": relation.get("source", {}), **evidence}, *evidence.get("translations", [])]:
            if str(row.get("source", {}).get("pageid")) == str(subject_id):
                covered.add((row.get("actor_cell", ""), row.get("role_cell", "")))
    raw = snapshot["raw"]
    raw["relations"] = [row for row in raw.get("relations", [])
        if (row.get("evidence", {}).get("actor_cell", ""), row.get("evidence", {}).get("role_cell", "")) not in covered]
    # Covered performances do not make an actor's Wiki biography/infobox
    # redundant. Keep those profile fields, binding to the verified TMDB actor
    # rather than creating another card; only duplicate work roles are removed.
    matches = {}
    for match in data.get("actor_matches", []):
        matches.setdefault(match["wikipedia_pageid"], set()).add(str(match["tmdb_person_id"]))
    from people_catalog.source_links import build_source_index
    owners = build_source_index(repository)
    for profile in raw["profiles"]:
        identities = matches.get(profile["external_id"], set())
        if len(identities) == 1:
            identity = next(iter(identities))
            wiki_owner = owners.find("wikipedia", "person", profile["external_id"])
            tmdb_owner = owners.find("tmdb", "person", identity)
            if wiki_owner and tmdb_owner and wiki_owner != tmdb_owner:
                snapshot.setdefault("warnings", []).append(
                    f"演员 {profile.get('name_cn') or profile.get('name')} 的 Wiki {profile['external_id']} 与 TMDB {identity} "
                    f"分别属于 {wiki_owner} 和 {tmdb_owner}；保留两张资料卡，请手工核对合并，其他人物仍继续预览。")
                continue
            profile["source_refs"] = [{"provider": "tmdb", "entity_type": "person", "external_id": identity,
                "source_url": "https://www.themoviedb.org/person/" + identity}]
    return snapshot


def preview(item, snapshot, *, settings, people_root=None, deadline=None, replace_binding_id=None,
            excluded_bindings=(), prior_people_proposals=()):
    from people_catalog.repository import PeopleRepository
    from . import people, people_import, providers, wikipedia_people
    if snapshot.get("provider") != "wikipedia":
        raise ValueError("人物补充上下文目前仅支持中文维基百科")
    subject_id = providers._positive_id(snapshot.get("subject_id"))
    context = deepcopy(item["record"])
    refs = normalized_refs(context)
    previous = next((ref for ref in refs if ref["binding_id"] == replace_binding_id), None) if replace_binding_id else next(
        (ref for ref in refs if ref.get("provider") == "wikipedia" and str(ref.get("external_id")) == str(subject_id)), None)
    selected = {"provider": "wikipedia", "external_id": str(subject_id), "role": "supplement", "enabled": True, "scope": ""}
    if previous:
        selected.update({key: previous[key] for key in ("binding_id", "role", "enabled")})
        refs = [ref for ref in refs if ref["binding_id"] != previous["binding_id"]]
    selected = normalized_refs({"source_refs": [selected]})[0]
    # This preview is explicitly for the chosen page, not a refresh of every
    # other Wiki binding. Keep other bindings in the actual work untouched.
    context["source_refs"] = [*[ref for ref in refs if ref.get("provider") != "wikipedia"], selected]
    if not selected["enabled"]:
        return {"proposals": [], "preview": None, "warnings": []}
    proposals, previews, warnings, enriched = [], [], [], None
    repository = PeopleRepository(people_root)
    tmdb = [ref for ref in refs if ref.get("provider") == "tmdb" and ref.get("enabled") is True]
    # One work may bind several seasons/series. A Wiki page alone does not prove
    # which scope owns each performance; keep those cases explicit and separate.
    if len(tmdb) > 1:
        warnings.append("当前作品关联多个 TMDB 作品或季，维基角色未自动跨范围补充；请核对独立人物候选。")
    if len(tmdb) == 1:
        binding = tmdb[0]
        prior = next((proposal for proposal in prior_people_proposals if _same_tmdb(proposal, binding)), None)
        try:
            dependency = prior.get("supplement_dependency", {}) if prior else {}
            if prior and dependency.get("external_id") not in {None, str(subject_id)}:
                # Another selected Wiki page has already enriched this exact
                # TMDB scope in the current batch. Preserve that signed plan;
                # replacing it would silently drop its role translations.
                enriched = deepcopy(_snapshot(prior, repository))
                result = None
                warnings.append("本批已有另一维基条目补充同一 TMDB 范围；保留前项，当前条目作为独立候选核对，不覆盖前项资料。")
            elif prior:
                enriched = deepcopy(_snapshot(prior, repository))
                enriched.get("raw", {}).pop("wikipedia_people", None)
                wikipedia_people.enrich(enriched, context, deadline=deadline)
                result = people_import.prepare(enriched, item, settings=settings, repository=repository)
            elif binding["binding_id"] not in excluded_bindings:
                result = people.preview_for_import(item, binding["external_id"], settings=settings, people_root=people_root,
                    deadline=deadline, provider="tmdb", season_number=binding.get("season_number"),
                    episode_range=binding.get("episode_range"), supplement_record=context)
                enriched = _snapshot(result["proposal"], repository) if result and result.get("proposal") else None
            else:
                result = None
            if result and result.get("proposal") and result.get("preview"):
                result["proposal"]["binding_id"] = binding["binding_id"]
                proposals.append(result["proposal"])
                previews.append(result["preview"])
                warnings.extend(result["preview"].get("warnings", []))
        except (ValueError, OSError, RuntimeError) as error:
            enriched = None
            warnings.append("TMDB 人物的维基补充未完成，保留独立维基候选：" + str(error)[:500])
    # Wikipedia remains independent when no TMDB identity exists or transport
    # fails. Its additional performances are not discarded with covered ones.
    try:
        independent = wikipedia_people.fetch_people_snapshot(subject_id, source_snapshot=snapshot, deadline=deadline,
            original_language=enriched.get("original_language") if enriched else None,
            evidence_snapshot=enriched.get("raw", {}).get("wikipedia_people") if enriched else None)
        warnings.extend(independent.get("warnings", []))
        independent = _independent_remainder(independent, enriched, subject_id, repository)
        warnings.extend(independent.get("warnings", []))
        if independent["raw"].get("profiles") or independent["raw"].get("unresolved"):
            result = people_import.prepare(independent, item, settings=settings, repository=repository)
            result["proposal"]["binding_id"] = selected["binding_id"]
            proposals.append(result["proposal"])
            previews.append(result["preview"])
    except (ValueError, OSError, RuntimeError) as error:
        warnings.append("维基独立人物预览未完成，作品资料仍可采用：" + str(error)[:500])
    for proposal in proposals:
        proposal["supplement_dependency"] = {"provider": "wikipedia", "external_id": str(subject_id),
                                             "binding_id": selected["binding_id"]}
    warnings = list(dict.fromkeys(warnings))
    return {"proposals": proposals, "preview": {"sources": previews, "changes": [], "warnings": warnings} if previews else None,
            "warnings": warnings}


def validate(proposals, *, provider, subject_id, final_record=None):
    """Validate signed children and their selected supplement dependency."""
    if not isinstance(proposals, list) or len(proposals) > MAX_PEOPLE_BINDINGS:
        raise ValueError("人物补充提案列表无效，请重新预览")
    for proposal in proposals:
        dependency = proposal.get("supplement_dependency") if isinstance(proposal, dict) else None
        if (not isinstance(proposal, dict) or provider != "wikipedia" or proposal.get("provider") not in {"tmdb", "wikipedia"} or not isinstance(dependency, dict)
                or dependency.get("provider") != provider or dependency.get("external_id") != str(subject_id)):
            raise ValueError("人物补充提案与维基来源不一致，请重新预览")
        if final_record is not None and not any(ref.get("provider") == dependency["provider"]
                and str(ref.get("external_id")) == dependency["external_id"]
                and ref["binding_id"] == dependency.get("binding_id") and ref["enabled"]
                for ref in normalized_refs(final_record)):
            raise ValueError("人物补充依赖的维基来源未关联或已停用，请重新预览")
    return proposals
