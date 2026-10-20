"""Planner checks use small independent exhaustive optima and move replay."""

from copy import deepcopy
import itertools
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from apps.features.tower_assistant.backend.planner import CORE_OFFSETS, PlannerError, analyze


def entity(ident, kind, x, y, **extra):
    return {"id": ident, "kind": kind, "name": ident, "x": x, "y": y,
            "width": 3 if kind == "platform" else 1,
            "height": 3 if kind == "platform" else 1,
            "movable": kind not in {"platform", "resource"}, **extra}


def snapshot():
    return {"schemaVersion": 1, "source": {"name": "fixture.dat"},
            "bounds": {"minX": 0, "maxX": 12, "minY": 0, "maxY": 12},
            "entities": [entity("d", "platform", 6, 6)], "items": [], "warnings": [],
            "rules": {"auraRadius": 3, "auraMetric": "manhattan", "pedestalTierBonus": 0.15,
                      "qiRounding": "js-one-decimal", "allowItemReassignment": True, "coordinateAnchor": "game-grid",
                      "warehouseCapacity": 2500, "warehouseFreeSlots": 2400}}


def pedestal(snap, ident, x, y, qi=None, tier=1, **extra):
    pad = entity(ident, "pedestal", x, y, tier=tier, itemId=None, **extra)
    snap["entities"].append(pad)
    if qi is not None:
        pad["itemId"] = "i-" + ident
        snap["items"].append({"id": "i-" + ident, "name": "灵物" + ident,
                              "qi": qi, "location": "placed", "pedestalId": ident})
    return pad


def warehouse(snap, ident, qi):
    snap["items"].append({"id": ident, "name": ident, "qi": qi, "location": "warehouse"})


def footprint(obj, pos=None):
    pos = pos or obj
    left = pos["x"] - (obj["width"] - 1) // 2
    top = pos["y"] - (obj["height"] - 1) // 2
    return {(left + x, top + y) for x in range(obj["width"]) for y in range(obj["height"])}


class PlannerTests(unittest.TestCase):
    def assert_moves_replay(self, result):
        state = {obj["id"]: deepcopy(obj) for obj in result["entitiesBefore"]}
        for step in result["steps"]:
            if step["kind"] != "move":
                continue
            obj = state[step["entityId"]]
            self.assertEqual(step["from"], {"x": obj["x"], "y": obj["y"]})
            target = footprint(obj, step["to"])
            for other in state.values():
                if other["id"] != obj["id"]:
                    self.assertFalse(target & footprint(other), (step, other))
            if "allowedBounds" in obj:
                bound = obj["allowedBounds"]
                self.assertTrue(all(bound["minX"] <= x <= bound["maxX"] and bound["minY"] <= y <= bound["maxY"] for x, y in target))
            obj.update(step["to"])
        for obj in result["entitiesAfter"]:
            self.assertEqual((obj["x"], obj["y"]), (state[obj["id"]]["x"], state[obj["id"]]["y"]))

    def test_core_shape_is_sixteen_and_avoids_platform(self):
        self.assertEqual(len(CORE_OFFSETS), 16)
        self.assertTrue(all(abs(x) + abs(y) <= 3 and max(abs(x), abs(y)) > 1 for x, y in CORE_OFFSETS))

    def test_empty_city_requires_no_invented_pedestals(self):
        snap = snapshot()
        warehouse(snap, "treasure", 999)
        result = analyze(snap)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["metrics"]["targetQi"], 0)
        self.assertEqual(result["steps"], [])
        self.assertEqual(result["optimality"]["qi"], "exact")

    def test_warehouse_upgrade_needs_no_building_or_person_movement(self):
        snap = snapshot()
        for i, (x, y) in enumerate(CORE_OFFSETS):
            pedestal(snap, f"p{i}", x + 6, y + 6, qi=10)
        warehouse(snap, "best", 100)
        before = deepcopy(snap)
        result = analyze(snap)
        self.assertEqual(snap, before, "planner must not mutate its source snapshot")
        self.assertEqual(result["metrics"]["targetQi"], 15 * 11.5 + 115)
        self.assertEqual(result["metrics"]["movedBuildings"], 0)
        self.assertEqual(result["metrics"]["itemChanges"], 1)
        self.assertEqual(result["optimality"]["movement"], "exact")
        self.assertTrue(all(step["kind"] == "equip" for step in result["steps"]))
        incoming = next(step for step in result["steps"] if step.get("itemId") == "best")
        outgoing = next(step for step in result["steps"] if step.get("operation") == "unequip")
        self.assertIn("基础灵气 100", incoming["reason"])
        self.assertIn("原来源：仓库", incoming["reason"])
        self.assertIn("获得 115 灵气", incoming["reason"])
        self.assertIn("基础灵气 10", outgoing["reason"])

    def test_full_warehouse_blocks_unsafe_unequip_sequence(self):
        snap = snapshot()
        pedestal(snap, "p", 6, 3, qi=10)
        warehouse(snap, "upgrade", 20)
        snap["rules"]["warehouseFreeSlots"] = 0
        result = analyze(snap)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["steps"], [])
        self.assertIn("仓库仅有 0 个空位", result["warnings"][-1])

    def test_equal_aura_preserves_existing_core_and_items(self):
        snap = snapshot()
        for i, (x, y) in enumerate(CORE_OFFSETS):
            pedestal(snap, f"inside{i}", x + 6, y + 6, qi=10)
        for i in range(12):
            pedestal(snap, f"outside{i}", i, 0, qi=10)
        result = analyze(snap)
        self.assertEqual(result["steps"], [])
        self.assertEqual(result["metrics"]["currentQi"], result["metrics"]["targetQi"])

    def test_item_from_outside_is_removed_from_its_original_pedestal(self):
        snap = snapshot()
        for i, (x, y) in enumerate(CORE_OFFSETS):
            pedestal(snap, f"inside{i}", x + 6, y + 6, qi=10, tier=2)
        pedestal(snap, "outside", 1, 1, qi=100, tier=1)
        result = analyze(snap)
        original = next(obj for obj in result["entitiesAfter"] if obj["id"] == "outside")
        self.assertIsNone(original["itemId"])
        self.assertEqual((original["x"], original["y"]), (1, 1))
        self.assertEqual(sum(obj.get("itemId") == "i-outside" for obj in result["entitiesAfter"]), 1)
        self.assertEqual(result["metrics"]["movedBuildings"], 0)
        incoming = next(step for step in result["steps"] if step.get("itemId") == "i-outside")
        self.assertIn("基础灵气 100", incoming["reason"])
        self.assertIn("原来源：原台 (1, 1)", incoming["reason"])
        self.assertIn("获得 130 灵气", incoming["reason"])

    def test_rounding_and_matching_against_exhaustive_small_optimum(self):
        for values in ([0.05, 1.15, 2.25, 4.95], [4.05, 4.04, 0, 3.75], [7, 6, 5, 4]):
            snap = snapshot()
            slots = set(CORE_OFFSETS[:3])
            for i, (x, y) in enumerate(CORE_OFFSETS):
                if (x, y) not in slots:
                    snap["entities"].append(entity(f"rock{i}", "resource", x + 6, y + 6))
            tiers = [0, 1, 3, 2]
            for i, tier in enumerate(tiers):
                pedestal(snap, f"p{i}", 1 + i, 1, tier=tier)
            for i, value in enumerate(values):
                warehouse(snap, f"w{i}", value)
            best = 0
            for chosen_pads in itertools.combinations(range(4), 3):
                for chosen_items in itertools.permutations(values, 3):
                    units = sum(math.floor(10 * qi * (1 + tiers[pad] * .15) + .5)
                                for pad, qi in zip(chosen_pads, chosen_items))
                    best = max(best, units)
            result = analyze(snap)
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["metrics"]["targetQi"], best / 10)
            self.assertEqual(result["optimality"]["qi"], "exact")
            self.assert_moves_replay(result)

    def test_native_binary_rounding_boundary_is_preserved(self):
        # JS Math.round(10 * 5 * (1 + .15)) is 57, not decimal rounding's 58.
        snap = snapshot()
        pedestal(snap, "p", 6, 3, qi=5, tier=1)
        result = analyze(snap)
        self.assertEqual(result["metrics"]["currentQi"], 5.7)
        self.assertEqual(result["metrics"]["targetQi"], 5.7)

    def test_higher_tier_outside_is_worth_moving_but_not_all_sixteen(self):
        snap = snapshot()
        for i, (x, y) in enumerate(CORE_OFFSETS):
            pedestal(snap, f"p{i}", x + 6, y + 6, qi=10)
        pedestal(snap, "high-tier", 1, 1, qi=20, tier=3)
        result = analyze(snap)
        self.assertEqual(result["metrics"]["movedBuildings"], 2)
        self.assertEqual(result["metrics"]["targetQi"], 15 * 11.5 + 29)
        self.assert_moves_replay(result)

    def test_full_core_movement_lower_bound_has_exact_certificate(self):
        snap = snapshot()
        for i, (x, y) in enumerate(CORE_OFFSETS):
            if i:
                pedestal(snap, f"p{i}", x + 6, y + 6, qi=10)
        pedestal(snap, "outside", 1, 1, qi=10)
        x, y = CORE_OFFSETS[0]
        snap["entities"].append(entity("worker", "person", x + 6, y + 6))
        result = analyze(snap)
        self.assertEqual(result["metrics"]["movementLowerBound"], 2)
        self.assertEqual(result["metrics"]["movedBuildings"], 1)
        self.assertEqual(result["metrics"]["movedPeople"], 1)
        self.assertEqual(result["optimality"]["movement"], "exact")
        self.assertEqual(result["metrics"]["moveOperations"], 2)
        self.assert_moves_replay(result)

    def test_existing_person_overlay_does_not_force_an_unnecessary_move(self):
        snap = snapshot()
        for i, (x, y) in enumerate(CORE_OFFSETS):
            pedestal(snap, f"p{i}", x + 6, y + 6, qi=10)
        x, y = CORE_OFFSETS[0]
        snap["entities"].append(entity("worker", "person", x + 6, y + 6))
        warehouse(snap, "upgrade", 100)
        result = analyze(snap)
        self.assertEqual(result["metrics"]["movedPeople"], 0)
        self.assertEqual(result["optimality"]["movement"], "exact")

    def test_cycle_without_real_temporary_space_does_not_output_illegal_swap(self):
        snap = snapshot()
        snap["bounds"] = {"minX": 0, "maxX": 6, "minY": 0, "maxY": 6}
        snap["entities"][0].update(x=3, y=3)
        pedestal(snap, "outside", 0, 0, qi=10)
        snap["entities"].append(entity("worker", "person", 3, 0))
        covered = footprint(snap["entities"][0]) | {(0, 0), (3, 0)}
        for x in range(7):
            for y in range(7):
                if (x, y) not in covered:
                    snap["entities"].append(entity(f"rock-{x}-{y}", "resource", x, y))
        result = analyze(snap)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["steps"], [])
        self.assertIn("临时位置", result["warnings"][-1])

    def test_overlapping_pedestals_are_rejected(self):
        snap = snapshot()
        pedestal(snap, "a", 0, 0, qi=10)
        pedestal(snap, "b", 0, 0, qi=20)
        with self.assertRaisesRegex(PlannerError, "重叠"):
            analyze(snap)

    def test_unsupported_matching_size_fails_before_allocating_a_large_graph(self):
        snap = snapshot()
        snap["bounds"].update(maxX=30, maxY=30)
        for i in range(129):
            pedestal(snap, f"p{i}", i % 30, 15 + i // 30, qi=10)
        with self.assertRaisesRegex(PlannerError, "128"):
            analyze(snap)

    def test_associated_facility_stays_inside_original_parent_range(self):
        snap = snapshot()
        for i, (x, y) in enumerate(CORE_OFFSETS):
            if i:
                pedestal(snap, f"p{i}", x + 6, y + 6, qi=10)
        pedestal(snap, "outside", 1, 1, qi=10)
        parent = entity("parent", "building", 6, 1, movable=False)
        snap["entities"].append(parent)
        x, y = CORE_OFFSETS[0]
        facility = entity("farm", "facility", x + 6, y + 6, parentId="parent",
                          allowedBounds={"minX": 4, "maxX": 8, "minY": 1, "maxY": 4})
        snap["entities"].append(facility)
        snap["entities"].append(entity("worker", "person", x + 6, y + 6, parentId="farm"))
        result = analyze(snap)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["metrics"]["movedFacilities"], 1)
        self.assertEqual(result["metrics"]["movedPeople"], 1)
        moved = [step["entityId"] for step in result["steps"] if step["kind"] == "move"]
        self.assertLess(moved.index("worker"), moved.index("farm"))
        self.assert_moves_replay(result)

    def test_facility_with_no_legal_destination_is_blocked(self):
        snap = snapshot()
        for i, (x, y) in enumerate(CORE_OFFSETS):
            if i:
                pedestal(snap, f"p{i}", x + 6, y + 6, qi=10)
        pedestal(snap, "outside", 1, 1, qi=10)
        snap["entities"].append(entity("parent", "building", 6, 1, movable=False))
        x, y = CORE_OFFSETS[0]
        snap["entities"].append(entity("farm", "facility", x + 6, y + 6, parentId="parent",
                                       allowedBounds={"minX": x + 6, "maxX": x + 6, "minY": y + 6, "maxY": y + 6}))
        result = analyze(snap)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["steps"], [])
        self.assertEqual(result["entitiesBefore"], result["entitiesAfter"])

    def test_occupied_building_moves_worker_first(self):
        snap = snapshot()
        pedestal(snap, "outside", 1, 1, qi=20, occupantIds=["worker"])
        snap["entities"].append(entity("worker", "person", 1, 1, workingBuildingId="outside"))
        result = analyze(snap)
        self.assertEqual(result["status"], "ready")
        moves = [step["entityId"] for step in result["steps"] if step["kind"] == "move"]
        self.assertLess(moves.index("worker"), moves.index("outside"))
        self.assert_moves_replay(result)

    def test_invalid_item_binding_is_not_silently_ignored(self):
        snap = snapshot()
        pedestal(snap, "p", 1, 1, qi=10)
        snap["items"][0]["pedestalId"] = "unknown"
        with self.assertRaisesRegex(PlannerError, "绑定"):
            analyze(snap)

    def test_duplicate_entity_ids_and_nonfinite_qi_rejected(self):
        snap = snapshot()
        snap["entities"].append(deepcopy(snap["entities"][0]))
        with self.assertRaises(PlannerError):
            analyze(snap)
        snap = snapshot()
        warehouse(snap, "nan", float("nan"))
        with self.assertRaises(PlannerError):
            analyze(snap)

    def test_platform_selection_is_explicit_when_multiple_exist(self):
        snap = snapshot()
        snap["entities"].append(entity("d2", "platform", 2, 2))
        with self.assertRaisesRegex(PlannerError, "选择"):
            analyze(snap)
        self.assertEqual(analyze(snap, "d2")["platformId"], "d2")

    def test_unsupported_rules_refuse_to_guess(self):
        snap = snapshot()
        snap["rules"]["auraRadius"] = 4
        with self.assertRaises(PlannerError):
            analyze(snap)

    def test_edge_platform_clips_slots_and_preserves_it(self):
        snap = snapshot()
        snap["entities"][0].update(x=1, y=1)
        pedestal(snap, "p", 8, 8, qi=10)
        result = analyze(snap)
        self.assertEqual(result["status"], "ready")
        self.assertLess(result["metrics"]["usableCoreSlots"], 16)
        self.assertEqual(result["entitiesAfter"][0]["x"], 1)
        self.assert_moves_replay(result)


if __name__ == "__main__":
    unittest.main()
