"""Assemble Avatar ModelDocuments from verified object and texture exports."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from avatar_mesh_snapshot import load_exported_objects, material_texture_selections
from npc_avatar_model import build_static_avatar_mesh_document


@dataclass
class AvatarModelAssembly:
    meshes: dict
    materials: dict
    avatar: object
    texture_selections: list[dict]
    texture_uris: dict[str, str] = field(default_factory=dict)


class AvatarModelDocumentService:
    def load(self, object_root: Path, plan: dict) -> AvatarModelAssembly:
        meshes, materials, avatar = load_exported_objects(object_root, plan)
        return AvatarModelAssembly(
            meshes,
            materials,
            avatar,
            material_texture_selections(materials),
        )

    def attach_exported_textures(
        self,
        assembly: AvatarModelAssembly,
        worker_result: dict,
        uri_for_relative_path: Callable[[str], str],
    ) -> None:
        names = {
            (str(item["sourceFile"]).casefold(), int(item["pathId"])): str(item["name"])
            for item in assembly.texture_selections
        }
        for artifact in worker_result.get("artifacts", []):
            try:
                key = (
                    str(artifact.get("sourceFile") or "").casefold(),
                    int(artifact["pathId"]),
                )
                relative = str(artifact["relativePath"])
            except (KeyError, TypeError, ValueError) as error:
                raise RuntimeError("AvatarMesh texture worker returned an invalid identity") from error
            name = names.get(key)
            if name:
                if name in assembly.texture_uris:
                    raise RuntimeError(f"AvatarMesh selects duplicate Texture2D name: {name}")
                assembly.texture_uris[name] = uri_for_relative_path(relative)

    def build(
        self,
        avatar_mesh: dict,
        assembly: AvatarModelAssembly,
        *,
        lod: int,
        buffer_uri: str,
    ) -> tuple[dict, bytes]:
        return build_static_avatar_mesh_document(
            avatar_mesh,
            assembly.meshes,
            lod=lod,
            avatar=assembly.avatar,
            material_payloads=assembly.materials,
            texture_uris=assembly.texture_uris,
            buffer_uri=buffer_uri,
        )
