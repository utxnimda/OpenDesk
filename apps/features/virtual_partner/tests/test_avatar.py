import base64
from contextlib import closing
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest
import zlib

from apps.features.virtual_partner.backend.notebook import Notebook
from apps.features.virtual_partner.backend.store import PartnerStore, StoreError


class PartnerAvatarTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root / "partner.sqlite3"
        self.store = PartnerStore(self.root)
        self.addCleanup(self.close)
        self.pixels = bytes(range(256)) * 144
        self.encoded = base64.b64encode(self.pixels).decode("ascii")

    def close(self):
        if self.store is not None:
            self.store.close()
            self.store = None

    def test_png_preserves_rgba_and_contains_only_image_chunks(self):
        profile = self.store.save_persona("小云", "直率", avatar_pixels=self.encoded)
        prefix, encoded = profile["avatar"].split(",", 1)
        self.assertEqual(prefix, "data:image/png;base64")
        png = base64.b64decode(encoded, validate=True)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        offset = 8
        chunks = []
        while offset < len(png):
            length = struct.unpack_from(">I", png, offset)[0]
            kind = png[offset + 4:offset + 8]
            data = png[offset + 8:offset + 8 + length]
            crc = struct.unpack_from(">I", png, offset + 8 + length)[0]
            self.assertEqual(zlib.crc32(kind + data), crc)
            chunks.append((kind, data))
            offset += 12 + length
        self.assertEqual([kind for kind, _ in chunks], [b"IHDR", b"IDAT", b"IEND"])
        self.assertEqual(struct.unpack(">IIBBBBB", chunks[0][1]), (96, 96, 8, 6, 0, 0, 0))
        self.assertEqual(chunks[2][1], b"")
        image = zlib.decompress(chunks[1][1])
        self.assertEqual(len(image), 96 * 385)
        for row in range(96):
            self.assertEqual(image[row * 385], 0)
            self.assertEqual(image[row * 385 + 1:(row + 1) * 385], self.pixels[row * 384:(row + 1) * 384])

    def test_legacy_profile_edits_reopen_without_losing_avatar_and_clear_is_persistent(self):
        self.close()
        # A pre-avatar database already has the notebook extension.
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DROP TABLE persona_avatar")
        self.store = PartnerStore(self.root)
        original = self.store.state()
        self.assertIsNone(original["persona"]["avatar"])
        examples = [{"user": "好累", "assistant": "慢慢来"}]
        saved = self.store.save_persona("小云", "直率", examples, avatar_pixels=self.encoded)
        updated = self.store.save_persona("云", "轻声")
        self.assertEqual(updated, {**saved, "name": "云", "personality": "轻声"})
        self.close()
        # The 0.4.1 worker opens this notebook and updates these fields on normal profile saves.
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
            Notebook(db).initialize()
            db.execute("UPDATE persona SET name=?,personality=? WHERE singleton=1", ("旧包改名", "轻声"))
        self.store = PartnerStore(self.root)
        self.assertEqual(self.store.state()["persona"], {**updated, "name": "旧包改名"})
        run = self.store.begin_turn(original["currentTopicId"], "你好", "privacy")
        persona = self.store.prompt(run["id"])["persona"]
        self.assertEqual(persona, {"id": saved["id"], "name": "旧包改名", "personality": "轻声", "examples": examples})
        cleared = self.store.save_persona("云", "轻声", avatar_pixels=None)
        self.close()
        self.store = PartnerStore(self.root)
        self.assertEqual(self.store.state()["persona"], {**updated, "avatar": None})
        self.assertEqual(self.store.state()["persona"], cleared)

    def test_invalid_pixels_preserve_all_fields_from_previous_save(self):
        saved = self.store.save_persona("小云", "直率", [{"user": "你好", "assistant": "早"}],
                                       avatar_pixels=self.encoded)
        for invalid in (False, {}, [], "", self.encoded + "AAAA", self.encoded[:-4],
                        self.encoded[:-1] + "=", self.encoded[:-1] + "\n", self.encoded[:-1] + "界",
                        "data:image/png;base64," + self.encoded, "/" * 49151 + "_"):
            with self.subTest(value=repr(invalid)[:20]):
                with self.assertRaises(StoreError) as error:
                    self.store.save_persona("错误名字", "错误性格", [], avatar_pixels=invalid)
                self.assertEqual(error.exception.code, "INVALID_PARAMS")
                self.assertEqual(self.store.state()["persona"], saved)

    def test_unknown_or_malformed_avatar_fails_before_running_turn_recovery(self):
        topic = self.store.state()["currentTopicId"]
        self.store.begin_turn(topic, "保留运行状态", "running")
        self.close()
        for version, pixels in ((2, self.pixels), (1, b"short"), (1, "a" * 36864)):
            with self.subTest(version=version, data_type=type(pixels).__name__):
                with closing(sqlite3.connect(self.path)) as db, db:
                    db.execute("PRAGMA ignore_check_constraints=ON")
                    db.execute("UPDATE persona_avatar SET version=?,rgba=?", (version, pixels))
                before = self.path.read_bytes()
                with self.assertRaisesRegex(StoreError, "头像") as error:
                    PartnerStore(self.root)
                self.assertEqual(error.exception.code, "DATA_SCHEMA")
                self.assertEqual(self.path.read_bytes(), before)
                with closing(sqlite3.connect(self.path)) as db:
                    self.assertEqual(db.execute("SELECT status FROM turns").fetchone()[0], "running")
