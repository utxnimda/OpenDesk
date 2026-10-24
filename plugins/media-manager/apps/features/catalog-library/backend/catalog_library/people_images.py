"""Explicit portrait selection reusing signed person previews and local assets."""
from __future__ import annotations

import base64
import binascii
from copy import deepcopy
import re
import time

from people_catalog.model import validate_avatar_reference
from people_catalog.repository import PeopleRepository
from work_catalog_yaml.operation_progress import report_progress

from . import assets, people_assets, providers

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_REQUEST_BYTES = 3 * 1024 * 1024
_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_PROVIDERS = {"local", "bangumi", "wikipedia", "commons", "tmdb-still"}
_FILE_NAME = re.compile(r"[^<>:\"/\\|?*\x00-\x1f\x7f]{1,255}\Z")
_EVIDENCE_FIELDS = {"provider", "external_id", "entity_type", "title", "source_url", "page_url", "file_title",
                    "author", "license", "license_url", "description", "file_name", "mime_type", "description_url", "credit",
                    "image_kind", "season_number", "episode_number", "file_path"}


def _body(value, allowed):
    # Image providers are intentionally separate from work/metadata providers.
    # Extending the global provider enum would expose unsupported import flows.
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError("图片请求必须为仅包含所支持字段的对象")
    return value


def _upload(body, person, people_root):
    filename, mime, encoded = body.get("file_name"), body.get("mime_type"), body.get("image_base64")
    if (not isinstance(filename, str) or not _FILE_NAME.fullmatch(filename) or filename in {".", ".."}
            or filename.endswith((".", " "))):
        raise ValueError("上传图片必须提供普通文件名，不能包含目录或路径")
    if not isinstance(mime, str) or mime not in _MIMES:
        raise ValueError("上传图片只支持 JPEG、PNG、GIF 或 WebP，不接受 SVG")
    if (not isinstance(encoded, str) or not encoded or len(encoded) > ((MAX_UPLOAD_BYTES + 2) // 3) * 4):
        raise ValueError("上传图片为空或超过 2 MiB 上限")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("上传图片必须为有效的纯 Base64 图片数据") from None
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("上传图片为空或超过 2 MiB 上限")
    info = assets.store_image(data, mime, root=people_assets.asset_root(people_root), label="手动人物图片")
    asset_id = info["sha256"] + "." + info["extension"]
    return {"avatar": {"asset_id": asset_id, "source_url": "local:" + asset_id},
            "evidence": {"provider": "local", "entity_type": person["type"], "file_name": filename,
                         "mime_type": mime, "source_url": "local:" + asset_id}, "warnings": []}


def validate_candidate(avatar, evidence, person):
    """Validate cached-image proof without any provider or network operation."""
    avatar = validate_avatar_reference(avatar)
    if not avatar or not isinstance(evidence, dict) or set(evidence) - _EVIDENCE_FIELDS:
        raise ValueError("人物图片候选或来源证据无效")
    if (not isinstance(evidence.get("provider"), str) or evidence.get("provider") not in _PROVIDERS or evidence.get("entity_type") != person["type"]
            or evidence.get("source_url") != avatar["source_url"]):
        raise ValueError("人物图片来源证据与候选图片或人物类型不一致")
    if any(not isinstance(value, str) or len(value) > 6000 or "\x00" in value for value in evidence.values()):
        raise ValueError("人物图片来源说明必须为有界文本")
    if evidence["provider"] == "local":
        if avatar["source_url"] != "local:" + avatar["asset_id"]:
            raise ValueError("本地上传图片引用与来源不一致")
    elif not avatar["source_url"].startswith("https://") or not evidence.get("external_id"):
        raise ValueError("远程人物图片必须包含明确来源身份和 HTTPS 图片地址")
    return avatar, deepcopy(evidence)


def preview(body, *, settings=None, people_root=None, data_root=None, source_root=None):
    from . import people_library, people_image_sources
    body = _body(body, {"person_id", "version", "provider", "external_id", "file_name", "mime_type", "image_base64", "candidate_token"})
    provider = body.get("provider")
    if not isinstance(provider, str) or provider not in _PROVIDERS:
        raise ValueError("请选择支持的人物图片来源")
    expected = ({"person_id", "version", "provider", "candidate_token"} if provider == "tmdb-still" else
                {"person_id", "version", "provider", "file_name", "mime_type", "image_base64"} if provider == "local"
                else {"person_id", "version", "provider", "external_id"})
    if set(body) != expected:
        raise ValueError("图片预览只接受所选来源的明确身份或本地上传内容，不能提交远程图片网址或路径")
    repository = PeopleRepository(people_root)
    person, revision = people_library._verify_version(body, repository)
    report_progress("正在准备人物图片候选；确认前不会更改人物资料",
                    context={"stage": "人物图片预览", "provider": provider, "object": person["id"]})
    if provider == "local":
        result = _upload(body, person, repository.root)
    elif provider == "tmdb-still":
        result = _selected_candidate(body, person, revision, repository)
    else:
        identity = body["external_id"]
        if not isinstance(identity, (str, int)) or isinstance(identity, bool) or not str(identity).strip() or len(str(identity)) > 2000:
            raise ValueError("请提供明确的图片来源条目 ID、标题或支持的条目链接")
        result = people_image_sources.fetch_image(provider, str(identity), entity_type=person["type"],
            people_root=repository.root, previous=person.get("avatar"), deadline=time.monotonic() + 35)
    avatar, evidence = validate_candidate(result.get("avatar"), result.get("evidence"), person)
    if evidence["provider"] != provider:
        raise ValueError("返回的图片来源与所选择的来源不一致")
    people_assets.read_avatar(avatar["asset_id"], root=repository.root)
    preview = people_library._preview(person, revision, "image", {"avatar": avatar}, repository,
        selected=("avatar",), warnings=result.get("warnings", []), source=evidence, source_root=source_root)
    return {**preview, "current_avatar_url": people_assets.avatar_url(person.get("avatar")),
            "candidate_avatar_url": people_assets.avatar_url(avatar), "image_source": evidence}


def apply(body, *, settings=None, people_root=None, data_root=None, source_root=None):
    from . import people_library
    return people_library._apply(body, "image", settings=settings, people_root=people_root,
                                 data_root=data_root, source_root=source_root)


def candidates(body, *, settings=None, people_root=None, data_root=None, source_root=None):
    from . import people, people_image_candidates, people_library
    body = _body(body, {"person_id", "version", "provider", "external_id", "media_type", "season_number", "episode_number", "page"})
    if body.get("provider") != "tmdb":
        raise ValueError("剧照候选只支持明确选择的 TMDB 作品或章节")
    repository = PeopleRepository(people_root)
    person, revision = people_library._verify_version(body, repository)
    target = people_image_candidates.parse_target(body.get("external_id"), media_type=body.get("media_type", "tv"),
        season_number=body.get("season_number"), episode_number=body.get("episode_number"))
    result = people_image_candidates.fetch_candidates(target, page=body.get("page", 1), people_root=repository.root,
                                                       deadline=time.monotonic() + 45)
    if repository.revision([person["id"]]) != revision:
        raise ValueError("人物在读取剧照候选期间已变化，请重新打开图片选择")
    rows = []
    for candidate in result.get("candidates", []):
        if candidate.get("selectable") is False:
            rows.append({**deepcopy(candidate), "candidate_token": ""})
            continue
        payload = {"kind": "people-library-image-candidate", "person_id": person["id"], "revision": revision,
                   "people_scope": people._root_scope(repository), "expires": int(time.time()) + providers.PREVIEW_LIFETIME,
                   "target": target, "candidate": candidate}
        rows.append({**deepcopy(candidate), "candidate_token": providers._sign(payload)})
    return {**result, "ok": True, "person_id": person["id"], "candidates": rows}


def _selected_candidate(body, person, revision, repository):
    from . import people, people_image_candidates
    payload = providers._verify_token(body.get("candidate_token"))
    if (payload.get("kind") != "people-library-image-candidate" or payload.get("person_id") != person["id"]
            or payload.get("revision") != revision or payload.get("people_scope") != people._root_scope(repository)):
        raise ValueError("剧照候选凭据不属于当前人物或人物资料已变化，请重新读取候选")
    target, candidate = payload.get("target"), payload.get("candidate")
    result = people_image_candidates.download_candidate(target, candidate, people_root=repository.root,
                                                         deadline=time.monotonic() + 25)
    return {**result, "evidence": {**result["evidence"], "entity_type": person["type"], "source_url": result["avatar"]["source_url"]},
        "warnings": [*result.get("warnings", []), "剧照由你手动选为此人物的图片，不代表来源 API 已确认画面中的人物身份。"]}
