"""Bounded multilingual projections; script is evidence, nationality is not.

Explicit empty slots are meaningful: they suppress legacy projection for that
language. These helpers never translate, fetch or mutate an entity on reads.
"""
from copy import deepcopy
import json
import re
import unicodedata

LANGUAGES = {"zh-Hans": "中文（简体）", "zh-Hant": "中文（繁体）", "ja": "日文", "ko": "韩文",
             "ko-Latn": "韩文罗马字", "en": "英文", "und-Latn": "未确定语种（拉丁字母）", "und": "未确定语种"}
KINDS = ("name", "summary", "aliases")
MAX_LOCALIZED_BYTES = 384 * 1024
_LIMITS = {"name": 1000, "summary": 100000, "aliases": 1000}
_HANGUL = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]")
_KANA = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff\uff66-\uff9f]")
_HAN = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")


def language(value):
    """Normalize only explicit supported language/variant evidence."""
    if not isinstance(value, str):
        return ""
    # A Chinese source is not evidence of its writing system. In particular,
    # Wikipedia revision content may be traditional even after a zh-hans query.
    aliases = {"zh-cn": "zh-Hans", "zh-sg": "zh-Hans", "zh-hans": "zh-Hans",
               "zh-tw": "zh-Hant", "zh-hk": "zh-Hant", "zh-mo": "zh-Hant", "zh-hant": "zh-Hant",
               "ja-jp": "ja", "ko-kr": "ko", "en-us": "en", "en-gb": "en", "ko-latn": "ko-Latn",
               "und-latn": "und-Latn"}
    return value if value in LANGUAGES else aliases.get(value.lower(), "")


def infer_language(value, *, hint=""):
    """Do not label a Latin transliteration English or Korean without evidence."""
    if not isinstance(value, str) or not value.strip():
        return language(hint) or "und"
    evidence = language(hint)
    han, hangul, kana = len(_HAN.findall(value)), len(_HANGUL.findall(value)), len(_KANA.findall(value))
    # Chinese biographies often quote the subject's native name. A single
    # Korean/Japanese name is not evidence that the entire paragraph uses that
    # language. Retain a proven Chinese source language; without evidence do
    # not guess its simplified/traditional variant.
    if (han > hangul + kana and (hangul or kana and evidence in {"zh-Hans", "zh-Hant"})
            and evidence not in {"ja", "ko"}):
        return evidence if evidence in {"zh-Hans", "zh-Hant"} else "und"
    if _HANGUL.search(value):
        return "ko"
    if _KANA.search(value):
        return "ja"
    if _HAN.search(value):
        return evidence if evidence in {"zh-Hans", "zh-Hant", "ja", "ko"} else "und"
    if any("LATIN" in unicodedata.name(char, "") for char in value):
        return evidence if evidence in {"en", "ko-Latn", "und-Latn"} else "und-Latn"
    return "und"


def merge_aliases(*groups):
    """Stable, bounded alias union; no translations or fuzzy name matching.

    Canonically equivalent spelling and surrounding whitespace are duplicate
    evidence. Keep the first stored spelling and preserve every other alias.
    """
    result, seen = [], set()
    for group in groups:
        if group is None:
            continue
        if not isinstance(group, list) or any(not isinstance(value, str) for value in group):
            raise ValueError("别名必须为字符串数组")
        for value in group:
            key = unicodedata.normalize("NFC", value.strip())
            if key and key not in seen:
                seen.add(key)
                result.append(value)
    return _validate_slot("aliases", result)


def _validate_slot(kind, value):
    values = value if kind == "aliases" else [value]
    if kind == "aliases" and (not isinstance(value, list) or len(value) > 200):
        raise ValueError("各语种别名必须为最多 200 项字符串数组")
    if any(not isinstance(item, str) or len(item) > _LIMITS[kind] or "\x00" in item for item in values):
        raise ValueError("多语言字段值无效或超过长度限制")
    if kind == "aliases" and len(set(values)) != len(values):
        raise ValueError("同语种别名不能重复")
    return deepcopy(value)


def validate(value):
    if not isinstance(value, dict) or set(value) - set(KINDS):
        raise ValueError("localized 只能包含 name、summary 与 aliases")
    result = {}
    for kind, slots in value.items():
        if not isinstance(slots, dict) or set(slots) - set(LANGUAGES) or len(slots) > len(LANGUAGES):
            raise ValueError("多语言字段必须使用受支持的语言键")
        result[kind] = {lang: _validate_slot(kind, item) for lang, item in slots.items()}
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_LOCALIZED_BYTES:
        raise ValueError("多语言资料超过安全大小限制")
    return result


def is_field(path):
    if not isinstance(path, str):
        return False
    parts = path.split(".")
    return len(parts) == 3 and parts[0] == "localized" and parts[1] in KINDS and parts[2] in LANGUAGES


def _legacy_hint(entity, kind):
    fields = entity.get("field_sources", {})
    evidence = fields.get(kind, {}) if isinstance(fields, dict) else {}
    if isinstance(evidence, dict) and language(evidence.get("language")):
        return language(evidence["language"])
    sources = [source for source in entity.get("source_refs", []) if isinstance(source, dict)]
    if isinstance(entity.get("source"), dict):
        sources.append(entity["source"])
    hints = {language(source.get("language")) for source in sources if language(source.get("language"))}
    if len(hints) == 1:
        return next(iter(hints))
    if kind == "name" and sources and {source.get("provider") for source in sources} == {"bangumi"}:
        return "ja"
    return ""


def chinese_name_hint(entity, *, default="zh-Hans"):
    """Reuse explicit variant evidence before projecting legacy name_cn.

    Existing legacy records without language metadata keep their historical
    fallback. A provider's classified slot (including und) is authoritative:
    name_cn alone must never copy traditional/unknown text into simplified.
    """
    value = entity.get("name_cn", "") if isinstance(entity, dict) else ""
    slots = entity.get("localized", {}).get("name", {}) if isinstance(entity, dict) else {}
    known = [key for key, text in slots.items() if value and text == value]
    if known:
        return next((key for key in ("zh-Hans", "zh-Hant") if key in known), known[0])
    hint = _legacy_hint(entity, "name_cn")
    return hint or default


def project(entity):
    entity = entity if isinstance(entity, dict) else {}
    explicit = validate(entity.get("localized", {}))
    result = {kind: deepcopy(explicit.get(kind, {})) for kind in KINDS}
    for kind in ("name", "summary"):
        raw = entity.get(kind)
        if isinstance(raw, str) and raw.strip():
            _validate_slot(kind, raw)
            hint = _legacy_hint(entity, kind)
            if kind == "name" and raw == entity.get("name_cn"):
                hint = chinese_name_hint(entity)
            # An explicitly retained same value is already classified by more
            # precise source evidence; avoid a second guessed language slot.
            if raw not in result[kind].values():
                result[kind].setdefault(infer_language(raw, hint=hint), raw)
    chinese = entity.get("name_cn")
    if isinstance(chinese, str) and chinese.strip():
        _validate_slot("name", chinese)
        if chinese not in result["name"].values():
            result["name"].setdefault(infer_language(chinese, hint=chinese_name_hint(entity)), chinese)
    aliases = entity.get("aliases", [])
    # Schema 1 historically allowed repeated aliases. Deduplicate only this
    # legacy projection; explicitly supplied localized slots remain strict.
    if isinstance(aliases, list) and all(isinstance(alias, str) for alias in aliases):
        aliases = list(dict.fromkeys(aliases))
    _validate_slot("aliases", aliases)
    grouped = {}
    for alias in aliases:
        if not alias.strip():
            continue
        if any(alias in values for values in explicit.get("aliases", {}).values()):
            continue
        known = [lang for lang, name in result["name"].items() if name == alias]
        lang = known[0] if len(known) == 1 else infer_language(alias)
        if lang not in explicit.get("aliases", {}):
            grouped.setdefault(lang, [])
            if alias not in grouped[lang]:
                grouped[lang].append(alias)
    for lang, values in grouped.items():
        result["aliases"][lang] = values
    return validate(result)


def field_paths(candidate, target=None):
    left, right = project(candidate), project(target)
    return ["localized." + kind + "." + lang for kind in KINDS for lang in LANGUAGES
            if lang in left[kind] or lang in right[kind]]


def field_value(entity, path):
    if not is_field(path):
        return deepcopy(entity.get(path)) if isinstance(entity, dict) else None
    _, kind, lang = path.split(".")
    return deepcopy(project(entity)[kind].get(lang, [] if kind == "aliases" else ""))


value = field_value


def set_field(entity, path, value):
    """Apply one confirmed slot edit and keep only proven legacy mirrors in sync.

    Materializing the old projection preserves other languages. In particular,
    an explicitly classified Latin value must not reappear as und-Latn after
    its source language slot is cleared. No changes are committed on failure.
    """
    if not isinstance(entity, dict) or not is_field(path):
        raise ValueError("多语言字段路径无效")
    _, kind, lang = path.split(".")
    localized = project(entity)
    old = localized[kind].get(lang, [] if kind == "aliases" else "")
    localized[kind][lang] = _validate_slot(kind, value)
    localized = validate(localized)
    if kind == "name" and not any(item.strip() for item in localized["name"].values()):
        raise ValueError("至少保留一个非空名称")
    mirrors = {}
    if kind in {"name", "summary"} and old and entity.get(kind) == old:
        mirrored = value
        if kind == "name" and not value.strip():
            mirrored = next((localized["name"].get(key) for key in LANGUAGES
                             if localized["name"].get(key, "").strip()), "")
        mirrors[kind] = mirrored
    if kind == "name" and lang in {"zh-Hans", "zh-Hant"} and old and entity.get("name_cn") == old:
        mirrors["name_cn"] = value
    if kind == "aliases":
        retained_elsewhere = {item for key, values in localized["aliases"].items() if key != lang for item in values}
        legacy = list(dict.fromkeys(entity.get("aliases", [])))
        # Remove only items actually mirrored from the edited slot. An item
        # also retained in another language still has valid source evidence.
        legacy = [item for item in legacy if item not in old or item in value or item in retained_elsewhere]
        for item in value:
            if item not in legacy:
                legacy.append(item)
        mirrors["aliases"] = legacy[:200]
    entity["localized"] = localized
    entity.update(mirrors)


def label(path):
    if not is_field(path):
        raise ValueError("多语言字段路径无效")
    _, kind, lang = path.split(".")
    return LANGUAGES[lang] + {"name": "名称", "summary": "简介", "aliases": "别名"}[kind]


def compatibility(entity):
    """Return legacy fallback fields; callers decide if/when to persist them."""
    localized = project(entity)
    def first(kind, order):
        return next((localized[kind].get(lang) for lang in order if localized[kind].get(lang)), "")
    original_order = ("ko", "ja", "en", "ko-Latn", "und-Latn", "und", "zh-Hans", "zh-Hant")
    def mirrored(kind, order):
        old = entity.get(kind, "")
        hint = _legacy_hint(entity, kind)
        if kind == "name" and old and old == entity.get("name_cn"):
            hint = chinese_name_hint(entity)
        lang = infer_language(old, hint=hint)
        known = [key for key, value in localized[kind].items() if value == old and key not in {"und", "und-Latn"}]
        if len(known) == 1:
            lang = known[0]
        return (localized[kind].get(lang) if lang not in {"und", "und-Latn"} else "") or first(kind, order)
    aliases = list(dict.fromkeys(entity.get("aliases", [])))
    for lang in LANGUAGES:
        for alias in localized["aliases"].get(lang, []):
            if alias not in aliases:
                aliases.append(alias)
    chinese = first("name", ("zh-Hans", "zh-Hant"))
    legacy_chinese = entity.get("name_cn", "")
    # Keep readable legacy Chinese when its script is still unverified. This
    # is a display mirror, not permission to fabricate a zh-Hans language slot.
    if not chinese and legacy_chinese and legacy_chinese in localized["name"].values():
        chinese = legacy_chinese
    return {"name": mirrored("name", original_order),
            "name_cn": chinese,
            "summary": mirrored("summary", LANGUAGES), "aliases": aliases[:200]}


def add_evidence(localized, kind, value, *, hint=""):
    """Keep source evidence without replacing another value in the same slot.

    Additional names in an occupied language are retained as aliases. This is
    for provider candidates only, never a substitute for confirmed field edits.
    """
    if kind not in KINDS:
        raise ValueError("多语言字段类型无效")
    values = value if kind == "aliases" else [value]
    _validate_slot(kind, value)
    for text in values:
        if not text.strip():
            continue
        lang = infer_language(text, hint=hint)
        if not language(hint):
            known = [key for key, name in localized.get("name", {}).items() if name == text]
            if kind == "name" and known:
                continue
            if kind == "aliases":
                if any(text in rows for rows in localized.get("aliases", {}).values()):
                    continue
                if len(known) == 1:
                    lang = known[0]
        slots = localized.setdefault(kind, {})
        if kind == "aliases":
            if text not in slots.setdefault(lang, []):
                slots[lang].append(text)
        elif lang not in slots:
            slots[lang] = text
        elif kind == "name" and slots[lang] != text:
            aliases = localized.setdefault("aliases", {}).setdefault(lang, [])
            if text not in aliases:
                aliases.append(text)
    validate(localized)
