"""Versioned additions to the partner database, transacted by its single owner."""
from __future__ import annotations

from hashlib import sha256
import json
import re
from unicodedata import normalize
from urllib.parse import urlsplit

from .records import StoreError, _checked, _key, _now, _tokens, _uid


EXTENSION_VERSION = 1
MAX_EXPERIENCES = 48
_TABLES = {
    "vp2_meta": "singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL",
    "vp2_examples": """persona_id TEXT NOT NULL REFERENCES persona(id), position INTEGER NOT NULL,
        user_text TEXT NOT NULL, assistant_text TEXT NOT NULL, PRIMARY KEY(persona_id,position)""",
    "vp2_suggestions": """source_id TEXT PRIMARY KEY REFERENCES turns(user_id), topic_id TEXT NOT NULL REFERENCES topics(id),
        epoch INTEGER NOT NULL, fact_key TEXT NOT NULL UNIQUE,
        decision TEXT NOT NULL CHECK(decision IN ('pending','accept','dismiss'))""",
    "vp2_experiences": """id TEXT PRIMARY KEY, persona_id TEXT NOT NULL REFERENCES persona(id),
        request_id TEXT NOT NULL UNIQUE, signature TEXT NOT NULL, title TEXT NOT NULL, excerpt TEXT NOT NULL,
        url TEXT NOT NULL, impression TEXT NOT NULL, source TEXT NOT NULL CHECK(source='user-share'),
        scope TEXT NOT NULL CHECK(scope='private'), revision INTEGER NOT NULL,
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, removed INTEGER NOT NULL DEFAULT 0""",
    "vp2_recalled": """turn_id TEXT NOT NULL REFERENCES turns(id),
        experience_id TEXT NOT NULL REFERENCES vp2_experiences(id), revision INTEGER NOT NULL,
        PRIMARY KEY(turn_id,experience_id)""",
}
_EXPERIENCE_VIEW = """SELECT id,persona_id AS personaId,title,excerpt,url,impression,source,scope,revision,
    created_at AS createdAt,updated_at AS updatedAt,
    CASE WHEN excerpt='' THEN 'title' ELSE 'excerpt' END AS evidence FROM vp2_experiences
    WHERE removed=0 AND scope='private' AND persona_id=(SELECT id FROM persona WHERE singleton=1)"""
_PREFERENCE = re.compile(r"我(?:平时|一直|通常|更|最|比较)?(?:喜欢|偏爱|习惯)([\u3400-\u9fffA-Za-z ]{1,42})[。！.!]?")
_SENSITIVE = re.compile(r"密码|密钥|密碼|账号|账户|证件|身份证|住址|地址|病历|病史|银行|信用卡|验证码|token|passw|api\s*key|secret", re.I)
_RECENT = re.compile(r"(?:最近|这几天|上次).{0,6}(?:新鲜事|近况|分享|摘录|经历)|(?:分享|摘录)过(?:什么|哪些)|有什么近况")
_GENERIC = frozenset("今天 明天 现在 最近 周末 平时 喜欢 比较 可以 什么 怎么 推荐 帮忙 建议 以前 用户 分享 什么".split())
_BRIDGE_STOP = frozenset("想今天明周末可是就只做以前时候了最喜爱欢过用看看常平很更想需要帮问知道听说感到打算最近怎样")


def _digest(parts):
    return sha256(json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def checked_examples(examples):
    if not isinstance(examples, list) or len(examples) > 3:
        raise StoreError("INVALID_PARAMS", "最多可以保存三组对话示例。")
    result = []
    for item in examples:
        if not isinstance(item, dict) or set(item) != {"user", "assistant"}:
            raise StoreError("INVALID_PARAMS", "每组示例需要一条用户消息和一条伙伴回复。")
        result.append((_checked(item["user"], 240, "示例原话"), _checked(item["assistant"], 240, "示例回复")))
    return result


def _share_fields(title, excerpt, url, impression):
    title = _checked(title, 80, "标题")
    excerpt = _checked(excerpt, 1000, "摘录") if excerpt != "" else ""
    impression = _checked(impression, 300, "分享者感想") if impression != "" else ""
    if url != "":
        url = _checked(url, 512, "来源链接")
        invalid = StoreError("INVALID_PARAMS", "来源只接受不含账号密码的完整 HTTP 或 HTTPS 链接。")
        if any(ch.isspace() or ch == chr(92) for ch in url):
            raise invalid
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username is not None or parsed.port == 0:
                raise invalid
        except ValueError:
            raise invalid from None
    return (title, excerpt, url, impression)


class Notebook:
    def __init__(self, db):
        self.db = db

    def initialize(self):
        existing = {row[0] for row in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name GLOB 'vp2_*'")}
        if not existing:
            for name, columns in _TABLES.items():
                self.db.execute(f"CREATE TABLE {name}({columns})")
            self.db.execute("CREATE INDEX vp2_pending ON vp2_suggestions(topic_id,epoch,decision)")
            self.db.execute("INSERT INTO vp2_meta VALUES(1,?)", (EXTENSION_VERSION,))
            return
        if existing == _TABLES.keys():
            version = self.db.execute("SELECT version FROM vp2_meta WHERE singleton=1").fetchone()
            if version and version[0] == EXTENSION_VERSION:
                return
        raise StoreError("DATA_SCHEMA", "伙伴档案扩展不完整或版本不兼容，原数据已保留。")

    def examples(self, persona_id):
        return [{"user": row[0], "assistant": row[1]} for row in self.db.execute(
            "SELECT user_text,assistant_text FROM vp2_examples WHERE persona_id=? ORDER BY position", (persona_id,))]

    def replace_examples(self, persona_id, examples):
        self.db.execute("DELETE FROM vp2_examples WHERE persona_id=?", (persona_id,))
        self.db.executemany("INSERT INTO vp2_examples VALUES(?,?,?,?)", [
            (persona_id, index, user, assistant) for index, (user, assistant) in enumerate(examples)])

    def propose(self, turn):
        question = turn["question"]
        if turn["kind"] != "chat" or not _PREFERENCE.fullmatch(question) or _SENSITIVE.search(question):
            return
        text = question.rstrip("。！.!")
        normalized = normalize("NFKC", text).casefold()
        if self.db.execute("SELECT 1 FROM memories WHERE text IN (?,?) OR source_id=?",
                           (question, text, turn["user_id"])).fetchone():
            return
        if self.db.execute("""SELECT count(*) FROM vp2_suggestions
                WHERE topic_id=? AND epoch=? AND decision='pending'""", (turn["topic_id"], turn["epoch"])).fetchone()[0] >= 8:
            return
        self.db.execute("""INSERT INTO vp2_suggestions(source_id,topic_id,epoch,fact_key,decision)
            VALUES(?,?,?,?,'pending') ON CONFLICT DO NOTHING""",
                        (turn["user_id"], turn["topic_id"], turn["epoch"], _digest(normalized)))

    def suggestions(self, topic_id):
        rows = self.db.execute("""SELECT t.user_id,t.question FROM turns t
            JOIN vp2_suggestions s ON s.source_id=t.user_id
            WHERE s.topic_id=? AND s.epoch=(SELECT epoch FROM persona WHERE singleton=1)
            AND s.decision='pending' AND NOT EXISTS(SELECT 1 FROM memories WHERE source_id=t.user_id)
            ORDER BY t.seq DESC LIMIT 8""", (topic_id,))
        return [{"id": row[0], "sourceMessageId": row[0], "text": row[1].rstrip("。！.!")} for row in rows]

    def decide(self, source_id, decision, save_memory):
        if decision not in ("accept", "dismiss"):
            raise StoreError("INVALID_PARAMS", "请选择确认或忽略这条建议。")
        row = self.db.execute("""SELECT s.decision,t.question,t.epoch,p.epoch AS current_epoch
            FROM vp2_suggestions s JOIN turns t ON t.user_id=s.source_id JOIN persona p ON p.singleton=1
            WHERE s.source_id=?""", (_key(source_id),)).fetchone()
        if not row:
            raise StoreError("NOT_FOUND", "找不到这条记忆建议。")
        if row["decision"] != decision:
            if row["decision"] != "pending" or row["epoch"] != row["current_epoch"]:
                raise StoreError("STALE_SUGGESTION", "建议已经处理或记忆发生变化，请刷新。")
            if decision == "accept":
                text = row["question"].rstrip("。！.!")
                if not self.db.execute("SELECT 1 FROM memories WHERE text=? OR source_id=?", (text, source_id)).fetchone():
                    save_memory(text, source_id, None)
            self.db.execute("UPDATE vp2_suggestions SET decision=? WHERE source_id=?", (decision, source_id))
        return {"id": source_id, "decision": decision}

    def experiences(self):
        return [dict(row) for row in self.db.execute(_EXPERIENCE_VIEW + " ORDER BY createdAt DESC,rowid DESC")]

    def _experience(self, identity):
        row = self.db.execute(_EXPERIENCE_VIEW + " AND id=?", (_key(identity),)).fetchone()
        if not row:
            raise StoreError("NOT_FOUND", "这条近况已经移除或不存在。")
        return dict(row)

    def share(self, persona_id, title, excerpt, url, impression, request_id):
        fields = _share_fields(title, excerpt, url, impression)
        signature = _digest(fields)
        old = self.db.execute("SELECT id,signature,removed FROM vp2_experiences WHERE request_id=?",
                              (_key(request_id),)).fetchone()
        if old:
            if signature != old["signature"]:
                raise StoreError("REQUEST_CONFLICT", "分享编号已经用于另一条近况。")
            return {"id": old["id"], "removed": True} if old["removed"] else self._experience(old["id"])
        if self.db.execute("SELECT count(*) FROM vp2_experiences WHERE removed=0").fetchone()[0] >= MAX_EXPERIENCES:
            raise StoreError("EXPERIENCE_LIMIT", "近况已满，请先整理已有记录。")
        identity, stamp = _uid(), _now()
        self.db.execute("""INSERT INTO vp2_experiences(id,persona_id,request_id,signature,title,excerpt,url,
            impression,source,scope,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,'user-share','private',1,?,?)""",
                        (identity, persona_id, request_id, signature, *fields, stamp, stamp))
        return self._experience(identity)

    def edit(self, identity, title, excerpt, url, impression):
        fields = _share_fields(title, excerpt, url, impression)
        old = self._experience(identity)
        if fields != (old["title"], old["excerpt"], old["url"], old["impression"]):
            self.db.execute("""UPDATE vp2_experiences SET title=?,excerpt=?,url=?,impression=?,
                revision=revision+1,updated_at=? WHERE id=?""", (*fields, _now(), identity))
            self.db.execute("UPDATE persona SET epoch=epoch+1 WHERE singleton=1")
        return self._experience(identity)

    def remove(self, identity):
        row = self.db.execute("SELECT removed FROM vp2_experiences WHERE id=?", (_key(identity),)).fetchone()
        if not row:
            raise StoreError("NOT_FOUND", "找不到这条近况。")
        if not row["removed"]:
            # Keep only receipt metadata so a retried share cannot recreate forgotten text.
            self.db.execute("""UPDATE vp2_experiences SET removed=1,title='',excerpt='',url='',impression='',
                revision=revision+1,updated_at=? WHERE id=?""", (_now(), identity))
            self.db.execute("UPDATE persona SET epoch=epoch+1 WHERE singleton=1")

    def recall(self, turn, recalled_memories):
        query = {word: weight for word, weight in _tokens(turn["question"]).items()
                 if word not in _GENERIC and not word.startswith("#")}
        # A short Chinese query may share only one content character (画) with the
        # user's plan, while that confirmed plan names the exact subject (纸船).
        has_latin = any(key.isascii() for key in query)
        bridges = []
        for memory in recalled_memories:
            words = _tokens(memory["text"])
            overlap = words.keys() & query.keys()
            if any(query[word] >= 3 or not has_latin and word not in _BRIDGE_STOP for word in overlap):
                bridges.append({word for word, weight in words.items()
                                if weight >= 3 and word not in _GENERIC and not word.startswith("#")})
        recent = bool(_RECENT.search(turn["question"]))
        ranked = []
        for item in self.experiences():
            content = _tokens(" ".join((item["title"], item["excerpt"], item["impression"])))
            direct = sum(weight for token, weight in query.items() if token in content and weight >= 3)
            via_memory = max((len(terms & content.keys()) for terms in bridges), default=0)
            if direct or via_memory or recent:
                ranked.append(((direct, via_memory), item))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        selected = [item for _, item in ranked[:2]]
        self.db.execute("DELETE FROM vp2_recalled WHERE turn_id=?", (turn["id"],))
        self.db.executemany("INSERT INTO vp2_recalled VALUES(?,?,?)",
                            [(turn["id"], item["id"], item["revision"]) for item in selected])
        return selected

    def references(self, turn_id):
        return [row[0] for row in self.db.execute("""SELECT e.id FROM vp2_recalled r
            JOIN vp2_experiences e ON r.experience_id=e.id AND r.revision=e.revision
            WHERE r.turn_id=? AND e.removed=0 AND e.scope='private' ORDER BY r.rowid""", (turn_id,))]
