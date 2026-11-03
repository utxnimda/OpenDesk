"""Validation and identifiers shared by the partner's local SQLite records."""
import re
from time import time_ns
from unicodedata import normalize
from uuid import uuid4

_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}\Z")
_WORDS = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]+")
_FILLER = frozenset("的是了我你他她它这那在有和也就么吗谁要想会不很什么个一点该能好呢过请把去上来着都到得做最给")
_NAMES = re.compile(r"叫我|我叫|称呼|名字|my name|call me")


class StoreError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _checked(value, limit, name):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise StoreError("INVALID_PARAMS", f"{name}须为 1 至 {limit} 字的文本。")
    if any(0xD800 <= ord(ch) <= 0xDFFF or ord(ch) < 32 and ch not in "\t\r\n" for ch in value):
        raise StoreError("INVALID_PARAMS", f"{name}包含不支持的字符。")
    return value.strip()


def _key(value):
    if not isinstance(value, str) or not _REQUEST_ID.fullmatch(value):
        raise StoreError("INVALID_PARAMS", "无效的记录编号。")
    return value


def _tokens(text):
    """Retain both Chinese bigrams and distinctive single characters for short queries."""
    text = normalize("NFKC", text).casefold()
    tokens = {}
    for word in _WORDS.findall(text):
        if word.isascii():
            if len(word) > 1:
                tokens[word] = 3
            continue
        for char in word:
            if char not in _FILLER:
                tokens[char] = 1
        for a, b in zip(word, word[1:]):
            if a not in _FILLER and b not in _FILLER:
                tokens[a + b] = 4
    if _NAMES.search(text):
        tokens["#name"] = 6
    return tokens


def _now():
    return time_ns() // 1_000_000


def _uid():
    return uuid4().hex
