"""Tests for Node.js version checks, pnpm/corepack detection, and dsh binary/source repo discovery."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from deepseek_harness.probe import (
    NodeInfo,
    PnpmInfo,
    build_pnpm_command,
    inspect_source_repo,
    is_dsh_source_repo,
    parse_semver,
    resolve_custom_dsh,
    resolve_managed_cli,
)


def _create_fake_source_repo(root: Path, *, with_deps: bool = False, with_artifacts: bool = False) -> None:
    (root / "package.json").write_text(
        json.dumps({"name": "@deepseek-ai/dsh-root", "version": "0.2.3-alpha.2", "engines": {"node": "^24.22.3"}}),
        encoding="utf-8",
    )
    (root / "pnpm-workspace.yaml").write_text("packages:\n  - apps/*\n", encoding="utf-8")
    (root / "apps" / "cli").mkdir(parents=True, exist_ok=True)
    (root / "apps" / "cli" / "package.json").write_text(
        json.dumps({"name": "@deepseek-ai/dsh", "version": "0.2.3-alpha.2"}),
        encoding="utf-8",
    )
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "build.ts").write_text("// build\n", encoding="utf-8")
    (root / "scripts" / "dev-web.ts").write_text("// dev-web\n", encoding="utf-8")
    if with_deps:
        (root / "node_modules" / ".pnpm").mkdir(parents=True, exist_ok=True)
    if with_artifacts:
        (root / "apps" / "cli" / "lib").mkdir(parents=True, exist_ok=True)
        (root / "apps" / "cli" / "lib" / "bin.js").write_text("// built cli\n", encoding="utf-8")
        (root / "apps" / "web" / "dist").mkdir(parents=True, exist_ok=True)
        (root / "apps" / "web" / "dist" / "index.html").write_text("<!doctype html>", encoding="utf-8")


class ProbeTests(unittest.TestCase):
    def test_semver_parsing(self):
        self.assertEqual(parse_semver("v24.14.1\n"), (24, 14, 1))
        self.assertEqual(parse_semver("24.9.0"), (24, 9, 0))
        self.assertIsNone(parse_semver("invalid"))

    def test_resolves_managed_cli_package_entry(self):
        with tempfile.TemporaryDirectory(prefix="dsh-probe-") as tmp:
            root = Path(tmp)
            pkg = root / "runtime-cli" / "node_modules" / "@deepseek-ai" / "dsh"
            (pkg / "lib").mkdir(parents=True)
            bin_js = pkg / "lib" / "bin.js"
            bin_js.write_text("console.log('ok');\n", encoding="utf-8")
            (pkg / "package.json").write_text(
                json.dumps({"name": "@deepseek-ai/dsh", "version": "0.2.2-rc.5", "bin": {"dsh": "lib/bin.js"}}),
                encoding="utf-8",
            )

            ok_node = NodeInfo(True, "C:\\node\\node.exe", "24.14.1", True, None)
            entry = resolve_managed_cli(root, ok_node)
            self.assertTrue(entry.available)
            self.assertEqual(entry.kind, "node-script")
            self.assertEqual(entry.source, "managed-cli")
            self.assertEqual(entry.version, "0.2.2-rc.5")
            self.assertEqual(Path(entry.script), bin_js.resolve())

            old_node = NodeInfo(True, "C:\\node\\node.exe", "22.12.0", False, "too old")
            rejected = resolve_managed_cli(root, old_node)
            self.assertFalse(rejected.available)
            self.assertIn("too old", rejected.error or "")

    def test_resolves_custom_repo_checkout_built_and_dev_modes(self):
        ok_node = NodeInfo(True, "C:\\node\\node.exe", "24.14.1", True, None)
        ok_pnpm = PnpmInfo(True, "corepack", "C:\\node\\node.exe", "C:\\node\\corepack.js", "corepack pnpm")
        self.assertEqual(
            build_pnpm_command(ok_pnpm, ["run", "build:artifacts"]),
            ["C:\\node\\node.exe", "C:\\node\\corepack.js", "pnpm", "run", "build:artifacts"],
        )

        with tempfile.TemporaryDirectory(prefix="dsh-repo-") as tmp:
            repo = Path(tmp)
            self.assertFalse(is_dsh_source_repo(repo))

            # Unbuilt repo with no deps yet is recognized as a valid source repo with actionable hint
            _create_fake_source_repo(repo, with_deps=False, with_artifacts=False)
            self.assertTrue(is_dsh_source_repo(repo))
            info = inspect_source_repo(repo)
            self.assertIsNotNone(info)
            assert info is not None
            self.assertFalse(info.depsInstalled)
            self.assertFalse(info.artifactsBuilt)
            self.assertTrue(info.hasDevWeb)

            entry, repo_info = resolve_custom_dsh(str(repo), ok_node, pnpm_info=ok_pnpm, source_launch_mode="built")
            self.assertFalse(entry.available)
            self.assertIsNotNone(repo_info)
            self.assertIn("安装依赖", entry.error or "")

            # With deps installed, dev mode becomes available immediately
            _create_fake_source_repo(repo, with_deps=True, with_artifacts=False)
            dev_entry, dev_repo = resolve_custom_dsh(str(repo), ok_node, pnpm_info=ok_pnpm, source_launch_mode="dev")
            self.assertTrue(dev_entry.available)
            self.assertEqual(dev_entry.kind, "source-dev")
            self.assertIsNotNone(dev_repo)
            assert dev_repo is not None
            self.assertTrue(dev_repo.depsInstalled)

            # With artifacts built, built mode resolves directly to apps/cli/lib/bin.js
            _create_fake_source_repo(repo, with_deps=True, with_artifacts=True)
            built_entry, built_repo = resolve_custom_dsh(str(repo), ok_node, pnpm_info=ok_pnpm, source_launch_mode="built")
            self.assertTrue(built_entry.available)
            self.assertEqual(built_entry.kind, "node-script")
            self.assertEqual(built_entry.source, "custom-repo")
            self.assertEqual(built_entry.version, "0.2.3-alpha.2")
            self.assertIsNotNone(built_repo)
            assert built_repo is not None
            self.assertTrue(built_repo.artifactsBuilt)


if __name__ == "__main__":
    unittest.main()
