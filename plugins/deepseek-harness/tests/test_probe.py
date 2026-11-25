"""Tests for Node.js version checks, pnpm/corepack detection, and dsh binary/source repo discovery."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from deepseek_harness.probe import (
    NodeInfo,
    PnpmInfo,
    _resolve_windows_cmd_to_js,
    build_pnpm_command,
    ensure_pnpm_shim,
    inspect_source_repo,
    is_dsh_source_repo,
    parse_semver,
    probe_pnpm,
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

    def test_resolve_windows_cmd_supports_npm_and_pnpm_shims(self):
        with tempfile.TemporaryDirectory(prefix="dsh-cmd-") as tmp:
            root = Path(tmp)
            npm_target = root / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
            npm_target.parent.mkdir(parents=True)
            npm_target.write_text("// npm bin\n", encoding="utf-8")
            (npm_target.parent.parent / "package.json").write_text(json.dumps({"version": "1.2.3"}), encoding="utf-8")
            npm_cmd = root / "dsh.cmd"
            npm_cmd.write_text('"@node" "%dp0%\\node_modules\\@deepseek-ai\\dsh\\lib\\bin.js" %*\r\n', encoding="utf-8")

            script, version = _resolve_windows_cmd_to_js(npm_cmd)
            self.assertEqual(Path(script), npm_target.resolve())
            self.assertEqual(version, "1.2.3")

            bin_dir = root / "node_modules" / ".bin"
            bin_dir.mkdir(parents=True, exist_ok=True)
            pnpm_target = root / "node_modules" / "pnpm" / "bin" / "pnpm.mjs"
            pnpm_target.parent.mkdir(parents=True)
            pnpm_target.write_text("// pnpm bin\n", encoding="utf-8")
            (pnpm_target.parent.parent / "package.json").write_text(json.dumps({"version": "11.28.5"}), encoding="utf-8")
            pnpm_cmd = bin_dir / "pnpm.CMD"
            pnpm_cmd.write_text('node "%~dp0\\..\\pnpm\\bin\\pnpm.mjs" %*\r\n', encoding="utf-8")

            pnpm_script, pnpm_version = _resolve_windows_cmd_to_js(pnpm_cmd)
            self.assertEqual(Path(pnpm_script), pnpm_target.resolve())
            self.assertEqual(pnpm_version, "11.28.5")

    def test_probe_pnpm_falls_back_to_repo_pnpm_then_corepack(self):
        with tempfile.TemporaryDirectory(prefix="dsh-pnpm-probe-") as tmp:
            root = Path(tmp)
            node_dir = root / "nodejs"
            node_dir.mkdir()
            fake_node = node_dir / ("node.exe" if os.name == "nt" else "node")
            fake_node.write_text("", encoding="utf-8")
            corepack_js = node_dir / "node_modules" / "corepack" / "dist" / "corepack.js"
            corepack_js.parent.mkdir(parents=True)
            corepack_js.write_text("// corepack\n", encoding="utf-8")

            repo = root / "repo"
            repo.mkdir()
            _create_fake_source_repo(repo, with_deps=True, with_artifacts=True)
            repo_pnpm_mjs = repo / "node_modules" / "pnpm" / "bin" / "pnpm.mjs"
            repo_pnpm_mjs.parent.mkdir(parents=True)
            repo_pnpm_mjs.write_text("// repo pnpm\n", encoding="utf-8")

            node_info = NodeInfo(True, str(fake_node.resolve()), "24.14.1", True, None)
            with mock.patch("deepseek_harness.probe.shutil.which", return_value=None):
                repo_detected = probe_pnpm(node_info, source_repo_root=repo)
                self.assertTrue(repo_detected.available)
                self.assertEqual(repo_detected.kind, "repo-pnpm")
                self.assertEqual(Path(repo_detected.script), repo_pnpm_mjs.resolve())

                # probe_environment pointing directly at <repo>/apps/cli/lib/bin.js should also discover repo pnpm
                with mock.patch("deepseek_harness.probe.probe_node", return_value=node_info):
                    from deepseek_harness.probe import probe_environment

                    env_probe = probe_environment(
                        root / "data",
                        dsh_source="custom",
                        custom_dsh_path=str((repo / "apps" / "cli" / "lib" / "bin.js").resolve()),
                    )
                    self.assertTrue(env_probe.pnpm.available)
                    self.assertEqual(env_probe.pnpm.kind, "repo-pnpm")
                    self.assertEqual(Path(env_probe.pnpm.script), repo_pnpm_mjs.resolve())

                cp_detected = probe_pnpm(node_info, source_repo_root=None)
                self.assertTrue(cp_detected.available)
                self.assertEqual(cp_detected.kind, "corepack")
                self.assertEqual(Path(cp_detected.script), corepack_js.resolve())

    def test_ensure_pnpm_shim_skips_native_and_forwards_args_and_exit_code(self):
        node_bin = shutil.which("node")
        if not node_bin:
            self.skipTest("node is required to execute generated pnpm shim")

        with tempfile.TemporaryDirectory(prefix="dsh-shim-中文-") as tmp:
            root = Path(tmp)
            data_dir = root / "data"

            # Native pnpm must never generate a fallback shim
            native = PnpmInfo(True, "pnpm", node_bin, "/some/pnpm.mjs", "pnpm")
            self.assertIsNone(ensure_pnpm_shim(data_dir, native))
            self.assertFalse((data_dir / "runtime-shims").exists())

            # Invalid control characters in command path must be rejected
            bad = PnpmInfo(True, "corepack", node_bin, "bad\npath.js", "corepack pnpm")
            with self.assertRaises(ValueError):
                ensure_pnpm_shim(data_dir, bad)

            # Real executable test for corepack fallback shim (verifies exit code, env var, Chinese path, &path:/plugin)
            echo_js = root / "fake-corepack.mjs"
            echo_js.write_text(
                "const args = process.argv.slice(2);\n"
                "console.log(JSON.stringify({ args, prompt: process.env.COREPACK_ENABLE_DOWNLOAD_PROMPT || '' }));\n"
                "if (args.includes('--fail-7')) process.exit(7);\n",
                encoding="utf-8",
            )
            cp_info = PnpmInfo(True, "corepack", str(Path(node_bin).resolve()), str(echo_js.resolve()), "corepack pnpm")
            shim_dir = ensure_pnpm_shim(data_dir, cp_info)
            self.assertIsNotNone(shim_dir)
            assert shim_dir is not None

            spec = "git+https://git.example.com/team/sample-dsh-plugin.git#0123456789abcdef0123456789abcdef01234567&path:/plugin"
            if os.name == "nt":
                shim_file = shim_dir / "pnpm.cmd"
                self.assertTrue(shim_file.is_file())
                # Invoke through cmd.exe /d /s /c with quoted args matching cross-spawn/execa on Windows
                proc = subprocess.run(
                    f'cmd.exe /d /s /c ""{shim_file}" add "{spec}" "中文 参数""',
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                fail_proc = subprocess.run(
                    f'cmd.exe /d /s /c ""{shim_file}" --fail-7"',
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            else:
                shim_file = shim_dir / "pnpm"
                self.assertTrue(shim_file.is_file())
                proc = subprocess.run(
                    [str(shim_file), "add", spec, "中文 参数"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )
                fail_proc = subprocess.run(
                    [str(shim_file), "--fail-7"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout.strip())
            self.assertEqual(payload["args"], ["pnpm", "add", spec, "中文 参数"])
            self.assertEqual(payload["prompt"], "0")
            self.assertEqual(fail_proc.returncode, 7)


if __name__ == "__main__":
    unittest.main()
