"""Resolve one NPC AvatarMesh selection to concrete manifest resources."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

from manifest_index import ManifestIndex


def _strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} has no resolved logical path")
    paths = []
    seen = set()
    for index, path in enumerate(value):
        if not isinstance(path, str) or not path.strip():
            raise ValueError(f"{label}[{index}] is not a logical path")
        key = path.casefold()
        if key not in seen:
            seen.add(key)
            paths.append(path)
    return paths


def _resolve_one(
    index: ManifestIndex,
    paths: Sequence[str],
    label: str,
) -> dict[str, Any]:
    matches = {
        asset["assetIndex"]: asset
        for path in paths
        for asset in index.assets_by_path(path)
    }
    if not matches:
        raise ValueError(f"{label} is absent from manifest: {', '.join(paths)}")
    if len(matches) != 1:
        details = ", ".join(
            f"{asset['path']} (asset {asset_id})"
            for asset_id, asset in sorted(matches.items())
        )
        raise ValueError(f"{label} is ambiguous in manifest: {details}")
    return next(iter(matches.values()))


def _material_assets(
    index: ManifestIndex,
    value: Any,
    label: str,
) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{label} is not an array")
    result = []
    for material_index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"{label}[{material_index}] is not an object")
        paths = _strings(item.get("paths"), f"{label}[{material_index}].paths")
        result.append(_resolve_one(index, paths, f"{label}[{material_index}]"))
    return result


def _avatar_path(mesh_paths: Sequence[str]) -> str:
    source_files = set()
    for path in mesh_paths:
        marker = path.casefold().find(".fbx##")
        if marker < 0:
            continue
        source_files.add(path[: marker + 4])
    if not source_files:
        raise ValueError(
            "cannot infer Avatar asset because selected meshes contain no FBX subasset path"
        )
    normalized = {path.casefold(): path for path in source_files}
    if len(normalized) != 1:
        raise ValueError(
            "selected meshes refer to multiple FBX sources: "
            + ", ".join(sorted(normalized.values(), key=str.casefold))
        )
    source = next(iter(normalized.values()))
    return f"{source}##{PurePosixPath(source).stem}avatar"


def build_avatar_mesh_resource_plan(
    index: ManifestIndex,
    avatar_mesh: Mapping[str, Any],
    *,
    lod: int = 0,
    avatar_path: str | None = None,
) -> dict[str, Any]:
    """Resolve an AvatarMesh LOD without opening any referenced bundle.

    The plan is the stable boundary between configuration parsing and Unity
    object extraction. Material entries deliberately preserve renderer slot
    order and duplicates.
    """

    if lod not in range(4):
        raise ValueError(f"LOD must be in 0..3, got {lod}")
    name = avatar_mesh.get("name")
    main_prefab_path = avatar_mesh.get("mainPrefabPath")
    slots = avatar_mesh.get("slots")
    if not isinstance(name, str) or not name:
        raise ValueError("AvatarMesh has no name")
    if not isinstance(main_prefab_path, str) or not main_prefab_path:
        raise ValueError("AvatarMesh has no mainPrefabPath")
    if not isinstance(slots, list) or not slots:
        raise ValueError("AvatarMesh has no slots")

    parts = []
    selected_mesh_paths = []
    for slot_index, slot in enumerate(slots):
        if not isinstance(slot, Mapping):
            raise ValueError(f"slot {slot_index} is not an object")
        lods = slot.get("lods")
        meshes = lods.get(str(lod)) if isinstance(lods, Mapping) else None
        if not isinstance(meshes, list):
            raise ValueError(f"slot {slot_index} has no LOD{lod}")
        for mesh_index, mesh in enumerate(meshes):
            label = f"slot {slot_index} LOD{lod} mesh {mesh_index}"
            if not isinstance(mesh, Mapping):
                raise ValueError(f"{label} is not an object")
            mesh_name = mesh.get("meshName")
            if not isinstance(mesh_name, str) or not mesh_name:
                raise ValueError(f"{label} has no meshName")
            mesh_paths = _strings(mesh.get("meshPaths"), f"{label}.meshPaths")
            mesh_asset = _resolve_one(index, mesh_paths, f"{label} ({mesh_name})")
            selected_mesh_paths.extend(mesh_paths)
            parts.append(
                {
                    "slotIndex": slot_index,
                    "slotName": slot.get("name"),
                    "partType": slot.get("partType"),
                    "meshIndex": mesh_index,
                    "meshName": mesh_name,
                    "rootBoneName": mesh.get("rootBoneName"),
                    "active": not bool(mesh.get("isRendererDisabled")),
                    "meshAsset": mesh_asset,
                    "materialAssets": _material_assets(
                        index,
                        mesh.get("materialPaths"),
                        f"{label}.materialPaths",
                    ),
                }
            )
    if not parts:
        raise ValueError(f"AvatarMesh LOD{lod} selects no meshes")

    resolved_avatar_path = avatar_path or _avatar_path(selected_mesh_paths)
    avatar_asset = _resolve_one(index, [resolved_avatar_path], "Avatar asset")
    assets = [avatar_asset]
    for part in parts:
        assets.append(part["meshAsset"])
        assets.extend(part["materialAssets"])
    bundles = {
        asset["bundleIndex"]: {
            "bundleIndex": asset["bundleIndex"],
            "bundleName": asset["bundleName"],
        }
        for asset in assets
    }
    return {
        "kind": "avatarMeshResources",
        "name": name,
        "lod": lod,
        "mainPrefabPath": main_prefab_path,
        "avatarAsset": avatar_asset,
        "parts": parts,
        "bundles": [bundles[key] for key in sorted(bundles)],
    }
