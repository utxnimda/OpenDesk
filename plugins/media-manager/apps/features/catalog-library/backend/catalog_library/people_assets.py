"""Bangumi portrait adapter using shared bounded image I/O and local storage.

Only explicit source refreshes acquire images. Snapshot application and normal
profile/image views are offline: they use content-addressed local references.
"""
from __future__ import annotations

from pathlib import Path
import re
from urllib.parse import urlsplit

from people_catalog.model import validate_avatar_reference
from work_catalog_yaml.layout import feature_data_root

from . import assets

_PORTRAIT_PATH = re.compile(
    r"/(?:r/[1-9][0-9]{0,3}(?:x[1-9][0-9]{0,3})?/)?pic/crt/[lcmsg]/"
    r"(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.(?:jpe?g|png|gif|webp)\Z"
)
_REVISION_QUERY = re.compile(r"r=[0-9]{1,20}\Z")
def validate_source_url(value):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(char) < 33 for char in value):
        raise ValueError("人物图片地址缺失或无效")
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme == "https" and parsed.hostname == "lain.bgm.tv" and parsed.port in {None, 443}
                 and parsed.username is None and parsed.password is None and not parsed.fragment
                 and (not parsed.query or _REVISION_QUERY.fullmatch(parsed.query))
                 and _PORTRAIT_PATH.fullmatch(parsed.path))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("人物图片地址不属于允许的 Bangumi HTTPS 人物 CDN 路径")
    return value


def select_source_url(raw):
    images = raw.get("images") if isinstance(raw, dict) else None
    if images is None:
        return None
    if not isinstance(images, dict):
        raise ValueError("人物图片地址列表格式无效")
    for key in ("medium", "large", "common", "small", "grid"):
        if images.get(key):
            return validate_source_url(images[key])
    return None


def asset_root(root=None):
    """The optional root always denotes the independent people repository."""
    people_root = Path(root) if root is not None else feature_data_root("people-catalog")
    return assets.asset_root(people_root / "assets", label="人物图片")


def read_avatar(filename, *, root=None):
    return assets.read_asset(filename, root=asset_root(root), label="人物图片")


def avatar_url(avatar):
    if not avatar:
        return ""
    try:
        validate_avatar_reference(avatar)
    except (ValueError, TypeError):
        return ""
    return f"/api/catalog-library/people/assets/{avatar['asset_id']}"


def acquire_avatar(raw, *, user_agent, root=None, deadline=None, previous=None):
    """Cache a portrait, or raise a bounded diagnostic for the optional adapter.

    A failure never changes an existing reference/file. The caller should retain
    the old avatar and report a warning without aborting the profile import.
    """
    url = select_source_url(raw)
    if url is None:
        raise ValueError("Bangumi 人物没有图片地址")
    # An unchanged source is already local. A missing file may be reacquired;
    # corrupted or linked content is never silently overwritten by store_image.
    if previous:
        try:
            validate_avatar_reference(previous)
            if previous["source_url"] == url:
                read_avatar(previous["asset_id"], root=root)
                return dict(previous)
        except (ValueError, OSError):
            pass
    target = asset_root(root)
    data, mime = assets.download_image(url, user_agent=user_agent, validate_url=validate_source_url,
                                       deadline=deadline, label="人物图片")
    info = assets.store_image(data, mime, root=target, label="人物图片")
    return {"asset_id": f"{info['sha256']}.{info['extension']}", "source_url": url}


def acquire_tmdb_avatar(raw, *, user_agent, root=None, deadline=None, previous=None):
    """Fixed TMDB profile-image adapter; never accepts an arbitrary remote URL."""
    from . import tmdb_source
    url = tmdb_source._image_url(raw.get("profile_path"))
    if previous:
        try:
            validate_avatar_reference(previous)
            if previous["source_url"] == url:
                read_avatar(previous["asset_id"], root=root)
                return dict(previous)
        except (ValueError, OSError):
            pass
    data, mime = assets.download_image(url, user_agent=user_agent, validate_url=tmdb_source.validate_cover_url,
                                      deadline=deadline, label="TMDB 人物图片")
    info = assets.store_image(data, mime, root=asset_root(root), label="人物图片")
    return {"asset_id": f"{info['sha256']}.{info['extension']}", "source_url": url}
