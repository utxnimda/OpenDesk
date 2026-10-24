from __future__ import annotations

import unittest

from directory_organizer.strategies import infer_release_metadata, propose_layout


class OrganizerFormatCaseTest(unittest.TestCase):
    def plan(self, source, **overrides):
        return propose_layout(source, [{"relative_path": name} for name in ("01.mkv", "01.ass", "cover.jpg")], overrides=overrides)

    def test_r2jraw_is_a_format_not_part_of_the_work_title(self):
        metadata = infer_release_metadata("Beck_R2Jraw")
        self.assertEqual(metadata["title"], "Beck")
        self.assertEqual(metadata["press_format"], "R2Jraw")
        self.assertEqual(metadata["issues"], [])
        plan = self.plan("Beck_R2Jraw")
        self.assertEqual(plan["release_name"], "Beck_R2Jraw")
        self.assertEqual([row["target_rel"] for row in plan["files"]], [
            "Beck_R2Jraw_Disc/Beck_R2Jraw_01/01.mkv",
            "Beck_R2Jraw_Disc/Beck_R2Jraw_01/01.ass", "Beck_R2Jraw_Image/cover.jpg"])
        self.assertEqual(plan["issues"], [])

    def test_bracketed_r2jraw_is_technical_metadata_not_a_title_candidate(self):
        for source in ("[R2Jraw][Beck]", "[Beck][R2Jraw]", "[R2JRAW][Beck]"):
            with self.subTest(source=source):
                metadata = infer_release_metadata(source)
                self.assertEqual(metadata["title"], "Beck")
                self.assertEqual(metadata["press_format"], "R2Jraw")
                self.assertEqual(metadata["issues"], [])

    def test_confirmed_r2jraw_and_unknown_formats_are_not_lowercased(self):
        for press_format in ("R2Jraw", "MyRawFormat", "480P", "576p"):
            with self.subTest(press_format=press_format):
                plan = self.plan("Original_BDRip", title="Work", press_format=press_format,
                                 press_group="JsuM", release_name="手动目录")
                prefix = "Work_" + press_format
                self.assertEqual(plan["press_format"], press_format)
                self.assertEqual(plan["press_group"], "JsuM")
                self.assertEqual(plan["files"][0]["target_rel"], prefix + "_Disc/" + prefix + "_01/01.mkv")
                self.assertEqual(plan["files"][2]["target_rel"], prefix + "_Image/cover.jpg")

    def test_target_release_r2jraw_controls_prefix_and_preserves_group_spelling(self):
        plan = self.plan("Original_BDRip", title="DB name", press_format="BDRip", release_name="Renamed_R2Jraw(JsuM)")
        self.assertEqual(plan["release_name"], "Renamed_R2Jraw(JsuM)")
        self.assertEqual(plan["files"][0]["target_rel"], "Renamed_R2Jraw_Disc/Renamed_R2Jraw_01/01.mkv")
        self.assertEqual(infer_release_metadata("Renamed_R2Jraw(JsuM)")["press_group"], "JsuM")

    def test_r2jraw_new_structure_is_stable_on_second_preview(self):
        first = self.plan("Beck_R2Jraw")
        second = propose_layout("Beck_R2Jraw", [{"relative_path": row["target_rel"]} for row in first["files"]])
        self.assertTrue(all(row["source_rel"] == row["target_rel"] for row in second["files"]))
        self.assertEqual(second["issues"], [])

    def test_configured_resolution_suffixes_preserve_their_case(self):
        for press_format in ("720p", "1080p", "2160p", "576p", "480P", "456p", "432p", "384p", "360p"):
            with self.subTest(press_format=press_format):
                plan = self.plan("Work_" + press_format)
                self.assertEqual(plan["title"], "Work")
                self.assertEqual(plan["press_format"], press_format)
                self.assertEqual(plan["files"][2]["target_rel"], "Work_" + press_format + "_Image/cover.jpg")


if __name__ == "__main__":
    unittest.main()
