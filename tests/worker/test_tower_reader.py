"""Contract and file-boundary tests; fixtures are synthetic, never personal saves."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from apps.features.tower_assistant.backend import reader


def array(x=201, y=51, z=22):
    return {"c2array": True, "size": [x, y, z],
            "data": [[[0 for _ in range(z)] for _ in range(y)] for _ in range(x)]}


def fixture():
    value = array()
    data = value["data"]
    data[0][0][1] = "szmnqea"
    data[2][1][0] = 7
    def row(slot, layer, values):
        for field, v in enumerate(values):
            data[slot][field][layer] = v
    row(1, 1, [1, 66, 10024, 10011, 0, 2])
    row(2, 1, [2, 67, 10026, 10011, 0, 2, 0, 0, 0, 1])
    row(3, 1, [3, 2, 10020, 10005, 0, 1])
    row(4, 1, [4, 67, 10026, 10015, 0, 1])
    row(1, 19, [300, 1, 80, 2, 6, 50, 17, 55, 42, 40, 42.5, 0, 2])
    row(1, 18, [301, 1, 80, 2, 6, 50, 17, 55, 42, 40, 58])
    row(1, 2, [1, "研究员", 1, 60, 0, 0, 1, 10026, 10012, "研究"])
    row(2, 2, [2, "出征者", 1, 60, 0, 0, 1, 30026, 30012, "研究"])
    row(1, 21, [1, 1, 3])
    data[171][8][6] = 1
    # z13 represents production queues, not a physical resource blocker.
    row(1, 13, [1, 10024, 10008, 78])
    buildings = [[i, f"建筑{i}", "", "功能", "", 0, 0, 0, 1, 1] for i in range(68)]
    buildings[2][8:10] = [2, 2]
    buildings[66][1] = "渡劫台"
    buildings[66][8:10] = [3, 3]
    buildings[67][1] = "置物台"
    tables = {"jz.json": buildings,
              "ss.json": [[0] * 10, [1, "林地", "", "资源", "伐木", 0, 0, 0, 1, 1]],
              "lw.json": [[0] * 3, [1, "灵物", "兽血1"]]}
    return value, tables


class ReaderParsingTests(unittest.TestCase):
    def setUp(self):
        self.value, self.tables = fixture()

    def snapshot(self):
        return reader._normalize(reader._array(self.value, "测试", save=True), self.tables, {})

    def test_recognizes_independent_qi_locations_and_actual_city(self):
        result = self.snapshot()
        self.assertEqual(result["bounds"], {"minX": 13, "maxX": 38, "minY": 1, "maxY": 26})
        self.assertEqual([(i["id"], i["qi"]) for i in result["items"]],
                         [("warehouse:1", 58), ("placed:1", 42.5)])
        platform = next(e for e in result["entities"] if e["kind"] == "platform")
        self.assertEqual(platform["currentQi"], 55.3)
        self.assertFalse(platform["movable"])
        self.assertEqual(sum(e["kind"] == "person" for e in result["entities"]), 1)
        self.assertFalse(any(e["kind"] == "resource" for e in result["entities"]))
        self.assertEqual(result["rules"]["warehouseCapacity"], 200)
        self.assertEqual(result["rules"]["warehouseFreeSlots"], 199)
        self.assertEqual(result["items"][0]["iconKey"], "item:兽血1")
        self.assertEqual(result["items"][0]["qualityName"], "神话")

    def add_character_details(self):
        data = self.value["data"]
        data[1][5][2] = "B6"
        data[1][16][2] = 4
        for field, value in enumerate([30, 1, 1, 2, 0, 6, 35, 1]):
            data[4][field][9] = value
        self.tables["wp.json"] = [[0] * 8, [1, "长弓", "弓弩2", "", "", 0, 1, "弓弩"]]
        self.tables["zy.json"] = [[0] * 6, [1, "碎岩师", "", "", "", "碎岩取矿的矿工。"]]

    def test_person_details_use_appearance_profession_and_bound_equipment(self):
        self.add_character_details()
        person = next(e for e in self.snapshot()["entities"] if e["kind"] == "person")
        self.assertEqual(person["iconKey"], "person:B6")
        self.assertEqual(person["job"], "研究")
        self.assertEqual(person["profession"], "碎岩师")
        self.assertEqual(person["professionDescription"], "碎岩取矿的矿工。")
        self.assertEqual(person["weapon"], {"slot": "武器", "name": "长弓", "type": "弓弩",
                         "templateId": 1, "iconKey": "item:弓弩2", "level": 35,
                         "quality": 6, "qualityName": "神话"})
        self.assertEqual(person["equipment"], [person["weapon"]])
        self.assertNotIn("detailWarnings", person)
        # Character cultivation level is not the equipment level or raw save field 3.
        self.assertNotIn("level", person)

    def test_unknown_optional_details_fall_back_without_fabricating_values(self):
        self.add_character_details()
        self.value["data"][1][6][2] = 99
        self.value["data"][4][7][9] = 2  # Owned by a different person.
        result = self.snapshot()
        person = next(e for e in result["entities"] if e["kind"] == "person")
        self.assertNotIn("profession", person)
        self.assertNotIn("weapon", person)
        self.assertEqual(person["equipment"], [])
        self.assertEqual(len(person["detailWarnings"]), 2)
        platform = next(e for e in result["entities"] if e["kind"] == "platform")
        self.assertEqual(platform["currentQi"], 55.3)

    def test_wrong_equipment_slot_is_not_presented_as_a_weapon(self):
        self.add_character_details()
        self.tables["wp.json"][1][6] = 2
        person = next(e for e in self.snapshot()["entities"] if e["kind"] == "person")
        self.assertNotIn("weapon", person)
        self.assertTrue(person["detailWarnings"])

    def test_rejects_warehouse_template_without_item_identity(self):
        self.value["data"][2][1][18] = 1
        with self.assertRaisesRegex(reader.ReaderError, "仓库灵物编号与模板记录不一致"):
            self.snapshot()

    def test_facility_preserves_parent_range_and_parent_position(self):
        entities = {e["id"]: e for e in self.snapshot()["entities"]}
        self.assertFalse(entities["building:3"]["movable"])
        self.assertTrue(entities["facility:1"]["movable"])
        self.assertEqual(entities["facility:1"]["parentId"], "building:3")
        self.assertEqual(entities["facility:1"]["allowedBounds"],
                         {"minX": 17, "maxX": 24, "minY": 2, "maxY": 9})

    def test_three_by_three_parent_has_symmetric_facility_range(self):
        self.tables["jz.json"][2][8:10] = [3, 3]
        facility = next(e for e in self.snapshot()["entities"] if e["kind"] == "facility")
        self.assertEqual(facility["allowedBounds"], {"minX": 16, "maxX": 24, "minY": 1, "maxY": 9})

    def test_even_footprint_uses_game_anchor(self):
        self.assertEqual(reader._footprint({"x": 20, "y": 5, "width": 2, "height": 2}),
                         {(20, 5), (20, 6), (21, 5), (21, 6)})

    def test_resource_matrix_is_an_actual_blocker(self):
        self.value["data"][73][8][6] = 4
        resource = next(e for e in self.snapshot()["entities"] if e["kind"] == "resource")
        self.assertEqual((resource["x"], resource["y"]), (23, 8))
        self.assertFalse(resource["movable"])

    def test_rejects_broken_item_binding_in_both_directions(self):
        self.value["data"][1][12][19] = 4
        with self.assertRaisesRegex(reader.ReaderError, "绑定不一致"):
            self.snapshot()
        self.value, self.tables = fixture()
        self.value["data"][1][0][19] = 0
        with self.assertRaisesRegex(reader.ReaderError, "不存在的灵物"):
            self.snapshot()

    def test_rejects_missing_facility_parent(self):
        self.value["data"][1][2][21] = 99
        with self.assertRaisesRegex(reader.ReaderError, "所属建筑"):
            self.snapshot()

    def test_rejects_duplicate_facility_map_reference(self):
        self.value["data"][172][8][6] = 1
        with self.assertRaisesRegex(reader.ReaderError, "重复引用"):
            self.snapshot()

    def test_nullable_unused_fields_are_allowed_but_used_values_are_strict(self):
        self.value["data"][1][40][2] = None
        self.snapshot()
        self.value["data"][1][10][18] = None
        with self.assertRaisesRegex(reader.ReaderError, "灵物基础灵气"):
            self.snapshot()

    def test_rejects_invalid_format_and_truncation(self):
        self.value["data"][0][0][1] = "another-game"
        with self.assertRaisesRegex(reader.ReaderError, "格式标识"):
            self.snapshot()
        self.value["data"].pop()
        with self.assertRaisesRegex(reader.ReaderError, "不完整"):
            self.snapshot()


class ReaderFileBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        (self.root / "package.nw").write_bytes(b"test placeholder")
        (self.root / "save").mkdir()
        self.value, self.tables = fixture()
        self.save = self.root / "save" / "ea1save.dat"
        self.save.write_text(json.dumps(self.value), encoding="utf-8")
        self.write_info([1700000000000])

    def tearDown(self):
        self.temp.cleanup()

    def write_info(self, timestamps):
        info = array(len(timestamps) + 1, 8, 1)
        for i, ts in enumerate(timestamps, 1):
            for y, v in enumerate([1, "测试", 7, 30, "空", "47年", "1小时", ts]):
                info["data"][i][y][0] = v
        (self.root / "save" / "saveinfo.dat").write_text(json.dumps(info), encoding="utf-8")

    def test_uses_game_timestamp_not_copy_mtime(self):
        second = self.root / "save" / "ea2save.dat"
        second.write_text(json.dumps(self.value), encoding="utf-8")
        self.write_info([1700000002000, 1700000001000])
        os.utime(second, (1800000000, 1800000000))
        saves = reader.discover_saves(self.root)
        self.assertEqual(saves[0]["id"], "save:ea1save.dat")

    def test_read_only_load_and_fresh_reload(self):
        before = {p.name: sha256(p.read_bytes()).hexdigest() for p in (self.root / "save").iterdir()}
        with patch.object(reader, "_tables", return_value=self.tables):
            result = reader.load_latest(self.root)
        after = {p.name: sha256(p.read_bytes()).hexdigest() for p in (self.root / "save").iterdir()}
        self.assertEqual(before, after)
        self.assertEqual(result["source"]["sha256"], before[self.save.name])
        self.value["data"][1][10][18] = 62
        self.save.write_text(json.dumps(self.value), encoding="utf-8")
        with patch.object(reader, "_tables", return_value=self.tables):
            self.assertEqual(reader.load_latest(self.root)["items"][0]["qi"], 62)

    def test_rejects_path_traversal_and_arbitrary_file_ids_before_read(self):
        for save_id in ["../../file", "save:../package.nw", "save:saveinfo.dat", "C:\\secret", "savedir:ea0save.dat"]:
            with self.subTest(save_id=save_id), self.assertRaisesRegex(reader.ReaderError, "编号无效"):
                reader.load_latest(self.root, save_id)

    def test_rejects_unknown_or_corrupt_package(self):
        with self.assertRaisesRegex(reader.ReaderError, "不完整|不受支持"):
            reader.load_latest(self.root)
        with ZipFile(self.root / "package.nw", "w") as package:
            package.writestr("package.json", json.dumps({"name": "com.ujwan.tfszmnq"}))
            package.writestr("data.json", "{}")
        with self.assertRaisesRegex(reader.ReaderError, "尚未核实"):
            reader.load_latest(self.root)

    def test_rejects_unsupported_size_and_non_finite_json(self):
        with self.assertRaises(reader.ReaderError):
            reader._json(b'{"value":NaN}', "test")
        bad = deepcopy(self.value)
        bad["size"][2] = 21
        with self.assertRaisesRegex(reader.ReaderError, "版本不受支持"):
            reader._array(bad, "test", save=True)

    def test_missing_selected_save_does_not_silently_fall_back(self):
        with self.assertRaisesRegex(reader.ReaderError, "已不存在"):
            reader.load_latest(self.root, "save:ea2save.dat")

    def test_missing_index_uses_visible_file_time_fallback(self):
        (self.root / "save" / "saveinfo.dat").unlink()
        with patch.object(reader, "_tables", return_value=self.tables):
            result = reader.load_latest(self.root)
        self.assertEqual(result["source"]["savedAtSource"], "file")
        self.assertTrue(any("文件修改时间" in warning for warning in result["warnings"]))

    def test_redirected_save_is_rejected(self):
        target = self.root / "unrelated.dat"
        target.write_text("private", encoding="utf-8")
        link = self.root / "save" / "ea2save.dat"
        try:
            link.symlink_to(target)
        except OSError:
            self.skipTest("Windows account cannot create symbolic links")
        with self.assertRaisesRegex(reader.ReaderError, "链接"):
            reader.discover_saves(self.root)


if __name__ == "__main__":
    unittest.main()
