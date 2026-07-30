"""Import an exported model GLB and build a Blender CharacterNPR preview.

Run this script with Blender rather than the system Python. Arguments after
``--`` belong to this script; arguments before it belong to Blender.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
import tempfile
from pathlib import Path

import bpy
from mathutils import Vector


def parse_args() -> argparse.Namespace:
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Input GLB exported by the VFS browser")
    parser.add_argument("output", type=Path, help="Output .blend file")
    parser.add_argument("--render", type=Path, help="Optional preview image path")
    parser.add_argument("--outline", action="store_true", help="Enable approximate Freestyle outlines")
    return parser.parse_args(arguments)


def id_property_to_dict(value):
    if hasattr(value, "to_dict"):
        return {key: id_property_to_dict(item) for key, item in value.to_dict().items()}
    if isinstance(value, (list, tuple)):
        return [id_property_to_dict(item) for item in value]
    return value


def material_preview_metadata(material: bpy.types.Material) -> dict:
    value = material.get("endfieldPreview")
    return id_property_to_dict(value) if value is not None else {}


def find_base_color_socket(nodes: bpy.types.Nodes):
    for node in nodes:
        if node.bl_idname != "ShaderNodeEmission":
            continue
        color = node.inputs.get("Color")
        if color and color.is_linked:
            return color.links[0].from_socket
    return None


def find_imported_image(texture_id: str | None):
    if not texture_id:
        return None
    return next(
        (
            image
            for image in bpy.data.images
            if image.name.rstrip(":") in {texture_id, f"{texture_id}:image"}
        ),
        None,
    )


def load_embedded_preview_images(input_path: Path) -> int:
    """Load GLB images referenced only by Endfield preview metadata."""

    if input_path.suffix.lower() != ".glb":
        return 0
    payload = input_path.read_bytes()
    if len(payload) < 20 or payload[:4] != b"glTF":
        return 0
    json_length = struct.unpack_from("<I", payload, 12)[0]
    gltf = json.loads(payload[20 : 20 + json_length])
    binary_start = 20 + json_length + 8
    preview_keys = {
        "diffuseRampTextureId",
        "specularRampTextureId",
        "sdfLightmapTextureId",
        "highlightTextureId",
        "silkStockingsMaskTextureId",
    }
    texture_ids = {
        metadata[key]
        for material in gltf.get("materials", [])
        for metadata in [material.get("extras", {}).get("endfieldPreview", {})]
        for key in preview_keys
        if metadata.get(key)
    }
    image_by_name = {image.get("name"): image for image in gltf.get("images", [])}
    loaded = 0
    with tempfile.TemporaryDirectory(prefix="endfield-preview-") as directory:
        for texture_id in texture_ids:
            if find_imported_image(texture_id) is not None:
                continue
            image_info = image_by_name.get(f"{texture_id}:image")
            if not image_info or "bufferView" not in image_info:
                continue
            view = gltf["bufferViews"][image_info["bufferView"]]
            offset = binary_start + view.get("byteOffset", 0)
            image_payload = payload[offset : offset + view["byteLength"]]
            suffix = ".png" if image_info.get("mimeType") == "image/png" else ".bin"
            temporary_path = Path(directory) / f"{loaded}{suffix}"
            temporary_path.write_bytes(image_payload)
            image = bpy.data.images.load(str(temporary_path), check_existing=False)
            image.name = texture_id
            image.pack()
            loaded += 1
    return loaded


def build_character_npr_nodes(material: bpy.types.Material) -> bool:
    metadata = material_preview_metadata(material)
    if (
        metadata.get("materialFamily") != "characterNpr"
        or metadata.get("materialRole") not in {"skin", "hair"}
        or not material.node_tree
    ):
        return False

    tree = material.node_tree
    nodes = tree.nodes
    links = tree.links
    base_color = find_base_color_socket(nodes)
    output = next((node for node in nodes if node.bl_idname == "ShaderNodeOutputMaterial"), None)
    if base_color is None or output is None:
        material["endfieldPreviewDiagnostic"] = "missing imported base-color or output node"
        return False

    for link in list(output.inputs["Surface"].links):
        links.remove(link)

    diffuse = nodes.new("ShaderNodeBsdfDiffuse")
    diffuse.name = "Endfield NPR Lighting"
    diffuse.label = "分段受光输入"
    diffuse.location = (240, -180)
    diffuse.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    diffuse.inputs["Roughness"].default_value = 0.75

    shader_to_rgb = nodes.new("ShaderNodeShaderToRGB")
    shader_to_rgb.name = "Endfield NPR Shader To RGB"
    shader_to_rgb.location = (450, -180)

    role = metadata["materialRole"]
    ramp_image = find_imported_image(metadata.get("diffuseRampTextureId"))
    if ramp_image is not None:
        geometry = nodes.new("ShaderNodeNewGeometry")
        geometry.name = "Endfield NPR Geometry"
        geometry.location = (600, -260)
        light_dot = nodes.new("ShaderNodeVectorMath")
        light_dot.name = "Endfield NPR Main Light Dot"
        light_dot.label = "世界法线 · 主光方向"
        light_dot.operation = "DOT_PRODUCT"
        direction = Vector((-1.0, -1.0, 1.0)).normalized()
        light_dot.inputs[1].default_value = tuple(direction)
        light_dot.location = (790, -260)
        normalize_light = nodes.new("ShaderNodeMath")
        normalize_light.name = "Endfield NPR Normalize NdotL"
        normalize_light.label = "NdotL -> Ramp [0.55,1]（环境光近似）"
        normalize_light.operation = "MULTIPLY_ADD"
        normalize_light.inputs[1].default_value = 0.225
        normalize_light.inputs[2].default_value = 0.775
        normalize_light.use_clamp = True
        normalize_light.location = (980, -260)
        ramp_vector = nodes.new("ShaderNodeCombineXYZ")
        ramp_vector.name = "Endfield NPR Ramp Coordinate"
        ramp_vector.location = (1150, -260)
        ramp_vector.inputs["Y"].default_value = 0.5
        ramp = nodes.new("ShaderNodeTexImage")
        ramp.name = "Endfield NPR Diffuse Ramp"
        ramp.label = "游戏 Diff Ramp"
        ramp.location = (1320, -260)
        ramp.image = ramp_image
        ramp.interpolation = "Linear"
        links.new(geometry.outputs["Normal"], light_dot.inputs[0])
        links.new(light_dot.outputs["Value"], normalize_light.inputs[0])
        links.new(normalize_light.outputs["Value"], ramp_vector.inputs["X"])
        links.new(ramp_vector.outputs["Vector"], ramp.inputs["Vector"])
        ramp_output = ramp.outputs["Color"]
    else:
        ramp = nodes.new("ShaderNodeValToRGB")
        ramp.name = "Endfield NPR Diffuse Ramp"
        ramp.label = "CharacterNPR 明暗分段（回退）"
        ramp.location = (650, -180)
        ramp.color_ramp.interpolation = "CONSTANT"
        ramp.color_ramp.elements[0].position = 0.40 if role == "skin" else 0.36
        ramp.color_ramp.elements[0].color = (
            (0.68, 0.64, 0.66, 1.0) if role == "skin" else (0.54, 0.57, 0.62, 1.0)
        )
        ramp.color_ramp.elements[1].position = 0.56 if role == "skin" else 0.62
        ramp.color_ramp.elements[1].color = (1.0, 1.0, 1.0, 1.0)
        links.new(shader_to_rgb.outputs["Color"], ramp.inputs["Fac"])
        ramp_output = ramp.outputs["Color"]

    multiply = nodes.new("ShaderNodeMixRGB")
    multiply.name = "Endfield NPR Apply Lighting"
    multiply.blend_type = "MULTIPLY"
    multiply.inputs[0].default_value = 1.0
    multiply.location = (1550, 80)

    emission = nodes.new("ShaderNodeEmission")
    emission.name = "Endfield NPR Output"
    emission.location = (1760, 80)
    emission.inputs["Strength"].default_value = 1.0

    links.new(diffuse.outputs["BSDF"], shader_to_rgb.inputs["Shader"])
    links.new(base_color, multiply.inputs[1])
    links.new(ramp_output, multiply.inputs[2])
    links.new(multiply.outputs["Color"], emission.inputs["Color"])
    links.new(emission.outputs["Emission"], output.inputs["Surface"])

    material["endfieldShaderBackend"] = "blender-eevee-character-npr-v1"
    return True


def scene_bounds() -> tuple[Vector, Vector]:
    points = [
        obj.matrix_world @ Vector(corner)
        for obj in bpy.context.scene.objects
        if obj.type == "MESH" and not obj.hide_render and obj.visible_get()
        for corner in obj.bound_box
    ]
    if not points:
        raise RuntimeError("Imported file contains no visible mesh bounds")
    minimum = Vector(tuple(min(point[index] for point in points) for index in range(3)))
    maximum = Vector(tuple(max(point[index] for point in points) for index in range(3)))
    return minimum, maximum


def point_camera(camera: bpy.types.Object, target: Vector) -> None:
    camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()


def add_area_light(name: str, location: Vector, energy: float, size: float, target: Vector) -> None:
    data = bpy.data.lights.new(name, "AREA")
    data.energy = energy
    data.shape = "DISK"
    data.size = size
    light = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(light)
    light.location = location
    point_camera(light, target)


def configure_preview_scene(enable_outline: bool) -> None:
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x = 768
    scene.render.resolution_y = 1024
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Medium High Contrast"

    if scene.world is None:
        scene.world = bpy.data.worlds.new("Endfield Preview World")
    scene.world.color = (0.025, 0.03, 0.04)
    minimum, maximum = scene_bounds()
    center = (minimum + maximum) * 0.5
    size = maximum - minimum

    camera_data = bpy.data.cameras.new("Endfield Preview Camera")
    camera_data.lens = 58
    camera_data.sensor_fit = "VERTICAL"
    camera = bpy.data.objects.new("Endfield Preview Camera", camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    vertical_fov = camera_data.angle_y
    distance = max(size.z / (2 * math.tan(vertical_fov / 2)) * 1.16, size.length * 0.8)
    camera.location = center + Vector((size.x * 0.12, -distance, size.z * 0.02))
    point_camera(camera, center + Vector((0.0, 0.0, size.z * 0.02)))

    light_scale = max(size.length, 1.0)
    add_area_light(
        "Endfield Key Light",
        center + Vector((-light_scale, -light_scale, light_scale)),
        260,
        light_scale,
        center,
    )
    add_area_light(
        "Endfield Fill Light",
        center + Vector((light_scale, -light_scale * 0.4, light_scale * 0.3)),
        110,
        light_scale * 0.8,
        center,
    )

    scene.render.use_freestyle = enable_outline
    if enable_outline:
        view_layer = scene.view_layers[0]
        line_set = view_layer.freestyle_settings.linesets[0]
        line_set.select_silhouette = True
        line_set.select_border = True
        line_set.select_crease = False
        line_set.select_material_boundary = False
        line_set.select_edge_mark = False
        if line_set.linestyle is None:
            line_set.linestyle = bpy.data.linestyles.new("Endfield NPR Outline")
        line_set.linestyle.color = (0.015, 0.018, 0.022)
        line_set.linestyle.thickness = 1.15


def main() -> None:
    args = parse_args()
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    if input_path.suffix.lower() not in {".glb", ".gltf"}:
        raise ValueError(f"Expected a GLB or glTF input, got: {input_path}")

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(input_path))
    loaded_preview_images = load_embedded_preview_images(input_path)
    converted = sum(build_character_npr_nodes(material) for material in bpy.data.materials)
    configure_preview_scene(args.outline)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output_path))
    if args.render:
        render_path = args.render.resolve()
        render_path.parent.mkdir(parents=True, exist_ok=True)
        bpy.context.scene.render.filepath = str(render_path)
        bpy.ops.render.render(write_still=True)
    print(
        f"Imported {input_path.name}; loaded {loaded_preview_images} preview images; "
        f"converted {converted} CharacterNPR materials"
    )


if __name__ == "__main__":
    main()
