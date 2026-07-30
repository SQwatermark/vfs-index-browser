"""Convert AnimeStudio JSON object snapshots into ModelDocument structure.

The adapter consumes AnimeStudio's public JSON metadata contract, especially
``$animestudio.pptrReferences``. It does not depend on AnimeStudio source code
or infer references from exported filenames.
"""

from __future__ import annotations

import json
import hashlib
import re
import struct
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from model_document import add_diagnostic, create_model_document


MODEL_COMPONENT_TYPES = {
    "MeshFilter",
    "MeshRenderer",
    "SkinnedMeshRenderer",
    "Animator",
    "LODGroup",
}
GEOMETRY_BUFFER_ID = "buffer:geometry"
LOD_RENDERER_PATH_RE = re.compile(r"^\$\.m_LODs\[(\d+)]\.renderers\[\d+]\.renderer$")


@dataclass(frozen=True, order=True)
class UnityObjectId:
    source_file: str
    path_id: int

    @property
    def document_id(self) -> str:
        return f"unity:{self.source_file}:{self.path_id}"


@dataclass(frozen=True)
class AnimeStudioObject:
    identity: UnityObjectId
    class_id: int | None
    type_name: str
    name: str
    metadata: Mapping[str, Any]
    payload: Mapping[str, Any]
    source_path: Path | None = None

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], source_path: Path | None = None):
        metadata = payload.get("$animestudio")
        if not isinstance(metadata, Mapping):
            raise ValueError("AnimeStudio JSON object has no $animestudio metadata")
        source_file = metadata.get("sourceFile")
        path_id = metadata.get("pathId")
        if not isinstance(source_file, str) or not source_file:
            raise ValueError("AnimeStudio object has no sourceFile identity")
        if not isinstance(path_id, int):
            raise ValueError("AnimeStudio object has no integer pathId identity")
        return cls(
            identity=UnityObjectId(source_file, path_id),
            class_id=metadata.get("classId") if isinstance(metadata.get("classId"), int) else None,
            type_name=str(metadata.get("type") or payload.get("type") or "Unknown"),
            name=str(metadata.get("name") or payload.get("m_Name") or payload.get("name") or ""),
            metadata=metadata,
            payload=payload,
            source_path=source_path,
        )


def load_animestudio_objects(root: Path) -> dict[UnityObjectId, AnimeStudioObject]:
    """Load every AnimeStudio JSON object below *root*, rejecting duplicates."""

    objects: dict[UnityObjectId, AnimeStudioObject] = {}
    for path in sorted(root.rglob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, Mapping) or "$animestudio" not in payload:
            continue
        obj = AnimeStudioObject.from_payload(payload, path)
        if obj.identity in objects:
            previous = objects[obj.identity].source_path
            raise ValueError(f"duplicate Unity object {obj.identity}: {previous} and {path}")
        objects[obj.identity] = obj
    return objects


def find_container_root_game_object(
    objects: Mapping[UnityObjectId, AnimeStudioObject], container: str
) -> UnityObjectId:
    """Find the unique root GameObject for a logical prefab path.

    AnimeStudio normally exposes the AssetBundle container in export metadata.
    Some Endfield bundles omit that mapping, so an entry-bundle export falls
    back to the prefab filename while retaining the same uniqueness check.
    """

    normalized = _normalize_container(container)
    game_objects = [obj for obj in objects.values() if obj.type_name == "GameObject"]
    candidates = [
        obj for obj in game_objects
        if _normalize_container(str(obj.metadata.get("container") or "")) == normalized
    ]
    if not candidates and not any(obj.metadata.get("container") for obj in game_objects):
        expected_name = Path(normalized).stem.casefold()
        named = [obj for obj in game_objects if obj.name.casefold() == expected_name]
        entry_objects = [
            obj for obj in named
            if Path(str(obj.metadata.get("sourceOriginalPath") or "")).name.casefold() == "entry.ab"
        ]
        candidates = entry_objects or named

    roots: list[UnityObjectId] = []
    for obj in candidates:
        transform = _first_target_of_type(obj, "Transform", objects, lambda _owner, ref: _reference_target(ref))
        if transform is None or _pointer_path_id(transform.payload.get("m_Father")) == 0:
            roots.append(obj.identity)
    if len(roots) != 1:
        rendered = ", ".join(identity.document_id for identity in roots) or "none"
        raise ValueError(f"expected one root GameObject for container {container!r}, found {len(roots)}: {rendered}")
    return roots[0]


def build_hierarchy_document(
    objects: Mapping[UnityObjectId, AnimeStudioObject],
    entry: UnityObjectId,
    *,
    logical_path: str,
    bundle: str,
) -> dict[str, Any]:
    """Build GameObject hierarchy and dependency evidence for one entry."""

    entry_object = objects.get(entry)
    if entry_object is None:
        raise KeyError(f"entry Unity object not found: {entry}")
    if entry_object.type_name != "GameObject":
        raise ValueError(f"entry must be GameObject, got {entry_object.type_name}")

    source = _source(entry_object, logical_path=logical_path, bundle=bundle)
    document = create_model_document(entry.document_id, entry_object.name, source)
    visited_game_objects: set[UnityObjectId] = set()
    visited_dependencies: set[tuple[UnityObjectId, UnityObjectId, str]] = set()

    def add_dependency(owner: AnimeStudioObject, reference: Mapping[str, Any]) -> UnityObjectId | None:
        target = _reference_target(reference)
        if target is None:
            add_diagnostic(
                document,
                "warning",
                "UNRESOLVED_PPTR",
                f"无法解析 {owner.type_name} 的引用 {reference.get('path', '')}",
                object_id=owner.identity.document_id,
                source=_source(owner),
                details={"reference": dict(reference)},
            )
            return None
        kind = str(reference.get("path") or "PPtr")
        edge = (owner.identity, target, kind)
        if edge not in visited_dependencies:
            document["dependencies"].append(
                {
                    "from": _source(owner),
                    "to": _source(objects[target]) if target in objects else {
                        "sourceFile": target.source_file,
                        "pathId": target.path_id,
                    },
                    "kind": kind,
                    "status": "resolved" if target in objects else "missing",
                }
            )
            visited_dependencies.add(edge)
        return target

    def visit_game_object(game_object_id: UnityObjectId, parent_node_id: str | None = None) -> str | None:
        if game_object_id in visited_game_objects:
            return game_object_id.document_id
        game_object = objects.get(game_object_id)
        if game_object is None or game_object.type_name != "GameObject":
            return None
        visited_game_objects.add(game_object_id)

        transform = _first_target_of_type(game_object, "Transform", objects, add_dependency)
        child_game_objects: list[UnityObjectId] = []
        if transform is None:
            add_diagnostic(
                document,
                "error",
                "MISSING_TRANSFORM",
                "GameObject 没有可解析的 Transform 组件",
                object_id=game_object_id.document_id,
                source=_source(game_object),
            )
        else:
            for reference in _references(transform):
                target = add_dependency(transform, reference)
                if target is None or not str(reference.get("path", "")).startswith("$.m_Children"):
                    continue
                child_transform = objects.get(target)
                if child_transform is None:
                    continue
                child_game_object = _first_target_of_type(child_transform, "GameObject", objects, add_dependency)
                if child_game_object is not None:
                    child_game_objects.append(child_game_object.identity)

        node_id = game_object_id.document_id
        node = {
            "id": node_id,
            "name": game_object.name,
            "active": _active_state(game_object),
            "children": [child.document_id for child in child_game_objects],
            "transform": _local_transform(transform.payload if transform else {}),
            "source": _source(game_object),
        }
        components = []
        for reference in _references(game_object):
            target = add_dependency(game_object, reference)
            component = objects.get(target) if target is not None else None
            if component is None or component.type_name not in MODEL_COMPONENT_TYPES:
                continue
            for component_reference in _references(component):
                add_dependency(component, component_reference)
            components.append(_component_summary(component))
        if components:
            node["extras"] = {"unityComponents": components}
        if parent_node_id is not None:
            node["parentId"] = parent_node_id
        document["nodes"].append(node)

        for child_id in child_game_objects:
            visit_game_object(child_id, node_id)
        return node_id

    root_id = visit_game_object(entry)
    if root_id is not None:
        document["asset"]["rootNodeIds"] = [root_id]
    _annotate_lod_renderers(document)
    return document


def attach_mesh_geometry(
    document: dict[str, Any],
    objects: Mapping[UnityObjectId, AnimeStudioObject],
    *,
    buffer_uri: str = "geometry.bin",
) -> bytes:
    """Attach renderer meshes and encode their numeric arrays into one buffer."""

    geometry = bytearray()
    emitted_meshes: set[str] = set()
    emitted_materials: set[str] = set()

    def add_accessor(owner_id, semantic, values, component_type, value_type, count, *, bounds=False):
        while len(geometry) % 4:
            geometry.append(0)
        offset = len(geometry)
        format_char = {"f32": "f", "u16": "H", "u32": "I"}[component_type]
        geometry.extend(struct.pack(f"<{len(values)}{format_char}", *values))
        view_id = f"{owner_id}:view:{semantic}"
        accessor_id = f"{owner_id}:accessor:{semantic}"
        document["bufferViews"].append(
            {
                "id": view_id,
                "bufferId": GEOMETRY_BUFFER_ID,
                "byteOffset": offset,
                "byteLength": len(geometry) - offset,
            }
        )
        accessor = {
            "id": accessor_id,
            "bufferViewId": view_id,
            "componentType": component_type,
            "type": value_type,
            "count": count,
        }
        component_count = {"scalar": 1, "vec2": 2, "vec3": 3, "vec4": 4, "mat4": 16}[value_type]
        if bounds and count:
            rows = [values[index:index + component_count] for index in range(0, len(values), component_count)]
            accessor["min"] = [min(row[index] for row in rows) for index in range(component_count)]
            accessor["max"] = [max(row[index] for row in rows) for index in range(component_count)]
        document["accessors"].append(accessor)
        return accessor_id

    def emit_material(material_id):
        if material_id in emitted_materials:
            return
        material = objects.get(material_id)
        if material is None or material.type_name != "Material":
            return
        emitted_materials.add(material_id)
        saved = material.payload.get("m_SavedProperties")
        saved = saved if isinstance(saved, Mapping) else {}
        texture_references = {
            str(reference.get("path") or ""): _reference_target(reference)
            for reference in _references(material)
        }
        texture_environments = {}
        raw_texture_environments = saved.get("m_TexEnvs")
        if isinstance(raw_texture_environments, Mapping):
            for slot, environment in raw_texture_environments.items():
                if not isinstance(environment, Mapping):
                    continue
                target = texture_references.get(
                    f"$.m_SavedProperties.m_TexEnvs.{slot}.m_Texture"
                )
                texture_environments[str(slot)] = {
                    "textureId": target.document_id if target is not None else None,
                    "scale": _vector(environment.get("m_Scale"), ("X", "Y")) or [1.0, 1.0],
                    "offset": _vector(environment.get("m_Offset"), ("X", "Y")) or [0.0, 0.0],
                }
        shader_reference = next(
            (reference for reference in _references(material) if reference.get("path") == "$.m_Shader"),
            None,
        )
        shader_target = _reference_target(shader_reference) if shader_reference else None
        shader = material.payload.get("m_Shader")
        shader_name = shader.get("Name") if isinstance(shader, Mapping) else None
        floats = _plain_mapping(saved.get("m_Floats"))
        colors = _plain_mapping(saved.get("m_Colors"))
        property_names = set(texture_environments) | set(floats) | set(colors)
        material_record = {
            "id": material_id.document_id,
            "name": material.name,
            "shader": str(shader_name or (shader_target.document_id if shader_target else "Unknown")),
            "properties": {
                "textureEnvironments": texture_environments,
                "ints": _plain_mapping(saved.get("m_Ints")),
                "floats": floats,
                "colors": colors,
            },
            "source": _source(material),
        }
        preview = {}
        if isinstance(colors.get("_BaseColor"), Mapping):
            color = colors["_BaseColor"]
            preview["baseColorFactor"] = [color.get(key, 1.0) for key in ("r", "g", "b", "a")]
        if floats.get("_SilkStockings") == 1.0 and isinstance(
            colors.get("_SilkStockingsColor"), Mapping
        ):
            base_color = preview.get("baseColorFactor", [1.0, 1.0, 1.0, 1.0])
            stocking_color = colors["_SilkStockingsColor"]
            affect = _clamp_number(floats.get("_SilkStockingsMaxAffect"), 0.0, 1.0)
            preview["baseColorFactor"] = [
                base_color[index] * (1.0 - affect)
                + float(stocking_color.get(channel, 0.0)) * affect
                for index, channel in enumerate(("r", "g", "b"))
            ] + [base_color[3]]
            preview["silkStockings"] = {
                "color": [
                    float(stocking_color.get(channel, 0.0))
                    for channel in ("r", "g", "b")
                ],
                "maxAffect": affect,
            }
        if isinstance(floats.get("_Metallic"), (int, float)):
            preview["metallicFactor"] = _clamp_number(floats["_Metallic"], 0.0, 1.0)
        if isinstance(floats.get("_Smoothness"), (int, float)):
            preview["roughnessFactor"] = 1.0 - _clamp_number(
                floats["_Smoothness"], 0.0, 1.0
            )
        if floats.get("_SurfaceType") == 1.0:
            preview["alphaMode"] = "BLEND"
        elif floats.get("_EnableAlphaTest") == 1.0:
            preview["alphaMode"] = "MASK"
            preview["alphaCutoff"] = _clamp_number(
                floats.get("_AlphaClipThreshold"), 0.0, 1.0, default=0.5
            )
        material_role = None
        if floats.get("_UseGrayAsAlpha") == 1.0:
            material_role = "overlayShadow"
            preview["alphaMode"] = "BLEND"
            preview["baseColorTextureUsesGrayAsAlpha"] = True
            preview["unlit"] = True
        elif isinstance(floats.get("_characterRenderQueue"), (int, float)):
            if "_EyeHighLight" in property_names:
                material_role = "eye"
            elif "_StrokeMap" in property_names:
                material_role = "hair"
            elif "_SDFLightmap" in property_names:
                material_role = "skin"
            elif "_EnableRealisticLighting" in property_names or "_ClearCoat" in property_names:
                material_role = "cloth"
            else:
                material_role = "generic"
            preview["materialFamily"] = "characterNpr"
            if material_role != "cloth":
                preview["unlit"] = True
        if material_role:
            preview["materialFamily"] = "characterNpr"
            preview["materialRole"] = material_role
        preview["doubleSided"] = floats.get("_Cull") == 0.0
        for slot, preview_key in (
            ("_BaseMap", "baseColorTextureId"),
            ("_BumpMap", "normalTextureId"),
            ("_EmissionMap", "emissiveTextureId"),
        ):
            texture_id = texture_environments.get(slot, {}).get("textureId")
            if texture_id:
                preview[preview_key] = texture_id
        for enabled, slot, preview_key in (
            (floats.get("_UseMetallicGlossMap") == 1.0, "_MetallicGlossMap", "metallicGlossTextureId"),
            (floats.get("_UseDiffRampMap") == 1.0, "_DiffRampMap", "diffuseRampTextureId"),
            (floats.get("_UseSpecRampMap") == 1.0, "_SpecRampMap", "specularRampTextureId"),
            (floats.get("_UseSDFLightmap") == 1.0, "_SDFLightmap", "sdfLightmapTextureId"),
            (floats.get("_FaceHighlightMap") == 1.0, "_HighlightMap", "highlightTextureId"),
            (floats.get("_SilkStockings") == 1.0, "_SilkStockingsMask", "silkStockingsMaskTextureId"),
        ):
            texture_id = texture_environments.get(slot, {}).get("textureId")
            if enabled and texture_id:
                preview[preview_key] = texture_id
        if preview:
            material_record["previewPbr"] = preview
        document["materials"].append(material_record)

    def emit_mesh(mesh_id, material_ids):
        mesh = objects.get(mesh_id)
        if mesh is None or mesh.type_name != "Mesh":
            return None
        if mesh_id.document_id in emitted_meshes:
            return mesh_id.document_id

        vertex_count = mesh.payload.get("m_VertexCount")
        positions = mesh.payload.get("m_Vertices")
        indices = mesh.payload.get("m_Indices")
        submeshes = mesh.payload.get("m_SubMeshes")
        if not isinstance(vertex_count, int) or not _numeric_array(positions, vertex_count * 3):
            return None
        if not _integer_array(indices) or not isinstance(submeshes, list):
            return None

        mesh_document_id = mesh_id.document_id
        attributes = {
            "POSITION": add_accessor(
                mesh_document_id, "POSITION", positions, "f32", "vec3", vertex_count, bounds=True
            )
        }
        for field, semantic, width in (
            ("m_Normals", "NORMAL", 3),
            ("m_UV0", "TEXCOORD_0", 2),
            ("m_Tangents", "TANGENT", 4),
        ):
            values = mesh.payload.get(field)
            if _numeric_array(values, vertex_count * width):
                attributes[semantic] = add_accessor(
                    mesh_document_id, semantic, values, "f32", f"vec{width}", vertex_count
                )

        skin = mesh.payload.get("m_Skin")
        if isinstance(skin, list) and len(skin) == vertex_count:
            weights = [value for item in skin if isinstance(item, Mapping) for value in item.get("weight", [])]
            joints = [value for item in skin if isinstance(item, Mapping) for value in item.get("boneIndex", [])]
            if (
                _numeric_array(weights, vertex_count * 4)
                and _integer_array(joints, vertex_count * 4)
                and max(joints, default=0) <= 65535
            ):
                attributes["WEIGHTS_0"] = add_accessor(
                    mesh_document_id, "WEIGHTS_0", weights, "f32", "vec4", vertex_count
                )
                attributes["JOINTS_0"] = add_accessor(
                    mesh_document_id, "JOINTS_0", joints, "u16", "vec4", vertex_count
                )

        primitives = []
        index_cursor = 0
        for index, submesh in enumerate(submeshes):
            if not isinstance(submesh, Mapping) or not isinstance(submesh.get("indexCount"), int):
                continue
            index_count = submesh["indexCount"]
            submesh_indices = indices[index_cursor:index_cursor + index_count]
            index_cursor += index_count
            if len(submesh_indices) != index_count:
                continue
            primitive = {
                "topology": _topology(submesh.get("topology")),
                "attributes": dict(attributes),
                "indicesAccessorId": add_accessor(
                    mesh_document_id, f"INDICES_{index}", submesh_indices, "u32", "scalar", index_count
                ),
            }
            if index < len(material_ids) and material_ids[index] in objects:
                emit_material(material_ids[index])
                primitive["materialId"] = material_ids[index].document_id
            primitives.append(primitive)

        document["meshes"].append(
            {
                "id": mesh_document_id,
                "name": mesh.name,
                "primitives": primitives,
                "source": _source(mesh),
            }
        )
        emitted_meshes.add(mesh_document_id)
        return mesh_document_id

    skin_bindings = []
    for node in document.get("nodes", []):
        mesh_id = None
        material_ids = []
        for component in node.get("extras", {}).get("unityComponents", []):
            if component.get("type") not in {"MeshFilter", "MeshRenderer", "SkinnedMeshRenderer"}:
                continue
            for reference in component.get("references", []):
                target = _document_unity_id(reference.get("target"))
                if target is None:
                    continue
                field = str(reference.get("field") or "")
                if field.endswith("m_Mesh"):
                    mesh_id = target
                elif ".m_Materials[" in field:
                    material_ids.append(target)
        if mesh_id is not None:
            normalized_mesh_id = emit_mesh(mesh_id, material_ids)
            if normalized_mesh_id is not None:
                node["meshId"] = normalized_mesh_id
                renderer = next(
                    (
                        component
                        for component in node.get("extras", {}).get("unityComponents", [])
                        if component.get("type") == "SkinnedMeshRenderer"
                    ),
                    None,
                )
                if renderer is not None:
                    skin_bindings.append((node, renderer, mesh_id))

    transform_nodes = {}
    for transform in objects.values():
        if transform.type_name != "Transform":
            continue
        game_object = _first_target_of_type(
            transform, "GameObject", objects, lambda _owner, ref: _reference_target(ref)
        )
        if game_object is not None:
            transform_nodes[transform.identity] = game_object.identity.document_id

    skeleton_bones = {}
    skeleton_id = f"{document['asset']['id']}:skeleton"
    for node, renderer, mesh_id in skin_bindings:
        mesh = objects.get(mesh_id)
        bind_poses = mesh.payload.get("m_BindPose") if mesh is not None else None
        bone_references = sorted(
            (
                reference
                for reference in renderer.get("references", [])
                if str(reference.get("field") or "").startswith("$.m_Bones[")
            ),
            key=lambda reference: _array_reference_index(str(reference.get("field") or "")),
        )
        joint_ids = []
        for reference in bone_references:
            transform_id = _document_unity_id(reference.get("target"))
            node_id = transform_nodes.get(transform_id)
            transform = objects.get(transform_id) if transform_id is not None else None
            if node_id is None or transform is None:
                continue
            joint_ids.append(node_id)
            source_node = next((item for item in document["nodes"] if item["id"] == node_id), None)
            if source_node is not None:
                skeleton_bones[node_id] = {
                    "id": node_id,
                    "name": source_node["name"],
                    "transform": source_node["transform"],
                    "source": _source(transform),
                    **(
                        {"parentId": source_node["parentId"]}
                        if source_node.get("parentId") in skeleton_bones
                        else {}
                    ),
                }
        if not isinstance(bind_poses, list) or len(bind_poses) != len(joint_ids):
            continue
        matrices = []
        for matrix in bind_poses:
            if not isinstance(matrix, Mapping):
                matrices = []
                break
            # AnimeStudio exposes these matrices in row-vector order. glTF uses
            # column vectors, so retaining the JSON row order performs the
            # convention transpose while writing its column-major accessor.
            values = [matrix.get(f"M{row}{column}") for row in range(4) for column in range(4)]
            if not all(isinstance(value, (int, float)) for value in values):
                matrices = []
                break
            matrices.extend(values)
        if not matrices:
            continue
        renderer_source = renderer.get("source", {})
        renderer_source_id = f"unity:{renderer_source.get('sourceFile')}:{renderer_source.get('pathId')}"
        skin_id = f"{renderer_source_id}:skin"
        inverse_bind_accessor = add_accessor(
            skin_id,
            "INVERSE_BIND_MATRICES",
            matrices,
            "f32",
            "mat4",
            len(joint_ids),
        )
        root_reference = next(
            (
                reference
                for reference in renderer.get("references", [])
                if str(reference.get("field") or "") == "$.m_RootBone"
            ),
            None,
        )
        root_transform = _document_unity_id(root_reference.get("target")) if root_reference else None
        skin = {
            "id": skin_id,
            "skeletonId": skeleton_id,
            "jointIds": joint_ids,
            "inverseBindMatricesAccessorId": inverse_bind_accessor,
            "source": dict(renderer_source),
        }
        root_bone_id = transform_nodes.get(root_transform)
        if root_bone_id in joint_ids:
            skin["rootBoneId"] = root_bone_id
        document["skins"].append(skin)
        node["skinId"] = skin_id
        node["skeletonId"] = skeleton_id

    if skeleton_bones:
        bone_ids = set(skeleton_bones)
        for bone_id, bone in skeleton_bones.items():
            source_node = next((item for item in document["nodes"] if item["id"] == bone_id), None)
            parent_id = source_node.get("parentId") if source_node else None
            if parent_id in bone_ids:
                bone["parentId"] = parent_id
            else:
                bone.pop("parentId", None)
        document["skeletons"].append(
            {
                "id": skeleton_id,
                "name": f"{document['asset']['name']} Skeleton",
                "bones": list(skeleton_bones.values()),
            }
        )

    if geometry:
        document["buffers"].append(
            {
                "id": GEOMETRY_BUFFER_ID,
                "uri": buffer_uri,
                "byteLength": len(geometry),
                "sha256": hashlib.sha256(geometry).hexdigest(),
            }
        )
    return bytes(geometry)


def collect_material_textures(
    document: Mapping[str, Any],
    objects: Mapping[UnityObjectId, AnimeStudioObject],
) -> dict[UnityObjectId, dict[str, Any]]:
    """Collect non-null Texture2D objects referenced by emitted materials."""

    material_ids = {
        _document_unity_id(material.get("id"))
        for material in document.get("materials", [])
        if isinstance(material, Mapping)
    }
    textures = {}
    for material_id in material_ids:
        material = objects.get(material_id) if material_id is not None else None
        if material is None:
            continue
        for reference in _references(material):
            if reference.get("targetType") != "Texture2D":
                continue
            target = _reference_target(reference)
            if target is None:
                continue
            textures[target] = {
                "name": str(reference.get("targetName") or ""),
                "source": {
                    "sourceFile": target.source_file,
                    "pathId": target.path_id,
                },
            }
    return textures


def attach_texture_images(
    document: dict[str, Any],
    textures: Mapping[UnityObjectId, Mapping[str, Any]],
    image_uris: Mapping[UnityObjectId, str],
) -> None:
    """Attach exported texture images while preserving Unity texture identity."""

    for texture_id, texture in textures.items():
        uri = image_uris.get(texture_id)
        if not uri:
            continue
        image_id = f"{texture_id.document_id}:image"
        source = dict(texture.get("source") or {})
        document["images"].append(
            {
                "id": image_id,
                "uri": uri,
                "mimeType": "image/png",
                "source": source,
            }
        )
        document["textures"].append(
            {
                "id": texture_id.document_id,
                "imageId": image_id,
                "source": source,
            }
        )


def _references(obj: AnimeStudioObject) -> list[Mapping[str, Any]]:
    value = obj.metadata.get("pptrReferences", [])
    references = [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []
    if obj.type_name == "GameObject":
        # Concrete GameObject JSON also contains convenience component fields.
        # Unity's serialized m_Components array is the authoritative source.
        return [item for item in references if str(item.get("path") or "").startswith("$.m_Components")]
    return references


def _reference_target(reference: Mapping[str, Any]) -> UnityObjectId | None:
    source_file = reference.get("targetSourceFile")
    path_id = reference.get("targetPathId")
    if isinstance(source_file, str) and source_file and isinstance(path_id, int):
        return UnityObjectId(source_file, path_id)
    return None


def _first_target_of_type(obj, type_name, objects, register):
    for reference in _references(obj):
        if reference.get("targetType") != type_name:
            continue
        target = register(obj, reference)
        if target is not None:
            return objects.get(target)
    return None


def _source(obj: AnimeStudioObject, *, logical_path: str | None = None, bundle: str | None = None) -> dict[str, Any]:
    source = {
        "sourceFile": obj.identity.source_file,
        "pathId": obj.identity.path_id,
    }
    if obj.class_id is not None:
        source["classId"] = obj.class_id
    container = obj.metadata.get("container")
    if isinstance(container, str) and container:
        source["container"] = container
    if logical_path is not None:
        source["logicalPath"] = logical_path
    if bundle is not None:
        source["bundle"] = bundle
    return source


def _active_state(game_object: AnimeStudioObject) -> bool:
    value = game_object.payload.get("m_IsActive")
    return bool(value) if isinstance(value, (bool, int)) else True


def _component_summary(component: AnimeStudioObject) -> dict[str, Any]:
    references = []
    for reference in _references(component):
        target = _reference_target(reference)
        references.append(
            {
                "field": str(reference.get("path") or ""),
                "targetType": str(reference.get("targetType") or "Unknown"),
                "target": target.document_id if target is not None else None,
            }
        )
    enabled = component.payload.get("m_Enabled")
    summary = {
        "type": component.type_name,
        "enabled": bool(enabled) if isinstance(enabled, (bool, int)) else True,
        "source": _source(component),
        "references": references,
    }
    if component.type_name == "LODGroup":
        levels = component.payload.get("m_LODs")
        summary["properties"] = {
            "localReferencePoint": _vector(
                component.payload.get("m_LocalReferencePoint"), ("x", "y", "z")
            ),
            "size": component.payload.get("m_Size"),
            "fadeMode": component.payload.get("m_FadeMode"),
            "levels": [
                {
                    "screenRelativeHeight": level.get("screenRelativeHeight"),
                    "fadeTransitionWidth": level.get("fadeTransitionWidth"),
                }
                for level in levels
                if isinstance(level, Mapping)
            ] if isinstance(levels, list) else [],
        }
    return summary


def _annotate_lod_renderers(document: dict[str, Any]) -> None:
    renderer_levels: dict[str, tuple[int, str]] = {}
    for node in document.get("nodes", []):
        for component in node.get("extras", {}).get("unityComponents", []):
            if component.get("type") != "LODGroup":
                continue
            source = component.get("source", {})
            group_id = f"unity:{source.get('sourceFile')}:{source.get('pathId')}"
            for reference in component.get("references", []):
                match = LOD_RENDERER_PATH_RE.match(str(reference.get("field") or ""))
                target = reference.get("target")
                if match and isinstance(target, str):
                    level = int(match.group(1))
                    previous = renderer_levels.get(target)
                    if previous is None or level < previous[0]:
                        renderer_levels[target] = (level, group_id)

    for node in document.get("nodes", []):
        for component in node.get("extras", {}).get("unityComponents", []):
            if component.get("type") not in {"MeshRenderer", "SkinnedMeshRenderer"}:
                continue
            source = component.get("source", {})
            renderer_id = f"unity:{source.get('sourceFile')}:{source.get('pathId')}"
            assignment = renderer_levels.get(renderer_id)
            if assignment is None:
                continue
            component["lodLevel"], component["lodGroupId"] = assignment
            node["extras"]["lodLevel"] = assignment[0]
            node["extras"]["lodGroupId"] = assignment[1]


def _local_transform(payload: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    translation = _vector(payload.get("m_LocalPosition"), ("x", "y", "z"))
    rotation = _vector(payload.get("m_LocalRotation"), ("x", "y", "z", "w"))
    scale = _vector(payload.get("m_LocalScale"), ("x", "y", "z"))
    if translation is not None:
        result["translation"] = translation
    if rotation is not None:
        result["rotation"] = rotation
    if scale is not None:
        result["scale"] = scale
    return result


def _vector(value: Any, fields: Iterable[str]) -> list[float] | None:
    if not isinstance(value, Mapping):
        return None
    values = [
        value.get(field) if field in value else value.get(field.upper())
        for field in fields
    ]
    if not all(isinstance(component, (int, float)) for component in values):
        return None
    return [float(component) for component in values]


def _pointer_path_id(value: Any) -> int | None:
    if not isinstance(value, Mapping):
        return None
    path_id = value.get("m_PathID")
    return path_id if isinstance(path_id, int) else None


def _numeric_array(value: Any, expected_length: int) -> bool:
    return (
        isinstance(value, list)
        and len(value) == expected_length
        and all(isinstance(item, (int, float)) for item in value)
    )


def _plain_mapping(value: Any) -> dict[str, Any]:
    return {str(key): item for key, item in value.items()} if isinstance(value, Mapping) else {}


def _clamp_number(value: Any, minimum: float, maximum: float, *, default: float = 0.0) -> float:
    if not isinstance(value, (int, float)):
        return default
    return max(minimum, min(maximum, float(value)))


def _integer_array(value: Any, expected_length: int | None = None) -> bool:
    return (
        isinstance(value, list)
        and (expected_length is None or len(value) == expected_length)
        and all(isinstance(item, int) and item >= 0 for item in value)
    )


def _document_unity_id(value: Any) -> UnityObjectId | None:
    if not isinstance(value, str) or not value.startswith("unity:"):
        return None
    source_file, separator, path_id = value[len("unity:"):].rpartition(":")
    if not separator:
        return None
    try:
        return UnityObjectId(source_file, int(path_id))
    except ValueError:
        return None


def _topology(value: Any) -> str:
    return {
        "Triangles": "triangles",
        "TriangleStrip": "triangleStrip",
        "Lines": "lines",
        "LineStrip": "lines",
        "Points": "points",
    }.get(str(value), "triangles")


def _array_reference_index(path: str) -> int:
    try:
        return int(path.rsplit("[", 1)[1].split("]", 1)[0])
    except (IndexError, ValueError):
        return 2**31 - 1


def _normalize_container(value: str) -> str:
    return value.replace("\\", "/").strip("/").casefold()
