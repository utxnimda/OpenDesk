"""Recording semantics: full output, truncation, bounded detail, and lost events."""
import json
import io
import unittest
from unittest.mock import patch

from apps.features.tower_assistant.backend import output_statistics
from apps.features.tower_assistant.backend.output_statistics import OutputStatistics, StatisticsError
from opendesk_worker.protocol import MAX_FRAME_BYTES, MAX_RESPONSE_BYTES, parse_request, read_frames, write_response


def event(sequence, amount=1_000_000, effective=10_000, **values):
    result = {"schemaVersion": 1, "sequence": sequence, "kind": "work", "personId": "person:1",
              "personName": "测试人物", "category": "锻造", "amount": amount, **values}
    if effective is not None:
        result["effectiveAmount"] = effective
    return result


class OutputTests(unittest.TestCase):
    def setUp(self):
        self.now = 20.0
        self.stats = OutputStatistics(clock=lambda: self.now, detail_limit=2)
        self.stats.start("test-stream", 100)

    def test_full_work_counts_even_when_task_needs_only_one_percent(self):
        self.now = 25.0
        self.stats.accept("test-stream", [event(101)])
        row = self.stats.snapshot()["totals"][0]
        self.assertEqual(row["amount"], 1_000_000)
        self.assertEqual(row["effectiveAmount"], 10_000)
        self.assertEqual(row["overflowAmount"], 990_000)
        self.assertEqual(row["perRealSecond"], 200_000)

    def test_work_damage_and_work_categories_do_not_compete_for_totals(self):
        self.stats.accept("test-stream", [event(101), event(102, category="采矿"), event(103, kind="damage", category="普通攻击")])
        snap = self.stats.snapshot()
        self.assertEqual(len(snap["totals"]), 3)
        self.assertEqual(snap["eventCount"], 3)
        self.assertEqual(snap["retainedEventCount"], 2)
        self.assertEqual(sum(r["amount"] for r in snap["totals"]), 3_000_000)

    def test_missing_effective_measurement_is_unknown_not_zero_overflow(self):
        self.stats.accept("test-stream", [event(101, effective=None)])
        row = self.stats.snapshot()["totals"][0]
        self.assertFalse(row["effectiveComplete"])
        self.assertIsNone(row["overflowAmount"])

    def test_sequence_gap_interrupts_without_accepting_partial_batch(self):
        self.stats.accept("test-stream", [event(101)])
        self.now = 26.0
        with self.assertRaises(StatisticsError):
            self.stats.accept("test-stream", [event(102), event(104)])
        self.now = 40.0
        snap = self.stats.snapshot()
        self.assertEqual(snap["state"], "interrupted")
        self.assertEqual(snap["eventCount"], 1)
        self.assertEqual(snap["elapsedRealSeconds"], 6.0)

    def test_stop_freezes_time_and_new_start_does_not_include_history(self):
        self.stats.accept("test-stream", [event(101)])
        self.now = 24.0
        self.stats.stop()
        self.now = 200.0
        self.assertEqual(self.stats.snapshot()["elapsedRealSeconds"], 4.0)
        self.stats.start("new-stream", 20)
        self.stats.accept("new-stream", [event(21, 5, 5)])
        self.assertEqual(self.stats.snapshot()["totals"][0]["amount"], 5)

    def test_changed_stream_marks_interruption(self):
        with self.assertRaises(StatisticsError):
            self.stats.accept("reloaded-game", [event(101)])
        self.assertEqual(self.stats.snapshot()["state"], "interrupted")

    def test_invalid_values_cannot_poison_totals(self):
        for invalid in [event(101, float("nan")), event(101, -1), event(101, True),
                        event(101, 10**400), event(101, effective=10**400), event(101, gameTime=10**400),
                        event(101, 2, 3), event(101, personName="bad\nname"), event(101, extra=True)]:
            with self.subTest(invalid=invalid):
                stats = OutputStatistics()
                stats.start("test", 100)
                with self.assertRaises(StatisticsError):
                    stats.accept("test", [invalid])
                self.assertEqual(stats.snapshot()["eventCount"], 0)
                self.assertEqual(stats.snapshot()["state"], "interrupted")

    def test_people_pivot_preserves_work_output_counts_and_stable_row_order(self):
        self.now = 25
        self.stats.accept("test-stream", [event(101, category="锻造", personId="b"),
            event(102, amount=300, effective=None, category="锻造", personId="b"),
            event(103, amount=40, effective=None, category="采矿", personId="a")])
        snap = self.stats.snapshot()
        self.assertEqual([p["personId"] for p in snap["people"]], ["a", "b"])
        self.assertEqual(snap["workCategories"], ["采矿", "锻造"])
        work = snap["people"][1]["work"][0]
        self.assertEqual((work["amount"], work["count"], work["latestAmount"]), (1000300, 2, 300))
        self.assertEqual(work["latestSequence"], 102)
        self.assertEqual(snap["summary"]["work"], {"amount": 1000340, "count": 3})
        self.assertEqual(snap["people"][1]["damage"]["amount"], 0)

    def test_damage_type_and_attack_breakdowns_keep_unknown_legacy_hits_honest(self):
        self.now = 25
        self.stats.accept("test-stream", [
            event(101, 100, None, kind="damage", attackKind="basic", damageType="physical", skillId="1", skillName="普攻"),
            event(102, 300, None, kind="damage", attackKind="skill", damageType="magic", skillId="2", skillName="火球"),
            event(103, 200, None, kind="damage", attackKind="skill", damageType="magic", skillId="2", skillName="火球"),
            event(104, 50, None, kind="damage")])
        snap = self.stats.snapshot()
        damage = snap["people"][0]["damage"]
        self.assertEqual((damage["amount"], damage["perRealSecond"]), (650, 130))
        self.assertEqual({row["damageType"]: row["amount"] for row in damage["types"]},
                         {"physical": 100, "magic": 500, "unknown": 50})
        skill = next(row for row in damage["attacks"] if row["attackKind"] == "skill")
        self.assertEqual((skill["count"], skill["averageAmount"], skill["latestAmount"]), (2, 250, 200))
        self.assertIsNone(skill["actionCount"])
        self.assertIsNone(skill["averagePerAction"])

    def test_multitarget_action_ids_count_once_and_bounded_dedup_never_overcounts(self):
        with patch.object(output_statistics, "MAX_ACTION_IDS", 2):
            self.stats.accept("test-stream", [event(101, 100, None, kind="damage", attackKind="skill", actionId="cast1"),
                event(102, 100, None, kind="damage", attackKind="skill", actionId="cast1"),
                event(103, 100, None, kind="damage", attackKind="skill", actionId="cast2")])
            metric = self.stats.snapshot()["people"][0]["damage"]["attacks"][0]
            self.assertEqual((metric["count"], metric["actionCount"], metric["averagePerAction"]), (3, 2, 150))
            self.stats.accept("test-stream", [event(104, 100, None, kind="damage", attackKind="skill", actionId="cast3")])
            metric = self.stats.snapshot()["people"][0]["damage"]["attacks"][0]
            self.assertEqual(metric["count"], 4)
            self.assertIsNone(metric["actionCount"])
            self.assertLessEqual(self.stats._action_id_count, 2)

    def test_latest_person_metadata_is_separate_from_event_time_details(self):
        self.stats.accept("test-stream", [event(101, person={"profession": "旧职业"}),
            event(102, person={"profession": "新职业"})])
        snap = self.stats.snapshot()
        self.assertEqual(snap["people"][0]["person"]["profession"], "新职业")
        self.assertEqual(snap["people"][0]["personSequence"], 102)
        self.assertNotIn("person", snap["events"][0])
        self.assertEqual(self.stats.explain(101)["event"]["person"]["profession"], "旧职业")
        snap["people"][0]["person"]["profession"] = "mutated copy"
        self.assertEqual(self.stats.snapshot()["people"][0]["person"]["profession"], "新职业")

    def test_signed_displayed_work_and_damage_survive_eviction_and_new_interval(self):
        stats = OutputStatistics()
        stats.start("stream", 0)
        original = [event(1, 1000, None, person={"profession": "原工作职业"}, workFormula={
            "expression": 5622, "inputs": [1, 1000, 1, 0, 0, 0, 1, 1, 1, 1], "flatBonus": 0, "result": 1000}),
            event(2, 1000, None, kind="damage", category="攻击", attackKind="skill", skillId="one",
                  person={"profession": "原战斗职业"}, formulaCoverage="main-product", formula={
                      "expression": 5584, "inputs": [1000, 1, 1, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1], "result": 1000})]
        stats.accept("stream", original)
        displayed = json.loads(json.dumps(stats.snapshot()))  # Real JSON crossing preserves the opaque payload string.
        stats.accept("stream", [{**original[index % 2], "sequence": index, "person": {"profession": "后续职业"}}
                                for index in range(3, 603)])
        for observed in displayed["events"]:
            sequence = observed["sequence"]
            with self.assertRaisesRegex(StatisticsError, "分类最近快照"):
                stats.explain(sequence, displayed["sessionId"])
            receipt = observed["detailSnapshot"]
            result = stats.explain_snapshot(sequence, displayed["sessionId"], receipt)
            self.assertEqual(result["event"]["sequence"], sequence)
            self.assertEqual(result["event"]["person"], original[sequence - 1]["person"])
            self.assertEqual(result["event"]["detailSnapshot"], receipt)
            self.assertTrue((result["workFormula"] or result["formula"])["matches"])
            result["event"]["person"]["profession"] = "UI mutation"
            self.assertEqual(stats.explain_snapshot(sequence, displayed["sessionId"], receipt)["event"]["person"], original[sequence - 1]["person"])
        self.assertEqual(len(stats._events), 500)
        self.assertEqual(len(stats._latest_events), 2)
        stats.stop()
        stats.start("next-stream", 0)
        stats.accept("next-stream", [event(1, person={"profession": "新会话"})])
        old = stats.explain_snapshot(1, displayed["sessionId"], displayed["events"][0]["detailSnapshot"])
        self.assertEqual(old["sessionId"], displayed["sessionId"])
        self.assertEqual(old["event"]["person"]["profession"], "原工作职业")
        with self.assertRaisesRegex(StatisticsError, "计时会话已改变"):
            stats.explain(1, displayed["sessionId"])
        self.assertEqual(stats.snapshot()["eventCount"], 1)

    def test_snapshot_rejects_tampering_identity_and_other_authority_before_parsing(self):
        self.stats.accept("test-stream", [event(101)])
        display = self.stats.snapshot()
        receipt = display["events"][0]["detailSnapshot"]
        session = display["sessionId"]
        for bad in ({**receipt, "payload": receipt["payload"].replace('1000000.0', '2000000.0')},
                    {**receipt, "payload": "{invalid json"}, {**receipt, "signature": "0" * 64}):
            with self.subTest(bad=bad), patch.object(output_statistics.json, "loads") as parse:
                with self.assertRaisesRegex(StatisticsError, "签名"):
                    self.stats.explain_snapshot(101, session, bad)
                parse.assert_not_called()
        for sequence, selected_session in ((102, session), (101, "other-session")):
            with self.assertRaisesRegex(StatisticsError, "会话或序号不一致"):
                self.stats.explain_snapshot(sequence, selected_session, receipt)
        with self.assertRaisesRegex(StatisticsError, "签名"):
            OutputStatistics().explain_snapshot(101, session, receipt)
        self.assertEqual(self.stats.explain_snapshot(101, session, receipt)["event"]["amount"], 1_000_000)

    def test_snapshot_shape_and_byte_limits_are_strict(self):
        self.stats.accept("test-stream", [event(101)])
        receipt = self.stats.snapshot()["events"][0]["detailSnapshot"]
        invalid = [None, [], {}, {**receipt, "extra": True}, {**receipt, "schemaVersion": True},
                   {**receipt, "schemaVersion": 2}, {**receipt, "payload": []}, {**receipt, "payload": ""},
                   {**receipt, "payload": "\ud800"}, {**receipt, "payload": "界" * (output_statistics.MAX_SNAPSHOT_PAYLOAD_BYTES // 3 + 1)},
                   {**receipt, "signature": receipt["signature"].upper()}, {**receipt, "signature": "g" * 64},
                   {**receipt, "signature": "0" * 63}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(StatisticsError):
                output_statistics.validate_detail_snapshot(value)

    def test_receipt_for_rich_exact_event_fits_request_and_explanation_transport(self):
        person = {"profession": "原职业", "equipment": [{"slot": str(i), "name": '装"备\\' * 20,
                  "type": "装饰", "iconKey": "item:1", "qualityName": "极品"} for i in range(6)]}
        self.stats.accept("test-stream", [event(101, 1e100, None, person=person, personName="名" * 100)])
        display = self.stats.snapshot()
        receipt = display["events"][0]["detailSnapshot"]
        request = {"protocolVersion": 1, "id": "snapshot", "method": "tower.statistics.explain", "params": {
            "gameDir": "E:/" + "目" * 4090, "sequence": 101, "sessionId": display["sessionId"], "snapshot": receipt}}
        encoded = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.assertLess(len(encoded), MAX_FRAME_BYTES)
        frames = list(read_frames(io.BytesIO(encoded + b"\n")))
        parsed = parse_request(frames[0])
        result = self.stats.explain_snapshot(parsed.params["sequence"], parsed.params["sessionId"], parsed.params["snapshot"])
        output = io.BytesIO()
        write_response(output, {"protocolVersion": 1, "id": "snapshot", "result": result})
        self.assertLess(len(output.getvalue()), MAX_RESPONSE_BYTES)
        self.assertEqual(result["event"]["person"], person)
        self.assertEqual(result["event"]["detailSnapshot"], receipt)

    def test_detail_byte_budget_evicts_old_details_but_keeps_complete_totals(self):
        stats = OutputStatistics()
        stats.start("stream", 0)
        with patch.object(output_statistics, "MAX_DETAIL_BYTES", 1000):
            stats.accept("stream", [event(index, personName="人" * 100) for index in range(1, 21)])
        snap = stats.snapshot()
        self.assertLess(snap["retainedEventCount"], 20)
        self.assertEqual(snap["eventCount"], 20)
        self.assertEqual(snap["summary"]["work"]["amount"], 20_000_000)
        self.assertEqual(snap["events"][-1]["sequence"], 20)

    def test_person_budget_failure_rejects_the_entire_batch_and_keeps_prior_totals(self):
        self.stats.accept("test-stream", [event(101)])
        with patch.object(output_statistics, "MAX_PEOPLE_BYTES", 20), self.assertRaisesRegex(StatisticsError, "人物详情总量"):
            self.stats.accept("test-stream", [event(102, personId="b", person={"profession": "职业" * 30})])
        self.assertEqual(self.stats.state, "interrupted")
        self.assertEqual(self.stats.last_sequence, 101)
        self.assertEqual(self.stats.snapshot()["eventCount"], 1)

    def test_bad_metadata_does_not_silently_create_false_classification(self):
        bad = [event(101, damageType="physical"), event(101, kind="damage", damageType="holy"),
               event(101, person={"profession": ""}), event(101, person={"iconKey": "../../private"}),
               event(101, person={"attributes": [{"id": "attack", "label": "攻击", "value": float("inf")}]})]
        for row in bad:
            stats = OutputStatistics()
            stats.start("stream", 100)
            with self.assertRaises(StatisticsError):
                stats.accept("stream", [row])
            self.assertEqual(stats.snapshot()["eventCount"], 0)

    def test_latest_work_detail_survives_many_damage_hits_but_is_replaced_by_new_work(self):
        self.stats.accept("test-stream", [event(101, category="锻造")])
        self.stats.accept("test-stream", [event(index, 100, None, kind="damage", category="普攻") for index in range(102, 702)])
        self.assertNotIn(101, [row["sequence"] for row in self.stats.snapshot()["events"]])
        self.assertEqual(self.stats.explain(101)["event"]["amount"], 1_000_000)
        self.stats.accept("test-stream", [event(702, category="锻造")])
        with self.assertRaisesRegex(StatisticsError, "分类最近快照"):
            self.stats.explain(101)
        self.assertEqual(self.stats.explain(702)["event"]["kind"], "work")
        self.assertEqual(len(self.stats._latest_events), 2)
        self.stats.stop()
        self.assertEqual(self.stats.explain(702)["event"]["sequence"], 702)
        self.stats.start("new-stream", 0)
        self.assertFalse(self.stats._latest_events)

    def test_latest_work_resolves_status_click_race_without_replacing_exact_history(self):
        stats = OutputStatistics()
        stats.start("stream", 0)
        def work(sequence, amount, profession):
            return event(sequence, amount, None, person={"profession": profession}, workFormula={
                "expression": 5617, "inputs": [1, amount, 1, 0, 0, 0, 1, 1, 1, 1],
                "flatBonus": 0, "result": amount})
        stats.accept("stream", [work(1, 100, "旧职业")])
        displayed = stats.snapshot()
        sequence = displayed["people"][0]["work"][0]["latestSequence"]
        session = displayed["sessionId"]
        stats.accept("stream", [event(index, 1, None, kind="damage", category="普攻") for index in range(2, 602)])
        stats.accept("stream", [work(602, 250, "新职业")])
        with self.assertRaisesRegex(StatisticsError, "分类最近快照"):
            stats.explain(sequence, session)
        selector = {"kind": "work", "personId": "person:1", "category": "锻造"}
        result = stats.explain_latest(selector, session)
        self.assertEqual(result["selection"], {"mode": "latest", **selector})
        self.assertEqual((result["sequence"], result["event"]["amount"]), (602, 250))
        self.assertEqual(result["event"]["person"]["profession"], "新职业")
        self.assertTrue(result["workFormula"]["matches"])
        result["event"]["person"]["profession"] = "前端改动"
        result["event"]["workFormula"]["inputs"][1] = 0
        result["selection"]["category"] = "采矿"
        retained = stats.explain_latest(selector, session)
        self.assertEqual(retained["event"]["person"]["profession"], "新职业")
        self.assertTrue(retained["workFormula"]["matches"])
        self.assertEqual(len(stats._latest_events), 2)
        self.assertEqual(stats.snapshot()["eventCount"], 602)
        stats.stop()
        self.assertEqual(stats.explain_latest(selector, session)["sequence"], 602)
        stats.start("new-stream", 0)
        for stale in (session, "", None):
            with self.subTest(session=stale), self.assertRaisesRegex(StatisticsError, "计时会话"):
                stats.explain_latest(selector, stale)

    def test_latest_damage_selector_keeps_person_attack_kind_and_skill_identity(self):
        def hit(sequence, person="person:1", kind="periodic", skill="one"):
            return event(sequence, sequence, None, kind="damage", personId=person,
                         category="同名技能", attackKind=kind, skillId=skill)
        self.stats.accept("test-stream", [hit(101), hit(102, skill="two"), hit(103, person="other"), hit(104, kind="skill")])
        session = self.stats.snapshot()["sessionId"]
        self.stats.accept("test-stream", [hit(index, person="noise") for index in range(105, 705)])
        self.stats.accept("test-stream", [hit(705)])
        for person, kind, skill, expected in (("person:1", "periodic", "one", 705), ("person:1", "periodic", "two", 102),
                                             ("other", "periodic", "one", 103), ("person:1", "skill", "one", 104)):
            selector = {"kind": "damage", "personId": person, "attackKind": kind, "skillId": skill}
            result = self.stats.explain_latest(selector, session)
            self.assertEqual(result["sequence"], expected)
            self.assertEqual(result["selection"], {"mode": "latest", **selector})
        with self.assertRaisesRegex(StatisticsError, "分类最近快照"):
            self.stats.explain(101, session)
        with self.assertRaisesRegex(StatisticsError, "不会替换"):
            self.stats.explain_latest({"kind": "damage", "personId": "person:1", "attackKind": "unknown", "skillId": None}, session)
        self.stats.accept("test-stream", [event(706, 12, None, kind="damage", category="旧事件")])
        result = self.stats.explain_latest({"kind": "damage", "personId": "person:1", "attackKind": "unknown", "skillId": None}, session)
        self.assertEqual(result["sequence"], 706)
        self.assertIsNone(result["formula"])

    def test_latest_lookup_remains_bounded_and_uses_only_the_true_latest_rolling_event(self):
        selector = {"kind": "work", "personId": "person:1", "category": "锻造"}
        session = self.stats.snapshot()["sessionId"]
        with patch.object(output_statistics, "MAX_LATEST_DETAIL_BYTES", 1):
            self.stats.accept("test-stream", [event(101), event(102)])
            self.assertFalse(self.stats._latest_events)
            self.assertEqual(self.stats.explain_latest(selector, session)["sequence"], 102)
            self.stats.accept("test-stream", [event(103, category="采矿"), event(104, personId="other")])
        with self.assertRaisesRegex(StatisticsError, "超出保留容量"):
            self.stats.explain_latest(selector, session)
        self.assertEqual(self.stats.snapshot()["eventCount"], 4)
        self.assertFalse(self.stats._latest_events)

    def test_latest_detail_cache_has_its_own_byte_limit_without_reducing_totals(self):
        with patch.object(output_statistics, "MAX_LATEST_DETAIL_BYTES", 1000):
            self.stats.accept("test-stream", [event(index, category=f"工作{index}", personName="人" * 100) for index in range(101, 121)])
        self.assertLessEqual(self.stats._latest_detail_bytes, 1000)
        self.assertLess(len(self.stats._latest_events), 20)
        self.assertEqual(self.stats.snapshot()["eventCount"], 20)
        self.assertEqual(self.stats.snapshot()["summary"]["work"]["amount"], 20_000_000)

    def _assert_latest_attack_identity(self, attack_kind, category):
        self.stats.accept("test-stream", [
            event(101, 100, None, kind="damage", category=category, attackKind=attack_kind, skillId="1", skillName="同名技能"),
            event(102, 200, None, kind="damage", category=category, attackKind=attack_kind, skillId="2", skillName="同名技能")])
        self.stats.accept("test-stream", [event(index, 1, None, kind="damage", category="普通攻击",
                                                personId="other", attackKind="basic", skillId="3")
                                          for index in range(103, 703)])
        self.assertNotIn(101, [row["sequence"] for row in self.stats.snapshot()["events"]])
        self.assertNotIn(102, [row["sequence"] for row in self.stats.snapshot()["events"]])
        self.assertEqual(self.stats.explain(101)["event"]["skillId"], "1")
        self.assertEqual(self.stats.explain(102)["event"]["skillId"], "2")
        self.assertEqual(len(self.stats._latest_events), 3)
        self.stats.accept("test-stream", [event(703, 300, None, kind="damage", category=category,
                                                attackKind=attack_kind, skillId="1", skillName="同名技能")])
        with self.assertRaisesRegex(StatisticsError, "分类最近快照"):
            self.stats.explain(101)
        self.assertEqual(self.stats.explain(102)["event"]["amount"], 200)
        self.assertEqual(self.stats.explain(703)["event"]["amount"], 300)
        self.assertEqual(len(self.stats._latest_events), 3)

    def test_latest_periodic_damage_preserves_distinct_skill_ids_after_rolling_eviction(self):
        self._assert_latest_attack_identity("periodic", "持续伤害")

    def test_latest_same_named_attacks_preserve_distinct_skill_ids_after_rolling_eviction(self):
        self._assert_latest_attack_identity("skill", "同名技能")

    def test_maximum_group_snapshot_with_rich_metadata_fits_real_transport(self):
        person = {"professionId": "职" * 100, "profession": "职" * 100, "professionDescription": "说" * 128,
                  "appearance": "人" * 100, "iconKey": "person:" + "像" * 100, "job": "工" * 100,
                  "equipment": [{"slot": str(index), "name": "装" * 80, "type": "型" * 20,
                                 "iconKey": "item:" + "图" * 60, "level": 1e100, "qualityName": "品" * 20}
                                for index in range(3)]}
        stats = OutputStatistics()
        stats.start("stream", 0)
        rows = [event(index, 1e100, None, kind="damage", personId=str(index), personName="人" * 100,
                      category="攻" * 100, targetName="敌" * 100, attackKind="skill", damageType="physical",
                      skillId=str(index), **({"person": person} if index <= 67 else {})) for index in range(1, 513)]
        stats.accept("stream", rows)
        snapshot = stats.snapshot()
        self.assertEqual(len(snapshot["people"]), 512)
        self.assertEqual(len(stats._presentation_groups), 1024)
        frame = io.BytesIO()
        write_response(frame, {"protocolVersion": 1, "id": "worst-case", "result": {"statistics": snapshot}})
        self.assertLess(len(frame.getvalue()), MAX_RESPONSE_BYTES - 32 * 1024)

    def test_four_byte_unicode_budget_rejects_before_status_can_exceed_transport(self):
        stats = OutputStatistics()
        stats.start("stream", 0)
        rejected_at = None
        for index in range(1, 513):
            row = event(index, 1e100, None, kind="damage", personId=str(index) + "🌟" * (100 - len(str(index))),
                        personName="🌟" * 100, category="🌟" * 100, targetName="🌟" * 100,
                        attackKind="skill", damageType="physical", skillId="🌟" * 100, skillName="🌟" * 100)
            try:
                stats.accept("stream", [row])
            except StatisticsError as error:
                self.assertIn("响应预算", str(error))
                rejected_at = index
                break
        self.assertIsNotNone(rejected_at)
        self.assertEqual(stats.last_sequence, rejected_at - 1)
        frame = io.BytesIO()
        write_response(frame, {"protocolVersion": 1, "id": "bounded", "result": {"statistics": stats.snapshot()}})
        self.assertLess(len(frame.getvalue()), MAX_RESPONSE_BYTES - 32 * 1024)


if __name__ == "__main__":
    unittest.main()
