from __future__ import annotations

import argparse
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from work_catalog_yaml.catalog import Catalog, CatalogFile, materialize_catalog
from work_catalog_yaml.cli import _cmd_jp_tv_materialize
from work_catalog_yaml.jp_tv.browse_settings import browse_config_resolve_path, load_jp_tv_browse_settings
from work_catalog_yaml.layout import (
    application_root,
    backend_roots,
    default_source_data_dir,
    resolve_workspace_path,
    workspace_parsed_yaml_dir,
    workspace_root,
)
from work_catalog_yaml.paths import resolve_output_path


class WorkspaceLayoutTest(unittest.TestCase):
    def test_unconfigured_media_defaults_stay_in_workspace_and_explicit_paths_win(self):
        from collection_detail import link_index
        from collection_info import service as collection_info
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "config").mkdir()
            (root / "data").mkdir()
            with patch.dict(os.environ, {"MEDIA_MANAGER_WORKSPACE_ROOT": str(root), "JP_TV_COLLECTION_FINISH_DIR": ""}), \
                 patch.object(link_index, "_feature_config", return_value={}), \
                 patch.object(collection_info, "_feature_config_paths", return_value={}):
                self.assertEqual(link_index.resource_roots(), [root / "data/media"])
                self.assertEqual(link_index.shortcut_root(), root / "data/shortcuts")
                self.assertEqual(collection_info.collection_finish_dir(), root / "data/finished")
                explicit = root / "explicit-media"
                with patch.object(link_index, "_feature_config", return_value={"paths": {
                    "resource_roots": [str(explicit)], "shortcut_root": str(explicit / "shortcuts")}}), \
                     patch.object(collection_info, "_feature_config_paths", return_value={"finish_dir": str(explicit / "finished")}):
                    self.assertEqual(link_index.resource_roots(), [explicit])
                    self.assertEqual(link_index.shortcut_root(), explicit / "shortcuts")
                    self.assertEqual(collection_info.collection_finish_dir(), explicit / "finished")

    def test_monorepo_source_and_workspace_are_independent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "plugins" / "media-manager"
            backend = source / "apps" / "framework" / "backend"
            backend.mkdir(parents=True)
            workspace = root / "workspaces" / "media-manager"
            with patch.dict(os.environ, {}, clear=True), patch(
                "work_catalog_yaml.layout.code_repo_root", return_value=backend
            ):
                self.assertEqual(application_root(), source)
                with self.assertRaisesRegex(ValueError, "external workspace"):
                    workspace_root()
                (workspace / "config").mkdir(parents=True)
                (workspace / "data").mkdir()
                self.assertEqual(workspace_root(), workspace)
                self.assertEqual(application_root(), source)

    def test_renamed_workspace_and_missing_default_directories(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "renamed-project"
            backend = root / "apps" / "framework" / "backend"
            backend.mkdir(parents=True)
            (root / "config").mkdir()
            (root / "data").mkdir()
            with patch.dict(os.environ, {}, clear=True), patch(
                "work_catalog_yaml.layout.code_repo_root", return_value=backend
            ):
                self.assertEqual(workspace_root(), root)
                self.assertEqual(default_source_data_dir(), root / "data" / "source")
                self.assertEqual(workspace_parsed_yaml_dir(), root / "data/features/collection-detail/db")

    def test_configured_relative_paths_do_not_depend_on_current_directory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "config").mkdir()
            database = root / "data" / "catalog"
            database.mkdir(parents=True)
            (database / "works.yaml").write_text("[]\n", encoding="utf-8")
            config = root / "config" / "browse.yaml"
            config.write_text("paths:\n  filesystem_root: data/catalog\n", encoding="utf-8")
            with patch.dict(os.environ, {"MEDIA_MANAGER_WORKSPACE_ROOT": str(root)}):
                settings = load_jp_tv_browse_settings(config)
                self.assertEqual(settings.filesystem_root, database.resolve())
                self.assertEqual(settings.resolved_catalog_yaml_paths, (str((database / "works.yaml").resolve()),))
                self.assertEqual(resolve_workspace_path("~/Nimda"), (Path.home() / "Nimda").resolve())
                with self.assertRaisesRegex(ValueError, "驱动器相对路径"):
                    resolve_workspace_path("S:media")

    def test_environment_roots_are_absolute_and_legacy_conflicts_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "apps").mkdir()
            (root / "config").mkdir()
            (root / "data").mkdir()
            for suffix, reader in (("APPLICATION_ROOT", application_root), ("WORKSPACE_ROOT", workspace_root)):
                canonical, legacy = f"MEDIA_MANAGER_{suffix}", f"NIMDA_{suffix}"
                with self.subTest(root=suffix), patch.dict(os.environ, {}, clear=True):
                    os.environ[legacy] = str(root)
                    self.assertEqual(reader(), root)
                    os.environ[canonical] = str(root)
                    self.assertEqual(reader(), root)
                    os.environ[canonical] = str(root / "different")
                    with self.assertRaisesRegex(ValueError, "conflicts"):
                        reader()
                    for invalid in ("", ".", "relative/root", "S:relative"):
                        os.environ[canonical] = invalid
                        with self.assertRaisesRegex(ValueError, "absolute"):
                            reader()

    def test_missing_external_workspace_never_selects_source_or_ancestor_data(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "plugins" / "media-manager"
            backend = source / "apps" / "framework" / "backend"
            backend.mkdir(parents=True)
            for candidate in (root, source):
                (candidate / "config").mkdir()
                (candidate / "data").mkdir()
            with patch.dict(os.environ, {}, clear=True), patch(
                "work_catalog_yaml.layout.code_repo_root", return_value=backend
            ):
                with self.assertRaisesRegex(ValueError, "external workspace"):
                    workspace_root()

    def test_browse_config_ignores_unrelated_cwd_and_parent_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            workspace = base / "workspace"
            config = workspace / "config" / "features" / "collection-detail" / "config.yaml"
            config.parent.mkdir(parents=True)
            (workspace / "data").mkdir()
            config.write_text("version: 1\npaths: {}\n", encoding="utf-8")
            unrelated = base / "another-app"
            child = unrelated / "nested"
            child.mkdir(parents=True)
            (unrelated / "jp-tv-browse.config.yaml").write_text("version: 999\n", encoding="utf-8")
            parent_config = unrelated / "config" / "features" / "collection-detail" / "config.yaml"
            parent_config.parent.mkdir(parents=True)
            parent_config.write_text("version: 999\n", encoding="utf-8")
            with patch.dict(os.environ, {"MEDIA_MANAGER_WORKSPACE_ROOT": str(workspace)}, clear=True):
                for cwd in (unrelated, child):
                    with patch("pathlib.Path.cwd", return_value=cwd):
                        self.assertEqual(browse_config_resolve_path(None), config)
                config.unlink()
                with patch("pathlib.Path.cwd", return_value=child):
                    self.assertEqual(browse_config_resolve_path(None).name, "browse_config.default.yaml")

    def test_explicit_browse_config_failure_never_uses_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td)
            (workspace / "config").mkdir()
            (workspace / "data").mkdir()
            with patch.dict(os.environ, {"MEDIA_MANAGER_WORKSPACE_ROOT": str(workspace)}, clear=True):
                with self.assertRaisesRegex(ValueError, "显式浏览配置"):
                    load_jp_tv_browse_settings(workspace / "missing.yaml")
                os.environ["JP_TV_BROWSE_CONFIG_PATH"] = "missing.yaml"
                with self.assertRaisesRegex(ValueError, "显式浏览配置"):
                    load_jp_tv_browse_settings()
                os.environ["JP_TV_BROWSE_CONFIG_PATH"] = ""
                with self.assertRaisesRegex(ValueError, "不能为空"):
                    load_jp_tv_browse_settings()

    def test_existing_configs_reject_corruption_and_unknown_versions(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td)
            (workspace / "data").mkdir()
            browse = workspace / "config" / "features" / "collection-detail" / "config.yaml"
            browse.parent.mkdir(parents=True)
            framework = workspace / "config" / "framework" / "app.yaml"
            framework.parent.mkdir(parents=True)
            with patch.dict(os.environ, {"MEDIA_MANAGER_WORKSPACE_ROOT": str(workspace)}, clear=True):
                for selected in (browse, framework):
                    browse.write_text("paths: {}\n", encoding="utf-8")
                    framework.write_text("app: {}\n", encoding="utf-8")
                    self.assertEqual(load_jp_tv_browse_settings().version, 1)
                    for invalid in ("version: 99\n", "version: true\n", "version: '1'\n", "[]\n", "invalid: [\n"):
                        selected.write_text(invalid, encoding="utf-8")
                        original = selected.read_bytes()
                        with self.subTest(file=selected.name, invalid=invalid), self.assertRaises(ValueError):
                            load_jp_tv_browse_settings()
                        self.assertEqual(selected.read_bytes(), original)
                framework.unlink()
                browse.write_text("version: 1\npaths: {}\n", encoding="utf-8")
                self.assertEqual(load_jp_tv_browse_settings().version, 1)

    def test_backend_discovery_includes_new_features_without_a_fixed_list(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            framework = root / "apps/framework/backend"
            composition = root / "apps/composition/backend"
            feature = root / "apps/features/new-feature/backend"
            framework.mkdir(parents=True)
            composition.mkdir(parents=True)
            feature.mkdir(parents=True)
            (root / "apps/features/frontend-only/frontend").mkdir(parents=True)
            self.assertEqual(backend_roots(root), (framework.resolve(), composition.resolve(), feature.resolve()))


class ExportPathTest(unittest.TestCase):
    def test_valid_export_preserves_content_and_windows_separators(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "Data" / "日本" / "works.txt"
            result = materialize_catalog(Catalog(files=[CatalogFile("日本\\works.txt", "作品\n")]), td)
            self.assertEqual(result, [target])
            self.assertEqual(target.read_text(encoding="utf-8"), "作品\n")

    def test_unsafe_batch_is_rejected_before_any_file_is_written(self) -> None:
        for path in ("../escape.txt", "nested/../../escape.txt", "/absolute.txt", "C:\\escape.txt", "C:escape.txt", "\\\\server\\share\\file", "file.txt:stream"):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                catalog = Catalog(files=[CatalogFile("valid.txt", "first"), CatalogFile(path, "bad")])
                with self.assertRaises(ValueError):
                    materialize_catalog(catalog, root)
                self.assertEqual(list(root.iterdir()), [])

    def test_absolute_catalog_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                materialize_catalog(Catalog(root="/", files=[CatalogFile("file.txt", "bad")]), td)
            self.assertEqual(list(Path(td).iterdir()), [])

    def test_duplicate_and_file_directory_conflicts_are_preflighted(self) -> None:
        for paths in (("same.txt", "./same.txt"), ("parent", "parent/child.txt")):
            with self.subTest(paths=paths), tempfile.TemporaryDirectory() as td:
                with self.assertRaises(ValueError):
                    materialize_catalog(Catalog(files=[CatalogFile(p, "value") for p in paths]), td)
                self.assertEqual(list(Path(td).iterdir()), [])

    def test_existing_directory_conflict_does_not_partially_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Data" / "occupied").mkdir(parents=True)
            existing = root / "Data" / "first.txt"
            existing.write_text("original", encoding="utf-8")
            with self.assertRaises(ValueError):
                materialize_catalog(Catalog(files=[CatalogFile("first.txt", "new"), CatalogFile("occupied", "bad")]), root)
            self.assertEqual(existing.read_text(encoding="utf-8"), "original")

    def test_resolved_path_cannot_escape_through_a_link(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "output"
            outside = Path(td) / "outside"
            root.mkdir()
            outside.mkdir()
            link = root / "linked"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("Creating symlinks is not permitted on this host")
            with self.assertRaises(ValueError):
                resolve_output_path(root, "linked/file.txt")

    def test_jp_tv_export_uses_the_same_path_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            args = argparse.Namespace(input="unused.yaml", output=td, relative_path="../escape.txt", data_root="Data")
            with patch("work_catalog_yaml.cli.load_jp_tv_yaml_file", return_value=[]):
                with self.assertRaises(ValueError):
                    _cmd_jp_tv_materialize(args)
            self.assertEqual(list(Path(td).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
