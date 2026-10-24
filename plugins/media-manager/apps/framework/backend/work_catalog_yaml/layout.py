"""Workspace layout helpers.

Application code and mutable workspace layout:

```
plugins/media-manager/
  apps/framework/
  apps/composition/
  apps/features/<feature-id>/
  defaults/config/
workspaces/media-manager/
  config/framework/
  config/features/<feature-id>/
  data/source/
  data/features/<feature-id>/
```

An explicitly configured external workspace is also supported. Older combined
source/workspace layouts remain readable for compatibility and small tests.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path, PureWindowsPath


APPLICATION_ROOT_ENV = "MEDIA_MANAGER_APPLICATION_ROOT"
WORKSPACE_ROOT_ENV = "MEDIA_MANAGER_WORKSPACE_ROOT"
_LEGACY_APPLICATION_ROOT_ENV = "NIMDA_APPLICATION_ROOT"
_LEGACY_WORKSPACE_ROOT_ENV = "NIMDA_WORKSPACE_ROOT"


def configured_root(primary: str, legacy: str) -> Path | None:
    """Resolve a named root; canonical/legacy conflicts fail before data access.

    An absent canonical value permits the old name. A present but empty or
    relative canonical value is an error, never a request to use another root.
    Empty legacy values retain their historical meaning of unconfigured.
    """
    canonical = os.environ.get(primary)
    previous = os.environ.get(legacy, "").strip()
    if canonical is None and not previous:
        return None

    def parse(value: str, name: str) -> Path:
        if not value.strip():
            raise ValueError(f"{name} must be a non-empty absolute path")
        path = Path(value.strip()).expanduser()
        if not path.is_absolute():
            raise ValueError(f"{name} must be an absolute path: {value}")
        return path.resolve()

    selected = parse(canonical, primary) if canonical is not None else parse(previous, legacy)
    if canonical is not None and previous and parse(previous, legacy) != selected:
        raise ValueError(f"{primary} conflicts with {legacy}; remove the old variable or select the same root")
    return selected


def configured_application_root() -> Path | None:
    return configured_root(APPLICATION_ROOT_ENV, _LEGACY_APPLICATION_ROOT_ENV)


def configured_workspace_root() -> Path | None:
    return configured_root(WORKSPACE_ROOT_ENV, _LEGACY_WORKSPACE_ROOT_ENV)


def code_repo_root() -> Path:
    """Backend code root for the framework app."""
    return Path(__file__).resolve().parents[1]


def _is_workspace(path: Path) -> bool:
    return all((path / name).is_dir() for name in ("config", "data"))


def application_root() -> Path:
    """Return the root containing the application's bundled ``apps`` resources."""
    root = configured_application_root()
    if root is not None:
        if not (root / "apps").is_dir():
            raise ValueError(
                f"{APPLICATION_ROOT_ENV} is not a valid media manager application root: {root} "
                "(missing apps)"
            )
        return root
    repo = code_repo_root()
    for candidate in (repo, *repo.parents):
        if (candidate / "apps" / "framework" / "backend").is_dir():
            return candidate
    raise ValueError(f"Set {APPLICATION_ROOT_ENV} to the application source/resource root (missing apps/framework/backend)")


def workspace_root() -> Path:
    """Return the mutable workspace containing shared config and data."""
    root = configured_workspace_root()
    if root is not None:
        missing = [name for name in ("config", "data") if not (root / name).is_dir()]
        if missing:
            raise ValueError(
                f"{WORKSPACE_ROOT_ENV} 不是有效的作品管理工作区：{root}（缺少 {', '.join(missing)}）"
            )
        return root
    source = application_root()
    if source.name == "media-manager" and source.parent.name == "plugins":
        candidate = source.parents[1] / "workspaces" / "media-manager"
        if _is_workspace(candidate):
            return candidate
    elif _is_workspace(source):
        # Compatibility for explicitly selected standalone combined workspaces.
        return source
    raise ValueError(f"Set {WORKSPACE_ROOT_ENV} to an external workspace containing config and data; no workspace was found")


def resolve_workspace_path(value: str | Path, *, resolve_links: bool = True) -> Path:
    """Resolve a configured path consistently, independent of the launch cwd."""
    path = Path(value).expanduser()
    windows_path = PureWindowsPath(path)
    if windows_path.drive and not windows_path.root:
        raise ValueError(f"配置路径不能使用驱动器相对路径，请使用完整路径：{value}")
    candidate = path if path.is_absolute() else workspace_root() / path
    # Security inspectors need the original link/reparse chain, not its target.
    return candidate.resolve() if resolve_links else Path(os.path.abspath(candidate))


def default_source_data_dir() -> Path:
    """Raw source data directory."""
    ws = workspace_root()
    modern = ws / "data" / "source"
    if modern.is_dir() or _is_workspace(ws):
        return modern
    return code_repo_root().parent / "Data"


def workspace_catalog_data_root() -> Path:
    """Legacy runtime catalog data root."""
    ws = workspace_root()
    modern = ws / "data" / "work-catalog"
    if modern.is_dir() or _is_workspace(ws):
        return modern
    return code_repo_root().parent / "work-catalog-data"


def workspace_config_root() -> Path:
    """Workspace-level application configuration root."""
    ws = workspace_root()
    modern = ws / "config"
    if modern.is_dir() or _is_workspace(ws):
        return modern
    return workspace_catalog_data_root() / "Config"


def framework_frontend_root() -> Path:
    return application_root() / "apps" / "framework" / "frontend"


def framework_config_path() -> Path:
    return workspace_config_root() / "framework" / "app.yaml"


def feature_root(feature_id: str) -> Path:
    return application_root() / "apps" / "features" / feature_id


def feature_frontend_root(feature_id: str) -> Path:
    return feature_root(feature_id) / "frontend"


def feature_backend_root(feature_id: str) -> Path:
    return feature_root(feature_id) / "backend"


def feature_config_path(feature_id: str) -> Path:
    return workspace_config_root() / "features" / feature_id / "config.yaml"


def feature_data_root(feature_id: str) -> Path:
    return workspace_root() / "data" / "features" / feature_id


def backend_roots(root: Path | None = None) -> tuple[Path, ...]:
    """Discover framework and feature backends for runtime and packaging."""
    app_root = root if root is not None else application_root()
    candidates = (
        app_root / "apps" / "framework" / "backend",
        app_root / "apps" / "composition" / "backend",
        *sorted((app_root / "apps" / "features").glob("*/backend")),
    )
    return tuple(path.resolve() for path in candidates if path.is_dir())


def ensure_feature_backend_paths(root: Path | None = None) -> None:
    for backend in backend_roots(root):
        s = str(backend.resolve())
        if s not in sys.path:
            sys.path.insert(0, s)


def workspace_parsed_yaml_dir() -> Path:
    """Parsed JP TV YAML DB directory."""
    modern = feature_data_root("collection-detail") / "db"
    if modern.is_dir() or _is_workspace(workspace_root()):
        return modern
    return workspace_catalog_data_root() / "DB"
