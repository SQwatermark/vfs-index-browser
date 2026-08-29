"""Build ordinary manifest ModelDocuments from verified Unity worker snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from animestudio_model import (
    attach_mesh_geometry,
    attach_texture_images,
    build_hierarchy_document,
    collect_material_textures,
    find_container_root_game_object,
    load_animestudio_objects,
)
from model_document import validate_model_document


@dataclass
class OrdinaryModelAssembly:
    document: dict
    geometry: bytes
    textures: list


class OrdinaryModelDocumentService:
    @property
    def builder_mtime_ns(self) -> int:
        return Path(build_hierarchy_document.__code__.co_filename).stat().st_mtime_ns

    def assemble(
        self,
        object_root: Path,
        *,
        logical_path: str,
        bundle: str,
        buffer_uri: str,
    ) -> OrdinaryModelAssembly:
        objects = load_animestudio_objects(object_root)
        if not objects:
            bare_snapshots = sum(
                1
                for asset_type in ("GameObject", "Transform")
                for _path in (object_root / asset_type).glob("*.json")
            )
            if bare_snapshots:
                raise RuntimeError(
                    f"AnimeStudio exported {bare_snapshots} GameObject/Transform JSON files "
                    "without required $animestudio identity metadata"
                )
            raise RuntimeError("AnimeStudio produced no GameObject/Transform JSON snapshots")
        entry = find_container_root_game_object(objects, logical_path)
        document = build_hierarchy_document(
            objects,
            entry,
            logical_path=logical_path,
            bundle=bundle,
        )
        geometry = attach_mesh_geometry(document, objects, buffer_uri=buffer_uri)
        textures = list(collect_material_textures(document, objects))
        return OrdinaryModelAssembly(document, geometry, textures)

    def attach_exported_textures(
        self,
        assembly: OrdinaryModelAssembly,
        worker_result: dict,
        uri_for_relative_path: Callable[[str], str],
    ) -> None:
        identities = {
            (identity.source_file.casefold(), identity.path_id): identity
            for identity in assembly.textures
        }
        image_uris = {}
        for artifact in worker_result.get("artifacts", []):
            try:
                key = (
                    str(artifact.get("sourceFile") or "").casefold(),
                    int(artifact["pathId"]),
                )
                relative = str(artifact["relativePath"])
            except (KeyError, TypeError, ValueError) as error:
                raise RuntimeError("texture worker returned an invalid identity") from error
            identity = identities.get(key)
            if identity is not None:
                image_uris[identity] = uri_for_relative_path(relative)
        attach_texture_images(assembly.document, assembly.textures, image_uris)
        missing = [
            identity.document_id
            for identity in assembly.textures
            if identity not in image_uris
        ]
        if missing:
            assembly.document["diagnostics"].append({
                "severity": "warning",
                "code": "MODEL_TEXTURES_MISSING",
                "message": "部分模型纹理未能导出为预览图片。",
                "details": {"textureIds": missing},
            })

    def finalize(self, assembly: OrdinaryModelAssembly, missing_bundles: list[dict]) -> None:
        if missing_bundles:
            assembly.document["diagnostics"].append({
                "severity": "warning",
                "code": "DEPENDENCY_BUNDLES_MISSING",
                "message": "部分跨 Bundle 依赖在当前 VFS 中不可用，模型层级可能不完整。",
                "details": {"bundles": missing_bundles},
            })
        errors = validate_model_document(assembly.document)
        if errors:
            raise RuntimeError("generated ModelDocument failed semantic validation")
