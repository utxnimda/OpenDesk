from __future__ import annotations

from collections import Counter
import unittest

from directory_organizer.strategies import propose_layout


def files(*paths):
    return [{"relative_path": path, "size": 1024} for path in paths]


def destinations(plan):
    return {item["source_rel"]: item["target_rel"] for item in plan["files"]}


class DiscEpisodeCompanionTests(unittest.TestCase):
    def assert_idempotent(self, release, plan):
        repeated = propose_layout(release, files(*destinations(plan).values()))
        self.assertEqual(repeated["issues"], [])
        self.assertTrue(all(source == target for source, target in destinations(repeated).items()))

    def test_beck_live_episodes_with_group_suffix_subtitles_get_26_episode_folders(self):
        paths = []
        expected = {}
        for episode in range(1, 27):
            stem = f"[BECK][Live_{episode:02d}]"
            video = stem + ("[v2]" if episode == 5 else "") + "[BDRip][1080P][X264_FLAC].mkv"
            subtitle = stem + "[BDRip][1080P][X264_FLAC].sc_dmhy.ssa"
            for name in (video, subtitle):
                path = "Beck_BDRip_Disc/" + name
                paths.append(path)
                expected[path] = f"Beck_BDRip_Disc/Beck_BDRip_{episode:02d}/{name}"
        plan = propose_layout("Beck_BDRip", files(*paths))
        self.assertEqual(plan["issues"], [])
        self.assertEqual(destinations(plan), expected)
        self.assertEqual(len(plan["files"]), 52)
        self.assertEqual(Counter(path.rsplit("/", 1)[0] for path in expected.values()),
                         {f"Beck_BDRip_Disc/Beck_BDRip_{episode:02d}": 2 for episode in range(1, 27)})
        self.assert_idempotent("Beck_BDRip", plan)

    def test_disc_root_unpacked_subtitles_and_external_audio_follow_episode(self):
        paths = ["Work_BDRip_Disc/[Work][01][BDRip].mkv",
                 "Work_BDRip_Disc/[Work][01].sc_group.ssa",
                 "Work_BDRip_Disc/[Work][01].tc_group.ass",
                 "Work_BDRip_Disc/[Work][01].JPN.flac",
                 "Work_BDRip_Disc/01.eng.mka"]
        plan = propose_layout("Work_BDRip", files(*paths))
        self.assertEqual(plan["issues"], [])
        self.assertEqual(destinations(plan), {
            path: "Work_BDRip_Disc/Work_BDRip_01/" + path.rsplit("/", 1)[-1] for path in paths})
        self.assertTrue(all(row["category"] == "Disc" for row in plan["files"]))
        self.assert_idempotent("Work_BDRip", plan)

    def test_external_audio_alone_is_enough_to_require_an_episode_folder(self):
        for root in ("", "Disc/", "Work_BDRip_Disc/"):
            with self.subTest(root=root):
                paths = [root + "Work [01].mkv", root + "Work [01].JPN.flac"]
                plan = propose_layout("Work_BDRip(VCB)", files(*paths))
                self.assertEqual(plan["issues"], [])
                self.assertEqual(destinations(plan), {
                    path: "Work_BDRip_Disc/Work_BDRip_01/" + path.rsplit("/", 1)[-1] for path in paths})

    def test_new_episode_prefix_comes_from_target_release_without_group(self):
        plan = propose_layout("Original_BDRip(VCB)", files("01.mkv", "01.ass"),
                              overrides={"release_name": "Renamed_DVDRip(Jsum)", "title": "Database alias"})
        self.assertEqual(destinations(plan), {
            path: "Renamed_DVDRip_Disc/Renamed_DVDRip_01/" + path for path in ("01.mkv", "01.ass")})

    def test_nonstandard_release_uses_confirmed_metadata_for_episode_prefix(self):
        plan = propose_layout("Original_BDRip(VCB)", files("01.mkv", "01.ass"),
                              overrides={"release_name": "Chosen folder", "title": "Confirmed title", "press_format": "dvd"})
        self.assertEqual(destinations(plan), {
            path: "Confirmed title_DVDRip_Disc/Confirmed title_DVDRip_01/" + path for path in ("01.mkv", "01.ass")})

    def test_r2jraw_episode_names_preserve_the_canonical_format_case(self):
        plan = propose_layout("作品_R2Jraw", files("01.mkv", "01.ass"))
        self.assertEqual(plan["issues"], [])
        self.assertEqual(destinations(plan), {
            path: "作品_R2Jraw_Disc/作品_R2Jraw_01/" + path for path in ("01.mkv", "01.ass")})

    def test_episode_width_is_based_on_maximum_video_episode_not_input_padding(self):
        for maximum, width in ((1, 2), (26, 2), (99, 2), (100, 3), (999, 3),
                               (1000, 4), (9999, 4), (10000, 5)):
            with self.subTest(maximum=maximum):
                paths = ["Show [00001].mkv", "Show [1].ass"]
                if maximum != 1:
                    paths += [f"Show [{maximum}].mkv", f"Show [{maximum}].ass"]
                plan = propose_layout("Show_BDRip", files(*paths))
                self.assertEqual(plan["issues"], [])
                self.assertEqual(destinations(plan)[paths[0]], f"Show_BDRip_Disc/Show_BDRip_{1:0{width}d}/{paths[0]}")
                self.assertEqual(destinations(plan)[paths[1]], f"Show_BDRip_Disc/Show_BDRip_{1:0{width}d}/{paths[1]}")
                if maximum != 1:
                    self.assertEqual(destinations(plan)[paths[2]], f"Show_BDRip_Disc/Show_BDRip_{maximum:0{width}d}/{paths[2]}")
                self.assert_idempotent("Show_BDRip", plan)

    def test_unaccompanied_later_episode_still_determines_padding(self):
        paths = ["Show [1].mkv", "Show [1].ass", "Show [1000].mkv"]
        plan = propose_layout("Show_BDRip", files(*paths))
        self.assertEqual(destinations(plan), {
            paths[0]: "Show_BDRip_Disc/Show_BDRip_0001/" + paths[0],
            paths[1]: "Show_BDRip_Disc/Show_BDRip_0001/" + paths[1],
            paths[2]: "Show_BDRip_Disc/" + paths[2],
        })

    def test_unmatched_subtitle_number_does_not_change_video_episode_padding(self):
        paths = ["Show [1].mkv", "Show [1].ass", "Show [10000].ass"]
        plan = propose_layout("Show_BDRip", files(*paths))
        self.assertEqual(destinations(plan)[paths[0]], "Show_BDRip_Disc/Show_BDRip_01/" + paths[0])
        self.assertEqual(destinations(plan)[paths[2]], "Show_BDRip_Disc/" + paths[2])

    def test_only_single_video_per_episode_remains_flat_under_disc(self):
        paths = [f"Show_BDRip_Disc/Show [{episode:03d}].mkv" for episode in range(1, 101)]
        plan = propose_layout("Show_BDRip", files(*paths))
        self.assertEqual(plan["issues"], [])
        self.assertEqual(destinations(plan), {path: path for path in paths})

    def test_subtitle_only_disc_does_not_create_episode_directories(self):
        paths = ["Work_BDRip_Disc/01.ass", "Work_BDRip_Disc/01.srt", "Work_BDRip_Disc/02.ass"]
        plan = propose_layout("Work_BDRip", files(*paths))
        self.assertEqual(plan["issues"], [])
        self.assertEqual(destinations(plan), {path: path for path in paths})

    def test_existing_episode_and_resource_directories_are_reused_unchanged(self):
        for old_root, episode_dir in (("_Disc", "_01"), ("Disc", "01"),
                                      ("Old_BDRip_Disc", "Old_01"), ("Old_BDRip_Disc", "Old_BDRip_01"),
                                      ("Work_BDRip_Disc", "Work_BDRip_01"), ("Disc", "version-a")):
            with self.subTest(old_root=old_root, episode_dir=episode_dir):
                parent = old_root + "/" + episode_dir
                paths = [parent + "/Work [01].mkv", parent + "/Work [01].ass",
                         "Work [01].JPN.flac", "Work [01].sc_group.ssa"]
                plan = propose_layout("Work_BDRip", files(*paths))
                self.assertEqual(plan["issues"], [])
                target_parent = "Work_BDRip_Disc/" + episode_dir + "/"
                self.assertEqual(destinations(plan), {
                    path: target_parent + path.rsplit("/", 1)[-1] for path in paths})
                self.assert_idempotent("Work_BDRip", plan)

    def test_manual_targets_take_precedence_over_automatic_episode_directories(self):
        paths = ["Disc/Work [01].mkv", "Disc/Work [01].ass", "Disc/Work [01].mka"]
        manual = {paths[0]: "Disc/Chosen video.mkv", paths[1]: "Manual/Chosen subtitles.ass"}
        plan = propose_layout("Work_BDRip", files(*paths), overrides={"targets": manual})
        self.assertEqual(plan["issues"], [])
        self.assertEqual(destinations(plan), {**manual, paths[2]: "Disc/Work [01].mka"})

    def test_multiple_existing_episode_parents_require_a_companion_target_choice(self):
        paths = ["Disc/version-a/Work [01].mkv", "Disc/version-b/Work [01].mkv",
                 "Disc/Work [01].sc_group.ssa", "Disc/Work [01].mka"]
        plan = propose_layout("Work_BDRip", files(*paths))
        self.assertEqual({issue["code"] for issue in plan["issues"]},
                         {"subtitle-video-ambiguous", "audio-video-ambiguous"})
        self.assertTrue(all(issue["blocking"] for issue in plan["issues"]))
        for path in paths[2:]:
            self.assertEqual(destinations(plan)[path], path)
        choices = {path: "Work_BDRip_Disc/version-b/" + path.rsplit("/", 1)[-1] for path in paths[2:]}
        fixed = propose_layout("Work_BDRip", files(*paths), overrides={"targets": choices})
        self.assertEqual(fixed["issues"], [])
        self.assertEqual({path: destinations(fixed)[path] for path in choices}, choices)

    def test_companions_prefer_same_parent_video_over_nested_alternative_versions(self):
        paths = ["Disc/Work [01].mkv", "Disc/version-a/Work [01].mkv",
                 "Disc/Work [01].sc_group.ssa", "Disc/Work [01].JPN.flac"]
        plan = propose_layout("Work_BDRip", files(*paths))
        self.assertEqual(plan["issues"], [])
        self.assertEqual(destinations(plan), {
            paths[0]: "Work_BDRip_Disc/Work_BDRip_01/Work [01].mkv",
            paths[1]: "Work_BDRip_Disc/version-a/Work [01].mkv",
            paths[2]: "Work_BDRip_Disc/Work_BDRip_01/Work [01].sc_group.ssa",
            paths[3]: "Work_BDRip_Disc/Work_BDRip_01/Work [01].JPN.flac",
        })
        self.assert_idempotent("Work_BDRip", plan)

    def test_release_years_dates_and_technical_numbers_are_not_episode_numbers(self):
        for name in ("Work 2004", "Work 20201001", "Work [2004]", "Work [20201001]",
                     "Work [BDRip][1080P][X264_FLAC]", "Work E1080p", "Work S01E1080p"):
            with self.subTest(name=name):
                paths = [name + ".mkv", name + ".ass"]
                plan = propose_layout("Work_BDRip", files(*paths))
                self.assertEqual(plan["issues"], [])
                self.assertEqual(destinations(plan), {path: "Work_BDRip_Disc/" + path for path in paths})

    def test_explicit_episode_markers_allow_numbers_that_look_like_years(self):
        for name in ("Work EP2011", "Work [Live_2011]"):
            with self.subTest(name=name):
                paths = [name + ".mkv", name + ".ass", "Work EP1.mkv", "Work EP1.ass"]
                plan = propose_layout("Work_BDRip", files(*paths))
                self.assertEqual(plan["issues"], [])
                self.assertEqual(destinations(plan)[paths[0]], "Work_BDRip_Disc/Work_BDRip_2011/" + paths[0])
                self.assertEqual(destinations(plan)[paths[2]], "Work_BDRip_Disc/Work_BDRip_0001/" + paths[2])

    def test_explicit_episode_version_suffixes_support_four_digit_numbers(self):
        for marker in ("E1000v2", "S01E1000v2"):
            with self.subTest(marker=marker):
                name = "Work " + marker
                paths = [name + ".mkv", name + ".ass"]
                plan = propose_layout("Work_BDRip", files(*paths))
                episode = "S01E1000" if marker.startswith("S") else "1000"
                self.assertEqual(plan["issues"], [])
                self.assertEqual(destinations(plan), {
                    path: "Work_BDRip_Disc/Work_BDRip_" + episode + "/" + path for path in paths})

    def test_season_episode_padding_is_consistent_without_padding_the_season_number(self):
        paths = ["Work S01E01.mkv", "Work S01E01.ass", "Work S02E100.mkv", "Work S02E100.ass"]
        plan = propose_layout("Work_BDRip", files(*paths))
        self.assertEqual(plan["issues"], [])
        self.assertEqual(destinations(plan), {
            path: "Work_BDRip_Disc/Work_BDRip_" + ("S01E001" if "S01" in path else "S02E100") + "/" + path
            for path in paths})
        self.assert_idempotent("Work_BDRip", plan)

    def test_preserved_episode_resource_does_not_claim_a_new_episode_folder_in_reason(self):
        paths = ["Work_BDRip_Disc/_01/Work [01].mkv", "Work_BDRip_Disc/_01/Work [01].ass"]
        plan = propose_layout("Work_BDRip", files(*paths))
        self.assertEqual(destinations(plan), {path: path for path in paths})
        for row in plan["files"]:
            self.assertNotIn("归入 Work_BDRip_01", row["reason"])

    def test_season_specific_companions_never_merge_same_episode_numbers(self):
        paths = [f"Disc/Work {season}E01.{extension}" for season in ("S01", "S02")
                 for extension in ("mkv", "sc_group.ssa", "JPN.flac")]
        plan = propose_layout("Work_BDRip", files(*paths))
        self.assertEqual(plan["issues"], [])
        for season in ("S01", "S02"):
            selected = {source: target for source, target in destinations(plan).items() if season in source}
            self.assertEqual(len(selected), 3)
            self.assertTrue(all(target.startswith(f"Work_BDRip_Disc/Work_BDRip_{season}E01/")
                                for target in selected.values()))
        self.assert_idempotent("Work_BDRip", plan)


if __name__ == "__main__":
    unittest.main()
