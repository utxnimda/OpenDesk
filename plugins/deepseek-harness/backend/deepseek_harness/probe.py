"""Environment probing for Node.js (>=24.9), npm, pnpm/corepack, source checkout, and DeepSeek Harness (dsh)."""
from __future__ import annotations

from dataclasses import dataclass, asdict
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Sequence

MIN_NODE_VERSION = (24, 9, 0)
NODE_VER_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True)
class NodeInfo:
    available: bool
    path: str
    version: str
    meetsMinimum: bool
    error: str | None = None


@dataclass(frozen=True)
class PnpmInfo:
    available: bool
    kind: str  # "pnpm" | "corepack" | "none"
    executable: str
    script: str
    display: str
    error: str | None = None


@dataclass(frozen=True)
class SourceRepoInfo:
    isRepo: bool
    repoRoot: str
    version: str
    enginesNode: str
    depsInstalled: bool
    artifactsBuilt: bool
    hasDevWeb: bool
    gitBranch: str
    gitCommit: str
    gitDirty: bool


@dataclass(frozen=True)
class DshEntry:
    available: bool
    kind: str  # "node-script" | "source-dev" | "executable" | "none"
    source: str  # "managed-cli" | "path" | "custom-repo" | "custom-file" | "none"
    executable: str
    script: str
    version: str
    error: str | None = None


@dataclass(frozen=True)
class ProbeResult:
    node: NodeInfo
    npmPath: str
    pnpm: PnpmInfo
    dsh: DshEntry
    sourceRepo: SourceRepoInfo | None
    managedCliDir: str
    managedCliInstalled: bool
    systemDshHome: str

    def to_dict(self) -> dict:
        return {
            "node": asdict(self.node),
            "npmPath": self.npmPath,
            "pnpm": asdict(self.pnpm),
            "dsh": asdict(self.dsh),
            "sourceRepo": asdict(self.sourceRepo) if self.sourceRepo else None,
            "managedCliDir": self.managedCliDir,
            "managedCliInstalled": self.managedCliInstalled,
            "systemDshHome": self.systemDshHome,
            "minNodeVersion": ".".join(str(x) for x in MIN_NODE_VERSION),
        }


def parse_semver(text: str) -> tuple[int, int, int] | None:
    match = NODE_VER_RE.search(text or "")
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _run_short(args: Sequence[str], *, cwd: Path | None = None, timeout: float = 8.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            list(args),
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except (OSError, subprocess.SubprocessError) as error:
        return -1, "", str(error)


def probe_node(custom_node_path: str = "") -> NodeInfo:
    candidate = custom_node_path.strip()
    if candidate:
        path = Path(candidate)
        if not path.is_absolute() or not path.is_file():
            return NodeInfo(False, candidate, "", False, "指定的 Node.js 路径不存在或不是文件。")
        resolved = str(path.resolve())
    else:
        found = shutil.which("node")
        if not found:
            return NodeInfo(False, "", "", False, "未在系统 PATH 中找到 Node.js，请安装 Node.js >= 24.9 或手动指定路径。")
        resolved = str(Path(found).resolve())

    code, stdout, stderr = _run_short([resolved, "-v"])
    if code != 0:
        return NodeInfo(False, resolved, "", False, f"无法查询 Node.js 版本：{stderr or '退出码非 0'}")
    parsed = parse_semver(stdout)
    if not parsed:
        return NodeInfo(False, resolved, stdout, False, f"无法解析 Node.js 版本号：{stdout}")
    meets = parsed >= MIN_NODE_VERSION
    version_text = f"{parsed[0]}.{parsed[1]}.{parsed[2]}"
    error = None if meets else f"当前 Node.js {version_text} 低于 DeepSeek Harness 要求的最低版本 24.9.0。"
    return NodeInfo(True, resolved, version_text, meets, error)


def probe_npm(node_path: str = "") -> str:
    if node_path:
        sibling = Path(node_path).parent / ("npm.cmd" if os.name == "nt" else "npm")
        if sibling.is_file():
            return str(sibling.resolve())
    found = shutil.which("npm.cmd" if os.name == "nt" else "npm") or shutil.which("npm")
    return str(Path(found).resolve()) if found else ""


def _resolve_windows_cmd_to_js(cmd_path: Path) -> tuple[str, str]:
    """Inspect a Windows npm/corepack .cmd shim to extract the underlying JS entry so node can run it directly."""
    try:
        text = cmd_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return "", ""
    # Typical npm shim: "%dp0%\node_modules\@deepseek-ai\dsh\lib\bin.js"
    match = re.search(r'"%dp0%\\([^"]+\.(?:m?js|cjs))"', text, re.IGNORECASE)
    if match:
        target = (cmd_path.parent / match.group(1).replace("\\", os.sep)).resolve()
        if target.is_file():
            version = _read_package_version(target.parent.parent / "package.json")
            return str(target), version
    return "", ""


def probe_pnpm(node_info: NodeInfo) -> PnpmInfo:
    """Detect pnpm directly, or fall back to Node's bundled corepack (`corepack pnpm`)."""
    node_dir = Path(node_info.path).parent if node_info.available and node_info.path else None

    # 1. Explicit pnpm in Node directory or PATH
    pnpm_candidates: list[Path] = []
    if node_dir is not None:
        for name in ("pnpm.cmd", "pnpm.exe", "pnpm") if os.name == "nt" else ("pnpm",):
            p = node_dir / name
            if p.is_file():
                pnpm_candidates.append(p.resolve())
    for name in ("pnpm.cmd", "pnpm.exe", "pnpm") if os.name == "nt" else ("pnpm",):
        found = shutil.which(name)
        if found:
            pnpm_candidates.append(Path(found).resolve())

    for cand in pnpm_candidates:
        if cand.suffix.lower() == ".cmd" and node_info.available:
            script, _ = _resolve_windows_cmd_to_js(cand)
            if script:
                return PnpmInfo(True, "pnpm", node_info.path, script, "pnpm")
        return PnpmInfo(True, "pnpm", str(cand), "", "pnpm")

    # 2. Corepack bundled with Node.js (runs pinned pnpm version automatically without global install)
    if node_dir is not None and node_info.available:
        corepack_js = node_dir / "node_modules" / "corepack" / "dist" / "corepack.js"
        if corepack_js.is_file():
            return PnpmInfo(True, "corepack", node_info.path, str(corepack_js.resolve()), "corepack pnpm")
        sibling_cp = node_dir / ("corepack.cmd" if os.name == "nt" else "corepack")
        if sibling_cp.is_file():
            return PnpmInfo(True, "corepack", str(sibling_cp.resolve()), "", "corepack pnpm")

    cp_found = shutil.which("corepack.cmd" if os.name == "nt" else "corepack") or shutil.which("corepack")
    if cp_found:
        cp_path = Path(cp_found).resolve()
        if cp_path.suffix.lower() == ".cmd" and node_info.available:
            script, _ = _resolve_windows_cmd_to_js(cp_path)
            if script:
                return PnpmInfo(True, "corepack", node_info.path, script, "corepack pnpm")
        return PnpmInfo(True, "corepack", str(cp_path), "", "corepack pnpm")

    return PnpmInfo(False, "none", "", "", "", "未检测到 pnpm 或 corepack，无法从源码仓安装依赖或构建。")


def build_pnpm_command(pnpm_info: PnpmInfo, pnpm_args: Sequence[str]) -> list[str]:
    if not pnpm_info.available:
        raise RuntimeError(f"PNPM_MISSING: {pnpm_info.error or '未检测到 pnpm 或 corepack。'}")
    if pnpm_info.kind == "corepack":
        if pnpm_info.script:
            return [pnpm_info.executable, pnpm_info.script, "pnpm", *pnpm_args]
        return [pnpm_info.executable, "pnpm", *pnpm_args]
    if pnpm_info.script:
        return [pnpm_info.executable, pnpm_info.script, *pnpm_args]
    return [pnpm_info.executable, *pnpm_args]


def _read_package_json(package_json: Path) -> dict:
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _read_package_version(package_json: Path) -> str:
    version = _read_package_json(package_json).get("version")
    return version if isinstance(version, str) else ""


def is_dsh_source_repo(directory: Path) -> bool:
    """Strictly verify a directory is a DeepSeek Harness source checkout before running workspace tasks."""
    if not directory.is_dir():
        return False
    required = (
        directory / "package.json",
        directory / "pnpm-workspace.yaml",
        directory / "apps" / "cli" / "package.json",
        directory / "scripts" / "build.ts",
    )
    if not all(p.is_file() for p in required):
        return False
    cli_pkg = _read_package_json(directory / "apps" / "cli" / "package.json")
    return cli_pkg.get("name") == "@deepseek-ai/dsh"


def inspect_source_repo(directory: Path) -> SourceRepoInfo | None:
    if not is_dsh_source_repo(directory):
        return None
    root = directory.resolve()
    root_pkg = _read_package_json(root / "package.json")
    version = root_pkg.get("version") if isinstance(root_pkg.get("version"), str) else ""
    engines = root_pkg.get("engines")
    engines_node = engines.get("node", "") if isinstance(engines, dict) and isinstance(engines.get("node"), str) else ""

    deps_installed = (root / "node_modules" / ".pnpm").is_dir() or (root / "node_modules" / "tsx").is_dir()
    cli_built = (root / "apps" / "cli" / "lib" / "bin.js").is_file()
    web_built = (root / "apps" / "web" / "dist" / "index.html").is_file()
    artifacts_built = cli_built and web_built
    has_dev_web = (root / "scripts" / "dev-web.ts").is_file()

    git_branch = ""
    git_commit = ""
    git_dirty = False
    if (root / ".git").exists() and shutil.which("git"):
        rc_b, out_b, _ = _run_short(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"], timeout=3.0)
        if rc_b == 0:
            git_branch = out_b
        rc_c, out_c, _ = _run_short(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], timeout=3.0)
        if rc_c == 0:
            git_commit = out_c
        rc_s, out_s, _ = _run_short(["git", "-C", str(root), "status", "--porcelain"], timeout=3.0)
        if rc_s == 0:
            git_dirty = bool(out_s.strip())

    return SourceRepoInfo(
        isRepo=True,
        repoRoot=str(root),
        version=version,
        enginesNode=engines_node,
        depsInstalled=deps_installed,
        artifactsBuilt=artifacts_built,
        hasDevWeb=has_dev_web,
        gitBranch=git_branch,
        gitCommit=git_commit,
        gitDirty=git_dirty,
    )


def _resolve_package_bin(pkg_dir: Path) -> tuple[str, str]:
    """Locate JS bin script and package version from an installed @deepseek-ai/dsh package directory."""
    pkg_json = pkg_dir / "package.json"
    if not pkg_json.is_file():
        return "", ""
    data = _read_package_json(pkg_json)
    version = data.get("version") if isinstance(data.get("version"), str) else ""
    bin_field = data.get("bin")
    rel = ""
    if isinstance(bin_field, str):
        rel = bin_field
    elif isinstance(bin_field, dict):
        rel = bin_field.get("dsh") or next((v for v in bin_field.values() if isinstance(v, str)), "")
    if rel:
        script = (pkg_dir / rel).resolve()
        if script.is_file():
            return str(script), version
    for fallback in ("lib/bin.js", "dist/bin.js", "bin/dsh.js"):
        candidate = (pkg_dir / fallback).resolve()
        if candidate.is_file():
            return str(candidate), version
    return "", version


def _resolve_repo_bin(repo_dir: Path) -> tuple[str, str]:
    """Locate built CLI entry from a local deepseek-harness repository checkout."""
    for rel in (
        "apps/cli/lib/bin.js",
        "apps/cli/dist/bin.js",
        "packages/cli/lib/bin.js",
        "node_modules/@deepseek-ai/dsh/lib/bin.js",
    ):
        candidate = (repo_dir / rel).resolve()
        if candidate.is_file():
            pkg_dir = candidate.parent.parent
            return str(candidate), _read_package_version(pkg_dir / "package.json")
    return "", ""


def resolve_managed_cli(data_dir: Path, node_info: NodeInfo) -> DshEntry:
    pkg_dir = data_dir / "runtime-cli" / "node_modules" / "@deepseek-ai" / "dsh"
    script, version = _resolve_package_bin(pkg_dir)
    if script:
        if not node_info.available or not node_info.meetsMinimum:
            return DshEntry(
                False, "node-script", "managed-cli", node_info.path, script, version,
                node_info.error or "需要 Node.js >= 24.9.0 才能运行已安装的 dsh。",
            )
        return DshEntry(True, "node-script", "managed-cli", node_info.path, script, version, None)
    return DshEntry(False, "none", "none", "", "", "", None)


def resolve_custom_dsh(
    custom_path: str,
    node_info: NodeInfo,
    pnpm_info: PnpmInfo | None = None,
    source_launch_mode: str = "built",
) -> tuple[DshEntry, SourceRepoInfo | None]:
    path = Path(custom_path)
    if not path.is_absolute() or not path.exists():
        return (DshEntry(False, "none", "custom-file", "", "", "", "自定义的 dsh 路径不存在。"), None)

    if path.is_dir():
        repo_info = inspect_source_repo(path)
        if repo_info is not None and source_launch_mode == "dev":
            if not node_info.available or not node_info.meetsMinimum:
                return (DshEntry(False, "source-dev", "custom-repo", node_info.path, repo_info.repoRoot, repo_info.version, node_info.error), repo_info)
            if pnpm_info is None or not pnpm_info.available:
                return (DshEntry(False, "source-dev", "custom-repo", "", repo_info.repoRoot, repo_info.version, "源码热开发模式需要 pnpm 或 corepack。"), repo_info)
            if not repo_info.hasDevWeb:
                return (DshEntry(False, "source-dev", "custom-repo", "", repo_info.repoRoot, repo_info.version, "该源码仓缺少 scripts/dev-web.ts，无法使用热开发模式。"), repo_info)
            if not repo_info.depsInstalled:
                return (DshEntry(False, "source-dev", "custom-repo", "", repo_info.repoRoot, repo_info.version, "源码仓尚未安装依赖，请先点击「安装依赖 (pnpm install)」。"), repo_info)
            return (DshEntry(True, "source-dev", "custom-repo", node_info.path, repo_info.repoRoot, repo_info.version, None), repo_info)

        script, version = _resolve_repo_bin(path)
        if not script:
            script, version = _resolve_package_bin(path)
        if not script:
            if repo_info is not None:
                hint = (
                    "已识别 DeepSeek Harness 源码仓，但尚未安装依赖；请依次点击「安装依赖」与「构建产物」。"
                    if not repo_info.depsInstalled
                    else "已识别 DeepSeek Harness 源码仓，但尚未构建产物（缺少 apps/cli/lib/bin.js）；请点击「构建产物 (pnpm run build:artifacts)」，或切换到源码热开发模式。"
                )
                return (DshEntry(False, "none", "custom-repo", "", "", repo_info.version, hint), repo_info)
            return (
                DshEntry(
                    False, "none", "custom-repo", "", "", "",
                    "目录中未找到已构建的 dsh CLI 入口（如 apps/cli/lib/bin.js）；若为源码仓请先执行 pnpm install && pnpm run build:artifacts。",
                ),
                None,
            )
        if not node_info.available or not node_info.meetsMinimum:
            return (DshEntry(False, "node-script", "custom-repo", node_info.path, script, version, node_info.error), repo_info)
        return (DshEntry(True, "node-script", "custom-repo", node_info.path, script, version, None), repo_info)

    suffix = path.suffix.lower()
    if suffix in (".js", ".mjs", ".cjs"):
        if not node_info.available or not node_info.meetsMinimum:
            return (DshEntry(False, "node-script", "custom-file", node_info.path, str(path.resolve()), "", node_info.error), None)
        version = _read_package_version(path.resolve().parent.parent / "package.json")
        return (DshEntry(True, "node-script", "custom-file", node_info.path, str(path.resolve()), version, None), None)

    if suffix == ".cmd":
        script, version = _resolve_windows_cmd_to_js(path)
        if script and node_info.available and node_info.meetsMinimum:
            return (DshEntry(True, "node-script", "custom-file", node_info.path, script, version, None), None)
        return (DshEntry(True, "executable", "custom-file", str(path.resolve()), "", version, None), None)

    if suffix in (".exe", ""):
        return (DshEntry(True, "executable", "custom-file", str(path.resolve()), "", "", None), None)

    return (DshEntry(False, "none", "custom-file", "", "", "", f"不支持的 dsh 入口文件类型：{suffix}"), None)


def resolve_path_dsh(node_info: NodeInfo) -> DshEntry:
    for candidate_name in ("dsh.cmd", "dsh.exe", "dsh") if os.name == "nt" else ("dsh",):
        found = shutil.which(candidate_name)
        if not found:
            continue
        path = Path(found).resolve()
        if path.suffix.lower() == ".cmd":
            script, version = _resolve_windows_cmd_to_js(path)
            if script:
                if not node_info.available or not node_info.meetsMinimum:
                    return DshEntry(False, "node-script", "path", node_info.path, script, version, node_info.error)
                return DshEntry(True, "node-script", "path", node_info.path, script, version, None)
        return DshEntry(True, "executable", "path", str(path), "", "", None)
    return DshEntry(False, "none", "none", "", "", "", "未检测到已安装的 dsh，可安装 npm 包或在下方指定本地 DeepSeek Harness 源码目录。")


def probe_environment(
    data_dir: Path,
    *,
    dsh_source: str = "auto",
    custom_dsh_path: str = "",
    node_path: str = "",
    source_launch_mode: str = "built",
) -> ProbeResult:
    node_info = probe_node(node_path)
    npm_path = probe_npm(node_info.path if node_info.available else "")
    pnpm_info = probe_pnpm(node_info)
    managed_dir = data_dir / "runtime-cli"
    managed_entry = resolve_managed_cli(data_dir, node_info)
    managed_installed = bool(managed_entry.script)
    source_repo: SourceRepoInfo | None = None

    if dsh_source == "custom" and custom_dsh_path:
        dsh_entry, source_repo = resolve_custom_dsh(
            custom_dsh_path,
            node_info,
            pnpm_info=pnpm_info,
            source_launch_mode=source_launch_mode,
        )
    else:
        if managed_entry.available:
            dsh_entry = managed_entry
        else:
            path_entry = resolve_path_dsh(node_info)
            if path_entry.available:
                dsh_entry = path_entry
            elif managed_installed:
                dsh_entry = managed_entry
            else:
                dsh_entry = path_entry

    return ProbeResult(
        node=node_info,
        npmPath=npm_path,
        pnpm=pnpm_info,
        dsh=dsh_entry,
        sourceRepo=source_repo,
        managedCliDir=str(managed_dir),
        managedCliInstalled=managed_installed,
        systemDshHome=str((Path.home() / ".dsh").resolve()),
    )
