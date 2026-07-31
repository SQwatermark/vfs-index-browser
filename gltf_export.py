"""Export a ModelDocument and its sidecars as a self-contained GLB."""

from __future__ import annotations

import json
import math
import struct
from collections.abc import Callable, Mapping
from io import BytesIO
from typing import Any

from PIL import Image


COMPONENT_TYPES = {"i8": 5120, "u8": 5121, "i16": 5122, "u16": 5123, "u32": 5125, "f32": 5126}


def _unity_texture_info(texture_index: int) -> dict[str, Any]:
    """Reference a flipped PNG using Unity's bottom-left texture coordinates."""

    return {
        "index": texture_index,
        "extensions": {
            "KHR_texture_transform": {
                "offset": [0.0, 1.0],
                "scale": [1.0, -1.0],
            }
        },
    }


def _is_preview_node(node: Mapping[str, Any]) -> bool:
    if not node.get("meshId") or not node.get("active", True):
        return False
    lod_level = node.get("extras", {}).get("lodLevel")
    if lod_level is not None:
        return lod_level == 0
    return "shadowproxy" not in str(node.get("name", "")).casefold()


def build_glb(
    document: Mapping[str, Any],
    geometry: bytes,
    image_loader: Callable[[Mapping[str, Any]], bytes],
) -> bytes:
    """Build a GLB while preserving ModelDocument object relationships."""

    nodes = document.get("nodes", [])
    selected_mesh_ids = {
        node["meshId"]
        for node in nodes
        if _is_preview_node(node)
    }
    selected_skin_ids = {
        node["skinId"]
        for node in nodes
        if (
            _is_preview_node(node)
            and node.get("skinId")
        )
    }
    selected_meshes = [
        mesh for mesh in document.get("meshes", []) if mesh.get("id") in selected_mesh_ids
    ]
    selected_material_ids = {
        primitive["materialId"]
        for mesh in selected_meshes
        for primitive in mesh.get("primitives", [])
        if primitive.get("materialId")
    }
    selected_materials = [
        material
        for material in document.get("materials", [])
        if material.get("id") in selected_material_ids
    ]
    selected_texture_ids = {
        texture_id
        for material in selected_materials
        for texture_id in (
            material.get("previewPbr", {}).get("baseColorTextureId"),
            material.get("previewPbr", {}).get("normalTextureId"),
            material.get("previewPbr", {}).get("metallicGlossTextureId"),
            material.get("previewPbr", {}).get("diffuseRampTextureId"),
            material.get("previewPbr", {}).get("specularRampTextureId"),
            material.get("previewPbr", {}).get("sdfLightmapTextureId"),
            material.get("previewPbr", {}).get("sdfMaskTextureId"),
            material.get("previewPbr", {}).get("shadowLutTextureId"),
            material.get("previewPbr", {}).get("highlightTextureId"),
            material.get("previewPbr", {}).get("silkStockingsMaskTextureId"),
        )
        if texture_id
    }
    selected_textures = [
        texture
        for texture in document.get("textures", [])
        if texture.get("id") in selected_texture_ids
    ]
    gray_alpha_texture_ids = {
        material.get("previewPbr", {}).get("baseColorTextureId")
        for material in selected_materials
        if material.get("previewPbr", {}).get("baseColorTextureUsesGrayAsAlpha")
    }
    gray_alpha_image_ids = {
        texture.get("imageId")
        for texture in selected_textures
        if texture.get("id") in gray_alpha_texture_ids
    }
    metallic_gloss_texture_ids = {
        material.get("previewPbr", {}).get("metallicGlossTextureId")
        for material in selected_materials
        if material.get("previewPbr", {}).get("metallicGlossTextureId")
    }
    metallic_gloss_image_ids = {
        texture.get("imageId")
        for texture in selected_textures
        if texture.get("id") in metallic_gloss_texture_ids
    }
    normal_texture_ids = {
        material.get("previewPbr", {}).get("normalTextureId")
        for material in selected_materials
        if material.get("previewPbr", {}).get("normalTextureId")
    }
    normal_image_ids = {
        texture.get("imageId")
        for texture in selected_textures
        if texture.get("id") in normal_texture_ids
    }
    selected_image_ids = {texture.get("imageId") for texture in selected_textures}
    selected_accessor_ids = {
        accessor_id
        for mesh in selected_meshes
        for primitive in mesh.get("primitives", [])
        for accessor_id in (
            *primitive.get("attributes", {}).values(),
            primitive.get("indicesAccessorId"),
        )
        if accessor_id
    }
    selected_skins = [
        skin for skin in document.get("skins", []) if skin.get("id") in selected_skin_ids
    ]
    selected_accessor_ids.update(
        skin["inverseBindMatricesAccessorId"]
        for skin in selected_skins
        if skin.get("inverseBindMatricesAccessorId")
    )
    node_ids = {node.get("id") for node in nodes}
    selected_animations = []
    for animation in document.get("animations", []):
        channels = [
            channel
            for channel in animation.get("channels", [])
            if (
                channel.get("targetId") in node_ids
                and channel.get("property") in {"translation", "rotation", "scale"}
            )
        ]
        if not channels:
            continue
        selected_animations.append((animation, channels))
        selected_accessor_ids.update(
            accessor_id
            for channel in channels
            for accessor_id in (
                channel.get("inputAccessorId"),
                channel.get("outputAccessorId"),
            )
            if accessor_id
        )
    selected_accessors = [
        accessor
        for accessor in document.get("accessors", [])
        if accessor.get("id") in selected_accessor_ids
    ]
    selected_view_ids = {accessor["bufferViewId"] for accessor in selected_accessors}

    binary = bytearray()
    gltf: dict[str, Any] = {
        "asset": {"version": "2.0", "generator": "Endfield VFS Index Browser"},
        "buffers": [{"byteLength": 0}],
        "bufferViews": [],
        "accessors": [],
        "images": [],
        "textures": [],
        "materials": [],
        "meshes": [],
        "nodes": [],
        "skins": [],
        "animations": [],
        "scenes": [{"nodes": []}],
        "scene": 0,
    }

    view_indexes = {}
    for view in document.get("bufferViews", []):
        if view.get("id") not in selected_view_ids:
            continue
        while len(binary) % 4:
            binary.append(0)
        source_offset = int(view["byteOffset"])
        source_end = source_offset + int(view["byteLength"])
        target_offset = len(binary)
        binary.extend(geometry[source_offset:source_end])
        view_indexes[view["id"]] = len(gltf["bufferViews"])
        value = {
            "buffer": 0,
            "byteOffset": target_offset,
            "byteLength": view["byteLength"],
        }
        if "byteStride" in view:
            value["byteStride"] = view["byteStride"]
        gltf["bufferViews"].append(value)

    accessor_indexes = {}
    for accessor in selected_accessors:
        accessor_indexes[accessor["id"]] = len(gltf["accessors"])
        value = {
            "bufferView": view_indexes[accessor["bufferViewId"]],
            "byteOffset": accessor.get("byteOffset", 0),
            "componentType": COMPONENT_TYPES[accessor["componentType"]],
            "type": accessor["type"].upper(),
            "count": accessor["count"],
        }
        for field in ("normalized", "min", "max"):
            if field in accessor:
                value[field] = accessor[field]
        gltf["accessors"].append(value)

    image_indexes = {}
    for image in document.get("images", []):
        if image.get("id") not in selected_image_ids:
            continue
        while len(binary) % 4:
            binary.append(0)
        offset = len(binary)
        payload = image_loader(image)
        if image.get("id") in gray_alpha_image_ids:
            payload = _red_channel_to_alpha_png(payload)
        elif image.get("id") in metallic_gloss_image_ids:
            payload = _metallic_gloss_to_gltf_png(payload)
        elif image.get("id") in normal_image_ids:
            payload = _rg_normal_to_gltf_png(payload)
        binary.extend(payload)
        view_index = len(gltf["bufferViews"])
        gltf["bufferViews"].append(
            {"buffer": 0, "byteOffset": offset, "byteLength": len(payload)}
        )
        image_indexes[image["id"]] = len(gltf["images"])
        converted_to_png = image.get("id") in (
            gray_alpha_image_ids | metallic_gloss_image_ids | normal_image_ids
        )
        gltf["images"].append({
            "name": image["id"],
            "bufferView": view_index,
            "mimeType": "image/png" if converted_to_png else image["mimeType"],
        })

    texture_indexes = {}
    for texture in selected_textures:
        image_index = image_indexes.get(texture["imageId"])
        if image_index is None:
            continue
        texture_indexes[texture["id"]] = len(gltf["textures"])
        gltf["textures"].append({"source": image_index})

    material_indexes = {}
    uses_unlit = False
    uses_texture_transform = False
    for material in selected_materials:
        preview = material.get("previewPbr", {})
        pbr = {
            "metallicFactor": preview.get("metallicFactor", 0.0),
            "roughnessFactor": preview.get("roughnessFactor", 0.8),
        }
        if "baseColorFactor" in preview:
            pbr["baseColorFactor"] = preview["baseColorFactor"]
        base_texture = texture_indexes.get(preview.get("baseColorTextureId"))
        if base_texture is not None:
            pbr["baseColorTexture"] = _unity_texture_info(base_texture)
            uses_texture_transform = True
        metallic_gloss_texture = texture_indexes.get(preview.get("metallicGlossTextureId"))
        if metallic_gloss_texture is not None:
            # HGRP packs metal/spec/shadow/smoothness in RGBA. Core glTF can
            # represent only metallic and roughness, so the other channels stay
            # available in ModelDocument for a future game-specific material.
            pbr["metallicFactor"] = 1.0
            pbr["roughnessFactor"] = 1.0
            pbr["metallicRoughnessTexture"] = _unity_texture_info(metallic_gloss_texture)
            uses_texture_transform = True
        value = {
            "name": material["name"],
            "pbrMetallicRoughness": pbr,
            "doubleSided": preview.get("doubleSided", True),
        }
        preview_metadata = {
            key: preview[key]
            for key in (
                "materialFamily",
                "materialRole",
                "silkStockings",
                "overlayShadow",
                "baseColorTextureId",
                "metallicGlossTextureId",
                "diffuseRampTextureId",
                "specularRampTextureId",
                "sdfLightmapTextureId",
                "sdfMaskTextureId",
                "shadowLutTextureId",
                "highlightTextureId",
                "silkStockingsMaskTextureId",
            )
            if key in preview
        }
        if preview_metadata:
            value["extras"] = {"endfieldPreview": preview_metadata}
        if preview.get("alphaMode") in {"MASK", "BLEND"}:
            value["alphaMode"] = preview["alphaMode"]
        if preview.get("alphaMode") == "MASK":
            value["alphaCutoff"] = preview.get("alphaCutoff", 0.5)
        if preview.get("unlit"):
            value["extensions"] = {"KHR_materials_unlit": {}}
            uses_unlit = True
        normal_texture = texture_indexes.get(preview.get("normalTextureId"))
        if normal_texture is not None:
            value["normalTexture"] = _unity_texture_info(normal_texture)
            uses_texture_transform = True
        material_indexes[material["id"]] = len(gltf["materials"])
        gltf["materials"].append(value)
    extensions_used = []
    if uses_unlit:
        extensions_used.append("KHR_materials_unlit")
    if uses_texture_transform:
        extensions_used.append("KHR_texture_transform")
    if extensions_used:
        gltf["extensionsUsed"] = extensions_used

    mesh_indexes = {}
    for mesh in selected_meshes:
        primitives = []
        for primitive in mesh.get("primitives", []):
            value = {
                "attributes": {
                    semantic: accessor_indexes[accessor_id]
                    for semantic, accessor_id in primitive.get("attributes", {}).items()
                },
                "mode": {"triangles": 4, "triangleStrip": 5, "lines": 1, "points": 0}.get(
                    primitive.get("topology"), 4
                ),
            }
            if primitive.get("indicesAccessorId") in accessor_indexes:
                value["indices"] = accessor_indexes[primitive["indicesAccessorId"]]
            if primitive.get("materialId") in material_indexes:
                value["material"] = material_indexes[primitive["materialId"]]
            primitives.append(value)
        mesh_indexes[mesh["id"]] = len(gltf["meshes"])
        gltf["meshes"].append({"name": mesh["name"], "primitives": primitives})

    node_indexes = {node["id"]: index for index, node in enumerate(nodes)}
    for node in nodes:
        value = {
            "name": node["name"],
            "extras": {"endfieldNodeId": node["id"]},
        }
        transform = node.get("transform", {})
        for source_field, target_field in (
            ("translation", "translation"),
            ("rotation", "rotation"),
            ("scale", "scale"),
        ):
            if source_field in transform:
                value[target_field] = transform[source_field]
        children = [node_indexes[child] for child in node.get("children", []) if child in node_indexes]
        if children:
            value["children"] = children
        if node.get("meshId") in mesh_indexes and _is_preview_node(node):
            value["mesh"] = mesh_indexes[node["meshId"]]
        gltf["nodes"].append(value)

    skin_indexes = {}
    for skin in selected_skins:
        joints = [node_indexes[joint] for joint in skin.get("jointIds", []) if joint in node_indexes]
        if not joints or skin.get("inverseBindMatricesAccessorId") not in accessor_indexes:
            continue
        value = {
            "joints": joints,
            "inverseBindMatrices": accessor_indexes[skin["inverseBindMatricesAccessorId"]],
        }
        if skin.get("rootBoneId") in node_indexes:
            value["skeleton"] = node_indexes[skin["rootBoneId"]]
        skin_indexes[skin["id"]] = len(gltf["skins"])
        gltf["skins"].append(value)
    for document_node, gltf_node in zip(nodes, gltf["nodes"]):
        if "mesh" in gltf_node and document_node.get("skinId") in skin_indexes:
            gltf_node["skin"] = skin_indexes[document_node["skinId"]]

    for animation, channels in selected_animations:
        gltf_samplers = []
        gltf_channels = []
        for channel in channels:
            input_index = accessor_indexes.get(channel.get("inputAccessorId"))
            output_index = accessor_indexes.get(channel.get("outputAccessorId"))
            target_index = node_indexes.get(channel.get("targetId"))
            if input_index is None or output_index is None or target_index is None:
                continue
            sampler_index = len(gltf_samplers)
            gltf_samplers.append(
                {
                    "input": input_index,
                    "output": output_index,
                    "interpolation": {
                        "step": "STEP",
                        "linear": "LINEAR",
                        "cubic": "CUBICSPLINE",
                    }[channel.get("interpolation", "linear")],
                }
            )
            gltf_channels.append(
                {
                    "sampler": sampler_index,
                    "target": {
                        "node": target_index,
                        "path": channel["property"],
                    },
                }
            )
        if gltf_channels:
            gltf["animations"].append(
                {
                    "name": animation.get("name", ""),
                    "samplers": gltf_samplers,
                    "channels": gltf_channels,
                }
            )

    roots = [node_indexes[root] for root in document.get("asset", {}).get("rootNodeIds", []) if root in node_indexes]
    wrapper_index = len(gltf["nodes"])
    gltf["nodes"].append(
        {"name": "UnityToGltf", "scale": [-1.0, 1.0, 1.0], "children": roots}
    )
    gltf["scenes"][0]["nodes"] = [wrapper_index]
    gltf["buffers"][0]["byteLength"] = len(binary)

    json_bytes = json.dumps(gltf, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * ((-len(json_bytes)) % 4)
    binary.extend(b"\0" * ((-len(binary)) % 4))
    total_length = 12 + 8 + len(json_bytes) + 8 + len(binary)
    return b"".join(
        (
            struct.pack("<III", 0x46546C67, 2, total_length),
            struct.pack("<II", len(json_bytes), 0x4E4F534A),
            json_bytes,
            struct.pack("<II", len(binary), 0x004E4942),
            binary,
        )
    )


def _red_channel_to_alpha_png(payload: bytes) -> bytes:
    with Image.open(BytesIO(payload)) as source:
        red = source.convert("RGBA").getchannel("R")
        converted = Image.new("RGBA", source.size, (255, 255, 255, 255))
        converted.putalpha(red)
        output = BytesIO()
        converted.save(output, format="PNG")
        return output.getvalue()


def _metallic_gloss_to_gltf_png(payload: bytes) -> bytes:
    """Convert HGRP metal/spec/shadow/smooth to glTF ORM plus Spec alpha."""

    with Image.open(BytesIO(payload)) as source:
        red, specular, _shadow, smoothness = source.convert("RGBA").split()
        roughness = smoothness.point(lambda value: 255 - value)
        opaque = Image.new("L", source.size, 255)
        # Core glTF reads only G/B from this texture. Alpha remains available
        # to game-specific preview backends without changing standard viewers.
        converted = Image.merge("RGBA", (opaque, roughness, red, specular))
        output = BytesIO()
        converted.save(output, format="PNG")
        return output.getvalue()


def _rg_normal_to_gltf_png(payload: bytes) -> bytes:
    """Expand HGRP's DirectX-style RG normal into a glTF RGB normal map."""

    with Image.open(BytesIO(payload)) as source:
        rgba = source.convert("RGBA")
        if rgba.getchannel("B").getextrema() != (0, 0):
            return payload
        source_bytes = rgba.tobytes()
        converted_bytes = bytearray(len(source_bytes))
        for offset in range(0, len(source_bytes), 4):
            red = source_bytes[offset]
            green = 255 - source_bytes[offset + 1]
            x = red / 127.5 - 1.0
            y = green / 127.5 - 1.0
            z = math.sqrt(max(0.0, 1.0 - x * x - y * y))
            converted_bytes[offset : offset + 4] = (
                red,
                green,
                round((z * 0.5 + 0.5) * 255),
                255,
            )
        converted = Image.frombytes("RGBA", rgba.size, bytes(converted_bytes))
        output = BytesIO()
        converted.save(output, format="PNG")
        return output.getvalue()
