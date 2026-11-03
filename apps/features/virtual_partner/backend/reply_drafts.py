"""Local replies to user-supplied comments, stored beside the original outing."""
import threading
import sqlite3

from .records import StoreError, _checked, _key
from .provider import ProviderError


def initialize_replies(db):
    # Separate prefix so the 0.7 outing schema can still be opened after an upgrade.
    db.execute("""CREATE TABLE IF NOT EXISTS partner_reply_drafts (
        id TEXT PRIMARY KEY, read_id TEXT NOT NULL REFERENCES outing_runs(id), version INTEGER NOT NULL DEFAULT 1 CHECK(version=1),
        incoming TEXT NOT NULL, draft TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL CHECK(status IN ('pending','draft','skipped','failed')))""")
    columns = [row[1] for row in db.execute("PRAGMA table_info(partner_reply_drafts)")]
    if (columns != ["id", "read_id", "version", "incoming", "draft", "note", "status"]
            or db.execute("SELECT 1 FROM partner_reply_drafts WHERE version!=1 LIMIT 1").fetchone()):
        raise StoreError("DATA_SCHEMA", "外出来评版本不兼容，原数据已保留。")
    db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS partner_reply_once ON partner_reply_drafts(read_id,incoming)
        WHERE status!='failed'""")
    db.execute("CREATE INDEX IF NOT EXISTS partner_replies_by_read ON partner_reply_drafts(read_id)")
    db.execute("""UPDATE partner_reply_drafts SET status='failed',note='上次拟稿中断。'
        WHERE status='pending'""")


def reply_notes(db, read_id):
    rows = db.execute("""SELECT id,incoming,draft,status,note FROM partner_reply_drafts
        WHERE read_id=? ORDER BY rowid DESC""", (read_id,))
    return [dict(row) for row in rows]


class ReplyDrafts:
    def __init__(self, store, connection):
        self.store, self.connection = store, connection
        self.stop = threading.Event()
        self.thread = None
        self.storage_error = False
        self.lock = threading.RLock()

    def notes(self, read_id):
        with self.lock, self.store._lock:
            if self.storage_error:
                raise StoreError("REPLY_STORAGE", "回复草稿未能保存。请检查本地存储并重新打开页签。")
            return reply_notes(self.store._db, _key(read_id))

    def begin(self, read_id, identity, incoming):
        read_id, identity = _key(read_id), _key(identity)
        incoming = _checked(incoming, 800, "对方评论")
        store = self.store
        with self.lock, store._lock:
            if self.storage_error:
                raise StoreError("REPLY_STORAGE", "回复草稿未能保存。请检查本地存储并重新打开页签。")
            if self.stop.is_set() or not store.outings.is_remembered(read_id):
                raise StoreError("OUTING_MISSING", "这条外出见闻不存在或已忘记。")
            db = store._db
            existing = db.execute("SELECT read_id,incoming FROM partner_reply_drafts WHERE id=?", (identity,)).fetchone()
            if existing:
                if existing["read_id"] != read_id or existing["incoming"] != incoming:
                    raise StoreError("REQUEST_CONFLICT", "此请求编号已用于另一条评论。")
                return {"id": identity}
            previous = db.execute("SELECT id FROM partner_reply_drafts WHERE read_id=? AND incoming=? AND status!='failed'",
                                  (read_id, incoming)).fetchone()
            if previous:
                return {"id": previous["id"]}
            if self.thread and self.thread.is_alive():
                raise StoreError("REPLY_BUSY", "上一个回复正在拟稿。")
            if len(reply_notes(db, read_id)) >= 10:
                raise StoreError("REPLY_LIMIT", "这一条见闻已保存十条来评。")
            target = self.connection.draft_target()
            if not target:
                raise StoreError("CONNECTION_REQUIRED", "请先连接用于拟回复的模型。")
            video = store.outings.run(read_id)["read"]
            personality = db.execute("SELECT personality FROM persona WHERE singleton=1").fetchone()[0]
            with db:
                db.execute("INSERT INTO partner_reply_drafts(id,read_id,incoming,status) VALUES(?,?,?,'pending')",
                           (identity, read_id, incoming))
            self.thread = threading.Thread(target=self._draft, args=(identity, target, personality, video, incoming),
                                           daemon=True)
            try:
                self.thread.start()
            except RuntimeError:
                with db:
                    db.execute("UPDATE partner_reply_drafts SET status='failed',note='未能启动拟稿。' WHERE id=?", (identity,))
            return {"id": identity}

    def _draft(self, identity, target, personality, video, incoming):
        from .outings import draft_comment

        try:
            draft = draft_comment(self.connection, target, personality, video, self.stop, incoming=incoming)
            status, note = ("draft", "") if draft else ("skipped", "伙伴决定略过这条评论。")
        except (ProviderError, StoreError) as exc:
            status, note, draft = "failed", str(exc), ""
        except Exception:
            status, note, draft = "failed", "拟稿失败，请检查模型连接。", ""
        with self.lock:
            if not self.stop.is_set():
                try:
                    with self.store._lock, self.store._db:
                        self.store._db.execute("""UPDATE partner_reply_drafts SET draft=?,status=?,note=?
                            WHERE id=? AND status='pending'""", (draft, status, note, identity))
                except (OSError, sqlite3.Error):
                    self.storage_error = True

    def close(self):
        with self.lock:
            self.stop.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=0.5)
