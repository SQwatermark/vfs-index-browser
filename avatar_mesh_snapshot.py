"""AnimeStudio export contract for resolved AvatarMesh resources."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

def asset_object_name(asset: Mapping[str, Any]) -> str:
    path = str(asset.get("path") or "")
    if not path:
        raise ValueError("manifest asset has no logical path")
    return path.rsplit("##", 1)[-1] if "##" in path else Path(path).stem


def asset_container_path(asset: Mapping[str, Any]) -> str:
    path = str(asset.get("path") or "")
    if not path:
        raise ValueError("manifest asset has no logical path")
    return path.split("##", 1)[0].replace("\\", "/").casefold()


def load_planned_object(
    root: Path,
    type_name: str,
    asset: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Load the exact exported Unity object selected by the manifest plan.

    Unity object names are not identities: an FBX sub-object and a standalone
    asset can legitimately share ``m_Name``. AnimeStudio's ``container`` field
    retains the logical asset path and therefore disambiguates them.
    """

    logical_path = str(asset.get("path") or "")
    expected_name = asset_object_name(asset)
    expected_container = asset_container_path(asset)
    selects_sub_object = "##" in logical_path
    matches = []
    for path in sorted((root / type_name).rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, Mapping):
            raise ValueError(f"{type_name} JSON is not an object: {path}")
        metadata = payload.get("$animestudio")
        container = metadata.get("container") if isinstance(metadata, Mapping) else None
        name = payload.get("m_Name")
        if (
            isinstance(container, str)
            and container.replace("\\", "/").casefold() == expected_container
            and (
                not selects_sub_object
                or (
                    isinstance(name, str)
                    and name.casefold() == expected_name.casefold()
                )
            )
        ):
            matches.append(payload)
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one {type_name} export for {asset.get('path')}, "
            f"found {len(matches)}"
        )
    return matches[0]


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


def selected_container_paths(plan: Mapping[str, Any]) -> list[str]:
    """Return the logical containers that uniquely identify planned objects."""

    names = selected_object_names(plan)
    if not any(names.values()):
        raise ValueError("AvatarMesh resource plan selects no objects")
    assets = [plan["avatarAsset"]]
    for part in plan["parts"]:
        assets.append(part["meshAsset"])
        assets.extend(part["materialAssets"])
    return list(dict.fromkeys(asset_container_path(asset) for asset in assets))


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
        "BuildCABMap",
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
    containers = selected_container_paths(plan)
    pattern = f"^(?:{'|'.join(re.escape(path) for path in containers)})$"
    return [
        str(cli),
        str(input_root),
        str(output_root),
        "--game",
        "ArknightsEndfield",
        "--map_op",
        "UseCABMap",
        "--map_name",
        map_name,
        "--types",
        "Mesh",
        "Material",
        "Avatar",
        "--containers",
        pattern,
        "--export_type",
        "ObjectJSON",
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
    parts = plan.get("parts")
    avatar_asset = plan.get("avatarAsset")
    if not isinstance(parts, list) or not isinstance(avatar_asset, Mapping):
        raise ValueError("AvatarMesh resource plan is incomplete")

    meshes = {}
    materials = {}
    for part in parts:
        if not isinstance(part, Mapping):
            raise ValueError("AvatarMesh resource part is not an object")
        mesh_asset = part.get("meshAsset")
        material_assets = part.get("materialAssets")
        if not isinstance(mesh_asset, Mapping) or not isinstance(material_assets, list):
            raise ValueError("AvatarMesh resource part is incomplete")
        mesh_name = asset_object_name(mesh_asset)
        mesh = load_planned_object(root, "Mesh", mesh_asset)
        previous = meshes.setdefault(mesh_name.casefold(), mesh)
        if previous is not mesh and previous != mesh:
            raise ValueError(f"resource plan selects distinct Mesh objects named {mesh_name}")
        for material_asset in material_assets:
            if not isinstance(material_asset, Mapping):
                raise ValueError("AvatarMesh material asset is not an object")
            material_name = asset_object_name(material_asset)
            material = load_planned_object(root, "Material", material_asset)
            previous = materials.setdefault(material_name.casefold(), material)
            if previous is not material and previous != material:
                raise ValueError(
                    f"resource plan selects distinct Material objects named {material_name}"
                )

    avatar = load_planned_object(root, "Avatar", avatar_asset)
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
        "UseCABMap",
        "--map_name",
        map_name,
        "--types",
        "Texture2D",
        "--names",
        pattern,
        "--export_type",
        "IdentifiedTexture",
        "--group_assets",
        "ByType",
        "--logger_flags",
        "Error",
        "Warning",
        "Info",
    ]


def load_texture_paths(root: Path, expected_names: list[str]) -> dict[str, Path]:
    """Load IdentifiedTexture outputs by their Unity object name.

    IdentifiedTexture appends ``_p<path-id>`` to prevent silent overwrites. The
    AvatarMesh material adapter currently identifies textures by name, so names
    must remain unique within one assembled model.
    """

    expected = {name.casefold(): name for name in expected_names}
    paths = {}
    for path in sorted(root.rglob("*.png")):
        stem = re.sub(r"_p[0-9a-f]{16}$", "", path.stem, flags=re.IGNORECASE)
        key = stem.casefold()
        if key not in expected:
            continue
        if key in paths:
            raise ValueError(f"duplicate Texture2D preview image: {stem}")
        paths[key] = path
    missing = [name for name in expected_names if name.casefold() not in paths]
    if missing:
        raise ValueError("AnimeStudio export is missing Texture2D: " + ", ".join(missing))
    return {name.casefold(): paths[name.casefold()] for name in expected_names}
