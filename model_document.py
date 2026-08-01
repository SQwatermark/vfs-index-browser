"""ModelDocument construction and semantic validation."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from model_assembly import validate_model_assembly


MODEL_DOCUMENT_FORMAT = "EndfieldModelDocument"
MODEL_DOCUMENT_VERSION = "2.0.0"
MODEL_DOCUMENT_SCHEMA = Path(__file__).with_name("schemas") / "model-document.schema.json"

COLLECTIONS = (
    "buffers", "bufferViews", "accessors", "nodes", "meshes", "skeletons",
    "skins", "materials", "textures", "images", "animations",
    "animatorControllers",
)

COMPONENT_SIZES = {"i8": 1, "u8": 1, "i16": 2, "u16": 2, "f16": 2, "u32": 4, "f32": 4}
TYPE_COMPONENTS = {"scalar": 1, "vec2": 2, "vec3": 3, "vec4": 4, "mat4": 16}


def create_model_document(
    asset_id: str,
    name: str,
    source: Mapping[str, Any],
    *,
    root_node_ids: Iterable[str] = (),
    assembly: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create an empty document with all collections present."""

    asset = {
        "id": asset_id,
        "name": name,
        "source": deepcopy(dict(source)),
        "rootNodeIds": list(root_node_ids),
    }
    if assembly is not None:
        asset["assembly"] = deepcopy(dict(assembly))
    return {
        "format": MODEL_DOCUMENT_FORMAT,
        "version": MODEL_DOCUMENT_VERSION,
        "asset": asset,
        **{collection: [] for collection in COLLECTIONS},
        "dependencies": [],
        "diagnostics": [],
    }


def add_diagnostic(
    document: dict[str, Any],
    severity: str,
    code: str,
    message: str,
    *,
    object_id: str | None = None,
    source: Mapping[str, Any] | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Append a structured diagnostic and return it."""

    diagnostic: dict[str, Any] = {"severity": severity, "code": code, "message": message}
    if object_id is not None:
        diagnostic["objectId"] = object_id
    if source is not None:
        diagnostic["source"] = deepcopy(dict(source))
    if details is not None:
        diagnostic["details"] = deepcopy(dict(details))
    document.setdefault("diagnostics", []).append(diagnostic)
    return diagnostic


def validate_model_document(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Check cross-object semantics that JSON Schema cannot express."""

    errors: list[dict[str, Any]] = []

    def error(code: str, message: str, object_id: str | None = None) -> None:
        value: dict[str, Any] = {"code": code, "message": message}
        if object_id is not None:
            value["objectId"] = object_id
        errors.append(value)

    for schema_error in _model_document_validator().iter_errors(document):
        path = ".".join(str(part) for part in schema_error.absolute_path)
        location = path or "<root>"
        error("SCHEMA_VALIDATION_ERROR", f"{location}: {schema_error.message}")

    if document.get("format") != MODEL_DOCUMENT_FORMAT:
        error("INVALID_FORMAT", f"format must be {MODEL_DOCUMENT_FORMAT!r}")
    if document.get("version") != MODEL_DOCUMENT_VERSION:
        error("UNSUPPORTED_VERSION", f"version must be {MODEL_DOCUMENT_VERSION!r}")

    asset = document.get("asset")
    if isinstance(asset, Mapping):
        assembly = asset.get("assembly")
        if isinstance(assembly, Mapping):
            errors.extend(validate_model_assembly(assembly))

    indexes: dict[str, dict[str, Mapping[str, Any]]] = {}
    global_ids: dict[str, str] = {}
    for collection in COLLECTIONS:
        values = document.get(collection)
        if not isinstance(values, list):
            error("INVALID_COLLECTION", f"{collection} must be an array")
            indexes[collection] = {}
            continue
        index: dict[str, Mapping[str, Any]] = {}
        for position, value in enumerate(values):
            if not isinstance(value, Mapping):
                error("INVALID_OBJECT", f"{collection}[{position}] must be an object")
                continue
            object_id = value.get("id")
            if not isinstance(object_id, str) or not object_id:
                error("MISSING_ID", f"{collection}[{position}] has no non-empty id")
                continue
            if object_id in index:
                error("DUPLICATE_ID", f"duplicate id {object_id!r} in {collection}", object_id)
                continue
            if object_id in global_ids:
                error(
                    "DUPLICATE_GLOBAL_ID",
                    f"id {object_id!r} is shared by {global_ids[object_id]} and {collection}",
                    object_id,
                )
            else:
                global_ids[object_id] = collection
            index[object_id] = value
        indexes[collection] = index

    _validate_buffers(indexes, error)
    _validate_nodes(document, indexes, error)
    _validate_assembly_references(document, indexes, error)

    for node in indexes["nodes"].values():
        _optional_ref(node, "meshId", "meshes", indexes, error)
        _optional_ref(node, "skinId", "skins", indexes, error)
        _optional_ref(node, "skeletonId", "skeletons", indexes, error)

    for mesh in indexes["meshes"].values():
        for primitive in _object_list(mesh.get("primitives")):
            attributes = primitive.get("attributes")
            if isinstance(attributes, Mapping):
                for accessor_id in attributes.values():
                    _value_ref(accessor_id, "accessors", mesh, indexes, error)
            _optional_ref(primitive, "indicesAccessorId", "accessors", indexes, error, owner=mesh)
            _optional_ref(primitive, "materialId", "materials", indexes, error, owner=mesh)
        for blend_shape in _object_list(mesh.get("blendShapes")):
            for frame in _object_list(blend_shape.get("frames")):
                attributes = frame.get("attributes")
                if isinstance(attributes, Mapping):
                    for accessor_id in attributes.values():
                        _value_ref(accessor_id, "accessors", mesh, indexes, error)

    for skin in indexes["skins"].values():
        _optional_ref(skin, "skeletonId", "skeletons", indexes, error, optional=False)
        _optional_ref(skin, "inverseBindMatricesAccessorId", "accessors", indexes, error, optional=False)
        skeleton = indexes["skeletons"].get(skin.get("skeletonId"))
        bone_ids = {
            bone.get("id") for bone in _object_list(skeleton.get("bones") if skeleton else None)
        }
        for joint_id in skin.get("jointIds", []):
            if joint_id not in bone_ids:
                error("UNRESOLVED_JOINT", f"joint {joint_id!r} is not in the skin skeleton", skin.get("id"))
        root_bone_id = skin.get("rootBoneId")
        if root_bone_id is not None and root_bone_id not in bone_ids:
            error("UNRESOLVED_ROOT_BONE", f"root bone {root_bone_id!r} is not in the skin skeleton", skin.get("id"))

    for texture in indexes["textures"].values():
        _optional_ref(texture, "imageId", "images", indexes, error, optional=False)

    target_ids = set(global_ids)
    for animation in indexes["animations"].values():
        for channel in _object_list(animation.get("channels")):
            if channel.get("targetId") not in target_ids:
                error(
                    "UNRESOLVED_ANIMATION_TARGET",
                    f"animation target {channel.get('targetId')!r} is missing",
                    animation.get("id"),
                )
            for field in ("inputAccessorId", "outputAccessorId"):
                _optional_ref(channel, field, "accessors", indexes, error, owner=animation, optional=False)

    return errors


@lru_cache(maxsize=1)
def _model_document_validator() -> Draft202012Validator:
    schema = json.loads(MODEL_DOCUMENT_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _validate_assembly_references(document, indexes, error) -> None:
    asset = document.get("asset")
    assembly = asset.get("assembly") if isinstance(asset, Mapping) else None
    if not isinstance(assembly, Mapping):
        return
    for part in _object_list(assembly.get("parts")):
        node_id = part.get("nodeId")
        if node_id not in indexes["nodes"]:
            error(
                "UNRESOLVED_ASSEMBLY_NODE",
                f"assembly node {node_id!r} is missing",
                part.get("id"),
            )


def _validate_buffers(indexes, error) -> None:
    for view in indexes["bufferViews"].values():
        buffer = indexes["buffers"].get(view.get("bufferId"))
        if buffer is None:
            error("UNRESOLVED_REFERENCE", f"missing buffer {view.get('bufferId')!r}", view.get("id"))
            continue
        values = (view.get("byteOffset"), view.get("byteLength"), buffer.get("byteLength"))
        if not all(isinstance(value, int) and value >= 0 for value in values):
            error("INVALID_BUFFER_RANGE", "buffer offsets and lengths must be non-negative integers", view.get("id"))
        elif values[0] + values[1] > values[2]:
            error("BUFFER_VIEW_OUT_OF_RANGE", "bufferView extends past its buffer", view.get("id"))

    for accessor in indexes["accessors"].values():
        view = indexes["bufferViews"].get(accessor.get("bufferViewId"))
        if view is None:
            error("UNRESOLVED_REFERENCE", f"missing bufferView {accessor.get('bufferViewId')!r}", accessor.get("id"))
            continue
        component_size = COMPONENT_SIZES.get(accessor.get("componentType"))
        component_count = TYPE_COMPONENTS.get(accessor.get("type"))
        count = accessor.get("count")
        offset = accessor.get("byteOffset", 0)
        if component_size is None or component_count is None or not isinstance(count, int) or count < 0:
            error("INVALID_ACCESSOR_LAYOUT", "accessor component type, shape or count is invalid", accessor.get("id"))
            continue
        element_size = component_size * component_count
        stride = view.get("byteStride", element_size)
        if not isinstance(offset, int) or offset < 0 or not isinstance(stride, int) or stride < element_size:
            error("INVALID_ACCESSOR_LAYOUT", "accessor offset or stride is invalid", accessor.get("id"))
            continue
        required = offset if count == 0 else offset + (count - 1) * stride + element_size
        if required > view.get("byteLength", -1):
            error("ACCESSOR_OUT_OF_RANGE", "accessor extends past its bufferView", accessor.get("id"))


def _validate_nodes(document, indexes, error) -> None:
    nodes = indexes["nodes"]
    asset = document.get("asset")
    roots = asset.get("rootNodeIds", []) if isinstance(asset, Mapping) else []
    for root_id in roots:
        node = nodes.get(root_id)
        if node is None:
            error("UNRESOLVED_ROOT_NODE", f"root node {root_id!r} is missing")
        elif node.get("parentId") is not None:
            error("ROOT_NODE_HAS_PARENT", f"root node {root_id!r} has a parent", root_id)

    for node_id, node in nodes.items():
        parent_id = node.get("parentId")
        if parent_id is not None:
            parent = nodes.get(parent_id)
            if parent is None:
                error("UNRESOLVED_PARENT", f"parent node {parent_id!r} is missing", node_id)
            elif node_id not in parent.get("children", []):
                error("INCONSISTENT_NODE_TREE", f"parent {parent_id!r} does not list this child", node_id)
        for child_id in node.get("children", []):
            child = nodes.get(child_id)
            if child is None:
                error("UNRESOLVED_CHILD", f"child node {child_id!r} is missing", node_id)
            elif child.get("parentId") != node_id:
                error("INCONSISTENT_NODE_TREE", f"child {child_id!r} points to a different parent", node_id)

    state: dict[str, int] = {}

    def visit(node_id: str) -> None:
        if state.get(node_id) == 1:
            error("NODE_CYCLE", f"node tree contains a cycle at {node_id!r}", node_id)
            return
        if state.get(node_id) == 2:
            return
        state[node_id] = 1
        for child_id in nodes[node_id].get("children", []):
            if child_id in nodes:
                visit(child_id)
        state[node_id] = 2

    for node_id in nodes:
        visit(node_id)


def _object_list(value: Any) -> list[Mapping[str, Any]]:
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _value_ref(value, collection, owner, indexes, error) -> None:
    if not isinstance(value, str) or value not in indexes[collection]:
        error("UNRESOLVED_REFERENCE", f"reference to missing {collection} id {value!r}", owner.get("id"))


def _optional_ref(owner_value, field, collection, indexes, error, *, owner=None, optional=True) -> None:
    value = owner_value.get(field)
    if value is None and optional:
        return
    _value_ref(value, collection, owner or owner_value, indexes, error)
