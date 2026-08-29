"""Validate published model inputs and derive a cached base GLB."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
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

    @property
    def exporter_mtime_ns(self) -> int:
        return self._exporter_path.stat().st_mtime_ns

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
        glb_path = model_path.with_name("model.glb")
        return self._ensure_glb(
            glb_path,
            document,
            geometry,
            image_paths,
            source_paths=[
                model_path,
                model_path.with_name("geometry.bin"),
                self._exporter_path,
                *image_paths.values(),
            ],
            identity={
                "version": self._version,
                "materialPlan": self._material_identity(),
            },
            cancel_event=cancel_event,
        )

    def ensure_animated(
        self,
        document: dict,
        geometry: bytes,
        image_paths: dict[str, Path],
        model_path: Path,
        clip_paths: list[Path],
        animation_asset_indexes: list[int],
        *,
        binding_path: Path,
        cancel_event: object | None = None,
    ) -> Path:
        selection_key = self.animation_selection_key(animation_asset_indexes)
        target = model_path.parent / "animation-sets" / selection_key / "model.glb"
        return self._ensure_glb(
            target,
            document,
            geometry,
            image_paths,
            source_paths=[
                model_path,
                model_path.with_name("geometry.bin"),
                *clip_paths,
                binding_path,
                self._exporter_path,
                *image_paths.values(),
            ],
            identity={
                "version": self._version,
                "materialPlan": self._material_identity(),
                "animationAssetIndexes": animation_asset_indexes,
            },
            cancel_event=cancel_event,
        )

    @staticmethod
    def animation_selection_key(animation_asset_indexes: list[int]) -> str:
        return hashlib.sha256(
            ",".join(map(str, animation_asset_indexes)).encode("ascii")
        ).hexdigest()[:16]

    def _ensure_glb(
        self,
        target: Path,
        document: dict,
        geometry: bytes,
        image_paths: dict[str, Path],
        *,
        source_paths: list[Path],
        identity: dict,
        cancel_event: object | None,
    ) -> Path:
        material_plans = {}
        if self._shader_root.is_dir():
            material_plans = self._build_material_plans(document, self._shader_root)
            source_paths.append(self._shader_root / self._character_shader_path)
        newest_source_mtime = max(path.stat().st_mtime_ns for path in source_paths)
        meta_path = target.with_suffix(".glb.meta.json")
        if (
            not target.is_file()
            or target.stat().st_mtime_ns < newest_source_mtime
            or self._load_identity(meta_path) != identity
        ):
            self._check_cancelled(cancel_event)
            payload = self._build_glb(
                document,
                geometry,
                lambda image: image_paths[str(image["id"])].read_bytes(),
                material_plans,
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            token = uuid.uuid4().hex
            temporary = target.with_name(f".{target.name}.{token}.tmp")
            temporary_meta = meta_path.with_name(f".{meta_path.name}.{token}.tmp")
            try:
                temporary.write_bytes(payload)
                os.replace(temporary, target)
                temporary_meta.write_text(
                    json.dumps(identity, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                os.replace(temporary_meta, meta_path)
            finally:
                temporary.unlink(missing_ok=True)
                temporary_meta.unlink(missing_ok=True)
        self._check_cancelled(cancel_event)
        return target

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
