"""Capture changes only temporary synthetic game installations in this suite."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo

from apps.features.tower_assistant.backend import capture, capture_install as installer


def event(sequence, amount=1000000):
    return {"schemaVersion": 1, "sequence": sequence, "kind": "work", "personId": "person:1",
            "personName": "合成人物", "category": "锻造", "amount": amount, "effectiveAmount": 10000}


class CaptureFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        (self.root / "game.exe").write_bytes(b"synthetic-not-executable")
        self.assets = self.root / "assets"
        self.assets.mkdir()
        for name in installer.ASSETS:
            (self.assets / name).write_text(f"// synthetic capture asset: {name}\n", encoding="utf-8")
        self.write_package()
        self.original = (self.root / "package.nw").read_bytes()
        self.patches = [patch.object(installer, "ASSET_DIR", self.assets),
                        patch.object(installer, "game_running", return_value=False),
                        patch.object(installer.reader, "_audited_package", return_value=(installer.PROFILE, {}))]
        for item in self.patches:
            item.start()
        self.addCleanup(self.temp.cleanup)
        for item in self.patches:
            self.addCleanup(item.stop)

    def write_package(self, manifest=None, extra=None):
        with ZipFile(self.root / "package.nw", "w") as package:
            package.writestr("package.json", json.dumps(manifest or {"name": "com.ujwan.tfszmnq", "main": "index.html"}))
            package.writestr("index.html", "synthetic game")
            package.writestr("workermain.js", "// original synthetic game worker\n")
            for name, value in (extra or []):
                package.writestr(name, value)

    def install(self):
        installer.install(self.root)
        self.state = self.root / installer.STATE_DIR
        self.token = installer.read_json(self.state / "config.json")["token"]

    def stream(self, events=None, **changes):
        events = [] if events is None else events
        value = {"schemaVersion": 1, "token": self.token, "ruleProfile": installer.PROFILE,
                 "streamId": "synthetic-stream", "lastSequence": events[-1]["sequence"] if events else 0,
                 "dropped": 0, "heartbeatMs": int(time.time() * 1000), "error": None, "events": events}
        value.update(changes)
        installer.atomic_json(self.state / "stream.json", value)
        return value


class InstallTests(CaptureFixture):
    def test_update_detection_compares_scripts_without_modifying_installed_game(self):
        self.install()
        before = (self.root / "package.nw").read_bytes()
        self.assertFalse(installer.needs_update(self.root))
        (self.assets / "observer.js").write_text("// newer original observer\n", encoding="utf-8")
        self.assertTrue(installer.needs_update(self.root))
        manager = capture.CaptureManager(auto_poll=False)
        self.addCleanup(manager.close)
        status = manager.status(self.root)["capture"]
        self.assertIs(status["installed"], True)
        self.assertIs(status["updateRequired"], True)
        self.assertFalse(status["canInstall"])
        self.assertEqual((self.root / "package.nw").read_bytes(), before)

    def test_installer_records_the_audited_profile_instead_of_defaulting_to_old_revision(self):
        profile = "audited-2026-10-25-r2"
        with patch.object(installer.reader, "_audited_package", return_value=(profile, {})):
            self.assertEqual(installer.audit_package(self.root / "package.nw"), profile)
            self.install()
            self.assertEqual(installer.read_json(self.state / "config.json")["ruleProfile"], profile)
            self.assertEqual(installer.installation(self.root)["ruleProfile"], profile)
            installer.remove(self.root)
            self.assertEqual((self.root / "package.nw").read_bytes(), self.original)

    def test_install_remove_roundtrip_retains_exact_original_and_no_save_writes(self):
        (self.root / "save").mkdir()
        save = self.root / "save" / "ea1save.dat"
        save.write_bytes(b"private fixture must remain unchanged")
        self.install()
        self.assertEqual((self.state / "original-package.nw").read_bytes(), self.original)
        self.assertTrue(installer.installation(self.root)["installed"])
        with ZipFile(self.root / "package.nw") as package:
            manifest = json.loads(package.read("package.json"))
            self.assertEqual(manifest["inject_js_start"], installer.ENTRY)
            expected_assets = {"inject.js": "opendesk-capture/inject.js",
                               "observer.js": "opendesk-capture/observer.js",
                               "runtime-worker.js": "opendesk-capture-worker.js"}
            self.assertEqual(set(package.namelist()),
                             {"package.json", "index.html", "workermain.js", *expected_assets.values()})
            for name, destination in expected_assets.items():
                self.assertEqual(package.read(destination), (self.assets / name).read_bytes())
            with ZipFile(self.state / "original-package.nw") as original:
                for name in ("index.html", "workermain.js"):
                    self.assertEqual(package.read(name), original.read(name))
                original_manifest = json.loads(original.read("package.json"))
                self.assertEqual({k: v for k, v in manifest.items() if k != "inject_js_start"}, original_manifest)
        self.assertRegex(self.token, "^[0-9a-f]{64}$")
        installer.remove(self.root)
        self.assertEqual((self.root / "package.nw").read_bytes(), self.original)
        self.assertFalse(installer.installation(self.root)["installed"])
        self.assertFalse((self.state / "config.json").exists())
        self.assertEqual(save.read_bytes(), b"private fixture must remain unchanged")
        installer.install(self.root)
        self.assertTrue(installer.installation(self.root)["installed"])

    def test_existing_root_worker_entry_is_never_overwritten(self):
        for name in ("opendesk-capture-worker.js", "OPENDESK-CAPTURE-WORKER.JS",
                     "opendesk-capture-worker.js/child.js"):
            with self.subTest(name=name):
                self.write_package(extra=[(name, "existing game-owned content")])
                original = (self.root / "package.nw").read_bytes()
                with self.assertRaisesRegex(installer.CaptureError, "已有采集文件"):
                    installer.install(self.root)
                self.assertEqual((self.root / "package.nw").read_bytes(), original)
                self.assertFalse((self.root / installer.STATE_DIR).exists())

    def test_missing_empty_or_oversized_capture_assets_leave_game_untouched(self):
        for name in installer.ASSETS:
            asset = self.assets / name
            original = asset.read_bytes()
            for contents in (None, b"", b" " * (1024 * 1024 + 1)):
                with self.subTest(name=name, size=None if contents is None else len(contents)):
                    if contents is None:
                        asset.unlink()
                    else:
                        asset.write_bytes(contents)
                    with self.assertRaisesRegex(installer.CaptureError, "采集素材.*重新安装塔防页签"):
                        installer.install(self.root)
                    self.assertEqual((self.root / "package.nw").read_bytes(), self.original)
                    self.assertFalse((self.root / installer.STATE_DIR).exists())
                    asset.write_bytes(original)

    def test_old_capture_requires_restore_before_installing_new_assets(self):
        legacy_assets = {"inject.js": "opendesk-capture/inject.js",
                         "observer.js": "opendesk-capture/observer.js"}
        with patch.object(installer, "ASSETS", legacy_assets):
            self.install()
        previous = (self.root / "package.nw").read_bytes()
        with self.assertRaisesRegex(installer.CaptureError, "先移除采集器恢复原包"):
            installer.install(self.root)
        self.assertEqual((self.root / "package.nw").read_bytes(), previous)
        installer.remove(self.root)
        self.assertEqual((self.root / "package.nw").read_bytes(), self.original)
        self.install()
        with ZipFile(self.root / "package.nw") as package:
            self.assertEqual(package.read("opendesk-capture-worker.js"),
                             (self.assets / "runtime-worker.js").read_bytes())
        installer.remove(self.root)
        self.assertEqual((self.root / "package.nw").read_bytes(), self.original)

    def test_running_game_blocks_install_and_remove_without_mutation(self):
        with patch.object(installer, "game_running", return_value=True):
            with self.assertRaisesRegex(installer.CaptureError, "手动保存并退出"):
                installer.install(self.root)
        self.assertEqual((self.root / "package.nw").read_bytes(), self.original)
        self.install()
        installed = (self.root / "package.nw").read_bytes()
        with patch.object(installer, "game_running", return_value=True):
            with self.assertRaises(installer.CaptureError):
                installer.remove(self.root)
        self.assertEqual((self.root / "package.nw").read_bytes(), installed)

    def test_game_starting_during_prepare_keeps_original(self):
        with patch.object(installer, "game_running", side_effect=[False, True]):
            with self.assertRaisesRegex(installer.CaptureError, "正在运行"):
                installer.install(self.root)
        self.assertEqual((self.root / "package.nw").read_bytes(), self.original)
        self.assertFalse(installer.installation(self.root)["installed"])
        installer.install(self.root)
        self.assertTrue(installer.installation(self.root)["installed"])

    def test_unknown_revision_and_existing_injection_are_rejected(self):
        with patch.object(installer.reader, "_audited_package", return_value=("audited-2026-10-18", {})):
            with self.assertRaisesRegex(installer.CaptureError, "2026-10-25"):
                installer.install(self.root)
        self.write_package({"name": "com.ujwan.tfszmnq", "inject_js_start": "existing.js"})
        before = (self.root / "package.nw").read_bytes()
        with self.assertRaisesRegex(installer.CaptureError, "已有注入"):
            installer.install(self.root)
        self.assertEqual((self.root / "package.nw").read_bytes(), before)

    def test_rejects_duplicate_traversal_and_zip_symlink(self):
        link = ZipInfo("link")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        redirected = ZipInfo("placeholder")
        redirected.filename = "folder\\file"
        for extra in ([('PACKAGE.JSON', '{}')], [('../outside', 'bad')], [(redirected, 'bad')],
                      [(link, "outside")], [('opendesk-capture/observer.js', 'existing')]):
            with self.subTest(extra=extra):
                self.write_package(extra=extra)
                with self.assertRaises(installer.CaptureError):
                    installer.install(self.root)
        self.assertFalse((self.root / installer.STATE_DIR).exists())

    def test_game_update_or_backup_tampering_is_never_overwritten(self):
        self.install()
        installed = (self.root / "package.nw").read_bytes()
        (self.root / "package.nw").write_bytes(b"updated game owned by user")
        with self.assertRaisesRegex(installer.CaptureError, "更新或被修改"):
            installer.remove(self.root)
        self.assertEqual((self.root / "package.nw").read_bytes(), b"updated game owned by user")
        (self.root / "package.nw").write_bytes(installed)
        (self.state / "original-package.nw").write_bytes(b"changed backup")
        with self.assertRaisesRegex(installer.CaptureError, "备份已改变"):
            installer.remove(self.root)
        self.assertEqual((self.root / "package.nw").read_bytes(), installed)

    def test_update_arriving_during_restore_copy_is_not_overwritten(self):
        self.install()
        copy = installer.shutil.copyfileobj
        def concurrent_update(source, target, length):
            copy(source, target, length)
            (self.root / "package.nw").write_bytes(b"new game update during restore")
        with patch.object(installer.shutil, "copyfileobj", side_effect=concurrent_update):
            with self.assertRaisesRegex(installer.CaptureError, "复制期间游戏包改变"):
                installer.remove(self.root)
        self.assertEqual((self.root / "package.nw").read_bytes(), b"new game update during restore")

    def test_changed_backup_bytes_during_restore_are_never_installed(self):
        self.install()
        installed = (self.root / "package.nw").read_bytes()
        with patch.object(installer.shutil, "copyfileobj", side_effect=lambda source, target, length: target.write(b"damaged copy")):
            with self.assertRaisesRegex(installer.CaptureError, "原始备份改变"):
                installer.remove(self.root)
        self.assertEqual((self.root / "package.nw").read_bytes(), installed)

    def test_atomic_replace_failure_keeps_original_and_retry_is_possible(self):
        original_replace = installer.os.replace
        def replace(source, target):
            if Path(target) == self.root / "package.nw":
                raise OSError("synthetic rename failure")
            return original_replace(source, target)
        with patch.object(installer.os, "replace", side_effect=replace):
            with self.assertRaisesRegex(OSError, "rename failure"):
                installer.install(self.root)
        self.assertEqual((self.root / "package.nw").read_bytes(), self.original)
        installer.install(self.root)
        self.assertTrue(installer.installation(self.root)["installed"])

    def test_failed_metadata_commit_keeps_original_and_verified_backup_can_retry(self):
        with patch.object(installer, "atomic_json", side_effect=OSError("metadata write failed")):
            with self.assertRaisesRegex(OSError, "metadata"):
                installer.install(self.root)
        self.assertEqual((self.root / "package.nw").read_bytes(), self.original)
        self.assertFalse(installer.installation(self.root)["installed"])
        installer.install(self.root)
        self.assertTrue(installer.installation(self.root)["installed"])

    def test_reparse_component_rejected_even_when_target_is_inside_game(self):
        original_lstat = Path.lstat
        def lstat(path):
            info = original_lstat(path)
            if path.name == "package.nw":
                class Reparse:
                    st_mode = info.st_mode
                    st_file_attributes = 0x400
                return Reparse()
            return info
        with patch.object(Path, "lstat", lstat):
            with self.assertRaisesRegex(installer.CaptureError, "目录重定向"):
                installer.install(self.root)


class PollingTests(CaptureFixture):
    def setUp(self):
        super().setUp()
        self.install()
        self.manager = capture.CaptureManager(auto_poll=False)
        self.addCleanup(self.manager.close)

    def test_installed_game_is_detected_before_any_save_or_event_exists(self):
        with patch.object(installer, "game_running", return_value=True):
            status = self.manager.status(self.root)["capture"]
            self.assertTrue(status["installed"])
            self.assertTrue(status["gameRunning"])
            self.assertEqual(status["state"], "waiting")
            self.assertIn("等待采集心跳", status["message"])
            self.assertFalse(status["canInstall"])
            self.assertFalse(status["canRemove"])
            self.stream()
            connected = self.manager.status(self.root)
            self.assertEqual(connected["capture"]["state"], "connected")
            self.assertEqual(connected["statistics"]["eventCount"], 0)

    def test_process_probe_failure_preserves_verified_installation_and_recovers(self):
        with patch.object(installer, "game_running", side_effect=installer.CaptureError("进程路径读取失败")):
            status = self.manager.status(self.root)["capture"]
        self.assertIs(status["installed"], True)
        self.assertIsNone(status["gameRunning"])
        self.assertEqual(status["state"], "error")
        self.assertIn("进程路径读取失败", status["message"])
        self.assertFalse(status["canInstall"])
        self.assertFalse(status["canRemove"])
        with patch.object(installer, "game_running", return_value=True):
            recovered = self.manager.status(self.root)["capture"]
        self.assertIs(recovered["gameRunning"], True)
        self.assertIs(recovered["installed"], True)

    def test_installation_probe_failure_does_not_hide_a_running_game(self):
        with patch.object(installer, "installation", side_effect=installer.CaptureError("安装元数据读取失败")), \
                patch.object(installer, "game_running", return_value=True):
            status = self.manager.status(self.root)["capture"]
        self.assertIsNone(status["installed"])
        self.assertIs(status["gameRunning"], True)
        self.assertEqual(status["state"], "error")
        self.assertIn("安装元数据读取失败", status["message"])
        self.assertFalse(status["canInstall"])
        self.assertFalse(status["canRemove"])

    def test_start_uses_sequence_baseline_stop_drains_and_freezes(self):
        self.stream([event(1)])
        self.assertEqual(self.manager.start(self.root)["statistics"]["eventCount"], 0)
        self.stream([event(1), event(2)])
        result = self.manager.stop(self.root)["statistics"]
        self.assertEqual(result["state"], "stopped")
        self.assertEqual(result["eventCount"], 1)
        self.assertEqual(result["totals"][0]["amount"], 1000000)
        self.assertEqual(result["totals"][0]["effectiveAmount"], 10000)
        self.stream([event(1), event(2), event(3)])
        self.assertEqual(self.manager.status(self.root)["statistics"]["eventCount"], 1)

    def test_latest_explanation_polls_then_freezes_the_matching_group_atomically(self):
        self.stream()
        self.manager.start(self.root)
        self.stream([event(1)])
        displayed = self.manager.status(self.root)["statistics"]
        session = displayed["sessionId"]
        damage = [{**event(index), "kind": "damage", "category": "普攻"} for index in range(2, 602)]
        self.stream([event(1), *damage])
        self.manager.status(self.root)
        latest_work = {**event(602, 25000), "workFormula": {
            "expression": 5617, "inputs": [1, 25000, 1, 0, 0, 0, 1, 1, 1, 1], "flatBonus": 0, "result": 25000}}
        # No status request observes this event: explain_latest must poll and
        # choose the true latest work within the same manager lock.
        self.stream([*damage, latest_work])
        selector = {"kind": "work", "personId": "person:1", "category": "锻造"}
        result = self.manager.explain_latest(self.root, selector, session)
        self.assertEqual((result["sequence"], result["event"]["amount"]), (602, 25000))
        self.assertTrue(result["workFormula"]["matches"])
        with self.assertRaisesRegex(ValueError, "分类最近快照"):
            self.manager.explain(self.root, 1, session)
        self.stream([latest_work, event(603, 50000)])
        self.assertEqual(self.manager.explain_latest(self.root, selector, session)["sequence"], 603)
        self.assertEqual(result["event"]["amount"], 25000)
        self.manager.stop(self.root)
        self.manager.start(self.root)
        with self.assertRaisesRegex(ValueError, "计时会话"):
            self.manager.explain_latest(self.root, selector, session)

    def test_signed_history_lookup_does_not_poll_and_survives_exact_history_eviction(self):
        self.stream()
        self.manager.start(self.root)
        original = {**event(1), "person": {"profession": "当时职业"}}
        self.stream([original])
        displayed = self.manager.status(self.root)["statistics"]
        receipt = displayed["events"][0]["detailSnapshot"]
        self.stream([event(index) for index in range(2, 603)])
        with patch.object(self.manager, "_poll") as poll, patch.object(self.manager, "_select") as select:
            result = self.manager.explain_snapshot(self.root, 1, displayed["sessionId"], receipt)
            poll.assert_not_called()
            select.assert_not_called()
        self.assertEqual(self.manager._statistics.last_sequence, 1)
        self.assertEqual(result["event"]["person"]["profession"], "当时职业")
        with self.assertRaisesRegex(ValueError, "分类最近快照"):
            self.manager.explain(self.root, 1, displayed["sessionId"])
        self.assertEqual(self.manager._statistics.last_sequence, 602)
        self.assertEqual(self.manager.explain_snapshot(self.root, 1, displayed["sessionId"], receipt)["event"], result["event"])
        self.manager.stop(self.root)
        self.manager.start(self.root)
        recovered = self.manager.explain_snapshot(self.root, 1, displayed["sessionId"], receipt)
        self.assertEqual(recovered["sessionId"], displayed["sessionId"])
        self.assertNotEqual(recovered["sessionId"], self.manager._statistics.snapshot()["sessionId"])

    def test_signed_history_uses_cached_root_without_switching_active_directory(self):
        self.stream()
        self.manager.start(self.root)
        self.stream([event(1)])
        displayed = self.manager.stop(self.root)["statistics"]
        receipt = displayed["events"][0]["detailSnapshot"]
        other = self.root / "other"
        other.mkdir()
        (other / "game.exe").write_bytes(b"synthetic-not-executable")
        (other / "package.nw").write_bytes(b"synthetic")
        self.manager.status(other)
        self.manager._statistics.start("other-stream", 0)
        active_statistics = self.manager._statistics
        with patch.object(self.manager, "_poll") as poll, patch.object(self.manager, "_select") as select:
            recovered = self.manager.explain_snapshot(self.root, 1, displayed["sessionId"], receipt)
            with self.assertRaisesRegex(ValueError, "签名"):
                self.manager.explain_snapshot(other, 1, displayed["sessionId"], receipt)
            poll.assert_not_called()
            select.assert_not_called()
        self.assertEqual(recovered["sequence"], 1)
        self.assertEqual(self.manager._root, other)
        self.assertIs(self.manager._statistics, active_statistics)
        self.assertEqual(active_statistics.state, "running")

    def test_signed_history_rejects_evicted_directory_authority_without_recreating_it(self):
        self.stream()
        self.manager.start(self.root)
        self.stream([event(1)])
        displayed = self.manager.stop(self.root)["statistics"]
        receipt = displayed["events"][0]["detailSnapshot"]
        for number in range(9):
            other = self.root / f"receipt-directory-{number}"
            other.mkdir()
            (other / "game.exe").write_bytes(b"synthetic-not-executable")
            (other / "package.nw").write_bytes(b"synthetic")
            self.manager.status(other)
        current = self.manager._root
        self.assertNotIn(self.root, self.manager._sessions)
        with self.assertRaisesRegex(ValueError, "统计进程或目录会话已失效"):
            self.manager.explain_snapshot(self.root, 1, displayed["sessionId"], receipt)
        self.assertEqual(self.manager._root, current)
        self.assertNotIn(self.root, self.manager._sessions)
        self.assertEqual(len(self.manager._sessions), 7)

    def test_config_and_stream_must_use_the_same_supported_profile(self):
        profile = "synthetic-audited-next"
        with patch.object(installer, "PROFILES", frozenset({installer.PROFILE, profile})):
            self.stream(ruleProfile=profile)
            with self.assertRaisesRegex(installer.CaptureError, "版本或字段"):
                capture.read_stream(self.root)

    def test_start_requires_installed_profile_and_running_interval_pins_it_even_with_same_token(self):
        profile = "synthetic-audited-next"
        with patch.object(installer, "PROFILES", frozenset({installer.PROFILE, profile})):
            self.stream()
            self.manager.start(self.root)
            self.stream([event(1)])
            self.manager.status(self.root)
            config = installer.read_json(self.state / "config.json")
            config["ruleProfile"] = profile  # Keep token and streamId unchanged.
            installer.atomic_json(self.state / "config.json", config)
            self.stream([event(1)], ruleProfile=profile)
            result = self.manager.status(self.root)["statistics"]
            self.assertEqual(result["state"], "interrupted")
            self.assertEqual(result["eventCount"], 1)
            self.assertIn("规则版本不一致", result["reason"])
            with self.assertRaisesRegex(installer.CaptureError, "规则版本不一致"):
                self.manager.start(self.root)

    def test_main_formula_expression_is_bound_to_the_exact_stream_profile(self):
        profile = "audited-2026-10-25-r2"
        row = {**event(1, 3375), "kind": "damage", "effectiveAmount": 100,
               "formulaCoverage": "main-product", "formula": {
                   "expression": 5584, "inputs": [1000, 2, 1.5, .5, 0, 0, 0, 0, .25, 1, 1, 1, 1, 1], "result": 3375}}
        with self.subTest(profile=profile):
            self.stream([row])
            with self.assertRaisesRegex(installer.CaptureError, "拒绝混用版本"):
                capture.read_stream(self.root)
            config = installer.read_json(self.state / "config.json")
            config["ruleProfile"] = profile
            installer.atomic_json(self.state / "config.json", config)
            self.stream([row], ruleProfile=profile)
            accepted = capture.read_stream(self.root)
            self.assertEqual(accepted["events"][0]["formula"]["expression"], 5584)
            row["formula"]["expression"] = 5579
            self.stream([row], ruleProfile=profile)
            with self.assertRaisesRegex(installer.CaptureError, "拒绝混用版本"):
                capture.read_stream(self.root)

    def test_work_formula_expression_is_bound_to_the_exact_stream_profile(self):
        row = {**event(1, 100), "effectiveAmount": 100, "workFormula": {
            "expression": 5621, "inputs": [1, 100, 1, 0, 0, 0, 1, 1, 1, 1],
            "flatBonus": 0, "result": 100}}
        self.stream([row])
        with self.assertRaisesRegex(installer.CaptureError, "工作公式.*拒绝混用版本"):
            capture.read_stream(self.root)
        config = installer.read_json(self.state / "config.json")
        config["ruleProfile"] = "audited-2026-10-25-r2"
        installer.atomic_json(self.state / "config.json", config)
        self.stream([row], ruleProfile=config["ruleProfile"])
        self.assertEqual(capture.read_stream(self.root)["events"][0]["workFormula"]["expression"], 5621)
        row["workFormula"]["expression"] = 5616
        self.stream([row], ruleProfile=config["ruleProfile"])
        with self.assertRaisesRegex(installer.CaptureError, "工作公式.*拒绝混用版本"):
            capture.read_stream(self.root)

    def test_daemon_accumulates_without_any_page_polling(self):
        self.manager.close()
        self.manager = capture.CaptureManager()
        self.addCleanup(self.manager.close)
        self.stream()
        self.manager.start(self.root)
        self.stream([event(1), event(2)])
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with self.manager._lock:
                if self.manager._statistics.snapshot()["eventCount"] == 2:
                    break
            time.sleep(0.03)
        with self.manager._lock:
            self.assertEqual(self.manager._statistics.snapshot()["eventCount"], 2)

    def test_gap_rotation_dropped_expired_and_error_interrupt_preserving_totals(self):
        cases = ({"events": [event(3)]}, {"streamId": "new-stream"}, {"dropped": 1},
                 {"heartbeatMs": 1}, {"error": "synthetic observer failure"}, {"token": "b" * 64})
        for changes in cases:
            with self.subTest(changes=changes):
                self.stream()
                self.manager.start(self.root)
                self.stream([event(1)])
                self.manager.status(self.root)
                self.stream(changes.get("events", [event(1)]), **{k:v for k,v in changes.items() if k != "events"})
                result = self.manager.status(self.root)["statistics"]
                self.assertEqual(result["state"], "interrupted")
                self.assertEqual(result["eventCount"], 1)
                self.assertTrue(result["reason"])

    def test_missing_stream_and_game_package_change_interrupt(self):
        for missing in (True, False):
            with self.subTest(missing=missing):
                installed = (self.root / "package.nw").read_bytes()
                self.stream()
                self.manager.start(self.root)
                if missing:
                    (self.state / "stream.json").unlink()
                else:
                    (self.root / "package.nw").write_bytes(b"game update")
                self.assertEqual(self.manager.status(self.root)["statistics"]["state"], "interrupted")
                (self.root / "package.nw").write_bytes(installed)

    def test_running_session_cannot_switch_directory(self):
        other = self.root / "other"
        other.mkdir()
        (other / "game.exe").write_bytes(b"fixture")
        (other / "package.nw").write_bytes(b"fixture")
        self.stream()
        self.manager.start(self.root)
        with self.assertRaisesRegex(installer.CaptureError, "不能切换"):
            self.manager.status(other)
        self.assertEqual(self.manager.status(self.root)["statistics"]["state"], "running")

    def test_stream_contract_rejects_schema_fields_nonfinite_and_oversize(self):
        value = self.stream([event(1)])
        variants = [{**value, "schemaVersion": True}, {**value, "unexpected": 1},
                    {**value, "token": "非ASCII身份"},
                    {**value, "events": [event(2), event(1)]}, {**value, "heartbeatMs": True},
                    {**value, "events": [event(1, float("inf"))]},
                    {**value, "events": [event(1)] * 1001}]
        for bad in variants:
            with self.subTest(keys=list(bad)):
                installer.atomic_json(self.state / "stream.json", bad)
                with self.assertRaises(installer.CaptureError):
                    capture.read_stream(self.root)
        (self.state / "stream.json").write_bytes(b" " * (capture.MAX_STREAM_BYTES + 1))
        with self.assertRaisesRegex(installer.CaptureError, "大小限制"):
            capture.read_stream(self.root)

    def test_valid_ring_truncation_and_repeated_poll_do_not_duplicate(self):
        self.stream([event(10)])
        self.manager.start(self.root)
        self.stream([event(11), event(12)])
        self.manager.status(self.root)
        result = self.manager.status(self.root)["statistics"]
        self.assertEqual(result["state"], "running")
        self.assertEqual(result["eventCount"], 2)

    def test_recovered_connection_allows_new_interval_keeps_prior_reason_until_start(self):
        self.stream()
        self.manager.start(self.root)
        self.stream(heartbeatMs=1)
        self.assertEqual(self.manager.status(self.root)["statistics"]["state"], "interrupted")
        self.stream(streamId="recovered")
        restored = self.manager.status(self.root)
        self.assertEqual(restored["capture"]["state"], "connected")
        self.assertTrue(restored["statistics"]["reason"])
        self.assertEqual(self.manager.start(self.root)["statistics"]["state"], "running")

    def test_stopped_results_survive_switching_away_and_back(self):
        self.stream()
        self.manager.start(self.root)
        self.stream([event(1)])
        self.manager.stop(self.root)
        other = self.root / "other"
        other.mkdir()
        (other / "game.exe").write_bytes(b"fixture")
        (other / "package.nw").write_bytes(b"fixture")
        self.manager.status(other)
        restored = self.manager.status(self.root)["statistics"]
        self.assertEqual(restored["state"], "stopped")
        self.assertEqual(restored["eventCount"], 1)

    def test_unchanged_status_reuses_package_verification(self):
        self.stream()
        with patch.object(installer, "digest", wraps=installer.digest) as hashed:
            self.manager.status(self.root)
            first = hashed.call_count
            self.stream([event(1)])  # Atomic stream writes must not invalidate package verification.
            self.manager.status(self.root)
            self.assertEqual(hashed.call_count, first)
        self.assertGreater(first, 0)

    def test_inactive_directory_history_is_bounded_without_evicting_active_session(self):
        self.manager.status(self.root)
        for number in range(10):
            other = self.root / f"other-{number}"
            other.mkdir()
            (other / "game.exe").write_bytes(b"fixture")
            (other / "package.nw").write_bytes(b"fixture")
            self.manager.status(other)
        self.assertEqual(len(self.manager._sessions), 7)
        self.assertNotIn(self.root, self.manager._sessions)
        self.assertEqual(self.manager._root, self.root / "other-9")


if __name__ == "__main__":
    unittest.main()
