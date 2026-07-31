"""AnimeStudio export contract for resolved AvatarMesh resources."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from animestudio_model import load_standalone_material_payloads
from npc_avatar_model import load_mesh_payloads


def asset_object_name(asset: Mapping[str, Any]) -> str:
    path = str(asset.get("path") or "")
    if not path:
        raise ValueError("manifest asset has no logical path")
    return path.rsplit("##", 1)[-1] if "##" in path else Path(path).stem


def selected_object_names(plan: Mapping[str, Any]) -> dict[str, list[str]]:
    parts = plan.get("parts")
    avatar_asset = plan.get("avatarAsset")
    if not isinstance(parts, list) or not isinstance(avatar_asset, Mapping):
        raise ValueError("AvatarMesh resource plan is incomplete")
    mesh_names = []
    material_names = []
    for index, part in enumerate(parts):
        if not isinstance(part, Mapping):
            raise ValueError(f"AvatarMesh resource part {index} is not an object")
        mesh_asset = part.get("meshAsset")
        materials = part.get("materialAssets")
        if not isinstance(mesh_asset, Mapping) or not isinstance(materials, list):
            raise ValueError(f"AvatarMesh resource part {index} is incomplete")
        mesh_names.append(asset_object_name(mesh_asset))
        for material_index, material in enumerate(materials):
            if not isinstance(material, Mapping):
                raise ValueError(
                    f"AvatarMesh resource part {index} material {material_index} "
                    "is not an object"
                )
            material_names.append(asset_object_name(material))
    return {
        "Mesh": list(dict.fromkeys(mesh_names)),
        "Material": list(dict.fromkeys(material_names)),
        "Avatar": [asset_object_name(avatar_asset)],
    }


def build_cab_map_command(
    cli: Path,
    input_root: Path,
    output_root: Path,
    map_name: str,
) -> list[str]:
    return [
        str(cli),
        str(input_root),
        str(output_root),
        "--game",
        "ArknightsEndfield",
        "--map_op",
        "CABMap",
        "--map_name",
        map_name,
        "--logger_flags",
        "Error",
        "Warning",
        "Info",
    ]


def build_object_export_command(
    cli: Path,
    input_root: Path,
    output_root: Path,
    map_name: str,
    plan: Mapping[str, Any],
) -> list[str]:
    names = selected_object_names(plan)
    selected = [name for values in names.values() for name in values]
    pattern = f"^(?:{'|'.join(re.escape(name) for name in selected)})$"
    return [
        str(cli),
        str(input_root),
        str(output_root),
        "--game",
        "ArknightsEndfield",
        "--map_op",
        "Load,CABMap",
        "--map_name",
        map_name,
        "--types",
        "Mesh",
        "Material",
        "Avatar",
        "--names",
        pattern,
        "--export_type",
        "JSON",
        "--group_assets",
        "ByType",
        "--logger_flags",
        "Error",
        "Warning",
        "Info",
    ]


def load_exported_objects(
    root: Path,
    plan: Mapping[str, Any],
) -> tuple[dict, dict, dict]:
    names = selected_object_names(plan)
    meshes = load_mesh_payloads(root / "Mesh")
    materials = load_standalone_material_payloads(root / "Material")
    missing_meshes = [name for name in names["Mesh"] if name.casefold() not in meshes]
    missing_materials = [
        name for name in names["Material"] if name.casefold() not in materials
    ]
    if missing_meshes or missing_materials:
        details = []
        if missing_meshes:
            details.append(f"Mesh: {', '.join(missing_meshes)}")
        if missing_materials:
            details.append(f"Material: {', '.join(missing_materials)}")
        raise ValueError("AnimeStudio export is missing " + "; ".join(details))

    avatar_name = names["Avatar"][0]
    avatars = {}
    for path in sorted((root / "Avatar").rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        name = payload.get("m_Name") if isinstance(payload, Mapping) else None
        if not isinstance(name, str) or not name:
            raise ValueError(f"Avatar JSON has no m_Name: {path}")
        key = name.casefold()
        if key in avatars:
            raise ValueError(f"duplicate Avatar export: {name}")
        avatars[key] = payload
    avatar = avatars.get(avatar_name.casefold())
    if avatar is None:
        raise ValueError(f"AnimeStudio export is missing Avatar: {avatar_name}")
    return meshes, materials, avatar


def material_texture_names(materials: Mapping[str, Mapping[str, Any]]) -> list[str]:
    names = []
    for material in materials.values():
        saved = material.get("m_SavedProperties")
        environments = saved.get("m_TexEnvs") if isinstance(saved, Mapping) else None
        if not isinstance(environments, Mapping):
            continue
        for environment in environments.values():
            texture = environment.get("m_Texture") if isinstance(environment, Mapping) else None
            name = texture.get("Name") if isinstance(texture, Mapping) else None
            if texture and texture.get("IsNull") is False and isinstance(name, str) and name:
                names.append(name)
    return list(dict.fromkeys(names))


def build_texture_export_command(
    cli: Path,
    input_root: Path,
    output_root: Path,
    map_name: str,
    texture_names: list[str],
) -> list[str]:
    if not texture_names:
        raise ValueError("texture export requires at least one Texture2D name")
    pattern = f"^(?:{'|'.join(re.escape(name) for name in texture_names)})$"
    return [
        str(cli),
        str(input_root),
        str(output_root),
        "--game",
        "ArknightsEndfield",
        "--map_op",
        "Load,CABMap",
        "--map_name",
        map_name,
        "--types",
        "Texture2D",
        "--names",
        pattern,
        "--export_type",
        "Convert",
        "--group_assets",
        "ByType",
        "--logger_flags",
        "Error",
        "Warning",
        "Info",
    ]


def load_texture_paths(root: Path, expected_names: list[str]) -> dict[str, Path]:
    paths = {}
    for path in sorted(root.rglob("*.png")):
        key = path.stem.casefold()
        if key in paths:
            raise ValueError(f"duplicate Texture2D preview image: {path.stem}")
        paths[key] = path
    missing = [name for name in expected_names if name.casefold() not in paths]
    if missing:
        raise ValueError("AnimeStudio export is missing Texture2D: " + ", ".join(missing))
    return {name.casefold(): paths[name.casefold()] for name in expected_names}
