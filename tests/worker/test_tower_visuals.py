"""Visual contracts use generated test pixels, never copied game assets."""
from __future__ import annotations

import base64
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from PIL import Image

from apps.features.tower_assistant.backend import visuals


class VisualTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.member = "images/角色形象-sheet0.webp"
        self.frame = [self.member, 0, 10, 20, 40, 80]
        self.item_frame = ["images/物品图标-sheet0.webp", 0, 0, 0, 100, 100]
        self.hash_patch = patch.dict(visuals.RULE_HASHES)
        self.hash_patch.start()
        self.write_package()

    def tearDown(self):
        visuals._CACHE.clear()
        visuals._catalogue.cache_clear()
        self.hash_patch.stop()
        self.temp.cleanup()

    def write_package(self, *, name="com.ujwan.tfszmnq", atlas_size=(120, 120),
                      duplicate=False, omit_atlas=False, image_format="WEBP"):
        objects = [[] for _ in range(421)]
        animation = ["B6", 0, 0, 0, 0, 0, 0, [self.frame]]
        objects[221] = ["角色形象", 0, 0, 0, 0, 0, 0, [animation] * (2 if duplicate else 1)]
        objects[420] = ["物品图标", 0, 0, 0, 0, 0, 0, [["兽血1", 0, 0, 0, 0, 0, 0, [self.item_frame]]]]
        raw = json.dumps({"project": [0, 0, 0, objects]}, ensure_ascii=False).encode()
        visuals.RULE_HASHES["data.json"] = sha256(raw).hexdigest()
        with Image.new("RGBA", atlas_size, (0, 0, 255, 255)) as image:
            image.paste((255, 0, 0, 255), (10, 20, 50, 100))
            stream = BytesIO()
            image.save(stream, image_format, lossless=True)
            pixels = stream.getvalue()
        with ZipFile(self.root / "package.nw", "w") as package:
            package.writestr("package.json", json.dumps({"name": name}))
            package.writestr("data.json", raw)
            if not omit_atlas:
                package.writestr(self.member, pixels)
                package.writestr("images/物品图标-sheet0.webp", pixels)
        visuals._CACHE.clear()
        visuals._catalogue.cache_clear()

    def test_exact_first_frame_crop_and_aspect_preserving_png(self):
        result = visuals.load(self.root, ["person:B6", "item:兽血1"])
        frame = result["sprites"]["person:B6"]
        self.assertEqual((frame["x"], frame["y"], frame["width"], frame["height"]), (0, 0, 32, 64))
        sheet = result["sheets"][frame["sheetId"]]
        prefix, raw = sheet["dataUrl"].split(",", 1)
        self.assertEqual(prefix, "data:image/png;base64")
        with Image.open(BytesIO(base64.b64decode(raw))) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.getpixel((16, 32)), (255, 0, 0, 255))
            self.assertEqual(image.size, (32, 64))
        self.assertEqual(result["warnings"], [])
        self.assertLess(len(json.dumps(result).encode()), visuals.MAX_RESPONSE_BYTES)

    def test_unknown_key_is_explicit_fallback_and_duplicates_are_deduplicated(self):
        result = visuals.load(self.root, ["person:B6", "person:B6", "person:Unknown"])
        self.assertEqual(list(result["sprites"]), ["person:B6"])
        self.assertEqual(len(result["sheets"]), 1)
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("person:Unknown", result["warnings"][0])

    def test_path_keys_and_oversized_batches_are_rejected_before_package_read(self):
        for keys in [["person:../save"], ["item:C:\\private"], ["person:\x00B6"], ["file:data.json"], [1], ["person:B6"] * 33]:
            with self.subTest(keys=keys), self.assertRaises(visuals.VisualError):
                visuals.load(self.root, keys)

    def test_unknown_package_revision_and_wrong_game_are_rejected(self):
        visuals.RULE_HASHES["data.json"] = "0" * 64
        with self.assertRaisesRegex(visuals.VisualError, "尚未核实"):
            visuals.load(self.root, ["person:B6"])
        self.write_package(name="another-game")
        with self.assertRaisesRegex(visuals.VisualError, "无法提供"):
            visuals.load(self.root, ["person:B6"])

    def test_frame_cannot_read_an_arbitrary_zip_member(self):
        self.frame[0] = "save/ea1save.dat"
        self.write_package()
        with self.assertRaisesRegex(visuals.VisualError, "未允许"):
            visuals.load(self.root, ["person:B6"])

    def test_crop_outside_image_is_rejected(self):
        self.frame[2] = 110
        self.write_package()
        with self.assertRaisesRegex(visuals.VisualError, "超出图集边界"):
            visuals.load(self.root, ["person:B6"])

    def test_large_decoded_image_and_non_webp_are_rejected(self):
        self.write_package(atlas_size=(4097, 120))
        with self.assertRaisesRegex(visuals.VisualError, "解压尺寸"):
            visuals.load(self.root, ["person:B6"])
        self.write_package(image_format="PNG")
        with self.assertRaisesRegex(visuals.VisualError, "解压尺寸"):
            visuals.load(self.root, ["person:B6"])

    def test_duplicate_mapping_and_missing_atlas_are_rejected(self):
        self.write_package(duplicate=True)
        with self.assertRaisesRegex(visuals.VisualError, "动画名称重复"):
            visuals.load(self.root, ["person:B6"])
        self.write_package(omit_atlas=True)
        with self.assertRaisesRegex(visuals.VisualError, "资源不完整"):
            visuals.load(self.root, ["person:B6"])

    def test_bounds_check_compressed_member_png_and_response_sizes(self):
        for name, value, message in [("MAX_ATLAS_BYTES", 10, "图集文件"),
                                      ("MAX_PNG_BYTES", 10, "压缩结果"),
                                      ("MAX_RESPONSE_BYTES", 10, "响应")]:
            visuals._CACHE.clear()
            with self.subTest(name=name), patch.object(visuals, name, value), self.assertRaisesRegex(visuals.VisualError, message):
                visuals.load(self.root, ["person:B6"])

    def test_read_is_unchanged_and_second_load_uses_only_small_thumbnail_cache(self):
        package = self.root / "package.nw"
        before = sha256(package.read_bytes()).hexdigest()
        first = visuals.load(self.root, ["person:B6"])
        with patch.object(visuals, "_decode_group", side_effect=AssertionError("decoded again")):
            self.assertEqual(visuals.load(self.root, ["person:B6"]), first)
        self.assertEqual(sha256(package.read_bytes()).hexdigest(), before)
        self.assertEqual(list(self.root.iterdir()), [package])
        self.assertTrue(all(isinstance(value[0], bytes) and len(value[0]) < visuals.MAX_PNG_BYTES
                            for value in visuals._CACHE.values()))

    def test_package_change_during_read_fails(self):
        original = visuals._signature
        initial = original(self.root / "package.nw")
        with patch.object(visuals, "_signature", side_effect=[initial, ("changed", 0, 0, 0)]):
            with self.assertRaisesRegex(visuals.VisualError, "发生变化"):
                visuals.load(self.root, ["person:B6"])


if __name__ == "__main__":
    unittest.main()
