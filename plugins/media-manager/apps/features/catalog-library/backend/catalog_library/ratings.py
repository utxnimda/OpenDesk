"""Local, source-separated rating values shared by imports and projections."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import math
import re
from urllib.parse import urlsplit


def validate_rating(provider, value):
    if not isinstance(provider, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", provider):
        raise ValueError("评分来源名称无效")
    keys = {"score", "scale", "votes", "source_url", "external_id", "fetched_at"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"{provider} 评分必须包含评分、满分、人数、来源链接、条目 ID 和获取时间")
    score, scale, votes = value["score"], value["scale"], value["votes"]
    if (type(score) not in {int, float} or type(scale) not in {int, float}
            or not 0 < scale <= 100 or not 0 <= score <= scale or not math.isfinite(score) or not math.isfinite(scale)):
        raise ValueError(f"{provider} 评分或满分范围无效")
    if type(votes) is not int or not 0 <= votes <= 10**12:
        raise ValueError(f"{provider} 评分人数必须为非负整数")
    identity, url, fetched = value["external_id"], value["source_url"], value["fetched_at"]
    if not isinstance(identity, str) or not identity.strip() or len(identity) > 200:
        raise ValueError("评分来源条目 ID 无效")
    if not isinstance(url, str) or len(url) > 2048 or any(ord(char) < 33 for char in url) or "\\" in url:
        raise ValueError("评分来源链接无效")
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None
                or parsed.port not in {None, 443} or parsed.fragment):
            raise ValueError
    except ValueError:
        raise ValueError("评分来源必须为无身份信息的 HTTPS 链接") from None
    if provider == "douban" and (not re.fullmatch(r"[1-9][0-9]{0,11}", identity) or scale != 10
                                  or url != f"https://movie.douban.com/subject/{identity}/"):
        raise ValueError("豆瓣评分必须指向同一作品的豆瓣电影条目，满分为 10")
    try:
        if not isinstance(fetched, str) or len(fetched) > 64:
            raise ValueError
        timestamp = datetime.fromisoformat(fetched)
        if timestamp.tzinfo is None:
            raise ValueError
    except ValueError:
        raise ValueError("评分获取时间必须为含时区的 ISO 日期时间") from None
    return deepcopy(value)


def validate_ratings(value):
    if not isinstance(value, dict) or len(value) > 32:
        raise ValueError("metadata.ratings 必须为最多 32 个来源的对象")
    return {provider: validate_rating(provider, rating) for provider, rating in value.items()}


def project_ratings(value):
    """An invalid optional old rating must not prevent opening a work."""
    if not isinstance(value, dict) or len(value) > 32:
        return {}, ["旧评分格式不标准，已忽略展示；原始记录保留。"]
    result, warnings = {}, []
    for provider, rating in value.items():
        try:
            result[provider] = validate_rating(provider, rating)
        except ValueError as exc:
            warnings.append(f"旧评分 {str(provider)[:64]} 无效：{exc}；原始记录保留。")
    return result, warnings
