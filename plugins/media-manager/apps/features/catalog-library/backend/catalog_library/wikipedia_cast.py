"""Pure, bounded parsers for explicit Chinese/Korean Wikipedia cast evidence.

This is deliberately not a general MediaWiki renderer. Tables must identify
actor and character columns; prose lists must be in a cast section and contain
an explicit casting separator. No requests, searches or actor-order matching.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import re

from . import wikipedia_source as wiki

MAX_ROWS = 600
MAX_TABLES = 100
MAX_TEXT_LENGTH = 1024 * 1024
_HAN = re.compile(r"[\u3400-\u9fff]")
_HANGUL = re.compile(r"[\uac00-\ud7af]")
_KANA = re.compile(r"[\u3040-\u30ff]")
_ACTOR_HEADERS = {"演员", "演員", "饰演", "飾演", "演出者", "演員姓名", "演员姓名", "배우", "출연자"}
_ROLE_HEADERS = {"角色", "角色名", "角色名稱", "角色名称", "劇中角色", "剧中角色", "배역", "역할", "등장인물"}
_CAST_SECTIONS = {"演员", "演員", "演员阵容", "演員陣容", "演员与角色", "演員與角色", "演員及角色",
                  "演员及角色", "角色", "角色介绍", "角色介紹", "登場人物", "등장인물", "등장 인물", "출연진", "출연 배우"}
_EXCLUDED_SECTIONS = {"配音", "配音員", "配音演员", "配音演員", "製作人員", "制作人员", "제작진", "성우", "더빙"}
_AGE_VALUES = {"少年": "teen", "少女時期": "teen", "少女时期": "teen", "童年": "child", "幼年": "child",
               "嬰兒": "child", "婴儿": "child", "青年": "young", "成年": "adult", "老年": "elder",
               "아역": "child", "어린 시절": "child", "성인": "adult", "노년": "elder"}
_AGE_PREFIX = re.compile(r"\s*(" + "|".join(re.escape(label) for label in _AGE_VALUES) + r")\s*[:：]\s*(.+)", re.S)
_EMPTY = {"names": [], "native_names": [], "links": []}


def _title(value):
    value = value.strip().replace("_", " ")
    return value if (value and len(value) <= 300 and not any(char in value for char in "|:#[]{}\\")
                     and not any(ord(char) < 32 for char in value)) else ""


def _split(value, separator, *, parentheses=False):
    parts, start, braces, links, parens, index = [], 0, 0, 0, 0, 0
    while index < len(value):
        token = value[index:index + 2]
        if token in {"{{", "}}", "[[", "]]"}:
            braces += (token == "{{") - (token == "}}")
            links += (token == "[[") - (token == "]]")
            if min(braces, links) < 0 or max(braces, links) > 30:
                return []
            index += 2
        elif parentheses and not braces and not links and value[index] in "()（）":
            parens += 1 if value[index] in "(（" else -1
            if not 0 <= parens <= 10:
                return []
            index += 1
        elif not braces and not links and not parens and value.startswith(separator, index):
            parts.append(value[start:index].strip())
            index += len(separator)
            start = index
        else:
            index += 1
    return parts + [value[start:].strip()] if not braces and not links and not parens else []


def _clean_name(value):
    value = wiki._plain_name(value).strip()
    if not value or len(value) > 200 or not re.fullmatch(r"[\w\s\u00b7\u2027\-\u2010\u2011.'’]+", value):
        return ""
    return " ".join(value.split())


def _korean_title_name(value):
    # These are conventional article-title disambiguators, not name aliases.
    return re.sub(r"\s*\((?:배우|여배우|가수|희극인|\d{4}년)\)$", "", value).strip()


def _atom(value):
    """Parse one name atom, retaining exactly evidenced language/link data."""
    value = value.strip()
    link = re.fullmatch(r"\[\[([^\[\]]+)\]\]", value)
    if link:
        args = link[1].split("|")
        target = _title(args[0])
        display = _clean_name(args[-1]) if len(args) == 2 else _clean_name(_korean_title_name(args[0]))
        if not target or len(args) > 2 or not display:
            return deepcopy(_EMPTY)
        return {"names": [display], "native_names": [], "links": [target]}
    template = re.fullmatch(r"\{\{\s*([^{}]+)\}\}", value)
    if template:
        args = [arg.strip() for arg in wiki._split_template(template[1])]
        key = args.pop(0).lower()
        if key in {"lk", "link-ko"} and 2 <= len(args) <= 3:
            chinese = _clean_name(args[2] if len(args) == 3 and args[2] else args[0])
            korean = _clean_name(_korean_title_name(args[1]))
            if not chinese or not korean or not _title(args[0]) or not _title(args[1]):
                return deepcopy(_EMPTY)
            # This template may be a redlink; never pretend it is a confirmed
            # local article link. The exact foreign title is evidence only.
            return {"names": list(dict.fromkeys([chinese, korean])),
                    "native_names": [korean] if _HANGUL.search(korean) else [], "links": []}
        if key == "lang" and len(args) == 2:
            plain = _clean_name(args[1])
            if plain:
                return {"names": [plain], "native_names": [plain] if args[0].lower() == "ko" and _HANGUL.search(plain) else [], "links": []}
        if key in {"nowrap", "small"} and len(args) == 1:
            return _atom(args[0])
        return deepcopy(_EMPTY)
    # Do not interpret partial wikilinks (e.g. a linked dog breed), unknown
    # template arguments or arbitrary HTML as an actor's identity.
    if any(token in value for token in ("[[", "]]", "{{", "}}", "<", ">")):
        return deepcopy(_EMPTY)
    plain = _clean_name(value)
    return {"names": [plain] if plain else [], "native_names": [], "links": []}


def _merge_names(parts):
    return {key: list(dict.fromkeys(value for part in parts for value in part[key])) for key in _EMPTY}


def names(value):
    """Names/aliases explicitly written in one actor or character cell.

Examples: ``金奎元（김규원）``, ``金娜娜（金娜鳳）``, and lang/link-ko
templates. Unknown constructs return no evidence, rather than partial names.
    """
    if not isinstance(value, str) or len(value) > 10000:
        return deepcopy(_EMPTY)
    value = wiki._without_annotations(value).strip().replace("'''", "").replace("''", "")
    pieces = re.split(r"<br\s*/?>|\n", value, flags=re.I)
    if len(pieces) > 10:
        return deepcopy(_EMPTY)
    result = []
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        atom = _atom(piece)
        if atom["names"]:
            result.append(atom)
            continue
        # Only a single balanced, explicit parenthetical alternative is an
        # alias. Actor age qualifiers are handled separately, not as names.
        match = re.fullmatch(r"(.+?)[（(]([^()（）]+)[）)]", piece)
        if not match:
            return deepcopy(_EMPTY)
        primary, alias = _atom(match[1]), _atom(match[2])
        if not primary["names"] or not alias["names"] or len(primary["names"]) != 1 or len(alias["names"]) != 1:
            return deepcopy(_EMPTY)
        primary_name, alias_name = primary["names"][0], alias["names"][0]
        english_original = bool(re.fullmatch(r"[A-Za-z][A-Za-z .’'\-]{0,99}", alias_name))
        if not ((_HAN.search(primary_name) and (_HAN.search(alias_name) or _HANGUL.search(alias_name)))
                or ((_HAN.search(primary_name) or _HANGUL.search(primary_name)) and english_original)):
            return deepcopy(_EMPTY)
        if _HANGUL.search(alias["names"][0]):
            alias["native_names"] = list(alias["names"])
        result.extend((primary, alias))
    return _merge_names(result)


def _cell(value):
    parts = _split(value, "|")
    if len(parts) == 2 and re.fullmatch(r"\s*(?:(?:style|align|valign|width|class|scope)\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s]+)\s*)+", parts[0], re.I):
        return parts[1]
    return value if len(parts) == 1 else ""


def _unformat(value):
    """Unwrap only whole, known presentation templates, never their siblings."""
    for _ in range(5):
        value = value.strip()
        if not value.startswith("{{") or not value.endswith("}}"):
            break
        parts = _split(value[2:-2], "|")
        if len(parts) != 2 or parts[0].strip().lower() not in {"small", "nowrap"}:
            break
        value = parts[1]
    return value.strip()


def _actor_entries(value, language):
    # The outer age annotation may contain a link title with parentheses.
    pieces = re.split(r"<br\s*/?>|\n", value, flags=re.I)
    clean = " ".join(_unformat(piece) for piece in pieces if piece.strip()) if len(pieces) <= 10 else value
    annotations, braces, links, parens, start, index = [], 0, 0, 0, 0, 0
    while index < len(clean):
        token = clean[index:index + 2]
        if token in {"{{", "}}", "[[", "]]"}:
            braces += (token == "{{") - (token == "}}")
            links += (token == "[[") - (token == "]]")
            if min(braces, links) < 0 or max(braces, links) > 30:
                return []
            index += 2
            continue
        if not braces and not links:
            if clean[index] in "(（":
                if not parens:
                    start = index
                parens += 1
            elif clean[index] in ")）":
                parens -= 1
                if parens < 0:
                    return []
                if not parens:
                    match = _AGE_PREFIX.fullmatch(clean[start + 1:index])
                    if match:
                        annotations.append((start, index + 1, match[1], match[2]))
        index += 1
    if braces or links or parens:
        return []
    if not annotations:
        return [(value, None)]
    primary_raw = clean[:annotations[0][0]].strip()
    primary = names(primary_raw)
    if not primary["names"] or len(primary["links"]) > 1:
        return []
    # A child/teen branch proves the same role, not the primary portrayal's
    # age: the main character may still be a student. Do not invent adulthood.
    result, end = [(primary_raw, None)], annotations[0][0]
    for start, next_end, label, actor_raw in annotations:
        actor = names(actor_raw)
        if clean[end:start].strip() or not actor["names"] or len(actor["links"]) > 1:
            return []
        result.append((actor_raw.strip(), {"kind": "age", "value": _AGE_VALUES[label], "label": label,
                                          "language": language, "explicit": True}))
        end = next_end
    return result if not clean[end:].strip() else []


def _row(actor_raw, role_raw, language, qualifier=None, aliases=()):
    actor, role = names(actor_raw), names(role_raw)
    if not actor["names"] or not role["names"] or len(actor["links"]) > 1:
        return None
    actor_chinese = [name for name in actor["names"] if _HAN.search(name) and not _HANGUL.search(name)]
    actor_native = actor["native_names"]
    if len(actor_chinese) > 1 or len(actor_native) > 1:
        return None
    if re.search(r"<br\s*/?>", actor_raw, re.I) and not actor_native:
        return None
    chinese = [name for name in role["names"] if _HAN.search(name) and not _HANGUL.search(name) and not _KANA.search(name)]
    native = role["native_names"] or ([name for name in role["names"] if _HANGUL.search(name)] if language == "ko" else [])
    # Multiple Chinese names are accepted only in the explicit alias form.
    if len(chinese) > 1 and not re.fullmatch(r".+[（(][^()（）]+[）)]", wiki._without_annotations(role_raw).strip()):
        return None
    if len(native) > 1:
        return None
    if language == "ko" and (not native or chinese):
        return None
    for alias in aliases:
        if alias not in role["names"]:
            role["names"].append(alias)
    result = {"actor": actor, "role": role, "character_name_cn": chinese[0] if chinese and language == "zh" else "",
              "character_name": native[0] if native else "", "actor_raw": actor_raw, "role_raw": role_raw}
    original = re.fullmatch(r"(.+?)[（(]\s*([A-Za-z][A-Za-z .’'\-]{0,99})\s*[）)]", wiki._without_annotations(role_raw).strip())
    if original and (_HANGUL.search(original[1]) or _HAN.search(original[1])):
        role["original_name"] = original[2].strip()
        role["original_name_language"] = "en"
        role["original_name_evidence"] = {"kind": "parenthetical_original", "raw": role_raw, "explicit": True}
        result["character_name"] = role["original_name"]
    if qualifier:
        result["qualifier"] = deepcopy(qualifier)
        if result["character_name_cn"] and qualifier["value"] != "adult":
            result["character_name_cn"] += f"（{qualifier['label']}）"
    return result


def _unresolved_row(actor_raw, role_raw, language, location):
    # Slash/newline names may mean aliases, dual roles or identifiers. Preserve
    # the source atoms for review without choosing any of those interpretations.
    candidates = []
    clean = wiki._without_annotations(role_raw)
    pieces = re.split(r"<br\s*/?>|\n", clean, flags=re.I)
    for piece in pieces[:10]:
        for separator in ("／", "/"):
            if len(_split(piece, separator)) > 1:
                pieces_for_part = _split(piece, separator)
                break
        else:
            pieces_for_part = [piece]
        for part in pieces_for_part[:10]:
            for name in names(part)["names"]:
                if name not in candidates:
                    candidates.append(name)
    return {"actor": names(actor_raw), "actor_raw": actor_raw, "role_raw": role_raw,
            "role_name_candidates": candidates, "language": language, "location": location,
            "reason": "演员或角色姓名结构无法确定；多名不自动视为别名或拆成多个角色，保留原文待核对"}


def _parsed_rows(actor_raw, role_raw, language, location, unresolved):
    entries = _actor_entries(actor_raw, language)
    parsed = [_row(actor, role_raw, language, qualifier) for actor, qualifier in entries]
    if not parsed or any(row is None for row in parsed):
        unresolved.append(_unresolved_row(actor_raw, role_raw, language, location))
        return []
    if len(parsed) > 1 and any(row.get("qualifier") for row in parsed):
        signature = "\n".join((language, location, actor_raw, role_raw))
        group = "wiki-cast:" + hashlib.sha256(signature.encode("utf-8")).hexdigest()[:24]
        for row in parsed:
            row["role_group"] = group
    return parsed


def _table_rows(wikitext, language, warnings, unresolved):
    tables, current, depth, bad = [], [], 0, False
    for line in wikitext.splitlines():
        if line.lstrip().startswith("{|"):
            depth += 1
            if depth == 1:
                current, bad = [], False
            else:
                bad = True
        elif line.lstrip().startswith("|}") and depth:
            depth -= 1
            if not depth and not bad:
                tables.append(current)
                if len(tables) > MAX_TABLES:
                    raise ValueError("演员表数量超过安全限制")
            elif not depth and any(label in "\n".join(current) for label in _ACTOR_HEADERS) and any(label in "\n".join(current) for label in _ROLE_HEADERS):
                warnings.append("嵌套演员表无法确认单元格对应关系，已跳过")
        elif depth == 1:
            current.append(line.strip())
    result = []
    for table_index, lines in enumerate(tables):
        table_warnings = []
        headers, rows, cells, in_header, invalid = [], [], [], False, False
        for line in [*lines, "|-"]:
            if line.startswith("|-"):
                if cells:
                    ordinary = [wiki._plain_name(cell).strip() for cell in cells]
                    is_header = in_header or (sum(value in _ACTOR_HEADERS for value in ordinary) == 1
                                              and sum(value in _ROLE_HEADERS for value in ordinary) == 1)
                    if is_header and not headers and not invalid:
                        headers = cells
                    elif not in_header and not invalid:
                        rows.append(cells)
                    elif invalid:
                        table_warnings.append("合并单元格无法确认演员/角色对应关系，已跳过")
                cells, in_header, invalid = [], False, False
            elif line.startswith("|+"):
                continue
            elif line.startswith(("!", "|")):
                header = line.startswith("!")
                if cells and in_header != header:
                    if in_header and not headers:
                        headers = cells
                    cells, invalid = [], False
                in_header = header
                if re.search(r"\b(?:rowspan|colspan)\s*=", line, re.I):
                    invalid = True
                parts = _split(line[1:], "!!" if header else "||")
                cells.extend(_cell(part) for part in parts)
            elif line and cells:
                cells[-1] += "\n" + line
        labels = [wiki._plain_name(header).strip() for header in headers]
        actors = [index for index, value in enumerate(labels) if value in _ACTOR_HEADERS]
        roles = [index for index, value in enumerate(labels) if value in _ROLE_HEADERS]
        if len(actors) != 1 or len(roles) != 1:
            continue
        warnings.extend(table_warnings)
        for row_index, cells in enumerate(rows):
            if len(cells) != len(headers):
                warnings.append("演员表列数不一致，已跳过不确定行")
                continue
            parsed = _parsed_rows(cells[actors[0]], cells[roles[0]], language,
                                  f"table:{table_index}:row:{row_index}", unresolved)
            if not parsed:
                warnings.append(f"演员表第 {table_index + 1} 表第 {row_index + 1} 行含无法确认的姓名或多个角色，已保留为未决行")
                continue
            result.extend(parsed)
    return result


def _list_rows(wikitext, language, warnings, unresolved):
    result, headings, pending, table_depth = [], [], [], 0
    for number, line in enumerate(wikitext.splitlines(), 1):
        if line.lstrip().startswith("{|"):
            table_depth += 1
        if table_depth:
            if line.lstrip().startswith("|}"):
                table_depth -= 1
            pending = []
            continue
        heading = re.fullmatch(r"\s*(={2,6})\s*(.*?)\s*\1\s*", line)
        if heading:
            level, title = len(heading[1]), wiki._plain_name(heading[2]).strip()
            while headings and headings[-1][0] >= level:
                headings.pop()
            headings.append((level, title))
            pending = []
            continue
        active = any(title in _CAST_SECTIONS for _, title in headings) and not any(title in _EXCLUDED_SECTIONS for _, title in headings)
        if not active:
            continue
        alias = re.match(r"^\s*[:：]\s*(?:本名|原名|又名)\s*([\u3400-\u9fff·‧]{2,30})(?=[，,。；;]|$)", line)
        if alias and pending and language == "zh":
            for row in pending:
                if alias[1] not in row["role"]["names"]:
                    row["role"]["names"].append(alias[1])
                row.setdefault("role_alias_evidence", []).append({"value": alias[1], "raw": line.strip(), "kind": "explicit_name"})
            continue
        bullet = re.fullmatch(r"\s*\*(?![*#:;])\s*(.+?)\s*", line)
        if not bullet:
            if line.strip() and not line.lstrip().startswith(":"):
                pending = []
            continue
        pending = []
        body = bullet[1]
        if language == "zh":
            parts = re.split(r"飾演|饰演", body)
            if len(parts) != 2:
                warnings.append(f"演员列表第 {number} 行未明确唯一饰演关系，已跳过")
                continue
            actor_raw, role_raw = (part.strip() for part in parts)
            # A description is not part of the role name. Only a clearly
            # separated suffix can be discarded; comma prose stays unparsed.
            role_raw = re.split(r"\s+[—–-]\s+", role_raw, maxsplit=1)[0].strip()
        else:
            parts = _split(body, ":", parentheses=True)
            if len(parts) != 2:
                warnings.append(f"韩语演员列表第 {number} 行未明确唯一演员/角色分隔，已跳过")
                continue
            actor_raw, remainder = parts
            match = re.fullmatch(r"(.+?)\s+역(?:\s+[—–-]\s+.*)?", remainder)
            if not match:
                warnings.append(f"韩语演员列表第 {number} 行缺少明确角色标记，已跳过")
                continue
            role_raw = match[1].strip()
        parsed = _parsed_rows(actor_raw, role_raw, language, f"list:{number}", unresolved)
        if not parsed:
            warnings.append(f"演员列表第 {number} 行含复杂或歧义姓名，已保留为未决行")
            continue
        result.extend(parsed)
        pending = parsed
    return result


def parse(wikitext, language="zh", *, unresolved=None):
    """Return ``(rows, diagnostics)`` without I/O or source identity decisions."""
    if language not in {"zh", "ko"}:
        raise ValueError("演员列表只支持已确认的中文或韩语条目")
    if not isinstance(wikitext, str) or len(wikitext) > MAX_TEXT_LENGTH:
        raise ValueError("演员条目正文超出安全大小限制")
    text = wiki._without_annotations(wikitext)
    warnings = []
    pending = []
    rows = [*_table_rows(text, language, warnings, pending), *_list_rows(text, language, warnings, pending)]
    if len(rows) + len(pending) > MAX_ROWS:
        raise ValueError("演员表行数超过安全限制")
    if unresolved is not None:
        unresolved.extend(pending)
    return rows, list(dict.fromkeys(warnings))


def cast_rows(wikitext, language="zh"):
    return parse(wikitext, language)[0]


def diagnostics(wikitext, language="zh"):
    return parse(wikitext, language)[1]
