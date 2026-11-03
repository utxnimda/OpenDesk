"""Local identity, conversations and user-approved memories of one virtual partner."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
from threading import RLock

from .avatar import avatar_data_url, initialize_avatar, save_avatar
from .notebook import Notebook, checked_examples
from .outing_schedule import OutingSchedule
from .reply_drafts import initialize_replies
from .outings import OutingLog
from .records import StoreError, _checked, _key, _now, _tokens, _uid


SCHEMA_VERSION = 1
APPLICATION_ID = 0x4F445650  # ODVP
MAX_TOPICS = 256
MAX_MEMORIES = 128
MAX_QUESTION = 4000
MAX_ANSWER = 16000
MAX_MEMORY = 500
HISTORY_BUDGET = 14000
DISPLAY_BUDGET = 60000
_TERMINAL = frozenset(("completed", "failed", "cancelled", "interrupted"))
_OMITTED = object()


_SCHEMA = (
    "CREATE TABLE topics(id TEXT PRIMARY KEY,title TEXT NOT NULL,created_at INTEGER NOT NULL)",
    """CREATE TABLE persona(singleton INTEGER PRIMARY KEY CHECK(singleton=1),id TEXT NOT NULL UNIQUE,
       name TEXT NOT NULL,personality TEXT NOT NULL,epoch INTEGER NOT NULL,
       current_topic TEXT NOT NULL REFERENCES topics(id))""",
    """CREATE TABLE turns(seq INTEGER PRIMARY KEY,id TEXT NOT NULL UNIQUE,
       topic_id TEXT NOT NULL REFERENCES topics(id),request_id TEXT NOT NULL UNIQUE,
       kind TEXT NOT NULL CHECK(kind IN ('chat','memory')),epoch INTEGER NOT NULL,
       user_id TEXT NOT NULL UNIQUE,assistant_id TEXT NOT NULL UNIQUE,question TEXT NOT NULL,
       answer TEXT NOT NULL DEFAULT '',status TEXT NOT NULL
       CHECK(status IN ('running','completed','failed','cancelled','interrupted')),
       created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)""",
    "CREATE INDEX topic_history ON turns(topic_id,seq)",
    "CREATE UNIQUE INDEX one_running ON turns(status) WHERE status='running'",
    """CREATE TABLE memories(id TEXT PRIMARY KEY,text TEXT NOT NULL UNIQUE,
       source_id TEXT UNIQUE REFERENCES turns(user_id),revision INTEGER NOT NULL,updated_at INTEGER NOT NULL)""",
    """CREATE TABLE recalled(turn_id TEXT NOT NULL REFERENCES turns(id),
       memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,revision INTEGER NOT NULL,
       PRIMARY KEY(turn_id,memory_id))""",
)

_MEMORY_VIEW = """SELECT m.id,m.text,m.source_id AS sourceMessageId,m.updated_at AS updatedAt,
    t.topic_id AS sourceTopicId,substr(t.question,1,120) AS sourceExcerpt
    FROM memories m LEFT JOIN turns t ON t.user_id=m.source_id"""


class PartnerStore:
    def __init__(self, directory):
        root = Path(directory)
        if not root.is_absolute():
            raise StoreError("DATA_DIR", "伙伴数据目录必须是绝对路径。")
        root.mkdir(parents=True, exist_ok=True)
        path = root / "partner.sqlite3"
        self._lock = RLock()
        fresh = not path.exists()
        self._db = sqlite3.connect(path, timeout=0.5, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._notebook = Notebook(self._db)
        self.outings = OutingLog(self._db, self._lock)
        self.outing_schedule = OutingSchedule(self._db, self._lock)
        try:
            self._initialize(fresh)
        except BaseException:
            self._db.close()
            raise

    def _initialize(self, fresh):
        self._db.execute("PRAGMA foreign_keys=ON")
        # Hold SQLite's exclusive ownership until close, including between transactions.
        self._db.execute("PRAGMA locking_mode=EXCLUSIVE")
        try:
            self._db.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError as error:
            if getattr(error, "sqlite_errorcode", None) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise StoreError("PARTNER_IN_USE", "伙伴数据正被另一个实例使用。") from error
            raise
        with self._db:
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            app = self._db.execute("PRAGMA application_id").fetchone()[0]
            if fresh and version == 0 and app == 0 and not self._db.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone():
                for statement in _SCHEMA:
                    self._db.execute(statement)
                topic = _uid()
                self._db.execute("INSERT INTO topics VALUES(?,?,?)", (topic, "初次见面", _now()))
                self._db.execute("INSERT INTO persona VALUES(1,?,?,?,?,?)", (
                    _uid(), "小满", "温和又好奇，闲聊时轻松自然，讨论正事时直接给出清楚的建议。", 1, topic))
                self._db.execute(f"PRAGMA application_id={APPLICATION_ID}")
                self._db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            elif version != SCHEMA_VERSION or app != APPLICATION_ID:
                raise StoreError("DATA_SCHEMA", "伙伴数据库版本或身份不兼容，原数据已保留。")
            profile = self._db.execute("SELECT * FROM persona WHERE singleton=1").fetchone()
            if not profile or not self._db.execute("SELECT 1 FROM topics WHERE id=?", (profile["current_topic"],)).fetchone():
                raise StoreError("DATA_SCHEMA", "伙伴档案或当前话题损坏。")
            self._notebook.initialize()
            self.outings.initialize()
            initialize_replies(self._db)
            self.outing_schedule.initialize()
            initialize_avatar(self._db)
            self._db.execute("UPDATE turns SET status='interrupted',updated_at=? WHERE status='running'", (_now(),))

    def close(self):
        with self._lock:
            self._db.close()

    def interrupt_outing(self):
        with self._lock, self._db:
            self.outings.interrupt()

    def _profile(self):
        return self._db.execute("SELECT * FROM persona WHERE singleton=1").fetchone()

    def _prompt_persona(self, row):
        return {"id": row["id"], "name": row["name"], "personality": row["personality"],
                "examples": self._notebook.examples(row["id"])}

    def _public_persona(self, row):
        return {**self._prompt_persona(row), "avatar": avatar_data_url(self._db)}

    def _topic(self, topic_id):
        row = self._db.execute("SELECT id,title FROM topics WHERE id=?", (_key(topic_id),)).fetchone()
        if not row:
            raise StoreError("NOT_FOUND", "找不到这个话题。")
        return dict(row)

    def _turn(self, turn_id):
        row = self._db.execute("SELECT * FROM turns WHERE id=?", (_key(turn_id),)).fetchone()
        if not row:
            raise StoreError("NOT_FOUND", "找不到这次对话。")
        return row

    def _memory_ids(self, turn_id):
        rows = self._db.execute("""SELECT r.memory_id FROM recalled r JOIN memories m
            ON r.memory_id=m.id AND r.revision=m.revision WHERE turn_id=? ORDER BY r.rowid""", (turn_id,))
        return [r[0] for r in rows]

    def _turn_view(self, row):
        return {"id": row["id"], "topicId": row["topic_id"], "status": row["status"],
                "text": row["answer"], "userMessageId": row["user_id"],
                "memoryIds": self._memory_ids(row["id"]), "experienceIds": self._notebook.references(row["id"]),
                "outingSources": self.outings.references(row["id"]),
                "updatedAt": row["updated_at"]}

    def turn(self, turn_id):
        with self._lock:
            return self._turn_view(self._turn(turn_id))

    def _memory_list(self):
        rows = self._db.execute(_MEMORY_VIEW + " ORDER BY m.updated_at DESC,m.rowid DESC")
        return [dict(row) for row in rows]

    def state(self, topic_id=None, before_message_id=None):
        """A bounded page of whole turns, in reading order. Old pages remain on disk."""
        with self._lock:
            profile = self._profile()
            topic = self._topic(profile["current_topic"] if topic_id is None else topic_id)
            params = [topic["id"]]
            condition = ""
            if before_message_id is not None:
                cursor = _key(before_message_id)
                row = self._db.execute("SELECT seq FROM turns WHERE topic_id=? AND (user_id=? OR assistant_id=?)",
                                       (topic["id"], cursor, cursor)).fetchone()
                if not row:
                    raise StoreError("INVALID_CURSOR", "消息页码无效。")
                condition = " AND seq<?"
                params.append(row["seq"])
            turns = self._db.execute("SELECT * FROM turns WHERE topic_id=?" + condition +
                                     " ORDER BY seq DESC LIMIT 21", params).fetchall()
            page = []
            size = 0
            for row in turns:
                length = len(row["question"]) + len(row["answer"])
                if len(page) >= 20 or page and size + length > DISPLAY_BUDGET:
                    break
                page.append(row)
                size += length
            messages = []
            for row in reversed(page):
                common = {"turnId": row["id"], "status": row["status"]}
                messages.append({**common, "id": row["user_id"], "role": "user", "text": row["question"],
                                 "createdAt": row["created_at"], "memoryIds": []})
                if row["status"] == "completed":
                    messages.append({**common, "id": row["assistant_id"], "role": "assistant", "text": row["answer"],
                                     "createdAt": row["updated_at"], "memoryIds": self._memory_ids(row["id"]),
                                     "experienceIds": self._notebook.references(row["id"]),
                                     "outingSources": self.outings.references(row["id"])})
            latest = self._db.execute("SELECT * FROM turns WHERE topic_id=? ORDER BY seq DESC LIMIT 1",
                                      (topic["id"],)).fetchone()
            return {"persona": self._public_persona(profile),
                    "topics": [dict(r) for r in self._db.execute("SELECT id,title FROM topics ORDER BY rowid DESC")],
                    "currentTopicId": topic["id"], "messages": messages, "memories": self._memory_list(),
                    "suggestions": self._notebook.suggestions(topic["id"]), "experiences": self._notebook.experiences(),
                    "latestTurn": self._turn_view(latest) if latest else None,
                    "hasOlderMessages": len(page) < len(turns)}

    def save_persona(self, name, personality, examples=_OMITTED, *, avatar_pixels=_OMITTED):
        fields = (_checked(name, 32, "伙伴名字"), _checked(personality, 1000, "性格设定"))
        with self._lock, self._db:
            if examples is not _OMITTED:
                self._notebook.replace_examples(self._profile()["id"], checked_examples(examples))
            if avatar_pixels is not _OMITTED:
                save_avatar(self._db, avatar_pixels)
            self._db.execute("UPDATE persona SET name=?,personality=? WHERE singleton=1", fields)
            return self._public_persona(self._profile())

    def decide_suggestion(self, identity, decision):
        with self._lock, self._db:
            return self._notebook.decide(identity, decision, self._save_memory)

    def share_experience(self, title, excerpt, url, impression, request_id):
        with self._lock, self._db:
            return self._notebook.share(self._profile()["id"], title, excerpt, url, impression, request_id)

    def edit_experience(self, identity, title, excerpt, url, impression):
        with self._lock, self._db:
            return self._notebook.edit(identity, title, excerpt, url, impression)

    def remove_experience(self, identity):
        with self._lock, self._db:
            self._notebook.remove(identity)

    def new_topic(self, title):
        title = _checked(title, 64, "话题")
        with self._lock, self._db:
            if self._db.execute("SELECT count(*) FROM topics").fetchone()[0] >= MAX_TOPICS:
                raise StoreError("TOPIC_LIMIT", "话题已达上限，请继续使用已有话题。")
            topic = _uid()
            self._db.execute("INSERT INTO topics VALUES(?,?,?)", (topic, title, _now()))
            self._db.execute("UPDATE persona SET current_topic=? WHERE singleton=1", (topic,))
            return {"id": topic, "title": title}

    def _save_memory(self, text, source_message_id, memory_id):
        if source_message_id is not None:
            if not self._db.execute("SELECT 1 FROM turns WHERE user_id=?", (_key(source_message_id),)).fetchone():
                raise StoreError("INVALID_SOURCE", "记忆只能关联已有的用户原话。")
        if memory_id is not None:
            prev = self._db.execute("SELECT * FROM memories WHERE id=?", (_key(memory_id),)).fetchone()
            if not prev:
                raise StoreError("NOT_FOUND", "找不到这条记忆。")
            if source_message_id is not None and source_message_id != prev["source_id"]:
                raise StoreError("INVALID_SOURCE", "已有记忆的来源不可替换。")
            source_message_id = prev["source_id"]
        elif self._db.execute("SELECT count(*) FROM memories").fetchone()[0] >= MAX_MEMORIES:
            raise StoreError("MEMORY_LIMIT", "记忆簿已满，请先整理已有记忆。")
        try:
            if memory_id is None:
                memory_id = _uid()
                self._db.execute("INSERT INTO memories VALUES(?,?,?,?,?)", (memory_id, text, source_message_id, 1, _now()))
                self._db.execute("""UPDATE vp2_suggestions SET decision='accept'
                    WHERE source_id=? AND decision='pending'""", (source_message_id,))
            else:
                self._db.execute("UPDATE memories SET text=?,revision=revision+1,updated_at=? WHERE id=?",
                                 (text, _now(), memory_id))
                self._db.execute("UPDATE persona SET epoch=epoch+1 WHERE singleton=1")
        except sqlite3.IntegrityError as error:
            raise StoreError("DUPLICATE_MEMORY", "这条内容或原话已经记下了。") from error
        return dict(self._db.execute(_MEMORY_VIEW + " WHERE m.id=?", (memory_id,)).fetchone())

    def save_memory(self, text, source_message_id=None, memory_id=None):
        text = _checked(text, MAX_MEMORY, "记忆")
        with self._lock, self._db:
            return self._save_memory(text, source_message_id, memory_id)

    def forget_memory(self, memory_id):
        with self._lock, self._db:
            deleted = self._db.execute("DELETE FROM memories WHERE id=?", (_key(memory_id),)).rowcount
            if not deleted:
                raise StoreError("NOT_FOUND", "找不到这条记忆。")
            # Keep the transcript for reading, but never replay prior turns (including late echoes).
            self._db.execute("UPDATE persona SET epoch=epoch+1 WHERE singleton=1")

    def _begin(self, topic_id, question, request_id, kind):
        topic_id = self._topic(topic_id)["id"]
        question = _checked(question, MAX_QUESTION, "消息")
        request_id = _key(request_id)
        old = self._db.execute("SELECT * FROM turns WHERE request_id=?", (request_id,)).fetchone()
        if old:
            if (old["topic_id"], old["question"], old["kind"]) != (topic_id, question, kind):
                raise StoreError("REQUEST_CONFLICT", "请求编号已经用于另一条消息。")
            return {**self._turn_view(old), "created": False}
        if self._db.execute("SELECT 1 FROM turns WHERE status='running'").fetchone():
            raise StoreError("PARTNER_BUSY", "请等待上一条回答结束。")
        turn_id = _uid()
        stamp = _now()
        self._db.execute("""INSERT INTO turns(id,topic_id,request_id,kind,epoch,user_id,assistant_id,
            question,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (
            turn_id, topic_id, request_id, kind, self._profile()["epoch"], _uid(), _uid(), question, "running", stamp, stamp))
        self._db.execute("UPDATE persona SET current_topic=? WHERE singleton=1", (topic_id,))
        return {**self._turn_view(self._turn(turn_id)), "created": True}

    def begin_turn(self, topic_id, text, request_id):
        with self._lock, self._db:
            return self._begin(topic_id, text, request_id, "chat")

    def remember_turn(self, topic_id, text, request_id, memory_text):
        memory_text = _checked(memory_text, MAX_MEMORY, "记忆")
        with self._lock, self._db:
            result = self._begin(topic_id, text, request_id, "memory")
            if result["created"]:
                self._save_memory(memory_text, result["userMessageId"], None)
                self._db.execute("UPDATE turns SET status='completed',answer='已记住。',updated_at=? WHERE id=?",
                                 (_now(), result["id"]))
                result = {**self._turn_view(self._turn(result["id"])), "created": True}
            return result

    def prompt(self, turn_id):
        with self._lock, self._db:
            turn = self._turn(turn_id)
            profile = self._profile()
            if turn["kind"] != "chat" or turn["status"] != "running" or turn["epoch"] != profile["epoch"]:
                raise StoreError("STALE_TURN", "本次请求的记忆或状态已变化，请重新发送。")
            history = self._db.execute("""SELECT question,answer FROM turns WHERE topic_id=?
                AND epoch=? AND kind='chat' AND status='completed' AND seq<? ORDER BY seq DESC LIMIT 12""",
                                       (turn["topic_id"], turn["epoch"], turn["seq"]))
            size = len(turn["question"])
            previous = []
            with closing(history):
                for row in history:
                    size += len(row["question"]) + len(row["answer"])
                    if size > HISTORY_BUDGET:
                        break
                    previous.append(row)
            messages = []
            for row in reversed(previous):
                messages.extend(({"role": "user", "text": row["question"]}, {"role": "assistant", "text": row["answer"]}))
            messages.append({"role": "user", "text": turn["question"]})
            query = _tokens(turn["question"])
            ranked = []
            for item in self._memory_list():
                overlap = query.keys() & _tokens(item["text"]).keys()
                score = sum(query[token] for token in overlap)
                if score:
                    ranked.append((score, item))
            ranked.sort(key=lambda item: item[0], reverse=True)
            recalled = [entry for _, entry in ranked[:3]]
            self._db.execute("DELETE FROM recalled WHERE turn_id=?", (turn_id,))
            self._db.executemany("""INSERT INTO recalled SELECT ?,id,revision FROM memories WHERE id=?""",
                                 [(turn_id, entry["id"]) for entry in recalled])
            return {"persona": self._prompt_persona(profile), "messages": messages,
                    "memories": [{"id": entry["id"], "text": entry["text"]} for entry in recalled],
                    "experiences": self._notebook.recall(turn, recalled), "outings": self.outings.recall(turn)}

    def finish_turn(self, turn_id, status, text=""):
        if not isinstance(status, str) or status not in _TERMINAL:
            raise StoreError("INVALID_STATUS", "未知的结束状态。")
        if status == "completed" or text:
            text = _checked(text, MAX_ANSWER, "回复")
        elif not isinstance(text, str):
            raise StoreError("INVALID_PARAMS", "无效的回复。")
        with self._lock, self._db:
            turn = self._turn(turn_id)
            if turn["status"] != "running":
                if turn["status"] == status and turn["answer"] == text:
                    return
                raise StoreError("TURN_FINISHED", "这次回答已经结束。")
            self._db.execute("UPDATE turns SET status=?,answer=?,updated_at=? WHERE id=?",
                             (status, text, _now(), turn_id))
            if status == "completed" and turn["epoch"] == self._profile()["epoch"]:
                self._notebook.propose(turn)
