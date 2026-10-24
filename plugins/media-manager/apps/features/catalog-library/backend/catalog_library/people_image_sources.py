"""Explicit image candidates, not identity bindings or person-record writes.

All network origins are fixed. Wikimedia page redirects are resolved inside its
API; HTTP redirects and arbitrary image URLs are never followed. Only validated
bitmap bytes are saved in the shared, content-addressed people asset directory.
"""
from __future__ import annotations

from copy import deepcopy
from html.parser import HTMLParser
from http.client import HTTPException
import re
import socket
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, build_opener

from work_catalog_yaml.operation_progress import operation_context

from . import assets, people_assets, providers, tmdb_source
from .network import failure_reason

API_ROOTS = {"wikipedia": "https://zh.wikipedia.org/w/api.php",
             "commons": "https://commons.wikimedia.org/w/api.php"}
PAGE_HOSTS = {"wikipedia": "zh.wikipedia.org", "commons": "commons.wikimedia.org"}
TOTAL_TIMEOUT = 45
_FILE_PREFIX = re.compile(r"^(?:File|Image|文件|檔案|档案|图像|圖像|圖片|图片):", re.I)
_BITMAP = re.compile(r"\.(?:jpe?g|png|gif|webp)\Z", re.I)
_SOURCE_IMAGE = re.compile(r"\.(?:jpe?g|png|gif|webp|svg)\Z", re.I)


def _https_parts(value):
    if not isinstance(value, str) or not value or len(value) > 2048 or any(ord(c) < 33 for c in value):
        raise ValueError("图片来源地址无效")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.port not in {None, 443} or parsed.username is not None
                or parsed.password is not None or "\\" in value):
            raise ValueError
    except ValueError:
        raise ValueError("图片来源必须是无账户信息的 HTTPS 官方地址") from None
    return parsed


def validate_wikimedia_url(value):
    """Allow only image files/thumbs in the Commons or Chinese-Wikipedia repo."""
    parsed = _https_parts(value)
    if parsed.hostname not in {"upload.wikimedia.org", "thumb.wikimedia.org"} or parsed.query or parsed.fragment:
        raise ValueError("图片地址不属于允许的 Wikimedia 图片仓库")
    if re.search(r"%(?![0-9a-fA-F]{2})", parsed.path):
        raise ValueError("Wikimedia 图片路径编码无效")
    try:
        path = unquote(parsed.path, errors="strict")
    except UnicodeError:
        raise ValueError("Wikimedia 图片路径编码无效") from None
    parts = path.split("/")
    thumbnail = parts[3:4] == ["thumb"]
    offset = 4 if thumbnail else 3
    valid = ((parsed.hostname != "thumb.wikimedia.org" or thumbnail)
             and parts[:2] == ["", "wikipedia"] and parts[2:3] in (["commons"], ["zh"])
             and len(parts) == offset + (4 if thumbnail else 3)
             and re.fullmatch(r"[0-9a-f]", parts[offset] or "")
             and re.fullmatch(r"[0-9a-f]{2}", parts[offset + 1] or "")
             and parts[offset + 1].startswith(parts[offset])
             and _SOURCE_IMAGE.search(parts[offset + 2]) and _BITMAP.search(parts[-1])
             and all(p not in {"", ".", ".."} for p in parts[1:])
             and not re.search(r"[\\\x00-\x1f\x7f]|%[0-9a-fA-F]{2}", path))
    if not valid:
        raise ValueError("Wikimedia 图片路径不属于允许的位图或位图缩略图")
    return value


def validate_source_url(value):
    """Shared candidate provenance check; never initiates network access."""
    parsed = _https_parts(value)
    if parsed.hostname in {"upload.wikimedia.org", "thumb.wikimedia.org"}:
        return validate_wikimedia_url(value)
    if parsed.hostname == "lain.bgm.tv":
        return people_assets.validate_source_url(value)
    if parsed.hostname == "image.tmdb.org":
        return tmdb_source.validate_cover_url(value)
    raise ValueError("不支持的图片来源域名")


def _value(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("请输入图源条目链接、标题或 ID")
    value = str(value).strip()
    if not value or len(value) > 2048 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("图片来源条目无效或过长")
    return value


def _bangumi_id(value, entity_type):
    value = _value(value)
    if "://" in value:
        parsed = _https_parts(value)
        match = re.fullmatch(r"/(character|person)/([1-9][0-9]{0,9})/?", parsed.path)
        if (parsed.hostname not in {"bangumi.tv", "bgm.tv", "chii.in"} or parsed.query or parsed.fragment
                or not match or match[1] != entity_type):
            raise ValueError("Bangumi 图片条目类型必须与当前人物／角色一致")
        value = match[2]
    return str(providers._positive_id(value, "Bangumi 人物／角色 ID"))


def _title(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 400 or re.search(r"[\x00-\x1f\x7f|#<>\[\]{}\\]", value):
        raise ValueError("Wiki 图源标题无效；一次只能读取一个条目")
    return _FILE_PREFIX.sub("File:", value.strip().replace("_", " "), count=1)


def _wiki_target(provider, value):
    value = _value(value)
    explicit_title = False
    if "://" in value or value.startswith("//"):
        parsed = _https_parts(value)
        if parsed.hostname != PAGE_HOSTS[provider] or parsed.fragment:
            raise ValueError("图源条目必须来自所选 Wiki 平台")
        if parsed.path == "/w/index.php":
            query = parse_qs(parsed.query, strict_parsing=True)
            if set(query) not in ({"title"}, {"curid"}) or any(len(v) != 1 for v in query.values()):
                raise ValueError("Wiki 图源链接参数无效")
            value = next(iter(query.values()))[0]
            explicit_title = "title" in query
            if not explicit_title and not re.fullmatch(r"[1-9][0-9]{0,11}", value):
                raise ValueError("Wiki 图源页面 ID 无效")
        else:
            match = re.fullmatch(r"/(?:wiki|zh-hans|zh-cn|zh-hant|zh-tw)/(.+)", parsed.path)
            if not match or parsed.query:
                raise ValueError("请输入 Wiki 条目链接，不接受 API 或任意图片地址")
            try:
                value = unquote(match[1], errors="strict")
                explicit_title = True
            except UnicodeError:
                raise ValueError("Wiki 图源链接编码无效") from None
    if not explicit_title and re.fullmatch(r"[1-9][0-9]{0,11}", value):
        if provider == "commons":
            raise ValueError("Commons 图源请填写 File: 文件标题或文件条目链接")
        return {"pageids": value}
    title = _title(value)
    if provider == "commons" and not title.startswith("File:"):
        raise ValueError("Commons 图源必须是 File: 文件条目")
    return {"titles": title}


def _request_json(provider, query, *, deadline):
    params = {**query, "action": "query", "format": "json", "formatversion": "2", "utf8": "1", "maxlag": "5"}
    if provider == "wikipedia":
        params.update(variant="zh-hans", uselang="zh-hans")
    request = Request(API_ROOTS[provider] + "?" + urlencode(params), headers={
        "User-Agent": providers.USER_AGENT, "Accept": "application/json", "Accept-Encoding": "identity"})
    with operation_context(stage="Wiki 图片来源请求", action=str(query.get("prop", "query"))):
        try:
            with build_opener(assets._NoRedirects()).open(request, timeout=providers._remaining(deadline)) as response:
                length = response.headers.get("Content-Length")
                if (length and (not length.isdecimal() or int(length) > providers.MAX_RESPONSE_BYTES)) or response.headers.get("Content-Encoding", "identity").lower() not in {"", "identity"}:
                    raise ValueError("Wiki 图片资料响应超出安全限制")
                data = providers._read_response(response, deadline)
                if length and len(data) != int(length):
                    raise ValueError("Wiki 图片资料未完整接收")
            raw = providers._load_json(data)
            if not isinstance(raw, dict) or raw.get("error"):
                raise ValueError("Wiki 图片 API 未返回有效资料")
            return raw
        except HTTPError as exc:
            status = exc.code
            exc.close()
            raise ValueError(f"Wiki 图片资料获取失败（HTTP {status}）") from None
        except (URLError, TimeoutError, socket.timeout, ssl.SSLError, ConnectionError, HTTPException) as exc:
            raise ValueError("Wiki 图片资料连接失败：" + failure_reason(exc)) from None


def _page(raw):
    rows = raw.get("query", {}).get("pages") if isinstance(raw.get("query"), dict) else None
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict) or "invalid" in rows[0]:
        raise ValueError("Wiki 图片条目不存在或返回了多个不明确的条目")
    row = rows[0]
    shared_image = row.get("ns") == 6 and isinstance(row.get("imageinfo"), list) and row.get("imageinfo")
    if "missing" in row and not shared_image:
        raise ValueError("Wiki 图片条目不存在")
    if not re.fullmatch(r"[1-9][0-9]{0,11}", str(row.get("pageid", ""))):
        # Shared Commons files may have no local Wikipedia page ID.
        if not shared_image:
            raise ValueError("Wiki 图片条目缺少有效页面 ID")
    _title(row.get("title"))
    return row


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag in {"br", "p", "div", "li"} and not self.hidden:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1
        elif tag in {"p", "div", "li"} and not self.hidden:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _plain(value, maximum=2000):
    if not isinstance(value, str):
        return ""
    parser = _PlainText()
    parser.feed(value[:20000])
    parser.close()
    return re.sub(r"\s+", " ", "".join(parser.parts)).strip()[:maximum]


def _page_url(provider, title):
    return "https://" + PAGE_HOSTS[provider] + "/wiki/" + quote(title.replace(" ", "_"), safe="():")


def _description_url(value, fallback):
    try:
        parsed = _https_parts(value)
        provider = next(key for key, host in PAGE_HOSTS.items() if host == parsed.hostname)
        target = _wiki_target(provider, value)
        return _page_url(provider, target["titles"]) if target.get("titles", "").startswith("File:") else fallback
    except (ValueError, StopIteration):
        return fallback


def _wikimedia_api_url(value, provider, *, thumbnail):
    """Discard only MediaWiki's exact Imageinfo analytics tuple, not arbitrary queries.

    This normalization is only used on an official API response. User-supplied
    URLs and saved image references still pass the strict query-free validator.
    New Wikimedia thumbnail hosts retain the same repository/path restrictions.
    """
    parsed = _https_parts(value)
    if parsed.query:
        expected = {"utm_source": PAGE_HOSTS[provider], "utm_campaign": "imageinfo",
                    "utm_content": "thumbnail" if thumbnail else "original"}
        try:
            pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        except ValueError:
            raise ValueError("Wikimedia 图片 API 返回不支持的地址参数") from None
        if len(pairs) != len(expected) or dict(pairs) != expected:
            raise ValueError("Wikimedia 图片 API 返回不支持的地址参数")
        value = urlunsplit(parsed._replace(query=""))
    return validate_wikimedia_url(value)


def _wiki_image(provider, target, *, deadline):
    requested_file = target.get("titles", "").startswith("File:")
    if requested_file:
        page = None
        file_target = target
    else:
        page = _page(_request_json(provider, {**target, "prop": "pageimages", "piprop": "name", "pilicense": "any", "redirects": "1"}, deadline=deadline))
        filename = page.get("pageimage")
        if page.get("ns") == 6:
            file_target = {"titles": _title(page["title"])}
        elif isinstance(filename, str) and filename.strip():
            file_target = {"titles": _title("File:" + filename)}
        else:
            raise ValueError("此 Wiki 条目没有可用的主图片；可改填具体 File: 文件条目")
    image_page = _page(_request_json(provider, {**file_target, "prop": "imageinfo", "iiprop": "url|mime|extmetadata", "iiurlwidth": "800", "iiurlheight": "1000", "iilimit": "1", "redirects": "1"}, deadline=deadline))
    infos = image_page.get("imageinfo")
    if image_page.get("ns") != 6 or not isinstance(infos, list) or len(infos) != 1 or not isinstance(infos[0], dict):
        raise ValueError("Wiki 文件条目没有可读取的图片资料")
    info = infos[0]
    thumbnail = bool(info.get("thumburl"))
    url = _wikimedia_api_url(info.get("thumburl") or info.get("url"), provider, thumbnail=thumbnail)
    metadata = info.get("extmetadata") if isinstance(info.get("extmetadata"), dict) else {}
    def text(key, maximum=2000):
        row = metadata.get(key)
        return _plain(row.get("value"), maximum) if isinstance(row, dict) else ""
    license_url = text("LicenseUrl")
    try:
        parsed_license = _https_parts(license_url)
        if parsed_license.hostname != "creativecommons.org" or parsed_license.query or parsed_license.fragment:
            license_url = ""
    except ValueError:
        license_url = ""
    description_url = _description_url(info.get("descriptionurl"), _page_url(provider, image_page["title"]))
    identity = str((page or image_page).get("pageid", ""))
    if not re.fullmatch(r"[1-9][0-9]{0,11}", identity):
        identity = image_page["title"]
    evidence = {"title": (page or image_page)["title"], "external_id": identity,
                "page_url": _page_url(provider, page["title"]) if page else description_url,
                "description_url": description_url, "file_title": image_page["title"], "source_url": url,
                "author": text("Artist", 1000), "license": text("LicenseShortName", 200) or text("UsageTerms", 200),
                "license_url": license_url, "description": text("ImageDescription"), "credit": text("Credit", 1000)}
    warnings = ["Wiki 图片仅为手动候选，不证明人物或角色身份；请核对是否确为当前角色，而非演员本人或其他图片。"]
    if not evidence["license"]:
        warnings.append("此图片未返回明确许可摘要，请在来源文件页核对授权信息。")
    return url, evidence, warnings


def fetch_image(provider, external_id, *, entity_type, people_root=None, previous=None, deadline=None):
    """Fetch/cache one candidate. No entity/source_refs are created or updated."""
    if (not isinstance(provider, str) or provider not in {"bangumi", "wikipedia", "commons"}
            or not isinstance(entity_type, str) or entity_type not in {"person", "character"}):
        raise ValueError("图片来源或人物／角色类型不支持")
    target_root = people_assets.asset_root(people_root)
    deadline = min(deadline, time.monotonic() + TOTAL_TIMEOUT) if deadline is not None else time.monotonic() + TOTAL_TIMEOUT
    providers._remaining(deadline)
    if provider == "bangumi":
        identity = _bangumi_id(external_id, entity_type)
        raw = providers._request_json(f"/v0/{'characters' if entity_type == 'character' else 'persons'}/{identity}", deadline=deadline)
        if not isinstance(raw, dict) or str(raw.get("id")) != identity:
            raise ValueError("Bangumi 图片资料与所选条目 ID 不一致")
        url = people_assets.select_source_url(raw)
        if not url:
            raise ValueError("此 Bangumi 条目没有可用图片")
        evidence = {"external_id": identity, "title": _plain(raw.get("name"), 400), "source_url": url,
                    "page_url": f"https://bangumi.tv/{entity_type}/{identity}", "author": "", "license": "", "license_url": ""}
        warnings = ["请确认该图片对应当前人物／角色；选图不会自动关联来源身份。"]
    else:
        url, evidence, warnings = _wiki_image(provider, _wiki_target(provider, external_id), deadline=deadline)
    avatar = None
    if isinstance(previous, dict) and previous.get("source_url") == url:
        try:
            people_assets.read_avatar(previous.get("asset_id"), root=people_root)
            avatar = {"asset_id": previous["asset_id"], "source_url": url}
        except (ValueError, OSError):
            pass
    if avatar is None:
        data, mime = assets.download_image(url, user_agent=providers.USER_AGENT, validate_url=validate_source_url,
                                          deadline=deadline, label="人物／角色图片")
        stored = assets.store_image(data, mime, root=target_root, label="人物／角色图片")
        avatar = {"asset_id": f"{stored['sha256']}.{stored['extension']}", "source_url": url}
    return {"avatar": deepcopy(avatar), "evidence": {"provider": provider, "entity_type": entity_type, **evidence}, "warnings": warnings}
