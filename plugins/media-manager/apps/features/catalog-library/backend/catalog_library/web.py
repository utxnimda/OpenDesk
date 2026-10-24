"""Thin HTTP composition for the local catalog and explicit provider imports."""
from __future__ import annotations

from starlette.responses import JSONResponse, Response

from work_catalog_yaml.api_runtime import json_endpoint, query_endpoint
from work_catalog_yaml.common.http import error_response
from work_catalog_yaml.jp_tv.browse_settings import get_resolved_browse_settings
from . import assets, character_merge, display_settings, douban_browser, people, people_assets, people_images, people_import, people_library, people_repair, people_retry, playback, providers, role_catalog, service, source_sync, work_details


def _call(handler, body):
    try:
        settings, _ = get_resolved_browse_settings()
        result = handler(body, settings=settings)
        return JSONResponse({"ok": True, **result}, headers={"Cache-Control": "no-store"})
    except (ValueError, OSError, RuntimeError) as exc:
        return error_response(
            str(exc), status_code=400 if isinstance(exc, ValueError) else 500,
            context={"stage": "作品库", "action": getattr(handler, "__name__", "读取或修改作品数据")},
        )


@json_endpoint
def browse(body):
    return _call(service.browse, body)


@json_endpoint
def detail(body):
    return _call(service.detail, body)


@json_endpoint
def people_library_browse(body):
    return _call(people_library.browse, body)


@json_endpoint
def character_merge_candidates(body):
    return _call(character_merge.candidates, body)


@json_endpoint
def character_merge_preview(body):
    return _call(character_merge.preview, body)


@json_endpoint
def character_merge_apply(body):
    return _call(character_merge.apply, body)


@json_endpoint
def role_catalog_preview(body):
    return _call(role_catalog.preview, body)


@json_endpoint
def role_catalog_apply(body):
    return _call(role_catalog.apply, body)


@json_endpoint
def people_library_detail(body):
    return _call(people_library.detail, body)


@json_endpoint
def people_library_edit_preview(body):
    return _call(people_library.edit_preview, body)


@json_endpoint
def people_library_edit_apply(body):
    return _call(people_library.edit_apply, body)


@json_endpoint
def people_library_clear_preview(body):
    return _call(people_library.clear_preview, body)


@json_endpoint
def people_library_clear_apply(body):
    return _call(people_library.clear_apply, body)


@json_endpoint(network=True)
def people_library_source_preview(body):
    return _call(people_library.source_preview, body)


@json_endpoint
def people_library_source_apply(body):
    return _call(people_library.source_apply, body)


@json_endpoint(network=True, max_body_bytes=people_images.MAX_REQUEST_BYTES)
def people_library_image_preview(body):
    return _call(people_library.image_preview, body)


@json_endpoint(network=True)
def people_library_image_candidates(body):
    return _call(people_library.image_candidates, body)


@json_endpoint
def people_library_image_apply(body):
    return _call(people_library.image_apply, body)


@json_endpoint
def work_details_clear_preview(body):
    return _call(work_details.preview, body)


@json_endpoint
def work_details_clear_apply(body):
    return _call(work_details.apply, body)


@json_endpoint
def display_settings_read(body):
    return _call(service.display_settings_payload, body)


@json_endpoint
def display_settings_save(body):
    return _call(display_settings.save, body)


@json_endpoint
def playback_scan(body):
    return _call(playback.scan, body)


@json_endpoint
def playback_save(body):
    return _call(playback.save, body)


@json_endpoint
def playback_open(body):
    return _call(playback.open_chapter, body)


@json_endpoint
def playback_select(body):
    return _call(playback.select, body)


@json_endpoint(network=True)
def people_preview(body):
    return _call(people.preview, body)


@json_endpoint(network=True)
def people_retry_preview(body):
    return _call(people_retry.retry, body)


@json_endpoint
def people_apply(body):
    return _call(people.apply, body)


@json_endpoint
def people_selection_target(body):
    return _call(people_import.target_preview, body)


@json_endpoint(network=True)
def people_wikipedia_preview(body):
    return _call(people_import.wikipedia_preview, body)


@json_endpoint
def people_birthdays_preview(body):
    return _call(people_repair.preview, body)


@json_endpoint(network=True)
def douban_browser_start(body):
    return _call(douban_browser.start, body)


@json_endpoint(network=True)
def douban_browser_step(body):
    return _call(douban_browser.step, body)


@json_endpoint(network=True)
def douban_browser_cancel(body):
    return _call(douban_browser.cancel, body)


@json_endpoint
def people_birthdays_apply(body):
    return _call(people_repair.apply, body)


@query_endpoint
def classifications(body):
    return _call(lambda _body, **kwargs: service.classifications_payload(**kwargs), body)


@json_endpoint
def save_classification(body):
    return _call(service.save_classification, body)


@json_endpoint
def classification_preview(body):
    return _call(service.preview_classification_edits, body)


@json_endpoint
def identities_preview(body):
    return _call(service.preview_identity_initialization, body)


@json_endpoint
def edits_preview(body):
    return _call(service.preview_edits, body)


@json_endpoint
def edits_apply(body):
    return _call(service.apply_edits, body)


@json_endpoint(network=True)
def provider_search(body):
    return _call(providers.search_provider, body)


@json_endpoint(network=True)
def provider_preview(body):
    return _call(providers.preview_provider_import, body)


@json_endpoint(network=True)
def provider_cover_preview(body):
    return _call(providers.preview_provider_cover, body)


@json_endpoint
def provider_apply(body):
    return _call(providers.apply_provider_import, body)


@json_endpoint(network=True)
def provider_sync_preview(body):
    return _call(source_sync.preview, body)


@json_endpoint
def provider_sync_apply(body):
    return _call(source_sync.apply, body)


@json_endpoint
def provider_binding_update(body):
    return _call(service.binding_update, body)


async def asset(request):
    """Never serves arbitrary files or performs network I/O on an image GET."""
    return await _local_image(request, assets.read_asset)


async def people_asset(request):
    return await _local_image(request, people_assets.read_avatar)


async def _local_image(request, reader):
    filename = request.path_params.get("filename", "")
    try:
        data, mime = await request.app.state.api_disk_queue.run(reader, filename)
    except (ValueError, OSError, RuntimeError):
        return Response(status_code=404, headers={"Cache-Control": "no-store"})
    etag = '"' + filename.split(".", 1)[0] + '"'
    headers = {"Cache-Control": "private, max-age=31536000, immutable", "ETag": etag,
               "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    headers["Content-Length"] = str(len(data))
    return Response(b"" if request.method == "HEAD" else data, media_type=mime, headers=headers)
