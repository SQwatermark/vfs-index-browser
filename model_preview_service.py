"""Application service assembling model preview documents and task progress."""

from __future__ import annotations

from pathlib import Path
from typing import Callable
from urllib.parse import quote

from manifest_asset_service import is_model_entry_path
from npc_avatar_config import is_avatar_mesh_asset_path


class ModelPreviewService:
    def __init__(
        self,
        avatar_builder: Callable,
        ordinary_builder: Callable,
        bundle_source_resolver: Callable,
        animation_hint_builder: Callable[[str, dict], str],
        blender_available: Callable[[], bool],
        base_glb_provider: Callable,
        preview_builder: Callable | None = None,
        *,
        glb_version: int,
        blend_version: int,
        animation_version: int,
        max_blend_animation_count: int,
    ) -> None:
        self._avatar = avatar_builder
        self._ordinary = ordinary_builder
        self._resolve_bundle_sources = bundle_source_resolver
        self._animation_hint = animation_hint_builder
        self._blender_available = blender_available
        self._base_glb = base_glb_provider
        self._preview = preview_builder or self.build
        self._glb_version = glb_version
        self._blend_version = blend_version
        self._animation_version = animation_version
        self._max_blend_animations = max_blend_animation_count

    def build(
        self,
        manifest_id: int,
        resolved: tuple,
        animation_resolved: tuple | None,
        lod: int,
        *,
        cancel_event: object | None = None,
        progress: Callable[[dict], None] | None = None,
    ) -> dict:
        index, asset, bundle_record, bundle_chunk = resolved
        asset_path = str(asset["path"])
        animation_asset = animation_resolved[1] if animation_resolved else None
        is_prefab = Path(asset_path).suffix.casefold() == ".prefab"
        is_avatar = is_avatar_mesh_asset_path(asset_path)
        if not is_model_entry_path(asset_path):
            raise ValueError("resource is not a supported model entry")
        if lod not in range(4):
            raise ValueError("lod is invalid")
        if is_avatar:
            document, run_meta, _ = self._avatar(
                index,
                asset,
                bundle_record,
                bundle_chunk,
                lod,
                cancel_event=cancel_event,
                progress=progress,
            )
        else:
            dependencies = index.bundle_dependencies(int(asset["bundle_index"]))
            sources, missing = self._resolve_bundle_sources(dependencies)
            document, run_meta = self._ordinary(
                bundle_record,
                bundle_chunk,
                asset,
                dependencies,
                sources,
                missing,
                cancel_event=cancel_event,
                progress=progress,
            )

        asset_index = int(asset["asset_index"])
        lod_parameter = f"&lod={lod}" if is_avatar else ""
        hint = quote(self._animation_hint(asset_path, document))
        animation_parameter = (
            f"&animationAssetIndex={int(animation_asset['asset_index'])}"
            if animation_asset
            else ""
        )
        blend_available = (is_prefab or is_avatar) and self._blender_available()
        blend_base = (
            f"/api/manifest-asset/model-blend?manifestId={manifest_id}"
            f"&assetIndex={asset_index}{lod_parameter}"
        )
        return {
            "kind": "modelDocument",
            "status": self._document_status(document),
            "asset": asset,
            "animationAsset": animation_asset,
            "glbUrl": (
                f"/api/manifest-asset/model-glb?manifestId={manifest_id}"
                f"&assetIndex={asset_index}{lod_parameter}&v={self._glb_version}"
            ),
            "blendUrl": (
                f"{blend_base}{animation_parameter}&v={self._blend_version}"
                if blend_available
                else None
            ),
            "baseBlendUrl": (
                f"{blend_base}&v={self._blend_version}" if blend_available else None
            ),
            "animationCandidatesUrl": (
                f"/api/manifest-asset/model-animations?manifestId={manifest_id}"
                f"&assetIndex={asset_index}{lod_parameter}&queryHint={hint}"
            ),
            "maxBlendAnimationCount": self._max_blend_animations,
            "animationUrl": (
                f"/api/manifest-asset/model-animation?manifestId={manifest_id}"
                f"&assetIndex={asset_index}"
                f"&animationAssetIndex={int(animation_asset['asset_index'])}"
                f"&v={self._animation_version}"
                if animation_asset
                else None
            ),
            "document": document,
            "run": {
                "scope": run_meta.get("scope"),
                "builtAtEpoch": run_meta.get("builtAtEpoch"),
                "dependencyBundles": run_meta.get("dependencyBundles", []),
                "missingDependencyBundles": run_meta.get(
                    "missingDependencyBundles", []
                ),
                "lod": lod if is_avatar else None,
            },
        }

    def build_task(
        self,
        manifest_id: int,
        resolved: tuple,
        animation_resolved: tuple | None,
        lod: int,
        *,
        cancel_event: object,
        progress: Callable[[dict], None],
    ) -> dict:
        is_avatar = is_avatar_mesh_asset_path(str(resolved[1]["path"]))
        total = 6 if is_avatar else 5
        offsets = {
            "avatarPlan": 0,
            "cabMap": 1 if is_avatar else 0,
            "objects": 2 if is_avatar else 1,
            "textures": 3 if is_avatar else 2,
            "publish": 4 if is_avatar else 3,
            "cache": total - 1,
        }

        def report_model(value: dict) -> None:
            stage = str(value.get("stage") or "")
            progress({"stage": stage, "completed": offsets.get(stage, 0), "total": total})

        result = self._preview(
            manifest_id,
            resolved,
            animation_resolved,
            lod,
            cancel_event=cancel_event,
            progress=report_model,
        )
        self._check_cancelled(cancel_event)
        progress({"stage": "glb", "completed": total - 1, "total": total})
        self._base_glb(resolved, lod=lod, cancel_event=cancel_event)
        self._check_cancelled(cancel_event)
        progress({"stage": "ready", "completed": total, "total": total})
        return result

    @staticmethod
    def _document_status(document: dict) -> str:
        if document.get("images") and document.get("skins"):
            return "texturedSkinnedModel"
        if document.get("meshes"):
            return "staticGeometry"
        return "hierarchyOnly"

    @staticmethod
    def _check_cancelled(cancel_event: object) -> None:
        if cancel_event.is_set():
            raise RuntimeError("worker_cancelled")
