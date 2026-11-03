import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from apps.features.virtual_partner.backend.service import _chat_context
from apps.features.virtual_partner.backend.store import PartnerStore, StoreError


class OutingMemoryTests(unittest.TestCase):
    def setUp(self):
        root = TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.root = Path(root.name)
        self.store = PartnerStore(self.root)
        self.addCleanup(lambda: self.store.close())
        self.topic = self.store.state()["currentTopicId"]

    def add_read(self, read_id, bvid, title, draft=""):
        log = self.store.outings
        log.begin(read_id)
        log.record_read(read_id, {"bvid": bvid, "title": title, "description": "取得的简介", "author": "画室"}, "新内容")
        log.finish(read_id, "completed", "外出结束", "draft" if draft else "skipped", draft)

    def prompt(self, question, identity):
        turn = self.store.begin_turn(self.topic, question, identity)
        return turn["id"], self.store.prompt(turn["id"])

    def test_all_legacy_reads_page_and_recall_independently_of_limits(self):
        self.store.close()
        db = sqlite3.connect(self.root / "partner.sqlite3")
        with db:
            # Represent a pre-upgrade database. Insert only the fields version 0.7 wrote.
            db.execute("DROP TABLE partner_reply_drafts")
            db.execute("DROP TRIGGER partner_keep_forgotten_reads")
            db.execute("DROP TABLE partner_forgotten_reads_v1")
            db.executemany("""INSERT INTO outing_runs(id,status,bvid,title,author,description,read_at,reason,
                discussion,discussion_note,draft) VALUES(?,'completed',?,?, '旅人','来源文字',?,'兴趣匹配','skipped','','')""",
                           [(f"old-{i}", f"BVlegacy{i:04d}", "古琴入门" if i == 0 else f"游记 {i}", 123456 + i)
                            for i in range(139)])
        db.close()
        self.store = PartnerStore(self.root)
        log = self.store.outings
        log.begin("failed-trip")
        log.finish("failed-trip", "failed", "站点拒绝访问")
        self.assertEqual(len(log.state()["reads"]), 20)
        first = log.memories()
        ids = [entry["id"] for entry in first["entries"]]
        cursor = first["nextBeforeId"]
        # An outing completed while the reader is looking at the first page.
        self.add_read("just-added", "BV1xx411c7mD", "刚刚读到")
        while cursor is not None:
            page = log.memories(before_id=cursor)
            ids.extend(entry["id"] for entry in page["entries"])
            cursor = page["nextBeforeId"]
        self.assertEqual(ids, [f"old-{i}" for i in reversed(range(139))])
        oldest = log.memories(read_id="old-0")["entries"][0]
        self.assertEqual((oldest["title"], oldest["readAt"], oldest["description"]), ("古琴入门", 123456, "来源文字"))
        self.assertEqual(oldest["replyNotes"], [])
        self.assertEqual(self.store.state()["memories"], [])
        self.assertEqual(self.store.state()["experiences"], [])
        _, context = self.prompt("说说古琴", "cross-limit")
        self.assertEqual([row["id"] for row in context["outings"]], ["old-0"])
        with self.assertRaisesRegex(StoreError, "页码"):
            log.memories(before_id="missing")

    def test_forget_scrubs_sources_and_all_old_chat_echoes_but_retains_seen_receipt(self):
        self.add_read("folding", "BV1xx411c7mD", "折纸旅行", "本地草稿样本")
        self.add_read("other", "BV1ud411c7mD", "北极观察")
        with self.store._db:
            self.store._db.execute("""INSERT INTO partner_reply_drafts(id,read_id,incoming,draft,status)
                VALUES('note','folding','锡箔的折痕呢？','锡箔需要耐心。','draft')""")
        turn, context = self.prompt("那次锡箔的留言怎么回复？", "first")
        references = _chat_context(context)[0]["text"]
        self.assertIn("用户提供的评论（未经站点核实）", references)
        self.assertIn("本地回复草稿（未发布）", references)
        self.assertIn("锡箔需要耐心。", references)
        self.assertNotIn("BV1xx411c7mD", references)
        self.store.finish_turn(turn, "completed", "聊到了锡箔和折纸。")
        about_drafts, context = self.prompt("你在 B 站写过哪些回复？", "drafts")
        self.assertEqual([entry["id"] for entry in context["outings"]], ["folding"])
        self.assertIn("锡箔需要耐心。", _chat_context(context)[0]["text"])
        self.store.finish_turn(about_drafts, "completed", "那是一条本地草稿。")
        echo, _ = self.prompt("再讲一点", "echo")
        self.store.finish_turn(echo, "completed", "刚才说的折纸草稿很细心。")
        self.assertEqual(self.store.state()["messages"][1]["outingSources"], [{"id": "folding", "title": "折纸旅行"}])

        log = self.store.outings
        log.forget("folding")
        log.forget("folding")  # A lost acknowledgement can safely be retried.
        self.store.close()
        self.store = PartnerStore(self.root)
        log = self.store.outings
        self.assertFalse(log.is_remembered("folding"))
        self.assertIsNone(log.run("folding")["read"])
        self.assertEqual(log.memories(read_id="folding")["entries"], [])
        self.assertEqual([row["id"] for row in log.memories()["entries"]], ["other"])
        self.assertEqual(log.unseen([{"bvid": "BV1xx411c7mD"}]), [])
        log.begin("older-writer")
        with self.assertRaises(sqlite3.IntegrityError):
            log.record_read("older-writer", {"bvid": "BV1xx411c7mD", "title": "折纸旅行",
                                           "description": "旧版本又读到这条内容", "author": "画室"}, "新内容")
        log.finish("older-writer", "failed", "未能保存来源")
        past = self.store.state()["messages"]
        self.assertEqual(past[1]["outingSources"], [])
        self.assertIn("锡箔", past[1]["text"])
        self.assertEqual(self.store._db.execute("SELECT count(*) FROM partner_reply_drafts").fetchone()[0], 0)
        _, context = self.prompt("锡箔后来怎么样了？", "again")
        self.assertEqual(context["outings"], [])
        self.assertEqual(context["messages"], [{"role": "user", "text": "锡箔后来怎么样了？"}])

    def test_running_read_and_failed_attempt_are_distinct_from_a_forgotten_read(self):
        log = self.store.outings
        log.begin("in-progress")
        log.record_read("in-progress", {"bvid": "BV1xx411c7mD", "title": "纸船", "description": "", "author": "作者"}, "尝鲜")
        with self.assertRaisesRegex(StoreError, "结束"):
            log.forget("in-progress")
        self.assertTrue(log.is_remembered("in-progress"))
        log.finish("in-progress", "completed", "读到了标题", "skipped")
        log.begin("failed")
        log.finish("failed", "failed", "网络异常")
        with self.assertRaisesRegex(StoreError, "找不到"):
            log.forget("failed")
