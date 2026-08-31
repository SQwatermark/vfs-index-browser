"""HTTP API 路由分派；这里只决定入口，不实现领域行为。"""

from __future__ import annotations

from typing import Mapping, Sequence


GET_WITHOUT_INDEX = {
    "/api/health": ("handle_health", False),
    "/api/task": ("handle_task_status", True),
    "/api/task-artifact": ("handle_task_artifact", True),
}

GET_WITH_INDEX = {
    "/api/manifest": ("handle_manifest", False),
    "/api/list": ("handle_list", True),
    "/api/search": ("handle_search", True),
    "/api/manifest-assets/by-name": ("handle_manifest_assets_by_name", True),
    "/api/projectile": ("handle_projectile", True),
    "/api/audio-dialog/list": ("handle_audio_dialog_list", True),
    "/api/audio-dialog/entry": ("handle_audio_dialog_entry", True),
    "/api/audio-dialog/preview": ("handle_audio_dialog_preview", True),
    "/api/audio-dialog/raw": ("handle_audio_dialog_raw", True),
    "/api/wwise/list": ("handle_wwise_list", True),
    "/api/wwise/preview": ("handle_wwise_preview", True),
    "/api/wwise/raw": ("handle_wwise_raw", True),
    "/api/file": ("handle_file", True),
    "/api/preview": ("handle_preview", True),
    "/api/raw": ("handle_raw", True),
    "/api/manifest-asset/preview": ("handle_manifest_asset_preview", True),
    "/api/manifest-asset/raw": ("handle_manifest_asset_raw", True),
    "/api/manifest-asset/avatar-plan": ("handle_manifest_asset_avatar_plan", True),
    "/api/manifest-asset/model": ("handle_manifest_asset_model", True),
    "/api/manifest-asset/model-buffer": ("handle_manifest_asset_model_buffer", True),
    "/api/manifest-asset/model-texture": ("handle_manifest_asset_model_texture", True),
    "/api/manifest-asset/model-glb": ("handle_manifest_asset_model_glb", True),
    "/api/manifest-asset/model-blend": ("handle_manifest_asset_model_blend", True),
    "/api/manifest-asset/model-animation": ("handle_manifest_asset_model_animation", True),
    "/api/manifest-asset/model-animations": ("handle_manifest_asset_model_animations", True),
    "/api/tablecfg/json": ("handle_tablecfg_json", True),
    "/api/memorypack/json": ("handle_memorypack_json", True),
    "/api/internal/list": ("handle_internal_list", True),
    "/api/internal/preview": ("handle_internal_preview", True),
    "/api/internal/raw": ("handle_internal_raw", True),
}

POST_WITH_INDEX = {
    "/api/tasks/projectile": "handle_start_projectile_task",
    "/api/tasks/model": "handle_start_model_task",
    "/api/tasks/model-blend": "handle_start_model_blend_task",
    "/api/tasks/model-animation": "handle_start_model_animation_task",
}


def _invoke(handler, route: tuple[str, bool], query: Mapping[str, Sequence[str]]) -> None:
    method_name, accepts_query = route
    method = getattr(handler, method_name)
    method(query) if accepts_query else method()


def dispatch_get(
    handler,
    path: str,
    query: Mapping[str, Sequence[str]],
) -> bool:
    """分派 GET；返回 False 表示应继续按静态文件处理。"""

    route = GET_WITHOUT_INDEX.get(path)
    if route is not None:
        _invoke(handler, route, query)
        return True

    if path.startswith("/api/") and not handler.require_current_index():
        return True
    if path.startswith("/api/akedb-compatible/"):
        handler.handle_akedb_compatible(path)
        return True

    route = GET_WITH_INDEX.get(path)
    if route is None:
        return False
    _invoke(handler, route, query)
    return True


def dispatch_post(handler, path: str) -> bool:
    """分派 POST；未知路由由调用者统一返回 404。"""

    if path.startswith("/api/") and not handler.require_current_index():
        return True
    method_name = POST_WITH_INDEX.get(path)
    if method_name is None:
        return False
    getattr(handler, method_name)()
    return True
