"""ASGI composition: routes, static resources, local-origin checks and worker lifecycle."""
from __future__ import annotations

import os
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route, Router
from starlette.staticfiles import StaticFiles

from work_catalog_yaml.api_runtime import api_health, api_lifespan, install_api_queues
from work_catalog_yaml.operation_progress import (api_operation_progress, api_operation_history, api_operation_log,
    api_operation_log_search, api_operation_log_file, api_operation_log_open, api_client_operation)
from work_catalog_yaml.layout import ensure_feature_backend_paths, feature_frontend_root, framework_frontend_root, workspace_root
from work_catalog_yaml.jp_tv.browse_security import is_loopback_hostname, mutation_source_is_allowed
ensure_feature_backend_paths()


def _resolve_browse_static_dir() -> Path:
    raw = os.environ.get("JP_TV_BROWSE_STATIC_DIR", "").strip()
    if raw:
        p = Path(raw).expanduser().resolve()
        if not p.is_dir():
            raise ValueError(
                f"JP_TV_BROWSE_STATIC_DIR / --browse-static 不是目录：{p}（应指向 apps/framework/frontend）"
            )
        return p
    return framework_frontend_root()


def _resolve_feature_frontend_dir(feature_id: str) -> Path:
    p = feature_frontend_root(feature_id).resolve()
    if not p.is_dir():
        raise ValueError(f"feature frontend 目录不存在：{p}")
    return p


def browse_static_root() -> Path:
    """当前实际挂载的 ``browse_static`` 目录（用于日志 / 排错）。"""
    return _resolve_browse_static_dir()


class _BrowseNoCacheStaticMiddleware(BaseHTTPMiddleware):
    """避免浏览器长期缓存旧 ``browse.js`` / ``index.html`` 导致页面行为与仓库不一致。"""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        response = await call_next(request)
        if request.url.path.startswith("/api"):
            return response
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response


class _BrowseLocalApiSecurityMiddleware(BaseHTTPMiddleware):
    """Keep the local filesystem API local and reject cross-site mutations."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        allow_remote = os.environ.get("JP_TV_BROWSE_ALLOW_REMOTE", "").strip() == "1"
        if not allow_remote and not is_loopback_hostname(request.url.hostname):
            return JSONResponse(
                {"ok": False, "error": "仅允许通过 localhost / 环回地址访问本地应用"},
                status_code=403,
            )

        is_api_mutation = request.url.path.startswith("/api") and request.method.upper() not in {
            "GET",
            "HEAD",
            "OPTIONS",
        }
        is_progress_request = request.url.path == "/api/operations" or request.url.path.startswith("/api/operations/") or (
            request.url.path.startswith("/api") and "x-nimda-operation-id" in request.headers
        )
        if is_api_mutation or is_progress_request:
            fetch_site = request.headers.get("sec-fetch-site", "").strip().casefold()
            if fetch_site == "cross-site":
                return JSONResponse(
                    {"ok": False, "error": "已拒绝跨站 API 请求"},
                    status_code=403,
                )
            origin = request.headers.get("origin", "").strip()
            if not mutation_source_is_allowed(
                fetch_site=fetch_site,
                origin=origin,
                request_scheme=request.url.scheme,
                request_host=request.headers.get("host", ""),
            ):
                return JSONResponse(
                    {"ok": False, "error": "API 请求来源与当前应用不一致"},
                    status_code=403,
                )

        return await call_next(request)


def build_jp_tv_browse_app(*, operation_log_root: Path | None = None) -> Starlette:
    # Some legacy feature adapters initialize workspace-scoped services on
    # import. Load them only after the runtime has selected its workspace.
    from collection_detail import web as api
    from collection_info import web as info_api
    from directory_organizer import web as organizer_api
    from catalog_library import web as library_api

    static_dir = str(_resolve_browse_static_dir())
    collection_detail_frontend = str(_resolve_feature_frontend_dir("collection-detail"))
    collection_info_frontend = str(_resolve_feature_frontend_dir("collection-info"))
    organizer_frontend = str(_resolve_feature_frontend_dir("directory-organizer"))
    library_frontend = str(_resolve_feature_frontend_dir("catalog-library"))
    people_frontend = str(_resolve_feature_frontend_dir("people-catalog"))
    jp_tv_browse_api = Router(
        routes=[
            Route("/health", endpoint=api_health, methods=["GET"]),
            Route("/catalog-library/browse", endpoint=library_api.browse, methods=["POST"]),
            Route("/catalog-library/detail", endpoint=library_api.detail, methods=["POST"]),
            Route("/catalog-library/characters/merge/candidates", endpoint=library_api.character_merge_candidates, methods=["POST"]),
            Route("/catalog-library/characters/merge/preview", endpoint=library_api.character_merge_preview, methods=["POST"]),
            Route("/catalog-library/characters/merge/apply", endpoint=library_api.character_merge_apply, methods=["POST"]),
            Route("/catalog-library/characters/materialize/preview", endpoint=library_api.role_catalog_preview, methods=["POST"]),
            Route("/catalog-library/characters/materialize/apply", endpoint=library_api.role_catalog_apply, methods=["POST"]),
            Route("/catalog-library/people-library/browse", endpoint=library_api.people_library_browse, methods=["POST"]),
            Route("/catalog-library/people-library/detail", endpoint=library_api.people_library_detail, methods=["POST"]),
            Route("/catalog-library/people-library/edit-preview", endpoint=library_api.people_library_edit_preview, methods=["POST"]),
            Route("/catalog-library/people-library/edit-apply", endpoint=library_api.people_library_edit_apply, methods=["POST"]),
            Route("/catalog-library/people-library/clear-preview", endpoint=library_api.people_library_clear_preview, methods=["POST"]),
            Route("/catalog-library/people-library/clear-apply", endpoint=library_api.people_library_clear_apply, methods=["POST"]),
            Route("/catalog-library/people-library/source-preview", endpoint=library_api.people_library_source_preview, methods=["POST"]),
            Route("/catalog-library/people-library/source-apply", endpoint=library_api.people_library_source_apply, methods=["POST"]),
            Route("/catalog-library/people-library/image-preview", endpoint=library_api.people_library_image_preview, methods=["POST"]),
            Route("/catalog-library/people-library/image-candidates", endpoint=library_api.people_library_image_candidates, methods=["POST"]),
            Route("/catalog-library/people-library/image-apply", endpoint=library_api.people_library_image_apply, methods=["POST"]),
            Route("/catalog-library/work-details/clear-preview", endpoint=library_api.work_details_clear_preview, methods=["POST"]),
            Route("/catalog-library/work-details/clear-apply", endpoint=library_api.work_details_clear_apply, methods=["POST"]),
            Route("/catalog-library/display-settings", endpoint=library_api.display_settings_read, methods=["POST"]),
            Route("/catalog-library/display-settings/save", endpoint=library_api.display_settings_save, methods=["POST"]),
            Route("/catalog-library/playback/scan", endpoint=library_api.playback_scan, methods=["POST"]),
            Route("/catalog-library/playback/save", endpoint=library_api.playback_save, methods=["POST"]),
            Route("/catalog-library/playback/open", endpoint=library_api.playback_open, methods=["POST"]),
            Route("/catalog-library/playback/select", endpoint=library_api.playback_select, methods=["POST"]),
            Route("/catalog-library/people/preview", endpoint=library_api.people_preview, methods=["POST"]),
            Route("/catalog-library/people/apply", endpoint=library_api.people_apply, methods=["POST"]),
            Route("/catalog-library/people/selection-target", endpoint=library_api.people_selection_target, methods=["POST"]),
            Route("/catalog-library/people/retry", endpoint=library_api.people_retry_preview, methods=["POST"]),
            Route("/catalog-library/people/wikipedia-preview", endpoint=library_api.people_wikipedia_preview, methods=["POST"]),
            Route("/catalog-library/douban/browser/start", endpoint=library_api.douban_browser_start, methods=["POST"]),
            Route("/catalog-library/douban/browser/step", endpoint=library_api.douban_browser_step, methods=["POST"]),
            Route("/catalog-library/douban/browser/cancel", endpoint=library_api.douban_browser_cancel, methods=["POST"]),
            Route("/catalog-library/people/birthdays/preview", endpoint=library_api.people_birthdays_preview, methods=["POST"]),
            Route("/catalog-library/people/birthdays/apply", endpoint=library_api.people_birthdays_apply, methods=["POST"]),
            Route("/catalog-library/people/assets/{filename}", endpoint=library_api.people_asset, methods=["GET", "HEAD"]),
            Route("/catalog-library/classifications", endpoint=library_api.classifications, methods=["GET"]),
            Route("/catalog-library/classifications", endpoint=library_api.save_classification, methods=["POST"]),
            Route("/catalog-library/classification-edits/preview", endpoint=library_api.classification_preview, methods=["POST"]),
            Route("/catalog-library/identities/preview", endpoint=library_api.identities_preview, methods=["POST"]),
            Route("/catalog-library/identities/apply", endpoint=library_api.edits_apply, methods=["POST"]),
            Route("/catalog-library/edits/preview", endpoint=library_api.edits_preview, methods=["POST"]),
            Route("/catalog-library/edits/apply", endpoint=library_api.edits_apply, methods=["POST"]),
            Route("/catalog-library/provider/search", endpoint=library_api.provider_search, methods=["POST"]),
            Route("/catalog-library/provider/preview", endpoint=library_api.provider_preview, methods=["POST"]),
            Route("/catalog-library/provider/cover-preview", endpoint=library_api.provider_cover_preview, methods=["POST"]),
            Route("/catalog-library/provider/apply", endpoint=library_api.provider_apply, methods=["POST"]),
            Route("/catalog-library/provider/sync-preview", endpoint=library_api.provider_sync_preview, methods=["POST"]),
            Route("/catalog-library/provider/sync-apply", endpoint=library_api.provider_sync_apply, methods=["POST"]),
            Route("/catalog-library/provider/binding-update", endpoint=library_api.provider_binding_update, methods=["POST"]),
            Route("/catalog-library/assets/{filename}", endpoint=library_api.asset, methods=["GET", "HEAD"]),
            Route("/directory-organizer/scan", endpoint=organizer_api.scan, methods=["POST"]),
            Route("/directory-organizer/preview", endpoint=organizer_api.preview, methods=["POST"]),
            Route("/directory-organizer/execute", endpoint=organizer_api.execute, methods=["POST"]),
            Route("/directory-organizer/completion", endpoint=organizer_api.set_completion, methods=["POST"]),
            Route("/directory-organizer/catalog", endpoint=organizer_api.catalog_search, methods=["POST"]),
            Route("/directory-organizer/shortcuts", endpoint=organizer_api.shortcut_action, methods=["POST"]),
            Route("/directory-organizer/choose-directory", endpoint=organizer_api.choose_folder, methods=["POST"]),
            Route("/operations", endpoint=api_operation_history, methods=["GET"]),
            Route("/operations/client-events", endpoint=api_client_operation, methods=["POST"]),
            Route("/operations/{operation_id}/log/search", endpoint=api_operation_log_search, methods=["GET"]),
            Route("/operations/{operation_id}/log/file", endpoint=api_operation_log_file, methods=["GET"]),
            Route("/operations/{operation_id}/log/open", endpoint=api_operation_log_open, methods=["POST"]),
            Route("/operations/{operation_id}/log", endpoint=api_operation_log, methods=["GET"]),
            Route("/operations/{operation_id}", endpoint=api_operation_progress, methods=["GET"]),
            Route("/config", endpoint=api._get_config_api, methods=["GET"]),
            Route("/browse/default", endpoint=api._get_browse_default_api, methods=["GET"]),
            Route("/browse/catalog", endpoint=api._post_browse_catalog_api, methods=["POST"]),
            Route("/browse/save", endpoint=api._post_browse_save_api, methods=["POST"]),
            Route(
                "/collection-detail/work/detail",
                endpoint=api._post_collection_detail_work_detail_api,
                methods=["POST"],
            ),
            Route("/config/enum-edits", endpoint=api._post_config_enum_edits_api, methods=["POST"]),
            Route("/collection-info", endpoint=info_api._get_collection_records_api, methods=["GET"]),
            Route("/collection-info", endpoint=info_api._post_collection_records_api, methods=["POST"]),
            Route("/collection-records", endpoint=info_api._get_collection_records_api, methods=["GET"]),
            Route("/collection-records", endpoint=info_api._post_collection_records_api, methods=["POST"]),
            Route(
                "/collection-detail/library-status",
                endpoint=api._get_collection_detail_library_status_api,
                methods=["GET"],
            ),
            Route(
                "/collection-detail/link-index",
                endpoint=api._get_collection_detail_link_index_api,
                methods=["GET"],
            ),
            Route(
                "/collection-detail/link-index/preview",
                endpoint=api._post_collection_detail_link_index_preview_api,
                methods=["POST"],
            ),
            Route(
                "/collection-detail/link-index/save",
                endpoint=api._post_collection_detail_link_index_save_api,
                methods=["POST"],
            ),
            Route(
                "/collection-detail/link-index/generate",
                endpoint=api._post_collection_detail_link_index_generate_api,
                methods=["POST"],
            ),
            Route(
                "/collection-detail/link-index/generate-files",
                endpoint=api._post_collection_detail_link_index_generate_files_api,
                methods=["POST"],
            ),
            Route(
                "/collection-detail/link-index/validate",
                endpoint=api._post_collection_detail_link_index_validate_api,
                methods=["POST"],
            ),
            Route(
                "/collection-detail/link-index/open",
                endpoint=api._post_collection_detail_link_index_open_api,
                methods=["POST"],
            ),
            Route(
                "/collection-detail/press/open",
                endpoint=api._post_collection_detail_press_open_api,
                methods=["POST"],
            ),
            Route(
                "/collection-detail/link-index/resolve",
                endpoint=api._post_collection_detail_link_index_resolve_api,
                methods=["POST"],
            ),
            Route(
                "/collection-detail/link-index/fixes/apply",
                endpoint=api._post_collection_detail_link_index_fixes_apply_api,
                methods=["POST"],
            ),
            Route(
                "/collection-detail/resource-libraries/config",
                endpoint=api._post_collection_detail_resource_libraries_config_api,
                methods=["POST"],
            ),
            Route(
                "/collection-detail/resource-libraries/scan",
                endpoint=api._post_collection_detail_resource_libraries_scan_api,
                methods=["POST"],
            ),
            Route(
                "/collection-detail/resource-libraries/cache",
                endpoint=api._get_collection_detail_resource_libraries_cache_api,
                methods=["GET"],
            ),
            Route(
                "/collection-detail/resource-libraries/node",
                endpoint=api._get_collection_detail_resource_libraries_node_api,
                methods=["GET"],
            ),
            Route(
                "/collection-detail/resource-libraries/search",
                endpoint=api._get_collection_detail_resource_libraries_search_api,
                methods=["GET"],
            ),
            Route("/browse", endpoint=api._post_browse_api, methods=["POST"]),
        ],
    )
    routes = [
        Mount("/api", app=jp_tv_browse_api),
        Mount("/features/catalog-library", app=StaticFiles(directory=library_frontend), name="catalog_library_frontend"),
        Mount("/features/people-catalog", app=StaticFiles(directory=people_frontend), name="people_catalog_frontend"),
        Mount("/features/directory-organizer", app=StaticFiles(directory=organizer_frontend), name="directory_organizer_frontend"),
        Mount(
            "/features/collection-detail",
            app=StaticFiles(directory=collection_detail_frontend),
            name="collection_detail_frontend",
        ),
        Mount(
            "/features/collection-info",
            app=StaticFiles(directory=collection_info_frontend),
            name="collection_info_frontend",
        ),
        Mount(
            "/",
            app=StaticFiles(directory=static_dir, html=True),
            name="jp_tv_browse_static",
        ),
    ]
    app = Starlette(
        lifespan=api_lifespan,
        routes=routes,
        middleware=[
            Middleware(_BrowseLocalApiSecurityMiddleware),
            Middleware(_BrowseNoCacheStaticMiddleware),
        ],
    )
    install_api_queues(app, operation_log_root=operation_log_root)
    return app


# Resolve the mutable workspace only when a runtime explicitly requests the app.
# Build tools and API-factory tests may import composition without user data.
def get_application():
    global _application
    if _application is None:
        _application = build_jp_tv_browse_app(operation_log_root=workspace_root() / "data" / "framework" / "logs" / "operations")
    return _application


_application = None


def __getattr__(name):
    if name == "app":
        return get_application()
    raise AttributeError(name)
