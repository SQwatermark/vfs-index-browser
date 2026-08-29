"""Validate published model inputs and derive a cached base GLB."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, unquote, urlparse

from npc_avatar_config import is_avatar_mesh_asset_path


class ModelGlbService:
    def __init__(
        self,
        glb_builder: Callable,
        material_plan_builder: Callable,
        cache_identity_builder: Callable[[], object],
        *,
        version: int,
        exporter_path: Path,
        shader_archive_root: Path,
        character_shader_path: Path,
    ) -> None:
        self._build_glb = glb_builder
        self._build_material_plans = material_plan_builder
        self._material_identity = cache_identity_builder
        self._version = version
        self._exporter_path = exporter_path
        self._shader_root = shader_archive_root
        self._character_shader_path = character_shader_path

    def load_inputs(
        self,
        asset: dict,
        bundle_record: dict,
        model_path: Path,
        *,
        lod: int,
    ) -> tuple[dict, bytes, dict[str, Path]]:
        document = json.loads(model_path.read_text(encoding="utf-8"))
        geometry_path = model_path.with_name("geometry.bin")
        if not geometry_path.is_file():
            raise FileNotFoundError("model geometry buffer not found")
        geometry = geometry_path.read_bytes()

        is_avatar_mesh = is_avatar_mesh_asset_path(str(asset["path"]))
        texture_root = (model_path.parent / "textures").resolve()
        image_paths: dict[str, Path] = {}
        for image in document.get("images", []):
            parsed = urlparse(str(image.get("uri") or ""))
            query = parse_qs(parsed.query)
            if parsed.path != "/api/manifest-asset/model-texture":
                raise ValueError(f"unsupported model image URI: {image.get('uri')}")
            if int(query.get("recordId", ["-1"])[0]) != int(bundle_record["id"]):
                raise ValueError("model image recordId does not match the current model")
            if int(query.get("assetIndex", ["-1"])[0]) != int(asset["asset_index"]):
                raise ValueError("model image assetIndex does not match the current model")
            image_lod = query.get("lod")
            if is_avatar_mesh and (not image_lod or int(image_lod[0]) != lod):
                raise ValueError("model image LOD does not match the current AvatarMesh")
            if query.get("run", [""])[0] != model_path.parent.name:
                raise ValueError("model image run does not match the current model")
            relative = unquote(query.get("path", [""])[0]).replace("\\", "/").strip("/")
            target = (texture_root / relative).resolve()
            if not relative or texture_root not in target.parents or not target.is_file():
                raise FileNotFoundError(f"model texture not found: {relative}")
            image_paths[str(image["id"])] = target
        return document, geometry, image_paths

    def ensure(
        self,
        asset: dict,
        bundle_record: dict,
        model_path: Path,
        *,
        lod: int,
        cancel_event: object | None = None,
    ) -> Path:
        document, geometry, image_paths = self.load_inputs(
            asset, bundle_record, model_path, lod=lod
        )
        geometry_path = model_path.with_name("geometry.bin")
        glb_path = model_path.with_name("model.glb")
        meta_path = glb_path.with_suffix(".glb.meta.json")
        sources = [model_path, geometry_path, self._exporter_path, *image_paths.values()]
        material_plans = {}
        if self._shader_root.is_dir():
            material_plans = self._build_material_plans(document, self._shader_root)
            sources.append(self._shader_root / self._character_shader_path)
        newest_source_mtime = max(path.stat().st_mtime_ns for path in sources)
        identity = {
            "version": self._version,
            "materialPlan": self._material_identity(),
        }
        if (
            not glb_path.is_file()
            or glb_path.stat().st_mtime_ns < newest_source_mtime
            or self._load_identity(meta_path) != identity
        ):
            self._check_cancelled(cancel_event)
            payload = self._build_glb(
                document,
                geometry,
                lambda image: image_paths[str(image["id"])].read_bytes(),
                material_plans,
            )
            temporary = glb_path.with_suffix(".glb.tmp")
            temporary.write_bytes(payload)
            os.replace(temporary, glb_path)
            meta_path.write_text(
                json.dumps(identity, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        self._check_cancelled(cancel_event)
        return glb_path

    @staticmethod
    def _load_identity(path: Path) -> dict | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return None

    @staticmethod
    def _check_cancelled(cancel_event: object | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("worker_cancelled")
