"""Small, read-only thumbnails from audited game sprite animation frames.

Only known sprite keys are accepted. The game atlas and decoded full image are
short-lived; the bounded cache holds only small PNG thumbnails and metadata.
"""
from __future__ import annotations

import base64
from collections import OrderedDict, defaultdict
from functools import lru_cache
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
import warnings
from zipfile import BadZipFile, ZipFile

from .reader import ReaderError, RULE_PROFILES, _audited_package, _inside, _json, _root


MAX_KEYS = 32
MAX_ATLAS_BYTES = 8 * 1024 * 1024
MAX_ATLAS_SIDE = 4096
MAX_ATLAS_PIXELS = 16 * 1024 * 1024
MAX_PNG_BYTES = 32768
MAX_RESPONSE_BYTES = 1800 * 1024
THUMBNAIL_SIDE = 64
_KEY = re.compile(r"(?:person|item):[^\x00-\x1f\x7f/\\]{1,100}\Z")
_MEMBER = re.compile(r"images/(?:角色形象|物品图标)-sheet[0-9]{1,2}\.webp\Z")
_CACHE: OrderedDict[tuple, tuple[bytes, int, int]] = OrderedDict()
_CACHE_LIMIT = 512


class VisualError(ReaderError):
    """Readable visual lookup or image validation failure."""


def _signature(path: Path) -> tuple[str, int, int, int]:
    info = path.stat()
    return str(path), info.st_size, info.st_mtime_ns, info.st_ino


@lru_cache(maxsize=2)
def _catalogue(signature: tuple) -> dict[str, tuple[str, int, int, int, int]]:
    with ZipFile(signature[0]) as package:
        try:
            profile, members = _audited_package(package)
        except ReaderError as error:
            raise VisualError(str(error)) from error
        raw = members["data.json"]
        project = _json(raw, "图标索引")["project"]
        objects = project[3]
        result = {}
        for object_index, name, prefix in RULE_PROFILES[profile]["sprites"]:
            sprite = objects[object_index]
            if sprite[0] != name or len(sprite[7]) > 1200:
                raise VisualError("游戏图标对象映射不一致。")
            for animation in sprite[7]:
                key = f"{prefix}:{animation[0]}"
                if not _KEY.fullmatch(key):
                    continue
                frame = animation[7][0]
                member, x, y, width, height = frame[0], frame[2], frame[3], frame[4], frame[5]
                if not isinstance(member, str) or not _MEMBER.fullmatch(member):
                    raise VisualError("图标引用了未允许的资源。")
                if any(type(v) is not int for v in (x, y, width, height)) or min(x, y) < 0 or min(width, height) <= 0:
                    raise VisualError("图标裁剪坐标无效。")
                if max(x + width, y + height) > MAX_ATLAS_SIDE:
                    raise VisualError("图标裁剪坐标超出支持范围。")
                if key in result:
                    raise VisualError("图标动画名称重复，无法确定对应形象。")
                result[key] = (member, x, y, width, height)
        return result


def _remember(cache_key: tuple, data: tuple[bytes, int, int]) -> None:
    _CACHE[cache_key] = data
    _CACHE.move_to_end(cache_key)
    while len(_CACHE) > _CACHE_LIMIT:
        _CACHE.popitem(last=False)


def _decode_group(raw: bytes, entries: list[tuple[str, tuple]], signature: tuple) -> None:
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as error:
        raise VisualError("缺少游戏图标解码组件 Pillow；文字与分析功能仍可使用。") from error
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as atlas:
                width, height = atlas.size
                if atlas.format != "WEBP" or width > MAX_ATLAS_SIDE or height > MAX_ATLAS_SIDE or width * height > MAX_ATLAS_PIXELS:
                    raise VisualError("游戏图集格式或解压尺寸超出支持范围。")
                if getattr(atlas, "is_animated", False):
                    raise VisualError("不支持含动画的游戏图集。")
                atlas.load()
                for key, (_, x, y, crop_width, crop_height) in entries:
                    if x + crop_width > width or y + crop_height > height:
                        raise VisualError("人物或物品图标超出图集边界。")
                    with atlas.crop((x, y, x + crop_width, y + crop_height)) as crop:
                        with crop.convert("RGBA") as thumbnail:
                            thumbnail.thumbnail((THUMBNAIL_SIDE, THUMBNAIL_SIDE), Image.Resampling.LANCZOS)
                            stream = BytesIO()
                            thumbnail.save(stream, format="PNG", optimize=False)
                            png = stream.getvalue()
                            if len(png) > MAX_PNG_BYTES:
                                raise VisualError("图标压缩结果超出安全大小。")
                            _remember((signature, key), (png, thumbnail.width, thumbnail.height))
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise VisualError("游戏图集损坏或无法安全解码，文字信息仍可使用。") from error


def load(game_dir: str | Path, icon_keys: list[str]) -> dict:
    """Return at most 32 exact 64px thumbnails, never arbitrary package files."""
    if not isinstance(icon_keys, list) or len(icon_keys) > MAX_KEYS:
        raise VisualError("一次最多读取 32 个游戏图标。")
    if any(not isinstance(key, str) or not _KEY.fullmatch(key) for key in icon_keys):
        raise VisualError("图标编号无效，不能使用文件路径。")
    keys = list(dict.fromkeys(icon_keys))
    root = _root(game_dir)
    package_path = _inside(root, "package.nw")
    signature = _signature(package_path)
    try:
        catalogue = _catalogue(signature)
        missing = [key for key in keys if key not in catalogue]
        requested = [key for key in keys if key in catalogue]
        groups: dict[str, list] = defaultdict(list)
        for key in requested:
            cache_key = (signature, key)
            if cache_key in _CACHE:
                _CACHE.move_to_end(cache_key)
            else:
                frame = catalogue[key]
                groups[frame[0]].append((key, frame))
        if groups:
            with ZipFile(package_path) as package:
                for member, entries in groups.items():
                    info = package.getinfo(member)
                    if info.file_size > MAX_ATLAS_BYTES:
                        raise VisualError("游戏图集文件超出支持大小。")
                    raw = package.read(info)
                    _decode_group(raw, entries, signature)
        if _signature(package_path) != signature:
            raise VisualError("读取图标期间游戏包发生变化，请稍后重试。")
        sheets = {}
        sprites = {}
        for key in requested:
            png, width, height = _CACHE[(signature, key)]
            sheet_id = "icon-" + sha256(key.encode("utf-8")).hexdigest()[:24]
            sheets[sheet_id] = {"dataUrl": "data:image/png;base64," + base64.b64encode(png).decode("ascii"),
                                "width": width, "height": height}
            sprites[key] = {"sheetId": sheet_id, "x": 0, "y": 0, "width": width, "height": height}
        result = {"schemaVersion": 1, "sheets": sheets, "sprites": sprites,
                  "warnings": [f"没有找到图标 {key}，使用文字或轮廓占位。" for key in missing]}
        if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise VisualError("图标响应超出大小限制，请减少单次图标数量。")
        return result
    except (BadZipFile, KeyError, IndexError, TypeError, AttributeError, OSError) as error:
        raise VisualError("游戏图标资源不完整，文字信息仍可使用。") from error
