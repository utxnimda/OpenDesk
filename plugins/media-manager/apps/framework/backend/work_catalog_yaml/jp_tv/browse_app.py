"""Compatibility import only; application routes live in apps/composition.

Existing CLI imports and API test patches retain their module identity. New
entrypoints should import media_manager_app.application directly.
"""
import sys
from work_catalog_yaml.layout import ensure_feature_backend_paths

ensure_feature_backend_paths()
from media_manager_app import application as _implementation

sys.modules[__name__] = _implementation
