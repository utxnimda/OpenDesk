import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Barrier, Thread
import unittest

from apps.features.virtual_partner.backend.store import PartnerStore, StoreError


class PartnerStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = PartnerStore(self.root)
        self.addCleanup(lambda: self.store.close())
        self.topic = self.store.state()["currentTopicId"]

    def test_reopen_preserves_identity_topics_and_relevant_memory(self):
        identity = self.store.save_persona("青鸟", "温和直接。")["id"]
        command = self.store.remember_turn(self.topic, "记住：我的雨衣是绿色的", "initial", "我的雨衣是绿色的")
        self.store.save_memory("周一去游泳")
        topic = self.store.new_topic("旅行")["id"]
        self.store.close()
        self.store = PartnerStore(self.root)
        self.assertEqual(self.store.state()["persona"], {"id": identity, "name": "青鸟", "personality": "温和直接。",
                                                         "examples": [], "avatar": None})
        self.assertEqual(self.store.state()["currentTopicId"], topic)
        turn = self.store.begin_turn(topic, "雨衣是什么颜色？", "restored")
        prompt = self.store.prompt(turn["id"])
        self.assertEqual([m["text"] for m in prompt["memories"]], ["我的雨衣是绿色的"])
        self.assertEqual(prompt["messages"], [{"role": "user", "text": "雨衣是什么颜色？"}])
        self.store.finish_turn(turn["id"], "completed", "绿色。")
        state = self.store.state()
        linked = next(m for m in state["memories"] if m["sourceMessageId"] is not None)
        self.assertEqual(linked["sourceMessageId"], command["userMessageId"])
        self.assertEqual(linked["sourceExcerpt"], "记住：我的雨衣是绿色的")
        self.assertEqual(linked["sourceTopicId"], self.topic)
        self.assertEqual(state["messages"][-1]["memoryIds"], [linked["id"]])

    def test_edit_and_forget_exclude_transcript_and_in_flight_echoes_from_future_requests(self):
        first = self.store.begin_turn(self.topic, "我的雨衣是绿色", "one")
        self.store.finish_turn(first["id"], "completed", "知道了，绿色雨衣。")
        memory = self.store.save_memory("绿色雨衣", first["userMessageId"])
        self.store.save_memory("正在读红楼梦")
        self.store.save_memory("紫色雨衣", memory_id=memory["id"])
        active = self.store.begin_turn(self.topic, "我的雨衣呢", "two")
        prompt = self.store.prompt(active["id"])
        self.assertEqual([m["text"] for m in prompt["memories"]], ["紫色雨衣"])
        self.assertEqual(len(prompt["messages"]), 1)
        self.store.forget_memory(memory["id"])
        self.store.finish_turn(active["id"], "completed", "紫色雨衣。")
        current = self.store.begin_turn(self.topic, "我在读什么书？雨衣呢？", "three")
        prompt = self.store.prompt(current["id"])
        self.assertEqual([m["text"] for m in prompt["memories"]], ["正在读红楼梦"])
        self.assertEqual(prompt["messages"], [{"role":"user", "text":"我在读什么书？雨衣呢？"}])
        state = self.store.state()
        self.assertIn("紫色雨衣。", [m["text"] for m in state["messages"]])
        self.assertTrue(all(memory["id"] not in m["memoryIds"] for m in state["messages"]))

    def test_offline_remember_is_atomic_and_request_replay_has_no_side_effect(self):
        original = self.store.remember_turn(self.topic, "记住：称呼我小周", "remember", "称呼我小周")
        replay = self.store.remember_turn(self.topic, "记住：称呼我小周", "remember", "称呼我小周")
        self.assertTrue(original["created"])
        self.assertFalse(replay["created"])
        self.assertEqual(original["id"], replay["id"])
        with self.assertRaisesRegex(StoreError, "已经记下"):
            self.store.remember_turn(self.topic, "记住：称呼我小周", "duplicate", "称呼我小周")
        with self.assertRaisesRegex(StoreError, "另一条消息"):
            self.store.remember_turn(self.topic, "记住：新内容", "remember", "新内容")
        state = self.store.state()
        self.assertEqual(len(state["messages"]), 2)
        self.assertEqual(len(state["memories"]), 1)
        self.assertEqual(state["latestTurn"]["status"], "completed")
        new = self.store.begin_turn(self.topic, "我叫什么名字？", "after-remember")
        self.assertEqual([m["text"] for m in self.store.prompt(new["id"])["memories"]], ["称呼我小周"])

    def test_competing_instance_cannot_claim_recovery_and_interrupted_request_is_not_retried(self):
        active = self.store.begin_turn(self.topic, "开始一次对话", "attempt")
        with self.assertRaisesRegex(StoreError, "另一个实例"):
            PartnerStore(self.root)
        self.assertEqual(self.store.turn(active["id"])["status"], "running")
        self.store.close()
        self.store = PartnerStore(self.root)
        replay = self.store.begin_turn(self.topic, "开始一次对话", "attempt")
        self.assertFalse(replay["created"])
        self.assertEqual(replay["status"], "interrupted")
        current = self.store.begin_turn(self.topic, "重试新问题", "new-attempt")
        self.assertEqual(self.store.prompt(current["id"])["messages"], [{"role":"user","text":"重试新问题"}])

    def test_duplicate_sends_on_two_threads_create_one_turn_and_completion_is_idempotent(self):
        results = []
        barrier = Barrier(2)
        def send():
            barrier.wait(timeout=3)
            results.append(self.store.begin_turn(self.topic, "你好", "shared-token"))
        threads = [Thread(target=send) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(4)
        self.assertCountEqual([r["created"] for r in results], [True, False])
        self.assertEqual(results[0]["id"], results[1]["id"])
        with self.assertRaisesRegex(StoreError, "等待上一条"):
            self.store.begin_turn(self.topic, "第二条", "different")
        rid = results[0]["id"]
        self.store.finish_turn(rid, "completed", "你好呀")
        self.store.finish_turn(rid, "completed", "你好呀")
        with self.assertRaisesRegex(StoreError, "已经结束"):
            self.store.finish_turn(rid, "failed", "错误")
        self.assertEqual(len(self.store.state()["messages"]), 2)

    def test_bounded_page_and_prompt_preserve_full_turns_and_real_cursor(self):
        for i in range(6):
            turn = self.store.begin_turn(self.topic, "旅" * 4000, f"turn-{i}")
            self.store.finish_turn(turn["id"], "completed", "🌲" * 16000)
        state = self.store.state()
        self.assertTrue(state["hasOlderMessages"])
        self.assertLess(len(json.dumps(state, ensure_ascii=False).encode()), 2*1024*1024)
        older = self.store.state(self.topic, state["messages"][0]["id"])
        self.assertTrue(older["messages"])
        self.assertFalse(set(m["id"] for m in older["messages"]) & set(m["id"] for m in state["messages"]))
        turn = self.store.begin_turn(self.topic, "最后一个问题", "last")
        self.assertEqual(self.store.prompt(turn["id"])["messages"], [{"role": "user", "text": "最后一个问题"}])
        with self.assertRaisesRegex(StoreError, "页码无效"):
            self.store.state(self.topic, "missing")

    def test_invalid_source_and_conflicting_edit_leave_other_rows_unchanged(self):
        before = self.store.save_memory("去海边")
        second = self.store.save_memory("去森林")
        with self.assertRaisesRegex(StoreError, "用户原话"):
            self.store.save_memory("未知来源", "missing")
        with self.assertRaisesRegex(StoreError, "已经记下"):
            self.store.save_memory(second["text"], memory_id=before["id"])
        self.assertCountEqual([m["text"] for m in self.store.state()["memories"]], ["去海边", "去森林"])

    def test_relative_and_unrecognized_databases_refused_without_overwrite(self):
        with self.assertRaisesRegex(StoreError, "绝对路径"):
            PartnerStore(Path("relative"))
        other = self.root / "unknown"
        other.mkdir()
        path = other / "partner.sqlite3"
        db = sqlite3.connect(path)
        with db:
            db.execute("PRAGMA user_version=23")
            db.execute("CREATE TABLE future_data(note TEXT)")
            db.execute("INSERT INTO future_data VALUES('preserve')")
        db.close()
        original = path.read_bytes()
        with self.assertRaisesRegex(StoreError, "不兼容"):
            PartnerStore(other)
        self.assertEqual(original, path.read_bytes())
