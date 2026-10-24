"""Bounded local catalog inventory shared by person and character workflows."""
import hashlib

from collection_detail.catalog_repository import CatalogRepository
from people_catalog.repository import safe_path

from . import providers

MAX_PEOPLE = 10000
MAX_WORKS = 10000
MAX_TOTAL_BYTES = 64 * 1024 * 1024


def _hash(value):
    return hashlib.sha256(providers._json_bytes(value)).hexdigest()


def _work_versions(settings):
    paths = CatalogRepository(settings).catalog_paths()
    if len(paths) > 1000:
        raise ValueError("作品扫描文件数量超过安全限制")
    versions, total = {}, 0
    for path in paths:
        path = safe_path(path)
        if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("作品扫描文件无效或超过安全大小限制")
        with path.open("rb") as stream:
            content = stream.read(8 * 1024 * 1024 + 1)
        total += len(content)
        if len(content) > 8 * 1024 * 1024 or total > MAX_TOTAL_BYTES:
            raise ValueError("作品扫描超过安全大小限制")
        versions[str(path)] = hashlib.sha256(content).hexdigest()
    return _hash(versions)
