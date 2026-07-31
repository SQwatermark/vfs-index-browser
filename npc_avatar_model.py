"""将 NPC AvatarMesh 选择的裸 Mesh 组装为静态 ModelDocument。"""

from __future__ import annotations

import hashlib
import json
import math
import struct
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from animestudio_model import (
    GEOMETRY_BUFFER_ID,
    AnimeStudioObject,
    UnityObjectId,
    attach_mesh_geometry,
    attach_texture_images,
    build_standalone_material_objects,
    collect_material_textures,
)
from model_assembly import create_avatar_mesh_assembly
from model_document import create_model_document, validate_model_document


NPC_MESH_SOURCE = "npc-avatar-mesh"
NPC_MATERIAL_SOURCE = "npc-avatar-material"
NPC_TEXTURE_SOURCE = "npc-avatar-texture"
NPC_ROOT_ROTATION = [-0.7071067811865476, 0.0, 0.0, 0.7071067811865476]


def load_mesh_payloads(root: Path) -> dict[str, Mapping[str, Any]]:
    """按 ``m_Name`` 加载 AnimeStudio Mesh JSON，并拒绝重名对象。"""
    meshes: dict[str, Mapping[str, Any]] = {}
    for path in sorted(root.rglob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"无法读取 Mesh JSON {path}: {error}") from error
        if not isinstance(payload, Mapping):
            raise ValueError(f"Mesh JSON 顶层不是对象：{path}")
        name = payload.get("m_Name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"Mesh JSON 缺少 m_Name：{path}")
        key = name.casefold()
        if key in meshes:
            raise ValueError(f"Mesh 名称重复：{name}")
        meshes[key] = payload
    return meshes


def _build_material_objects(
    selected: list[tuple[int, int, str, Mapping[str, Any]]],
    material_payloads: Mapping[str, Mapping[str, Any]] | None,
) -> dict[UnityObjectId, AnimeStudioObject]:
    if material_payloads is None:
        return {}

    normalized_payloads = {
        key.casefold(): payload for key, payload in material_payloads.items()
    }
    material_names = []
    for _slot_index, _mesh_index, _mesh_name, definition in selected:
        material_names.extend(_material_names(definition))

    missing = []
    selected_payloads = {}
    for name in dict.fromkeys(material_names):
        payload = normalized_payloads.get(name.casefold())
        if payload is None:
            missing.append(name)
            continue
        selected_payloads[name.casefold()] = payload
    if missing:
        raise ValueError(f"missing AvatarMesh Material JSON: {', '.join(missing)}")
    return build_standalone_material_objects(
        selected_payloads,
        material_source_prefix=NPC_MATERIAL_SOURCE,
        texture_source_prefix=NPC_TEXTURE_SOURCE,
    )


def _material_names(definition: Mapping[str, Any]) -> list[str]:
    return [Path(path).stem for path in _material_paths(definition)]


def _material_paths(definition: Mapping[str, Any]) -> list[str]:
    values = definition.get("materialPaths")
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError("AvatarMesh materialPaths is not an array")
    result = []
    for index, value in enumerate(values):
        if not isinstance(value, Mapping):
            raise ValueError(f"AvatarMesh materialPaths[{index}] is not an object")
        paths = value.get("paths")
        if not isinstance(paths, list) or len(paths) != 1 or not isinstance(paths[0], str):
            raise ValueError(
                f"AvatarMesh materialPaths[{index}] must contain exactly one path"
            )
        if not paths[0]:
            raise ValueError(f"AvatarMesh materialPaths[{index}] has no material name")
        result.append(paths[0])
    return result


def _attach_material_references(
    node: dict[str, Any],
    definition: Mapping[str, Any],
    material_objects: Mapping[UnityObjectId, AnimeStudioObject],
) -> None:
    names = _material_names(definition)
    if not names:
        return
    objects_by_name = {
        material.name.casefold(): material
        for material in material_objects.values()
    }
    references = []
    for index, name in enumerate(names):
        material = objects_by_name.get(name.casefold())
        if material is None:
            raise ValueError(f"missing material object: {name}")
        references.append(
            {
                "field": f"$.m_Materials[{index}]",
                "targetType": "Material",
                "target": material.identity.document_id,
            }
        )
    node.setdefault("extras", {}).setdefault("unityComponents", []).append(
        {
            "type": "MeshRenderer",
            "enabled": True,
            "source": {"kind": "AvatarMeshMaterialBinding"},
            "references": references,
        }
    )


def build_static_avatar_mesh_document(
    avatar_mesh: Mapping[str, Any],
    mesh_payloads: Mapping[str, Mapping[str, Any]],
    *,
    lod: int = 0,
    avatar: Mapping[str, Any] | None = None,
    material_payloads: Mapping[str, Mapping[str, Any]] | None = None,
    texture_uris: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], bytes]:
    """将指定 LOD 的所有 Mesh 以绑定姿态放入同一模型文档。"""
    slots = avatar_mesh.get("slots")
    if not isinstance(slots, list) or not slots:
        raise ValueError("AvatarMesh 文档不包含 slots")
    if lod not in range(4):
        raise ValueError(f"LOD 必须位于 0..3，实际为 {lod}")

    asset_name = avatar_mesh.get("name")
    prefab_path = avatar_mesh.get("mainPrefabPath")
    if not isinstance(asset_name, str) or not asset_name:
        raise ValueError("AvatarMesh 文档缺少 name")
    if not isinstance(prefab_path, str) or not prefab_path:
        raise ValueError("AvatarMesh 文档缺少 mainPrefabPath")

    selected = []
    for slot_index, slot in enumerate(slots):
        if not isinstance(slot, Mapping):
            raise ValueError(f"slot {slot_index} 不是对象")
        lods = slot.get("lods")
        meshes = lods.get(str(lod)) if isinstance(lods, Mapping) else None
        if not isinstance(meshes, list):
            raise ValueError(f"slot {slot_index} 不包含 LOD{lod}")
        for mesh_index, mesh in enumerate(meshes):
            if not isinstance(mesh, Mapping):
                raise ValueError(f"slot {slot_index} LOD{lod} mesh {mesh_index} 不是对象")
            name = mesh.get("meshName")
            if not isinstance(name, str) or not name:
                raise ValueError(f"slot {slot_index} LOD{lod} mesh {mesh_index} 缺少 meshName")
            selected.append((slot_index, mesh_index, name, mesh))
    if not selected:
        raise ValueError(f"AvatarMesh 的 LOD{lod} 没有 Mesh")

    assembly_parts = []
    for slot_index, mesh_index, name, definition in selected:
        node_id = f"npc-avatar-node:{slot_index}:{mesh_index}:{name}"
        assembly_parts.append(
            {
                "id": f"npc-avatar-part:{slot_index}:{mesh_index}:{name}",
                "nodeId": node_id,
                "slotIndex": slot_index,
                "meshIndex": mesh_index,
                "lod": lod,
                "active": not bool(definition.get("isRendererDisabled")),
                "meshName": name,
                "meshPathHash": definition.get("meshPathHash"),
                "meshPaths": list(definition.get("meshPaths") or []),
                "materialPaths": _material_paths(definition),
                "rootBoneName": definition.get("rootBoneName"),
            }
        )
    entry_source = {"logicalPath": prefab_path}
    document = create_model_document(
        f"npc-avatar:{asset_name}:lod{lod}",
        asset_name,
        entry_source,
        assembly=create_avatar_mesh_assembly(
            entry_source=entry_source,
            lod=lod,
            parts=assembly_parts,
        ),
    )
    root_node_id = f"npc-avatar-root:{asset_name}:lod{lod}"
    document["nodes"].append(
        {
            "id": root_node_id,
            "name": asset_name,
            "active": True,
            "children": [],
            # 裸 NPC Mesh 使用 Z 轴向上；统一根节点负责转换到 glTF 的 Y 轴向上。
            "transform": {
                "translation": [0.0, 0.0, 0.0],
                "rotation": list(NPC_ROOT_ROTATION),
                "scale": [1.0, 1.0, 1.0],
            },
            "source": entry_source,
        }
    )
    document["asset"]["rootNodeIds"] = [root_node_id]
    objects = {}
    material_objects = _build_material_objects(
        selected,
        material_payloads,
    )
    objects.update(material_objects)
    missing = []
    bindings = []
    for object_index, (slot_index, mesh_index, name, definition) in enumerate(selected, 1):
        payload = mesh_payloads.get(name.casefold())
        if payload is None:
            missing.append(name)
            continue

        mesh_identity = UnityObjectId(NPC_MESH_SOURCE, object_index)
        objects[mesh_identity] = AnimeStudioObject(
            identity=mesh_identity,
            class_id=43,
            type_name="Mesh",
            name=name,
            metadata={},
            payload=payload,
        )
        node_id = f"npc-avatar-node:{slot_index}:{mesh_index}:{name}"
        mesh_paths = list(definition.get("meshPaths") or [])
        document["nodes"].append(
            {
                "id": node_id,
                "name": name,
                "active": not bool(definition.get("isRendererDisabled")),
                "children": [],
                "parentId": root_node_id,
                "transform": {
                    "translation": [0.0, 0.0, 0.0],
                    "rotation": [0.0, 0.0, 0.0, 1.0],
                    "scale": [1.0, 1.0, 1.0],
                },
                **({"source": {"logicalPath": mesh_paths[0]}} if mesh_paths else {}),
                "extras": {
                    "lodLevel": lod,
                    "avatarSlotIndex": slot_index,
                    "assemblyPartId": f"npc-avatar-part:{slot_index}:{mesh_index}:{name}",
                    "unityComponents": [
                        {
                            "type": "MeshFilter",
                            "enabled": True,
                            "source": {
                                "sourceFile": NPC_MESH_SOURCE,
                                "pathId": -object_index,
                            },
                            "references": [
                                {
                                    "field": "$.m_Mesh",
                                    "targetType": "Mesh",
                                    "target": mesh_identity.document_id,
                                }
                            ],
                        }
                    ],
                },
            }
        )
        if material_payloads is not None:
            _attach_material_references(
                document["nodes"][-1],
                definition,
                material_objects,
            )
        document["nodes"][0]["children"].append(node_id)
        bindings.append((document["nodes"][-1], payload))

    if missing:
        rendered = ", ".join(missing)
        raise ValueError(f"缺少 AvatarMesh 引用的 Mesh JSON：{rendered}")

    geometry = attach_mesh_geometry(document, objects)
    if texture_uris is not None:
        textures = collect_material_textures(document, objects)
        normalized_uris = {name.casefold(): uri for name, uri in texture_uris.items()}
        image_uris = {
            texture_id: normalized_uris.get(str(texture["name"]).casefold())
            for texture_id, texture in textures.items()
        }
        attach_texture_images(
            document,
            textures,
            {identity: uri for identity, uri in image_uris.items() if uri},
        )
        missing = sorted(
            str(texture["name"])
            for identity, texture in textures.items()
            if not image_uris[identity]
        )
        if missing:
            document["diagnostics"].append(
                {
                    "severity": "warning",
                    "code": "NPC_TEXTURES_MISSING",
                    "message": "Some NPC material textures have no preview image.",
                    "details": {"textureNames": missing},
                }
            )
    if avatar is not None:
        geometry = _attach_bind_skeleton(document, geometry, bindings, avatar, root_node_id)
    errors = validate_model_document(document)
    if errors:
        raise ValueError(f"生成的 ModelDocument 校验失败：{errors}")
    return document, geometry


def _attach_bind_skeleton(
    document: dict[str, Any],
    geometry: bytes,
    bindings: list[tuple[dict[str, Any], Mapping[str, Any]]],
    avatar: Mapping[str, Any],
    root_node_id: str,
) -> bytes:
    tos = avatar.get("m_TOS")
    if not isinstance(tos, Mapping):
        raise ValueError("Avatar 缺少 m_TOS 骨骼路径映射")
    avatar_data = avatar.get("m_Avatar")
    skeleton = avatar_data.get("m_AvatarSkeleton") if isinstance(avatar_data, Mapping) else None
    default_pose = avatar_data.get("m_DefaultPose") if isinstance(avatar_data, Mapping) else None
    skeleton_nodes = skeleton.get("m_Node") if isinstance(skeleton, Mapping) else None
    skeleton_ids = skeleton.get("m_ID") if isinstance(skeleton, Mapping) else None
    pose_values = default_pose.get("m_X") if isinstance(default_pose, Mapping) else None
    if not (
        isinstance(skeleton_nodes, list)
        and isinstance(skeleton_ids, list)
        and isinstance(pose_values, list)
        and len(skeleton_nodes) == len(skeleton_ids) == len(pose_values)
        and skeleton_nodes
    ):
        raise ValueError("Avatar 骨架节点、ID 与默认姿态数组不完整")
    if len(set(skeleton_ids)) != len(skeleton_ids):
        raise ValueError("Avatar 骨架包含重复 ID")
    skeleton_index = {path_hash: index for index, path_hash in enumerate(skeleton_ids)}

    bind_poses: dict[int, Mapping[str, Any]] = {}
    ordered_hashes = []
    for _node, mesh in bindings:
        hashes = mesh.get("m_BoneNameHashes")
        matrices = mesh.get("m_BindPose")
        if not isinstance(hashes, list) or not isinstance(matrices, list):
            raise ValueError(f"Mesh {mesh.get('m_Name')!r} 缺少骨骼哈希或 bind pose")
        if len(hashes) != len(matrices):
            raise ValueError(
                f"Mesh {mesh.get('m_Name')!r} 的骨骼数与 bind pose 数不一致"
            )
        for path_hash, matrix in zip(hashes, matrices):
            if not isinstance(path_hash, int) or not isinstance(matrix, Mapping):
                raise ValueError(f"Mesh {mesh.get('m_Name')!r} 包含非法 bind pose")
            previous = bind_poses.get(path_hash)
            if previous is not None and not _matrices_close(previous, matrix):
                raise ValueError(f"骨骼哈希 {path_hash} 在不同 Mesh 中的 bind pose 不一致")
            if previous is None:
                bind_poses[path_hash] = matrix
                ordered_hashes.append(path_hash)

    skeleton_id = f"{document['asset']['id']}:skeleton"
    local_defaults = [_pose_matrix(value) for value in pose_values]
    world_matrices: list[list[list[float]] | None] = [None] * len(skeleton_ids)
    authoritative_indices = set()
    for path_hash, matrix in bind_poses.items():
        index = skeleton_index.get(path_hash)
        if index is None:
            raise ValueError(f"Mesh 骨骼哈希不在 Avatar 骨架中：{path_hash}")
        world_matrices[index] = _invert_matrix(_bind_pose_matrix(matrix))
        authoritative_indices.add(index)
    _complete_skeleton_world_matrices(
        world_matrices,
        skeleton_nodes,
        local_defaults,
        skeleton_ids,
        authoritative_indices,
    )

    bone_node_ids = {}
    bones = []
    root = document["nodes"][0]
    for index, path_hash in enumerate(skeleton_ids):
        path = tos.get(str(path_hash))
        if not isinstance(path, str):
            raise ValueError(f"Avatar 无法解析骨骼哈希：{path_hash}")
        node_id = f"npc-avatar-bone:{path_hash}"
        parent_index = skeleton_nodes[index].get("m_ParentId")
        if (
            not isinstance(parent_index, int)
            or parent_index < -1
            or parent_index >= len(skeleton_ids)
        ):
            raise ValueError(f"Avatar 骨骼 {path_hash} 的父节点索引非法：{parent_index}")
        world = world_matrices[index]
        if world is None:
            raise ValueError(f"Avatar 骨骼 {path_hash} 未能恢复世界变换")
        local = (
            _matrix_multiply(_invert_matrix(world_matrices[parent_index]), world)
            if parent_index >= 0
            else world
        )
        transform = _decompose_matrix(local)
        parent_id = (
            f"npc-avatar-bone:{skeleton_ids[parent_index]}"
            if parent_index >= 0
            else root_node_id
        )
        node = {
            "id": node_id,
            "name": path.rsplit("/", 1)[-1] if path else f"{document['asset']['name']} AvatarRoot",
            "active": True,
            "children": [],
            "parentId": parent_id,
            "transform": transform,
            "extras": {
                "avatarBone": {
                    "pathHash": path_hash,
                    "path": path,
                    "bindPoseAuthoritative": index in authoritative_indices,
                }
            },
        }
        document["nodes"].append(node)
        bone_node_ids[path_hash] = node_id
        bone = {
            "id": node_id,
            "name": node["name"],
            "transform": transform,
            "extras": dict(node["extras"]),
        }
        if parent_index >= 0:
            bone["parentId"] = parent_id
        bones.append(bone)

    node_by_id = {node["id"]: node for node in document["nodes"]}
    for node in document["nodes"]:
        if not node["id"].startswith("npc-avatar-bone:"):
            continue
        parent = node_by_id[node["parentId"]]
        parent["children"].append(node["id"])
    document["skeletons"].append(
        {
            "id": skeleton_id,
            "name": f"{document['asset']['name']} Bind Skeleton",
            "bones": bones,
        }
    )

    output = bytearray(geometry)
    for node, mesh in bindings:
        hashes = mesh["m_BoneNameHashes"]
        matrices = mesh["m_BindPose"]
        accessor_id = _append_matrix_accessor(
            document,
            output,
            f"{node['id']}:inverse-bind-matrices",
            matrices,
        )
        skin_id = f"{node['id']}:skin"
        skin = {
            "id": skin_id,
            "skeletonId": skeleton_id,
            "jointIds": [bone_node_ids[path_hash] for path_hash in hashes],
            "inverseBindMatricesAccessorId": accessor_id,
        }
        if isinstance(node.get("source"), Mapping):
            skin["source"] = dict(node["source"])
        document["skins"].append(skin)
        node["skinId"] = skin_id
        node["skeletonId"] = skeleton_id

    buffer = next(
        (item for item in document["buffers"] if item.get("id") == GEOMETRY_BUFFER_ID),
        None,
    )
    if buffer is None:
        raise ValueError("ModelDocument 缺少几何缓冲")
    buffer["byteLength"] = len(output)
    buffer["sha256"] = hashlib.sha256(output).hexdigest()
    return bytes(output)


def _complete_skeleton_world_matrices(
    worlds: list[list[list[float]] | None],
    nodes: list[Mapping[str, Any]],
    local_defaults: list[list[list[float]]],
    skeleton_ids: list[int],
    authoritative_indices: set[int],
) -> None:
    """以 bind pose 为锚点，向上和向下补齐未参与蒙皮的 Avatar 节点。"""
    changed = True
    while changed:
        changed = False
        for index, node in enumerate(nodes):
            parent = node.get("m_ParentId")
            if not isinstance(parent, int) or parent < 0:
                continue
            if worlds[index] is not None:
                candidate = _matrix_multiply(worlds[index], _invert_matrix(local_defaults[index]))
                if worlds[parent] is None:
                    worlds[parent] = candidate
                    changed = True
                elif (
                    parent not in authoritative_indices
                    and not _matrix_values_close(worlds[parent], candidate, tolerance=1e-4)
                ):
                    raise ValueError(
                        f"Avatar 骨骼 {skeleton_ids[parent]} 的补齐候选不一致"
                    )

    for index, node in enumerate(nodes):
        parent = node.get("m_ParentId")
        if parent == -1 and worlds[index] is None:
            worlds[index] = local_defaults[index]

    changed = True
    while changed:
        changed = False
        for index, node in enumerate(nodes):
            if worlds[index] is not None:
                continue
            parent = node.get("m_ParentId")
            if isinstance(parent, int) and parent >= 0 and worlds[parent] is not None:
                worlds[index] = _matrix_multiply(worlds[parent], local_defaults[index])
                changed = True

    unresolved = [skeleton_ids[index] for index, world in enumerate(worlds) if world is None]
    if unresolved:
        raise ValueError(f"Avatar 骨架存在无法连接的节点：{unresolved}")


def _pose_matrix(pose: Any) -> list[list[float]]:
    if not isinstance(pose, Mapping):
        raise ValueError("Avatar 默认姿态不是对象")
    translation = pose.get("t")
    rotation = pose.get("q")
    scale = pose.get("s")
    if not all(isinstance(value, Mapping) for value in (translation, rotation, scale)):
        raise ValueError("Avatar 默认姿态缺少 t/q/s")
    values = [
        translation.get(axis) for axis in ("X", "Y", "Z")
    ] + [
        rotation.get(axis) for axis in ("X", "Y", "Z", "W")
    ] + [
        scale.get(axis) for axis in ("X", "Y", "Z")
    ]
    if not all(isinstance(value, (int, float)) for value in values):
        raise ValueError("Avatar 默认姿态包含非数值分量")
    tx, ty, tz, x, y, z, w, sx, sy, sz = map(float, values)
    return [
        [(1 - 2 * (y * y + z * z)) * sx, (2 * (x * y - z * w)) * sy, (2 * (x * z + y * w)) * sz, tx],
        [(2 * (x * y + z * w)) * sx, (1 - 2 * (x * x + z * z)) * sy, (2 * (y * z - x * w)) * sz, ty],
        [(2 * (x * z - y * w)) * sx, (2 * (y * z + x * w)) * sy, (1 - 2 * (x * x + y * y)) * sz, tz],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _matrix_multiply(
    left: list[list[float]],
    right: list[list[float]],
) -> list[list[float]]:
    return [
        [
            sum(left[row][inner] * right[inner][column] for inner in range(4))
            for column in range(4)
        ]
        for row in range(4)
    ]


def _matrix_values_close(
    left: list[list[float]],
    right: list[list[float]],
    *,
    tolerance: float,
) -> bool:
    return all(
        abs(left[row][column] - right[row][column]) <= tolerance
        for row in range(4)
        for column in range(4)
    )


def _append_matrix_accessor(
    document: dict[str, Any],
    geometry: bytearray,
    owner_id: str,
    matrices: list[Mapping[str, Any]],
) -> str:
    while len(geometry) % 4:
        geometry.append(0)
    offset = len(geometry)
    values = []
    for matrix in matrices:
        values.extend(_bind_pose_values(matrix))
    geometry.extend(struct.pack(f"<{len(values)}f", *values))

    view_id = f"{owner_id}:view"
    accessor_id = f"{owner_id}:accessor"
    document["bufferViews"].append(
        {
            "id": view_id,
            "bufferId": GEOMETRY_BUFFER_ID,
            "byteOffset": offset,
            "byteLength": len(geometry) - offset,
        }
    )
    document["accessors"].append(
        {
            "id": accessor_id,
            "bufferViewId": view_id,
            "componentType": "f32",
            "type": "mat4",
            "count": len(matrices),
        }
    )
    return accessor_id


def _bind_pose_values(matrix: Mapping[str, Any]) -> list[float]:
    values = [matrix.get(f"M{row}{column}") for row in range(4) for column in range(4)]
    if not all(isinstance(value, (int, float)) for value in values):
        raise ValueError("bind pose 不是完整的 4x4 数值矩阵")
    return [float(value) for value in values]


def _bind_pose_matrix(matrix: Mapping[str, Any]) -> list[list[float]]:
    # ModelDocument 沿用现有导出约定：按行写入 Unity 矩阵后，glTF
    # 以列主序读取，得到数学意义上的转置矩阵。
    values = _bind_pose_values(matrix)
    return [[values[column * 4 + row] for column in range(4)] for row in range(4)]


def _matrices_close(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    tolerance: float = 1e-5,
) -> bool:
    return all(
        abs(a - b) <= tolerance
        for a, b in zip(_bind_pose_values(left), _bind_pose_values(right))
    )


def _invert_matrix(matrix: list[list[float]]) -> list[list[float]]:
    size = 4
    augmented = [
        [float(value) for value in row]
        + [1.0 if row_index == column else 0.0 for column in range(size)]
        for row_index, row in enumerate(matrix)
    ]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-10:
            raise ValueError("bind pose 矩阵不可逆")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                value - factor * pivot_value
                for value, pivot_value in zip(augmented[row], augmented[column])
            ]
    return [row[size:] for row in augmented]


def _decompose_matrix(matrix: list[list[float]]) -> dict[str, list[float]]:
    translation = [matrix[row][3] for row in range(3)]
    columns = [[matrix[row][column] for row in range(3)] for column in range(3)]
    scale = [math.sqrt(sum(value * value for value in column)) for column in columns]
    if any(value < 1e-8 for value in scale):
        raise ValueError("bind pose 包含不可分解的零缩放")
    rotation = [
        [matrix[row][column] / scale[column] for column in range(3)]
        for row in range(3)
    ]
    if _determinant3(rotation) < 0:
        scale[0] = -scale[0]
        for row in range(3):
            rotation[row][0] = -rotation[row][0]
    return {
        "translation": translation,
        "rotation": _rotation_to_quaternion(rotation),
        "scale": scale,
    }


def _determinant3(matrix: list[list[float]]) -> float:
    return (
        matrix[0][0] * (matrix[1][1] * matrix[2][2] - matrix[1][2] * matrix[2][1])
        - matrix[0][1] * (matrix[1][0] * matrix[2][2] - matrix[1][2] * matrix[2][0])
        + matrix[0][2] * (matrix[1][0] * matrix[2][1] - matrix[1][1] * matrix[2][0])
    )


def _rotation_to_quaternion(matrix: list[list[float]]) -> list[float]:
    trace = matrix[0][0] + matrix[1][1] + matrix[2][2]
    if trace > 0:
        scale = math.sqrt(trace + 1.0) * 2
        w = 0.25 * scale
        x = (matrix[2][1] - matrix[1][2]) / scale
        y = (matrix[0][2] - matrix[2][0]) / scale
        z = (matrix[1][0] - matrix[0][1]) / scale
    elif matrix[0][0] > matrix[1][1] and matrix[0][0] > matrix[2][2]:
        scale = math.sqrt(1.0 + matrix[0][0] - matrix[1][1] - matrix[2][2]) * 2
        w = (matrix[2][1] - matrix[1][2]) / scale
        x = 0.25 * scale
        y = (matrix[0][1] + matrix[1][0]) / scale
        z = (matrix[0][2] + matrix[2][0]) / scale
    elif matrix[1][1] > matrix[2][2]:
        scale = math.sqrt(1.0 + matrix[1][1] - matrix[0][0] - matrix[2][2]) * 2
        w = (matrix[0][2] - matrix[2][0]) / scale
        x = (matrix[0][1] + matrix[1][0]) / scale
        y = 0.25 * scale
        z = (matrix[1][2] + matrix[2][1]) / scale
    else:
        scale = math.sqrt(1.0 + matrix[2][2] - matrix[0][0] - matrix[1][1]) * 2
        w = (matrix[1][0] - matrix[0][1]) / scale
        x = (matrix[0][2] + matrix[2][0]) / scale
        y = (matrix[1][2] + matrix[2][1]) / scale
        z = 0.25 * scale
    length = math.sqrt(x * x + y * y + z * z + w * w)
    return [x / length, y / length, z / length, w / length]
