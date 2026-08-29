"""Application service preparing model Blender artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Callable


class ModelBlendService:
    def __init__(
        self,
        base_glb_provider: Callable,
        animated_glb_provider: Callable,
        blend_provider: Callable,
    ) -> None:
        self._base_glb = base_glb_provider
        self._animated_glb = animated_glb_provider
        self._blend = blend_provider

    def prepare(
        self,
        resolved: tuple,
        animation_sources: list[tuple],
        lod: int,
        *,
        cancel_event: object,
        progress: Callable[[dict], None],
    ) -> dict:
        if animation_sources:
            bundle = self._animated_glb(
                resolved,
                animation_sources,
                lod=lod,
                skip_incompatible=len(animation_sources) > 1,
                cancel_event=cancel_event,
                progress=progress,
            )
            asset = bundle.asset
            animation_assets = bundle.animations
            glb_path = bundle.glb_path
            issues = bundle.issues
        else:
            progress({"stage": "modelGlb", "completed": 0, "total": 1})
            asset, _, glb_path = self._base_glb(
                resolved, lod=lod, cancel_event=cancel_event
            )
            animation_assets = []
            issues = []
        if animation_sources and not animation_assets:
            return self._result(
                requested_count=len(animation_sources),
                animation_assets=[],
                issues=issues,
            )
        self._check_cancelled(cancel_event)
        progress({"stage": "blender", "completed": 0, "total": 1})
        blend_path = self._blend(glb_path, cancel_event=cancel_event)
        progress({"stage": "blender", "completed": 1, "total": 1})
        return self._result(
            requested_count=len(animation_sources),
            animation_assets=animation_assets,
            issues=issues,
            asset=asset,
            blend_path=blend_path,
        )

    @staticmethod
    def _result(
        *,
        requested_count: int,
        animation_assets: list[dict],
        issues: list,
        asset: dict | None = None,
        blend_path: Path | None = None,
    ) -> dict:
        result = {
            "kind": "modelBlendPreparation",
            "requestedCount": requested_count,
            "exportedCount": len(animation_assets),
            "issues": [issue.as_json() for issue in issues],
            "artifactAvailable": blend_path is not None,
        }
        if blend_path is not None and asset is not None:
            suffix = f"-animations-{len(animation_assets)}" if animation_assets else ""
            result.update({
                "_artifactPath": str(blend_path.resolve()),
                "_artifactName": f"{Path(str(asset['path'])).stem}{suffix}.blend",
                "_artifactContentType": "application/x-blender",
            })
        return result

    @staticmethod
    def _check_cancelled(cancel_event: object) -> None:
        if cancel_event.is_set():
            raise RuntimeError("worker_cancelled")
