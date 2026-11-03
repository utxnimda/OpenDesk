from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import Mock

from apps.features.virtual_partner.backend.service import PartnerService
from apps.features.virtual_partner.backend.records import StoreError


class ReplyDraftTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.model = Mock()
        self.model.draft_target.return_value = ("test", "https://local.invalid", "fake", "ref")
        self.service = PartnerService(Path(folder.name), self.model)
        self.addCleanup(self.service.close)
        log = self.service.store.outings
        log.begin("outing")
        log.record_read("outing", {"bvid": "BV1xx411c7mD", "title": "纸船", "author": "作者", "description": "折痕与水彩"}, "兴趣")
        log.finish("outing", "completed", "已读。", "skipped")
        self.params = {"readId": "outing", "requestId": "reply-one", "incoming": "你会用什么纸？"}

    def test_public_projection_idempotency_and_retry_after_failure(self):
        self.service.profile({"name":"私聊昵称", "personality":"温和，只在私聊提到的设置"})
        self.service.remember({"text":"仅私聊的记忆"})
        self.model.reply.side_effect = ["broken response", '{"draft":"我会试试水彩纸。"}']
        self.service.draft_reply(self.params)
        self.service.reply_drafts.thread.join(2)
        self.assertEqual(self.service.reply_notes(self.params)["entries"][0]["status"], "failed")
        self.service.draft_reply(self.params)
        self.assertEqual(self.model.reply.call_count, 1)
        self.service.draft_reply({**self.params, "requestId": "new-attempt"})
        self.service.reply_drafts.thread.join(2)
        entries = self.service.reply_notes(self.params)["entries"]
        self.assertEqual((entries[0]["status"], entries[0]["draft"]), ("draft", "我会试试水彩纸。"))
        self.assertEqual(self.service.draft_reply({**self.params, "requestId": "same-text"}), {"id": "new-attempt"})
        payload = str(self.model.reply.call_args.args[:3])
        self.assertIn("你会用什么纸", payload)
        self.assertNotIn("私聊昵称", payload)
        self.assertNotIn("仅私聊的记忆", payload)
        self.assertNotIn("只在私聊提到的设置", payload)

    def test_forgotten_outing_cannot_regain_a_late_reply(self):
        entered, release = Event(), Event()
        def delayed(*args, **kwargs):
            entered.set()
            release.wait(2)
            return '{"draft":"延迟回复"}'
        self.model.reply.side_effect = delayed
        self.service.draft_reply(self.params)
        self.assertTrue(entered.wait(2))
        try:
            self.service.store.outings.forget("outing")
        finally:
            release.set()
        self.service.reply_drafts.thread.join(2)
        self.assertEqual(self.service.reply_notes(self.params)["entries"], [])
        with self.assertRaises(StoreError):
            self.service.draft_reply(self.params)
