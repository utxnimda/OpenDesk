from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from work_catalog_yaml.layout import backend_roots


ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "scripts" / "lib" / "workspace.ps1"
SHELLS = list(dict.fromkeys(path for name in ("powershell.exe", "pwsh") if (path := shutil.which(name))))


def quoted(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(SHELLS, "PowerShell is unavailable")
class WorkspaceScriptsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name) / "separate workspace"
        (self.workspace / "config").mkdir(parents=True)
        (self.workspace / "data").mkdir()

    def run_script(self, source: str, *, cwd: str | Path | None = None, env=None):
        source = source.replace("WORKROOT", quoted(self.workspace))
        output = []
        for shell in SHELLS:
            with self.subTest(shell=shell):
                result = subprocess.run(
                    [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", "$ErrorActionPreference = 'Stop'\n. " + quoted(HELPER) + "\n" + source],
                    cwd=cwd or ROOT, env=env, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=15,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                output.append(json.loads(result.stdout))
        return output

    def test_backend_discovery_matches_python_contract(self) -> None:
        outputs = self.run_script("@(Get-MediaManagerBackendRoots -RepositoryRoot " + quoted(ROOT) + ") | ConvertTo-Json -Compress")
        expected = [str(path) for path in backend_roots(ROOT)]
        for output in outputs:
            self.assertEqual(output, expected)

    def test_success_and_exception_restore_present_and_absent_environment(self) -> None:
        env = dict(os.environ)
        env.update({"MEDIA_MANAGER_APPLICATION_ROOT": "original-app", "PYTHONPATH": "original-python", "PYTHONDONTWRITEBYTECODE": "0", "PYTHONIOENCODING": "latin-1"})
        env.pop("MEDIA_MANAGER_WORKSPACE_ROOT", None)
        for fails in (False, True):
            with self.subTest(fails=fails), tempfile.TemporaryDirectory() as temporary:
                script = """
$originalLocation = (Get-Location).Path
$caught = $false
try {
    Invoke-MediaManagerWorkspace -RepositoryRoot REPO -WorkspaceRoot WORKROOT -Utf8PythonIO -Action {
        if ($env:MEDIA_MANAGER_APPLICATION_ROOT -ne REPO) { throw 'wrong application' }
        if ($env:MEDIA_MANAGER_WORKSPACE_ROOT -ne WORKROOT -or (Get-Location).Path -ne WORKROOT) { throw 'wrong workspace' }
        if ($env:PYTHONIOENCODING -ne 'utf-8' -or $env:PYTHONDONTWRITEBYTECODE -ne '1') { throw 'wrong Python settings' }
        FAILURE
    }
} catch {
    if ($_.Exception.Message -ne 'expected failure') { throw }
    $caught = $true
}
[ordered]@{
    app = $env:MEDIA_MANAGER_APPLICATION_ROOT
    python = $env:PYTHONPATH
    bytecode = $env:PYTHONDONTWRITEBYTECODE
    encoding = $env:PYTHONIOENCODING
    workspaceAbsent = -not (Test-Path -LiteralPath 'Env:MEDIA_MANAGER_WORKSPACE_ROOT')
    locationRestored = (Get-Location).Path -eq $originalLocation
    caught = $caught
} | ConvertTo-Json -Compress
""".replace("REPO", quoted(ROOT)).replace("FAILURE", "throw 'expected failure'" if fails else "")
                for output in self.run_script(script, cwd=temporary, env=env):
                    self.assertEqual(output, {"app": "original-app", "python": "original-python", "bytecode": "0", "encoding": "latin-1", "workspaceAbsent": True, "locationRestored": True, "caught": fails})

    def test_nested_invocations_restore_outer_snapshot(self) -> None:
        script = """
$before = $env:MEDIA_MANAGER_WORKSPACE_ROOT
Invoke-MediaManagerWorkspace -RepositoryRoot REPO -WorkspaceRoot WORKROOT -Action {
    $env:MEDIA_MANAGER_WORKSPACE_ROOT = 'outer override'
    Invoke-MediaManagerWorkspace -RepositoryRoot REPO -WorkspaceRoot WORKROOT -Action {
        if ($env:MEDIA_MANAGER_WORKSPACE_ROOT -ne WORKROOT) { throw 'inner workspace missing' }
    }
    if ($env:MEDIA_MANAGER_WORKSPACE_ROOT -ne 'outer override') { throw 'outer workspace lost' }
}
@{ restored = $env:MEDIA_MANAGER_WORKSPACE_ROOT -eq $before } | ConvertTo-Json -Compress
""".replace("REPO", quoted(ROOT))
        for output in self.run_script(script):
            self.assertTrue(output["restored"])

    def test_invalid_workspace_never_changes_environment_or_location(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            script = """
$before = $env:PYTHONPATH
$location = (Get-Location).Path
$caught = $false
try { Invoke-MediaManagerWorkspace -RepositoryRoot INVALID -Action { throw 'must not execute' } }
catch { $caught = $_.Exception.Message -like '*framework backend is missing*' }
@{ caught = $caught; environmentRestored = $env:PYTHONPATH -eq $before; locationRestored = (Get-Location).Path -eq $location } | ConvertTo-Json -Compress
""".replace("INVALID", quoted(temporary))
            for output in self.run_script(script):
                self.assertEqual(output, {"caught": True, "environmentRestored": True, "locationRestored": True})

    def test_native_exit_status_survives_cleanup(self) -> None:
        script = "Invoke-MediaManagerWorkspace -RepositoryRoot " + quoted(ROOT) + " -WorkspaceRoot WORKROOT -Action { & " + quoted(sys.executable) + " -c 'import sys; sys.exit(7)' }\n@{ code = $LASTEXITCODE } | ConvertTo-Json -Compress\nexit 0"
        for output in self.run_script(script):
            self.assertEqual(output["code"], 7)

    def test_default_workspace_is_under_host_root_not_source_or_cwd(self):
        project = Path(self.temporary.name) / "host"
        source = project / "plugins" / "media-manager"
        source.mkdir(parents=True)
        workspace = project / "workspaces" / "media-manager"
        (workspace / "config").mkdir(parents=True)
        (workspace / "data").mkdir()
        script = "@{ path = Get-MediaManagerWorkspaceRoot -RepositoryRoot " + quoted(source) + " } | ConvertTo-Json -Compress"
        for output in self.run_script(script, cwd=self.workspace):
            self.assertEqual(output["path"], str(workspace))

    def test_missing_external_workspace_fails_before_environment_changes(self):
        invalid = Path(self.temporary.name) / "no-data"
        invalid.mkdir()
        script = """
$before = $env:MEDIA_MANAGER_WORKSPACE_ROOT
$location = (Get-Location).Path
$caught = $false
try { Invoke-MediaManagerWorkspace -RepositoryRoot REPO -WorkspaceRoot INVALID -Action { throw 'must not execute' } }
catch { $caught = $_.Exception.Message -like '*Workspace is missing*' }
@{ caught = $caught; sameEnvironment = $env:MEDIA_MANAGER_WORKSPACE_ROOT -eq $before; sameLocation = (Get-Location).Path -eq $location } | ConvertTo-Json -Compress
""".replace("REPO", quoted(ROOT)).replace("INVALID", quoted(invalid))
        for output in self.run_script(script):
            self.assertEqual(output, {"caught": True, "sameEnvironment": True, "sameLocation": True})

    def test_drive_relative_and_root_relative_workspaces_are_rejected(self):
        for value in ["relative", "C:relative", "\\relative"]:
            script = """
$caught = $false
try { Get-MediaManagerWorkspaceRoot -RepositoryRoot REPO -WorkspaceRoot INVALID | Out-Null }
catch { $caught = $_.Exception.Message -like '*absolute path*' }
@{ caught = $caught } | ConvertTo-Json -Compress
""".replace("REPO", quoted(ROOT)).replace("INVALID", quoted(value))
            for output in self.run_script(script):
                self.assertTrue(output["caught"])

    def test_init_workspace_uses_public_templates_and_never_overwrites(self):
        for index, shell in enumerate(SHELLS):
            destination = Path(self.temporary.name) / f"new-{index}"
            command = ". " + quoted(HELPER) + "; Initialize-MediaManagerWorkspace -RepositoryRoot " + quoted(ROOT) + " -WorkspaceRoot " + quoted(destination)
            first = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command], capture_output=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertTrue((destination / "data/features/collection-detail/db").is_dir())
            self.assertEqual(list((destination / "data/features/collection-detail/db").iterdir()), [])
            self.assertFalse((destination / "config/features/catalog-library/providers.local.yaml").exists())
            before = {str(p.relative_to(destination)): p.read_bytes() for p in destination.rglob("*") if p.is_file()}
            second = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command], capture_output=True)
            self.assertNotEqual(second.returncode, 0)
            self.assertEqual(before, {str(p.relative_to(destination)): p.read_bytes() for p in destination.rglob("*") if p.is_file()})


if __name__ == "__main__":
    unittest.main()
