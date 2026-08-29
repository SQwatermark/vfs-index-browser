"""Application service preparing model Blender artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class ModelBlendBundle:
    asset: dict
    animation_assets: list[dict]
    glb_path: Path
    issues: list
    requested_count: int

    @property
    def all_animations_failed(self) -> bool:
        return self.requested_count > 0 and not self.animation_assets


@dataclass(frozen=True)
class ModelBlendArtifact:
    path: Path
    name: str
    skipped_animation_count: int


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
        bundle = self.prepare_bundle(
            resolved,
            animation_sources,
            lod,
            cancel_event=cancel_event,
            progress=progress,
        )
        if bundle.all_animations_failed:
            return self._result(
                requested_count=bundle.requested_count,
                animation_assets=[],
                issues=bundle.issues,
            )
        artifact = self.build_artifact(
            bundle,
            cancel_event=cancel_event,
            progress=progress,
        )
        return self._result(
            requested_count=bundle.requested_count,
            animation_assets=bundle.animation_assets,
            issues=bundle.issues,
            asset=bundle.asset,
            blend_path=artifact.path,
        )

    def prepare_bundle(
        self,
        resolved: tuple,
        animation_sources: list[tuple],
        lod: int,
        *,
        cancel_event: object | None = None,
        progress: Callable[[dict], None] | None = None,
    ) -> ModelBlendBundle:
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
            if progress is not None:
                progress({"stage": "modelGlb", "completed": 0, "total": 1})
            asset, _, glb_path = self._base_glb(
                resolved, lod=lod, cancel_event=cancel_event
            )
            animation_assets = []
            issues = []
        return ModelBlendBundle(
            asset=asset,
            animation_assets=animation_assets,
            glb_path=glb_path,
            issues=issues,
            requested_count=len(animation_sources),
        )

    def build_artifact(
        self,
        bundle: ModelBlendBundle,
        *,
        cancel_event: object | None = None,
        progress: Callable[[dict], None] | None = None,
    ) -> ModelBlendArtifact:
        if bundle.all_animations_failed:
            raise ValueError("cannot build Blend when all selected animations failed")
        self._check_cancelled(cancel_event)
        if progress is not None:
            progress({"stage": "blender", "completed": 0, "total": 1})
        blend_path = self._blend(bundle.glb_path, cancel_event=cancel_event)
        if progress is not None:
            progress({"stage": "blender", "completed": 1, "total": 1})
        suffix = (
            f"-animations-{len(bundle.animation_assets)}"
            if bundle.animation_assets
            else ""
        )
        return ModelBlendArtifact(
            path=blend_path,
            name=f"{Path(str(bundle.asset['path'])).stem}{suffix}.blend",
            skipped_animation_count=len(bundle.issues),
        )

    @staticmethod
    def preparation_document(
        bundle: ModelBlendBundle,
        download_url: str | None,
    ) -> dict:
        return {
            "kind": "modelAnimationBundlePreparation",
            "requestedCount": bundle.requested_count,
            "exportedCount": len(bundle.animation_assets),
            "issues": [issue.as_json() for issue in bundle.issues],
            "downloadUrl": download_url,
        }

    @staticmethod
    def failure_document(bundle: ModelBlendBundle) -> dict:
        return {
            "error": "none of the selected animations could be exported",
            "issues": [issue.as_json() for issue in bundle.issues],
        }

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
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("worker_cancelled")
