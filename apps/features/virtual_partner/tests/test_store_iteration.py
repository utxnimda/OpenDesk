from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from apps.features.virtual_partner.backend.store import APPLICATION_ID, PartnerStore, StoreError, _SCHEMA


class NotebookIterationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db_file = self.root / "partner.sqlite3"
        self.store = None
        self.addCleanup(self.close)

    def open(self):
        self.close()
        self.store = PartnerStore(self.root)
        return self.store.state()["currentTopicId"]

    def close(self):
        if self.store:
            self.store.close()
            self.store = None

    def legacy_db(self):
        db = sqlite3.connect(self.db_file)
        with db:
            for statement in _SCHEMA:
                db.execute(statement)
            db.execute("INSERT INTO topics VALUES('first','旧话题',0)")
            db.execute("INSERT INTO persona VALUES(1,'one','旧名','保持清晰',1,'first')")
            db.execute("INSERT INTO memories VALUES('prior','我的乐器是小提琴',NULL,1,0)")
            db.execute(f"PRAGMA application_id={APPLICATION_ID}")
            db.execute("PRAGMA user_version=1")
        db.close()

    def chat(self, topic, question, answer="收到。", status="completed"):
        turn = self.store.begin_turn(topic, question, uuid4().hex)
        self.store.finish_turn(turn["id"], status, answer)
        return turn

    def test_legacy_extension_is_atomic_and_old_writer_can_reopen(self):
        self.legacy_db()
        with patch("apps.features.virtual_partner.backend.notebook._TABLES",
                   {"vp2_meta": "version INTEGER", "vp2_fail": ")??"}):
            with self.assertRaises(sqlite3.DatabaseError):
                self.open()
        db = sqlite3.connect(self.db_file)
        self.assertFalse(db.execute("SELECT name FROM sqlite_master WHERE name GLOB 'vp2_*'").fetchone())
        db.close()
        topic = self.open()
        self.assertEqual(self.store.state()["persona"]["examples"], [])
        examples = [{"user": "好累", "assistant": "歇歇，等会儿再看。"}]
        self.store.save_persona("云", "说话简洁", examples)
        self.store.save_persona("小云", "说话简洁")
        for invalid in (None, examples * 4, [{"user": "缺一项"}], [{"user": "长" * 241, "assistant": "好"}]):
            with self.assertRaises(StoreError):
                self.store.save_persona("无效", "不应保存", invalid)
        self.close()
        # These statements are the persona update and a new memory insert used by 0.1.0.
        db = sqlite3.connect(self.db_file)
        with db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
            db.execute("UPDATE persona SET name=?,personality=? WHERE singleton=1", ("旧包改名", "轻声"))
            db.execute("INSERT INTO memories VALUES(?,?,?,?,?)", ("v1-note", "周末去散步", None, 1, 10))
        db.close()
        self.assertEqual(self.open(), topic)
        state = self.store.state()
        self.assertEqual(state["persona"], {"id":"one", "name":"旧包改名", "personality":"轻声",
                                            "examples":examples, "avatar":None})
        self.assertCountEqual([m["id"] for m in state["memories"]], ["prior", "v1-note"])
        self.store.save_persona("旧包改名", "轻声", [])
        self.assertEqual(self.store.state()["persona"]["examples"], [])

    def test_unknown_extension_precedes_run_recovery_and_writes(self):
        topic = self.open()
        run = self.store.begin_turn(topic, "应保留的运行", "request")
        self.close()
        db = sqlite3.connect(self.db_file)
        with db:
            db.execute("UPDATE vp2_meta SET version=99")
        db.close()
        before = self.db_file.read_bytes()
        with self.assertRaisesRegex(StoreError, "不兼容"):
            self.open()
        self.assertEqual(self.db_file.read_bytes(), before)
        db = sqlite3.connect(self.db_file)
        self.assertEqual(db.execute("SELECT status FROM turns WHERE id=?", (run["id"],)).fetchone()[0], "running")
        db.close()

    def test_suggestions_need_confirmed_user_source_and_survive_retries(self):
        one = self.open()
        turn = self.chat(one, "我喜欢用紫色铅笔画画。")
        self.chat(one, "还不错", "我喜欢被模型捏造的事实。")
        self.chat(one, "我喜欢芝士。", "", "cancelled")
        self.chat(one, "我喜欢这个密码。")
        self.chat(one, "我喜欢喝茶吗？")
        self.assertEqual(self.store.state(one)["suggestions"], [
            {"id":turn["userMessageId"], "sourceMessageId":turn["userMessageId"], "text":"我喜欢用紫色铅笔画画"}])
        two = self.store.new_topic("另一天")["id"]
        before = self.store.begin_turn(two, "喜欢的画具是什么？", "pre")
        self.assertEqual(self.store.prompt(before["id"])["memories"], [])
        self.assertEqual(self.store.state(two)["suggestions"], [])
        self.store.finish_turn(before["id"], "completed", "还不知道。")
        source = turn["userMessageId"]
        selected = self.store.decide_suggestion(source, "accept")
        self.open()
        self.assertEqual(self.store.decide_suggestion(source, "accept"), selected)
        memory = self.store.state()["memories"]
        self.assertEqual(len(memory), 1)
        self.assertEqual(memory[0]["sourceMessageId"], source)
        self.store.forget_memory(memory[0]["id"])
        self.assertEqual(self.store.decide_suggestion(source, "accept"), selected)
        self.chat(one, "我喜欢用紫色铅笔画画。")
        self.assertEqual(self.store.state(one)["suggestions"], [])
        self.assertEqual(self.store.state(one)["memories"], [])
        dismissed = self.chat(two, "我喜欢喝茶。")["userMessageId"]
        self.store.decide_suggestion(dismissed, "dismiss")
        self.open()
        self.assertEqual(self.store.decide_suggestion(dismissed, "dismiss")["decision"], "dismiss")
        with self.assertRaises(StoreError):
            self.store.decide_suggestion(dismissed, "accept")
        self.assertEqual(self.store.state(two)["suggestions"], [])

    def test_receipts_for_edit_and_delete_do_not_recreate_forgotten_evidence(self):
        topic = self.open()
        args = ("纸船笔记", "折痕的阴影很浅", "https://example.org/paper", "适合做画稿", "same-key")
        first = self.store.share_experience(*args)
        self.assertEqual(first, self.store.share_experience(*args))
        self.assertEqual((first["evidence"], first["source"], first["scope"]), ("excerpt", "user-share", "private"))
        with self.assertRaises(StoreError):
            self.store.share_experience("另一次", *args[1:])
        rid = self.store.begin_turn(topic, "最近有什么新鲜事？", "one")["id"]
        self.assertEqual(self.store.prompt(rid)["experiences"], [first])
        self.store.finish_turn(rid, "completed", "折痕的阴影很浅")
        self.assertEqual(self.store.state()["messages"][-1]["experienceIds"], [first["id"]])
        self.store.edit_experience(first["id"], "灯笼标题", "", "", "想了解配色")
        self.assertEqual(self.store.share_experience(*args)["evidence"], "title")
        self.assertEqual(self.store.state()["messages"][-1]["experienceIds"], [])
        query = self.store.begin_turn(topic, "我分享过什么？", "two")["id"]
        context = self.store.prompt(query)
        self.assertEqual(context["messages"], [{"role":"user","text":"我分享过什么？"}])
        self.assertEqual([item["title"] for item in context["experiences"]], ["灯笼标题"])
        self.store.remove_experience(first["id"])
        self.store.finish_turn(query, "completed", "灯笼标题")
        self.open()
        self.assertTrue(self.store.share_experience(*args)["removed"])
        self.store.remove_experience(first["id"])
        next_run = self.store.begin_turn(topic, "最近有什么新鲜事？", "three")["id"]
        clean = self.store.prompt(next_run)
        self.assertEqual(clean["experiences"], [])
        self.assertEqual(clean["messages"], [{"role":"user","text":"最近有什么新鲜事？"}])

    def test_shared_drawing_recalled_through_confirmed_memory_after_restart(self):
        topic = self.open()
        self.store.remember_turn(topic, "记住：周末想画一艘纸船", "remember", "周末想画一艘纸船")
        first = self.store.share_experience("纸船折痕", "用阴影表现折角", "", "", "share")
        next_topic = self.store.new_topic("周日")["id"]
        self.open()
        rid = self.store.begin_turn(next_topic, "今天只想画十分钟，有什么可以试试？", "drawing")["id"]
        self.assertEqual(self.store.prompt(rid)["experiences"], [first])
        self.store.finish_turn(rid, "completed", "试试画一道折痕。")
        rid = self.store.begin_turn(topic, "怎么修 Git 冲突？", "git")["id"]
        self.assertEqual(self.store.prompt(rid)["experiences"], [])

    def test_inputs_cannot_invent_sources_or_open_a_link(self):
        self.open()
        for url in ("javascript:alert(1)", "file:///C:/x", "https://p:q@example.org",
                    "https://example.org:invalid", "https://example.org/a\nb"):
            with self.assertRaises(StoreError):
                self.store.share_experience("坏链接", "", url, "", "bad")
        entry = self.store.share_experience("先留个标题", "", "https://example.invalid/", "", "url-only")
        self.assertEqual((entry["evidence"], entry["excerpt"]), ("title", ""))
        self.assertEqual(len(self.store.state()["experiences"]), 1)
