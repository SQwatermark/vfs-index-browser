"""Build one browser animation document for a published model."""

from __future__ import annotations

from typing import Callable

from manifest_asset_service import is_model_entry_path
from npc_avatar_config import is_avatar_mesh_asset_path


class ModelSingleAnimationService:
    def __init__(
        self,
        avatar_builder: Callable,
        ordinary_builder: Callable,
        bundle_source_resolver: Callable,
        morph_path_predicate: Callable[[str], bool],
        morph_builder: Callable,
        clip_exporter: Callable,
        clip_binder: Callable,
    ) -> None:
        self._avatar = avatar_builder
        self._ordinary = ordinary_builder
        self._resolve_bundle_sources = bundle_source_resolver
        self._is_morph = morph_path_predicate
        self._build_morph = morph_builder
        self._export_clip = clip_exporter
        self._bind_clip = clip_binder

    def build(
        self,
        model_resolved: tuple,
        animation_resolved: tuple,
        lod: int,
        *,
        cancel_event: object | None = None,
        progress: Callable[[dict], None] | None = None,
    ) -> dict:
        index, model_asset, model_record, model_chunk = model_resolved
        _, animation_asset, animation_record, animation_chunk = animation_resolved
        if not is_model_entry_path(str(model_asset["path"])):
            raise ValueError("resource is not a supported model entry")
        if lod not in range(4):
            raise ValueError("lod is invalid")
        cancel_options = (
            {"cancel_event": cancel_event} if cancel_event is not None else {}
        )
        self._report(progress, "model", 0)
        if is_avatar_mesh_asset_path(str(model_asset["path"])):
            document, _, _ = self._avatar(
                index,
                model_asset,
                model_record,
                model_chunk,
                lod,
                **cancel_options,
            )
        else:
            dependencies = index.bundle_dependencies(int(model_asset["bundle_index"]))
            sources, missing = self._resolve_bundle_sources(dependencies)
            document, _ = self._ordinary(
                model_record,
                model_chunk,
                model_asset,
                dependencies,
                sources,
                missing,
                **cancel_options,
            )
        self._check_cancelled(cancel_event)
        self._report(progress, "animation", 1)

        asset_index = int(animation_asset["asset_index"])
        if self._is_morph(str(animation_asset["path"])):
            animation = self._build_morph(
                index, model_asset, animation_asset, document
            )
        else:
            clip, _, _ = self._export_clip(
                animation_record,
                animation_chunk,
                animation_asset,
                **cancel_options,
            )
            self._check_cancelled(cancel_event)
            self._report(progress, "binding", 2)
            animation = self._bind_clip(
                document,
                clip,
                animation_id=f"animation:{asset_index}",
                source={
                    "logicalPath": str(animation_asset["path"]),
                    "bundle": str(animation_asset["bundle_name"]),
                },
                bake_humanoid=True,
            )
        self._check_cancelled(cancel_event)
        self._report(progress, "ready", 3)
        return animation

    @staticmethod
    def _report(
        progress: Callable[[dict], None] | None, stage: str, completed: int
    ) -> None:
        if progress is not None:
            progress({"stage": stage, "completed": completed, "total": 3})

    @staticmethod
    def _check_cancelled(cancel_event: object | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("worker_cancelled")
