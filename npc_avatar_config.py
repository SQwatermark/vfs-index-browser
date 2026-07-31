"""Parse and resolve Endfield NPC AvatarMesh configuration data."""

from __future__ import annotations

import re

from string_path_hash import StringPathHashIndex


LOD_PATTERN = re.compile(r"SubMeshInfo partSubMeshsLOD([0-3])$")
INDEX_PATTERN = re.compile(r"\[(\d+)]$")
INTEGER_PATTERN = re.compile(r"(-?\d+)$")
STRING_PATTERN = re.compile(r'"(.*)"$')


def is_avatar_mesh_asset_path(path: str) -> bool:
    normalized = path.replace("\\", "/").strip("/").casefold()
    return (
        normalized.startswith(
            "assets/beyond/dynamicassets/gameplay/npc/avatarmesh/"
        )
        and normalized.endswith(".asset")
        and "/data_npc_avatarmesh_" in normalized
    )


def indentation(line: str) -> int:
    return len(line) - len(line.lstrip("\t"))


def value_after_equals(line: str) -> str:
    if " = " not in line:
        raise ValueError(f"字段缺少赋值：{line.strip()!r}")
    return line.rsplit(" = ", 1)[1]


def parse_integer(line: str) -> int:
    match = INTEGER_PATTERN.search(value_after_equals(line))
    if not match:
        raise ValueError(f"字段不是整数：{line.strip()!r}")
    return int(match.group(1))


def parse_string(line: str) -> str:
    match = STRING_PATTERN.fullmatch(value_after_equals(line))
    if not match:
        raise ValueError(f"字段不是字符串：{line.strip()!r}")
    return match.group(1)


def parse_hash_vector(lines: list[str], start: int) -> tuple[list[int], int]:
    base_indent = indentation(lines[start])
    size = None
    values = []
    cursor = start + 1
    while cursor < len(lines) and indentation(lines[cursor]) > base_indent:
        stripped = lines[cursor].strip()
        if stripped.startswith("int size = "):
            size = parse_integer(lines[cursor])
        elif stripped.startswith("SInt64 data = "):
            values.append(parse_integer(lines[cursor]))
        cursor += 1
    if size is None:
        raise ValueError(f"{lines[start].strip()} 缺少 size")
    if len(values) != size:
        raise ValueError(
            f"{lines[start].strip()} 声明 {size} 项，实际读取 {len(values)} 项"
        )
    return values, cursor


def parse_submesh(lines: list[str], start: int) -> tuple[dict, int]:
    base_indent = indentation(lines[start])
    result = {
        "meshPathHash": None,
        "meshName": None,
        "materialPathHashes": [],
        "backupMaterialPathHashes": [],
        "isActive": None,
        "rootBoneName": None,
        "rootBoneId": None,
        "realTimeShadowCaster": None,
        "isRendererDisabled": None,
        "platformAvailability": None,
    }
    cursor = start + 1
    while cursor < len(lines) and indentation(lines[cursor]) > base_indent:
        stripped = lines[cursor].strip()
        if stripped.startswith("SInt64 meshPathHash = "):
            result["meshPathHash"] = parse_integer(lines[cursor])
        elif stripped.startswith("string meshName = "):
            result["meshName"] = parse_string(lines[cursor])
        elif stripped == "vector materialPathHashes":
            result["materialPathHashes"], cursor = parse_hash_vector(lines, cursor)
            continue
        elif stripped == "vector backupMaterialPathHashes":
            result["backupMaterialPathHashes"], cursor = parse_hash_vector(lines, cursor)
            continue
        elif stripped.startswith("UInt8 isActive = "):
            result["isActive"] = bool(parse_integer(lines[cursor]))
        elif stripped.startswith("string rootBoneName = "):
            result["rootBoneName"] = parse_string(lines[cursor])
        elif stripped.startswith("int rootBoneID = "):
            result["rootBoneId"] = parse_integer(lines[cursor])
        elif stripped.startswith("UInt8 realTimeShadowCaster = "):
            result["realTimeShadowCaster"] = bool(parse_integer(lines[cursor]))
        elif stripped.startswith("UInt8 isRendererDisabled = "):
            result["isRendererDisabled"] = bool(parse_integer(lines[cursor]))
        elif stripped.startswith("int platformAvailability = "):
            result["platformAvailability"] = parse_integer(lines[cursor])
        cursor += 1

    missing = [key for key, value in result.items() if value is None]
    if missing:
        raise ValueError(
            f"SubMeshInfo {result['meshName']!r} 缺少字段：{', '.join(missing)}"
        )
    return result, cursor


def parse_lod(lines: list[str], start: int) -> tuple[list[dict], int]:
    base_indent = indentation(lines[start])
    declared_size = None
    items = []
    cursor = start + 1
    while cursor < len(lines) and indentation(lines[cursor]) > base_indent:
        stripped = lines[cursor].strip()
        if stripped.startswith("int size = "):
            declared_size = parse_integer(lines[cursor])
        elif INDEX_PATTERN.fullmatch(stripped):
            if cursor + 1 >= len(lines) or lines[cursor + 1].strip() != "SubMeshInfo data":
                raise ValueError(f"LOD 元素后不是 SubMeshInfo：{stripped}")
            item, cursor = parse_submesh(lines, cursor + 1)
            items.append(item)
            continue
        cursor += 1
    if declared_size is None:
        raise ValueError(f"{lines[start].strip()} 缺少 size")
    if len(items) != declared_size:
        raise ValueError(f"LOD 声明 {declared_size} 项，实际读取 {len(items)} 项")
    return items, cursor


def parse_avatar_mesh(text: str) -> dict:
    lines = [line for line in text.splitlines() if line.strip()]
    result = {
        "name": None,
        "mainPrefabPath": None,
        "mainPrefabPathHash": None,
        "slots": [],
    }
    current_slot = None
    cursor = 0
    while cursor < len(lines):
        stripped = lines[cursor].strip()
        if stripped.startswith("string m_Name = ") and result["name"] is None:
            result["name"] = parse_string(lines[cursor])
        elif stripped.startswith("string mainPrefabPath = "):
            result["mainPrefabPath"] = parse_string(lines[cursor])
        elif stripped.startswith("SInt64 mainPrefabPathHash = "):
            result["mainPrefabPathHash"] = parse_integer(lines[cursor])
        elif stripped == "NPCAvatarLodMeshAssets data":
            current_slot = {"name": None, "partType": None, "lods": {}}
            result["slots"].append(current_slot)
        elif current_slot is not None and stripped.startswith("string name = "):
            current_slot["name"] = parse_string(lines[cursor])
        elif current_slot is not None and stripped.startswith("int partType = "):
            current_slot["partType"] = parse_integer(lines[cursor])
        else:
            match = LOD_PATTERN.fullmatch(stripped)
            if match:
                if current_slot is None:
                    raise ValueError("LOD 位于 AvatarMesh slot 之外")
                lod, cursor = parse_lod(lines, cursor)
                current_slot["lods"][match.group(1)] = lod
                continue
        cursor += 1

    missing = [
        key
        for key in ("name", "mainPrefabPath", "mainPrefabPathHash")
        if result[key] is None
    ]
    if missing:
        raise ValueError(f"AvatarMesh 缺少字段：{', '.join(missing)}")
    if not result["slots"]:
        raise ValueError("AvatarMesh 不包含任何 slot")
    for slot in result["slots"]:
        if slot["name"] is None or slot["partType"] is None or not slot["lods"]:
            raise ValueError(f"AvatarMesh slot 不完整：{slot!r}")
    return result


def attach_resolved_paths(document: dict, index: StringPathHashIndex) -> None:
    hashes = {document["mainPrefabPathHash"]}
    for slot in document["slots"]:
        for meshes in slot["lods"].values():
            for mesh in meshes:
                hashes.add(mesh["meshPathHash"])
                hashes.update(mesh["materialPathHashes"])
                hashes.update(mesh["backupMaterialPathHashes"])

    resolved = index.resolve_many(hashes)
    document["mainPrefabResolvedPaths"] = list(
        resolved[document["mainPrefabPathHash"]]
    )
    for slot in document["slots"]:
        for meshes in slot["lods"].values():
            for mesh in meshes:
                mesh["meshPaths"] = list(resolved[mesh["meshPathHash"]])
                mesh["materialPaths"] = [
                    {"hash": path_hash, "paths": list(resolved[path_hash])}
                    for path_hash in mesh["materialPathHashes"]
                ]
                mesh["backupMaterialPaths"] = [
                    {"hash": path_hash, "paths": list(resolved[path_hash])}
                    for path_hash in mesh["backupMaterialPathHashes"]
                ]


def summarize_avatar_mesh(document: dict) -> dict:
    meshes = [
        mesh
        for slot in document["slots"]
        for lod in slot["lods"].values()
        for mesh in lod
    ]
    path_references = [mesh["meshPaths"] for mesh in meshes] + [
        material["paths"]
        for mesh in meshes
        for material in mesh["materialPaths"] + mesh["backupMaterialPaths"]
    ]
    return {
        "slotCount": len(document["slots"]),
        "meshCountByLod": {
            lod: sum(len(slot["lods"].get(lod, [])) for slot in document["slots"])
            for lod in ("0", "1", "2", "3")
        },
        "meshReferenceCount": len(meshes),
        "unresolvedReferenceCount": sum(not paths for paths in path_references),
        "mainPrefabHashResolved": bool(document["mainPrefabResolvedPaths"]),
    }
