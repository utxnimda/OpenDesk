"""One authoritative SQLite store, located only in the assigned module data root."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import threading
import time

from .errors import ApiError


def now() -> int:
    return int(time.time() * 1000)


class Store:
    def __init__(self, directory: Path) -> None:
        directory = Path(directory)
        if not directory.is_absolute():
            raise ValueError("AI data root must be absolute.")
        directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._database = sqlite3.connect(directory / "workbench.sqlite3", check_same_thread=False)
        version = self._database.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            self._database.close()
            raise ApiError("DATA_VERSION", "AI 数据版本不兼容，原文件已保留。", 409)
        self._database.execute("PRAGMA foreign_keys=ON")
        self._database.execute("PRAGMA journal_mode=WAL")
        with self._database:
            for table in ("connections", "conversations", "runs", "workspaces", "preferences"):
                self._database.execute(f"CREATE TABLE IF NOT EXISTS {table}(id TEXT PRIMARY KEY, document TEXT NOT NULL)")
            self._database.execute("CREATE TABLE IF NOT EXISTS events(run_id TEXT NOT NULL, sequence INTEGER NOT NULL, document TEXT NOT NULL, PRIMARY KEY(run_id,sequence))")
            self._database.execute("PRAGMA user_version=1")
        for run in self.list("runs"):
            if run["status"] in ("running", "queued", "waiting_approval", "waiting_tool", "cancelling"):
                run.update(status="interrupted", error="应用上次在任务结束前退出；外部操作结果需要检查。", updatedAt=now())
                self.put("runs", run)
                conversation = self.get("conversations", run["conversationId"])
                if conversation and conversation.get("activeRun") == run["id"]:
                    conversation["activeRun"] = None
                    self.put("conversations", conversation)

    @staticmethod
    def _table(table: str) -> str:
        if table not in ("connections", "conversations", "runs", "workspaces", "preferences"):
            raise ValueError("Invalid store collection.")
        return table

    def list(self, table: str) -> list[dict]:
        with self._lock:
            return [json.loads(row[0]) for row in self._database.execute(f"SELECT document FROM {self._table(table)}")]

    def get(self, table: str, identity: str) -> dict | None:
        with self._lock:
            row = self._database.execute(f"SELECT document FROM {self._table(table)} WHERE id=?", (identity,)).fetchone()
            return json.loads(row[0]) if row else None

    def put(self, table: str, document: dict) -> None:
        encoded = json.dumps(document, ensure_ascii=False, allow_nan=False)
        with self._lock, self._database:
            self._database.execute(f"INSERT INTO {self._table(table)}(id,document) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document", (document["id"], encoded))

    def event(self, run_id: str, event: dict) -> dict:
        with self._lock, self._database:
            sequence = self._database.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM events WHERE run_id=?", (run_id,)).fetchone()[0]
            item = {**event, "sequence": sequence}
            self._database.execute("INSERT INTO events VALUES(?,?,?)", (run_id, sequence, json.dumps(item, ensure_ascii=False, allow_nan=False)))
            return item

    def events(self, run_id: str, after: int) -> list[dict]:
        with self._lock:
            rows = self._database.execute("SELECT document FROM events WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT 128", (run_id, after)).fetchall()
            return [json.loads(row[0]) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._database.close()
