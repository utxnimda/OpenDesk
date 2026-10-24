from __future__ import annotations

import unittest

from directory_organizer.strategies import GenericOrganizer, propose_layout


def preview(*paths, overrides=None):
    return propose_layout("Beck_BDRip", [{"relative_path": path} for path in paths], overrides=overrides)


class OthersDiscGuardTest(unittest.TestCase):
    def test_beck_unknown_videos_remain_in_others_and_second_preview_is_stable(self):
        paths = [f"Beck_BDRip_Others/[BECK][{title}][BDRip][1080P][X264_FLAC].mkv"
                 for title in ("MOON ON THE WATER", "SLIP OUT")]
        for plan in (preview(*paths), preview(*[row["target_rel"] for row in preview(*paths)["files"]])):
            self.assertEqual(plan["issues"], [])
            self.assertTrue(all(row["category"] == "Others" and row["source_rel"] == row["target_rel"] for row in plan["files"]))

    def test_others_aliases_keep_arbitrary_unnumbered_video_and_subtitle(self):
        for root in ("Other", "Others", "_Other", "_Others", "Old_BDRip_Others"):
            for name in ("Unidentified.mkv", "Music clip.mp4", "Unknown.ass"):
                with self.subTest(root=root, name=name):
                    row = preview(root + "/" + name)["files"][0]
                    self.assertEqual(row["category"], "Others")
                    self.assertEqual(row["target_rel"], "Beck_BDRip_Others/" + name)

    def test_explicit_resource_filters_still_rescreen_others(self):
        for name, category in (("CM01.mkv", "CM"), ("NCOP01.mkv", "OP+ED"), ("cover.jpg", "Image"),
                               ("Font.ttf", "Fonts"), ("Subtitles.rar", "Subs"), ("Menu01.mkv", "Menu")):
            with self.subTest(name=name):
                row = preview("_Others/" + name)["files"][0]
                self.assertEqual(row["category"], category)
                self.assertEqual(row["target_rel"], "Beck_BDRip_" + category + "/" + name)

    def test_explicit_episodes_in_others_can_become_disc(self):
        paths = ["_Others/[BECK][Live_01].mkv", "_Others/[BECK][Live_01].sc_dmhy.ssa"]
        plan = preview(*paths)
        self.assertEqual(plan["issues"], [])
        self.assertTrue(all(row["category"] == "Disc" and row["target_rel"].startswith("Beck_BDRip_Disc/Beck_BDRip_01/") for row in plan["files"]))

    def test_unknown_nested_resource_folder_remains_atomic(self):
        paths = ["_Others/Unknown/01.mkv", "_Others/Unknown/cover.jpg"]
        plan = preview(*paths)
        self.assertTrue(all(row["category"] == "Others" and row["target_rel"] == "Beck_BDRip_Others/" + row["source_rel"].split("/", 1)[1] for row in plan["files"]))

    def test_root_movie_and_publisher_wrapper_are_not_treated_as_others(self):
        for path in ("Movie.mkv", "SPs/Movie.mkv"):
            with self.subTest(path=path):
                row = preview(path)["files"][0]
                self.assertEqual(row["category"], "Disc")
                self.assertEqual(row["target_rel"], "Beck_BDRip_Disc/Movie.mkv")

    def test_manual_target_and_derived_promotion_hook_remain_authoritative(self):
        path = "_Others/Confirmed Movie.mkv"
        row = preview(path, overrides={"targets": {path: "manual/Movie.mkv"}})["files"][0]
        self.assertEqual(row["target_rel"], "manual/Movie.mkv")

        class ConfirmedMovieOrganizer(GenericOrganizer):
            def can_promote_from_others(self, relative_path, category):
                return True

        plan = ConfirmedMovieOrganizer().propose("Beck_BDRip", [{"relative_path": path}])
        self.assertEqual(plan["files"][0]["category"], "Disc")
        self.assertEqual(plan["files"][0]["target_rel"], "Beck_BDRip_Disc/Confirmed Movie.mkv")


if __name__ == "__main__":
    unittest.main()
