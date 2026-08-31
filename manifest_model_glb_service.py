"""协调 Manifest 模型准备与基础 GLB 构建。"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from npc_avatar_config import is_avatar_mesh_asset_path


class ManifestModelGlbService:
    def __init__(
        self,
        avatar_builder: Callable,
        ordinary_builder: Callable,
        bundle_source_resolver: Callable,
        run_store: object,
        glb_service: object,
    ) -> None:
        self._avatar = avatar_builder
        self._ordinary = ordinary_builder
        self._resolve_bundle_sources = bundle_source_resolver
        self._runs = run_store
        self._glb = glb_service

    def ensure(
        self,
        resolved: tuple,
        *,
        lod: int = 0,
        cancel_event: object | None = None,
    ) -> tuple[dict, Path, Path]:
        index, asset, bundle_record, bundle_chunk = resolved
        if is_avatar_mesh_asset_path(str(asset["path"])):
            _, _, model_path = self._avatar(
                index,
                asset,
                bundle_record,
                bundle_chunk,
                lod,
                cancel_event=cancel_event,
            )
        else:
            dependencies = index.bundle_dependencies(int(asset["bundle_index"]))
            dependency_sources, missing_dependencies = (
                self._resolve_bundle_sources(dependencies)
            )
            _, run_meta = self._ordinary(
                bundle_record,
                bundle_chunk,
                asset,
                dependencies,
                dependency_sources,
                missing_dependencies,
                cancel_event=cancel_event,
            )
            model_path = self._runs.resolve_model_path(
                int(bundle_record["id"]),
                int(asset["asset_index"]),
                str(run_meta.get("selectedRun") or ""),
            )
            if model_path is None:
                raise RuntimeError("published model run is unavailable")

        glb_path = self._glb.ensure(
            asset,
            bundle_record,
            model_path,
            lod=lod,
            cancel_event=cancel_event,
        )
        return asset, model_path, glb_path
