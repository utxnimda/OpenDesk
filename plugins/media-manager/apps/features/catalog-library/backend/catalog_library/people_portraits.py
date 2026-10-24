"""Bounded explicit portrait synchronization; never invoked by local views."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from copy import deepcopy
import time

from people_catalog.model import source_identity
from work_catalog_yaml.operation_progress import report_progress

from . import people_assets, providers

PORTRAIT_BUDGET = 60


def _image_fallback(row, fallback):
    result = {**fallback, **row}
    images = row.get("images")
    # An API images object with four empty strings also means "unavailable".
    # Keep malformed non-dicts intact so the URL adapter can diagnose them.
    missing = images is None or isinstance(images, dict) and not any(
        images.get(key) for key in ("medium", "large", "common", "small", "grid"))
    if missing:
        result["images"] = fallback.get("images")
    return result


def prepare(snapshot, repository, warn, *, deadline=None):
    raw = snapshot["raw"]
    fallback = {}
    for kind, rows in (("character", raw["characters"]), ("person", raw["persons"])):
        for row in rows:
            key = f"{kind}:{row['id']}"
            fallback[key] = _image_fallback(row, fallback.get(key, {}))
            if kind == "character":
                for actor in row.get("actors", []):
                    actor_key = f"person:{actor['id']}"
                    fallback[actor_key] = _image_fallback(actor, fallback.get(actor_key, {}))
    candidates = {**fallback}
    for key, detail in raw["details"].items():
        candidates[key] = _image_fallback(deepcopy(detail), fallback.get(key, {}))
    avatars, by_url = {}, {}
    for key, row in sorted(candidates.items()):
        avatars[key] = None
        try:
            url = people_assets.select_source_url(row)
        except (ValueError, OSError) as exc:
            warn(key, "avatar", exc)
            continue
        if url:
            by_url.setdefault(url, []).append((key, row))
    if not by_url:
        snapshot["avatars"] = avatars
        return
    deadline = min(time.monotonic() + PORTRAIT_BUDGET, deadline) if deadline is not None else time.monotonic() + PORTRAIT_BUDGET

    def acquire(rows):
        key, row = rows[0]
        previous = None
        for candidate_key, _candidate in rows:
            kind, external_id = candidate_key.split(":", 1)
            entity = repository.read(source_identity("bangumi", kind, external_id))
            cached = (entity or {}).get("avatar")
            if cached and cached.get("source_url") == people_assets.select_source_url(row):
                try:
                    people_assets.read_avatar(cached["asset_id"], root=repository.root)
                    return rows, cached, None
                except (ValueError, OSError, RuntimeError):
                    previous = cached
        try:
            avatar = people_assets.acquire_avatar(row, user_agent=providers.USER_AGENT,
                root=repository.root, deadline=deadline, previous=previous)
            return rows, avatar, None
        except (ValueError, OSError, RuntimeError) as exc:
            # Optional image failures cannot discard existing local portraits.
            return rows, None, exc

    completed = 0
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="nimda-portraits") as pool:
        futures = [pool.submit(copy_context().run, acquire, rows) for rows in by_url.values()]
        for future in as_completed(futures):
            rows, avatar, error = future.result()
            for key, _row in rows:
                avatars[key] = avatar
                if error:
                    warn(key, "avatar", error)
            completed += 1
            report_progress(f"已检查人物图片 {completed}/{len(by_url)}", completed=completed, total=len(by_url), unit="图片")
    snapshot["avatars"] = avatars
