"""Application service for binding animation selections to published models."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class AnimationExportIssue:
    asset_index: int
    path: str
    stage: str
    message: str

    def as_json(self) -> dict:
        return {
            "assetIndex": self.asset_index,
            "path": self.path,
            "stage": self.stage,
            "message": self.message,
        }


@dataclass(frozen=True)
class AnimatedModelBundle:
    asset: dict
    animations: list[dict]
    model_path: Path
    glb_path: Path
    issues: list[AnimationExportIssue]


class ModelAnimationService:
    def __init__(
        self,
        glb_service: object,
        base_model_provider: Callable,
        model_input_loader: Callable,
        clip_exporter: Callable,
        clip_binder: Callable,
        *,
        glb_version: int,
        clip_export_version: int,
        binding_path: Path,
    ) -> None:
        self._glb = glb_service
        self._base_model = base_model_provider
        self._load_model_inputs = model_input_loader
        self._export_clip = clip_exporter
        self._bind_clip = clip_binder
        self._glb_version = glb_version
        self._clip_version = clip_export_version
        self._binding_path = binding_path

    def ensure(
        self,
        model_resolved: tuple,
        animation_sources: list[tuple],
        *,
        lod: int,
        skip_incompatible: bool = False,
        cancel_event: object | None = None,
        progress: Callable[[dict], None] | None = None,
    ) -> AnimatedModelBundle:
        if not animation_sources:
            raise ValueError("at least one animation is required")
        animation_sources = sorted(
            animation_sources, key=lambda item: int(item[1]["asset_index"])
        )
        cancel_options = (
            {"cancel_event": cancel_event} if cancel_event is not None else {}
        )
        model_asset, model_path, model_glb = self._base_model(
            model_resolved, lod=lod, **cancel_options
        )
        requested_indexes = [int(item[1]["asset_index"]) for item in animation_sources]
        request_key = self._selection_key(requested_indexes)
        request_root = model_path.parent / "animation-requests" / request_key
        request_meta_path = request_root / "result.json"
        request_identity = self._request_identity(
            model_path, animation_sources, skip_incompatible
        )
        cached = self._load_cached_request(
            request_meta_path,
            request_identity,
            model_asset,
            model_path,
            model_glb,
            animation_sources,
            progress,
        )
        if cached is not None:
            return cached

        _, _, model_record, _ = model_resolved
        document, geometry, image_paths = self._load_model_inputs(
            model_asset, model_record, model_path, lod=lod
        )
        animated_document = copy.deepcopy(document)
        animated_geometry = geometry
        animation_assets: list[dict] = []
        issues: list[AnimationExportIssue] = []
        clip_paths: list[Path] = []
        for ordinal, (_, asset, record, chunk) in enumerate(animation_sources, start=1):
            self._check_cancelled(cancel_event)
            self._report(progress, "animations", ordinal - 1, len(animation_sources))
            asset_index = int(asset["asset_index"])
            asset_path = str(asset["path"])
            try:
                clip, clip_path, _ = self._export_clip(
                    record, chunk, asset, **cancel_options
                )
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
                if not skip_incompatible:
                    raise
                issues.append(
                    AnimationExportIssue(asset_index, asset_path, "clipExport", str(error))
                )
                continue

            candidate = copy.deepcopy(animated_document)
            try:
                candidate_geometry = self._bind_clip(
                    candidate,
                    animated_geometry,
                    clip,
                    animation_id=f"animation:{asset_index}",
                    source={
                        "logicalPath": asset_path,
                        "bundle": str(asset["bundle_name"]),
                    },
                    bake_humanoid=True,
                )
                if len(candidate.get("animations", [])) == len(
                    animated_document.get("animations", [])
                ):
                    raise RuntimeError(
                        "animation has no transform tracks compatible with this model"
                    )
            except (KeyError, RuntimeError, ValueError) as error:
                if not skip_incompatible:
                    raise
                issues.append(
                    AnimationExportIssue(asset_index, asset_path, "modelBinding", str(error))
                )
                continue

            animated_document = candidate
            animated_geometry = candidate_geometry
            animation_assets.append(asset)
            clip_paths.append(clip_path)
            self._report(progress, "animations", ordinal, len(animation_sources))

        self._report(
            progress, "animations", len(animation_sources), len(animation_sources)
        )
        animation_indexes = [int(asset["asset_index"]) for asset in animation_assets]
        if animation_assets:
            glb_path = self._glb.ensure_animated(
                animated_document,
                animated_geometry,
                image_paths,
                model_path,
                clip_paths,
                animation_indexes,
                binding_path=self._binding_path,
                cancel_event=cancel_event,
            )
        else:
            glb_path = model_glb
        result = AnimatedModelBundle(
            model_asset, animation_assets, model_path, glb_path, issues
        )
        self._publish_request(
            request_meta_path, request_identity, animation_indexes, issues
        )
        return result

    def _request_identity(
        self, model_path: Path, animation_sources: list[tuple], skip_incompatible: bool
    ) -> dict:
        return {
            "version": self._glb_version,
            "animationClipExportVersion": self._clip_version,
            "skipIncompatible": skip_incompatible,
            "modelMtimeNs": model_path.stat().st_mtime_ns,
            "bindingCodeMtimeNs": self._binding_path.stat().st_mtime_ns,
            "glbCodeMtimeNs": self._glb.exporter_mtime_ns,
            "animations": [
                {
                    "assetIndex": int(asset["asset_index"]),
                    "path": str(asset["path"]),
                    "recordId": record.get("id"),
                    "length": record.get("length"),
                    "fileDataMd5": record.get("file_data_md5"),
                    "chunkMtimeNs": chunk.stat().st_mtime_ns if chunk.is_file() else None,
                }
                for _, asset, record, chunk in animation_sources
            ],
        }

    def _load_cached_request(
        self,
        path: Path,
        identity: dict,
        model_asset: dict,
        model_path: Path,
        model_glb: Path,
        animation_sources: list[tuple],
        progress: Callable[[dict], None] | None,
    ) -> AnimatedModelBundle | None:
        if not path.is_file():
            return None
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
            if meta.get("identity") != identity:
                return None
            indexes = [int(value) for value in meta.get("animationAssetIndexes", [])]
            by_index = {int(item[1]["asset_index"]): item[1] for item in animation_sources}
            assets = [by_index[value] for value in indexes]
            glb_path = (
                model_path.parent
                / "animation-sets"
                / self._glb.animation_selection_key(indexes)
                / "model.glb"
                if indexes
                else model_glb
            )
            if not glb_path.is_file():
                return None
            self._report(
                progress, "animationCache", len(animation_sources), len(animation_sources)
            )
            return AnimatedModelBundle(
                model_asset,
                assets,
                model_path,
                glb_path,
                [
                    AnimationExportIssue(
                        int(issue["assetIndex"]),
                        str(issue["path"]),
                        str(issue["stage"]),
                        str(issue["message"]),
                    )
                    for issue in meta.get("issues", [])
                ],
            )
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            return None

    @staticmethod
    def _publish_request(
        path: Path,
        identity: dict,
        animation_indexes: list[int],
        issues: list[AnimationExportIssue],
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(
                    {
                        "identity": identity,
                        "animationAssetIndexes": animation_indexes,
                        "issues": [issue.as_json() for issue in issues],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _selection_key(indexes: list[int]) -> str:
        return hashlib.sha256(",".join(map(str, indexes)).encode("ascii")).hexdigest()[:16]

    @staticmethod
    def _report(
        progress: Callable[[dict], None] | None, stage: str, completed: int, total: int
    ) -> None:
        if progress is not None:
            progress({"stage": stage, "completed": completed, "total": total})

    @staticmethod
    def _check_cancelled(cancel_event: object | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("worker_cancelled")
