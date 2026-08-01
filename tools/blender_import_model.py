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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from character_lighting import (
    AmbientLighting,
    CharacterLighting,
    load_character_lighting,
)
from blender_materials import (
    MATERIAL_BACKEND_VERSION,
    create_silk_stockings_group,
)
from blender_material_plan import plan_texture_id, plan_value, silk_plan_inputs

DEFAULT_CHARINFO_AMBIENT = AmbientLighting(
    base_intensity=1.0,
    azimuth_degrees=180.0,
    elevation_degrees=0.0,
    directional_intensity=0.6,
    directional_parameter=0.15,
)
DEFAULT_PREVIEW_MAIN_DIRECTION = (0.0, -1.0, 0.0)


def parse_args() -> argparse.Namespace:
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Input GLB exported by the VFS browser")
    parser.add_argument("output", type=Path, help="Output .blend file")
    parser.add_argument("--render", type=Path, help="Optional preview image path")
    parser.add_argument(
        "--lighting",
        type=Path,
        help="Optional character-lighting JSON built from CharInfo and its cubemap",
    )
    parser.add_argument(
        "--framing",
        choices=("full", "portrait"),
        default="full",
        help="Camera framing used by the generated preview scene",
    )
    parser.add_argument(
        "--main-light-direction",
        type=float,
        nargs=3,
        metavar=("X", "Y", "Z"),
        default=DEFAULT_PREVIEW_MAIN_DIRECTION,
        help=(
            "Blender-space direction from the character toward the preview key "
            "light; this is independent from the CharacterVolume ambient direction"
        ),
    )
    parser.add_argument(
        "--outline",
        action="store_true",
        help="Enable approximate Freestyle outlines",
    )
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


def material_source_metadata(material: bpy.types.Material) -> dict:
    value = material.get("endfieldSourceMaterial")
    return id_property_to_dict(value) if value is not None else {}


def material_plan_metadata(material: bpy.types.Material) -> dict:
    value = material.get("endfieldMaterialPlan")
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


def linked_source(socket):
    return socket.links[0].from_socket if socket is not None and socket.is_linked else None


def image_color_source(nodes: bpy.types.Nodes, image):
    if image is None:
        return None
    texture = next(
        (
            node
            for node in nodes
            if node.bl_idname == "ShaderNodeTexImage" and node.image == image
        ),
        None,
    )
    return texture.outputs["Color"] if texture is not None else None


def effective_ambient(lighting: CharacterLighting | None) -> AmbientLighting:
    """Use the recovered global CharInfo profile when no document is supplied."""

    return lighting.ambient if lighting is not None else DEFAULT_CHARINFO_AMBIENT


def normalized_main_light_direction(value) -> Vector:
    direction = Vector(value)
    if direction.length_squared == 0:
        raise ValueError("main light direction must not be zero")
    return direction.normalized()


def scalar_math(
    nodes: bpy.types.Nodes,
    links: bpy.types.NodeLinks,
    operation: str,
    name: str,
    first,
    second=None,
):
    node = nodes.new("ShaderNodeMath")
    node.name = name
    node.operation = operation
    if hasattr(first, "is_output"):
        links.new(first, node.inputs[0])
    else:
        node.inputs[0].default_value = float(first)
    if second is not None:
        if hasattr(second, "is_output"):
            links.new(second, node.inputs[1])
        else:
            node.inputs[1].default_value = float(second)
    return node.outputs[0]


def linear_to_srgb_channel(
    nodes: bpy.types.Nodes,
    links: bpy.types.NodeLinks,
    channel,
    name: str,
):
    """Build the exact IEC sRGB branch used before b225 Shadow LUT lookup."""

    linear = scalar_math(
        nodes,
        links,
        "MULTIPLY",
        f"{name} Linear Segment",
        channel,
        12.92,
    )
    absolute = scalar_math(
        nodes,
        links,
        "ABSOLUTE",
        f"{name} Absolute",
        channel,
    )
    power = scalar_math(
        nodes,
        links,
        "POWER",
        f"{name} Power",
        absolute,
        1.0 / 2.4,
    )
    scale = scalar_math(
        nodes,
        links,
        "MULTIPLY",
        f"{name} Nonlinear Scale",
        power,
        1.055,
    )
    nonlinear = scalar_math(
        nodes,
        links,
        "SUBTRACT",
        f"{name} Nonlinear Segment",
        scale,
        0.055,
    )
    use_linear = scalar_math(
        nodes,
        links,
        "LESS_THAN",
        f"{name} Select Linear Segment",
        channel,
        0.0031308,
    )
    select = nodes.new("ShaderNodeMix")
    select.name = f"{name} Piecewise sRGB"
    select.data_type = "FLOAT"
    select.clamp_result = True
    links.new(use_linear, select.inputs["Factor"])
    links.new(nonlinear, select.inputs[2])
    links.new(linear, select.inputs[3])
    return select.outputs["Result"]


def sample_face_shadow_lut(
    nodes: bpy.types.Nodes,
    links: bpy.types.NodeLinks,
    shadow_lut_image,
    base_color,
):
    """Reproduce b225's flattened 32x32x32 Shadow LUT lookup."""

    separate = nodes.new("ShaderNodeSeparateColor")
    separate.name = "Endfield Face Separate Linear Base"
    separate.mode = "RGB"
    links.new(base_color, separate.inputs["Color"])
    red = linear_to_srgb_channel(
        nodes,
        links,
        separate.outputs["Red"],
        "Endfield Face Base R",
    )
    green = linear_to_srgb_channel(
        nodes,
        links,
        separate.outputs["Green"],
        "Endfield Face Base G",
    )
    blue = linear_to_srgb_channel(
        nodes,
        links,
        separate.outputs["Blue"],
        "Endfield Face Base B",
    )

    blue_scaled = scalar_math(
        nodes,
        links,
        "MULTIPLY",
        "Endfield Face LUT Blue Times 31",
        blue,
        31.0,
    )
    blue_slice = scalar_math(
        nodes,
        links,
        "FLOOR",
        "Endfield Face LUT Blue Slice",
        blue_scaled,
    )
    blue_fraction = scalar_math(
        nodes,
        links,
        "SUBTRACT",
        "Endfield Face LUT Blue Fraction",
        blue_scaled,
        blue_slice,
    )
    slice_x = scalar_math(
        nodes,
        links,
        "MULTIPLY",
        "Endfield Face LUT Slice X",
        blue_slice,
        1.0 / 32.0,
    )
    red_x = scalar_math(
        nodes,
        links,
        "MULTIPLY",
        "Endfield Face LUT Red X",
        red,
        31.0 / 1024.0,
    )
    first_x = scalar_math(
        nodes,
        links,
        "ADD",
        "Endfield Face LUT First X",
        slice_x,
        red_x,
    )
    first_x = scalar_math(
        nodes,
        links,
        "ADD",
        "Endfield Face LUT First Texel Center",
        first_x,
        0.5 / 1024.0,
    )
    second_x = scalar_math(
        nodes,
        links,
        "ADD",
        "Endfield Face LUT Second X",
        first_x,
        1.0 / 32.0,
    )
    green_y = scalar_math(
        nodes,
        links,
        "MULTIPLY",
        "Endfield Face LUT Green Y",
        green,
        31.0 / 32.0,
    )
    green_y = scalar_math(
        nodes,
        links,
        "ADD",
        "Endfield Face LUT Green Texel Center",
        green_y,
        0.5 / 32.0,
    )

    first_uv = nodes.new("ShaderNodeCombineXYZ")
    first_uv.name = "Endfield Face Shadow LUT UV 0"
    links.new(first_x, first_uv.inputs["X"])
    links.new(green_y, first_uv.inputs["Y"])
    second_uv = nodes.new("ShaderNodeCombineXYZ")
    second_uv.name = "Endfield Face Shadow LUT UV 1"
    links.new(second_x, second_uv.inputs["X"])
    links.new(green_y, second_uv.inputs["Y"])

    first_sample = nodes.new("ShaderNodeTexImage")
    first_sample.name = "Endfield Face Shadow LUT Slice 0"
    first_sample.image = shadow_lut_image
    first_sample.interpolation = "Closest"
    first_sample.extension = "EXTEND"
    links.new(first_uv.outputs["Vector"], first_sample.inputs["Vector"])
    second_sample = nodes.new("ShaderNodeTexImage")
    second_sample.name = "Endfield Face Shadow LUT Slice 1"
    second_sample.image = shadow_lut_image
    second_sample.interpolation = "Closest"
    second_sample.extension = "EXTEND"
    links.new(second_uv.outputs["Vector"], second_sample.inputs["Vector"])

    interpolate = nodes.new("ShaderNodeMixRGB")
    interpolate.name = "Endfield Face Interpolate Shadow LUT"
    interpolate.blend_type = "MIX"
    links.new(blue_fraction, interpolate.inputs[0])
    links.new(first_sample.outputs["Color"], interpolate.inputs[1])
    links.new(second_sample.outputs["Color"], interpolate.inputs[2])
    return interpolate.outputs["Color"]


def character_normal_signal(
    nodes: bpy.types.Nodes,
    links: bpy.types.NodeLinks,
    main_light_direction: Vector,
):
    """Build the source-proven normal carrier used before DiffRamp sampling."""

    geometry = nodes.new("ShaderNodeNewGeometry")
    geometry.name = "Endfield Character Geometry"
    geometry.location = (500, -260)

    light_dot = nodes.new("ShaderNodeVectorMath")
    light_dot.name = "Endfield Character Main Light Dot"
    light_dot.label = "世界法线 · 角色主光方向"
    light_dot.operation = "DOT_PRODUCT"
    light_dot.inputs[1].default_value = tuple(main_light_direction)
    light_dot.location = (700, -260)
    links.new(geometry.outputs["Normal"], light_dot.inputs[0])

    normal_bias = nodes.new("ShaderNodeValue")
    normal_bias.name = "Endfield Character Normal Bias"
    normal_bias.label = "CharacterParams11.w × CharacterParams12.x（待运行时取值）"
    normal_bias.outputs["Value"].default_value = 0.0
    normal_bias.location = (700, -420)

    add_bias = nodes.new("ShaderNodeMath")
    add_bias.name = "Endfield Character Apply Normal Bias"
    add_bias.operation = "ADD"
    add_bias.use_clamp = False
    add_bias.location = (920, -260)
    links.new(light_dot.outputs["Value"], add_bias.inputs[0])
    links.new(normal_bias.outputs["Value"], add_bias.inputs[1])

    clamp_signal = nodes.new("ShaderNodeClamp")
    clamp_signal.name = "Endfield Character Clamp Normal Signal"
    clamp_signal.inputs["Min"].default_value = -1.0
    clamp_signal.inputs["Max"].default_value = 1.0
    clamp_signal.location = (1110, -260)
    links.new(add_bias.outputs["Value"], clamp_signal.inputs["Value"])
    return clamp_signal.outputs["Result"]


def face_pseudo_normal_signal(
    nodes: bpy.types.Nodes,
    links: bpy.types.NodeLinks,
    sdf_blue,
    light_x: float,
    main_light_direction: Vector,
):
    """Build the b225 face pseudo normal from the SDF blue channel."""

    twice_blue = scalar_math(
        nodes,
        links,
        "MULTIPLY",
        "Endfield Face Twice SDF Blue",
        sdf_blue,
        2.0,
    )
    if light_x > 0:
        pseudo_x = scalar_math(
            nodes,
            links,
            "SUBTRACT",
            "Endfield Face Pseudo Normal X",
            twice_blue,
            1.0,
        )
    else:
        pseudo_x = scalar_math(
            nodes,
            links,
            "SUBTRACT",
            "Endfield Face Pseudo Normal X",
            1.0,
            twice_blue,
        )
    absolute_x = scalar_math(
        nodes,
        links,
        "ABSOLUTE",
        "Endfield Face Absolute Pseudo Normal X",
        pseudo_x,
    )
    pseudo_forward = scalar_math(
        nodes,
        links,
        "SUBTRACT",
        "Endfield Face Pseudo Normal Forward",
        1.0,
        absolute_x,
    )

    # The recovered character faces Blender -Y. Map the shader's positive
    # object-forward pseudo-normal component to Blender -Y.
    combine = nodes.new("ShaderNodeCombineXYZ")
    combine.name = "Endfield Face Object Pseudo Normal"
    combine.inputs["Z"].default_value = 0.001
    combine.location = (1020, -480)
    links.new(pseudo_x, combine.inputs["X"])
    negate_forward = scalar_math(
        nodes,
        links,
        "MULTIPLY",
        "Endfield Face Map Forward To Blender",
        pseudo_forward,
        -1.0,
    )
    links.new(negate_forward, combine.inputs["Y"])

    normalize = nodes.new("ShaderNodeVectorMath")
    normalize.name = "Endfield Face Normalize Pseudo Normal"
    normalize.operation = "NORMALIZE"
    normalize.location = (1210, -480)
    links.new(combine.outputs["Vector"], normalize.inputs[0])

    to_world = nodes.new("ShaderNodeVectorTransform")
    to_world.name = "Endfield Face Pseudo Normal To World"
    to_world.vector_type = "NORMAL"
    to_world.convert_from = "OBJECT"
    to_world.convert_to = "WORLD"
    to_world.location = (1400, -480)
    links.new(normalize.outputs["Vector"], to_world.inputs["Vector"])

    light_dot = nodes.new("ShaderNodeVectorMath")
    light_dot.name = "Endfield Face Pseudo Normal Light Dot"
    light_dot.operation = "DOT_PRODUCT"
    light_dot.inputs[1].default_value = tuple(main_light_direction)
    light_dot.location = (1590, -480)
    links.new(to_world.outputs["Vector"], light_dot.inputs[0])

    clamp_signal = nodes.new("ShaderNodeClamp")
    clamp_signal.name = "Endfield Face Clamp Pseudo Normal Signal"
    clamp_signal.inputs["Min"].default_value = -1.0
    clamp_signal.inputs["Max"].default_value = 1.0
    clamp_signal.location = (1780, -480)
    links.new(light_dot.outputs["Value"], clamp_signal.inputs["Value"])
    return clamp_signal.outputs["Result"]


def sample_diffuse_ramp(
    nodes: bpy.types.Nodes,
    links: bpy.types.NodeLinks,
    ramp_image,
    signal,
    location_y: float = -260,
):
    normalize = nodes.new("ShaderNodeMath")
    normalize.name = "Endfield DiffRamp Coordinate"
    normalize.operation = "MULTIPLY_ADD"
    normalize.inputs[1].default_value = 0.5
    normalize.inputs[2].default_value = 0.5
    normalize.location = (1300, location_y)
    links.new(signal, normalize.inputs[0])

    ramp_vector = nodes.new("ShaderNodeCombineXYZ")
    ramp_vector.name = "Endfield DiffRamp UV"
    ramp_vector.inputs["Y"].default_value = 0.5
    ramp_vector.location = (1490, location_y)
    links.new(normalize.outputs["Value"], ramp_vector.inputs["X"])

    ramp = nodes.new("ShaderNodeTexImage")
    ramp.name = "Endfield Diffuse Ramp"
    ramp.label = "游戏 DiffRamp"
    ramp.image = ramp_image
    ramp.interpolation = "Linear"
    ramp.extension = "REPEAT"
    ramp.location = (1680, location_y)
    links.new(ramp_vector.outputs["Vector"], ramp.inputs["Vector"])
    return ramp.outputs["Color"]


def connect_character_surface(
    nodes: bpy.types.Nodes,
    links: bpy.types.NodeLinks,
    output,
    base_color,
    ramp_color,
    recovered_shading_influence: float = 1.0,
    blender_shadow_influence: float = 0.18,
    recovered_base_color=None,
) -> None:
    """Connect the recovered color carrier and an explicit Blender shadow bridge."""

    multiply = nodes.new("ShaderNodeMixRGB")
    multiply.name = "Endfield Apply Character Lighting"
    multiply.blend_type = "MULTIPLY"
    multiply.inputs[0].default_value = 1.0
    multiply.location = (1900, 80)

    recovered_mix = nodes.new("ShaderNodeMixRGB")
    recovered_mix.name = "Endfield Recovered Shading Influence"
    recovered_mix.label = "已恢复着色链占比"
    recovered_mix.blend_type = "MIX"
    recovered_mix.inputs[0].default_value = recovered_shading_influence
    recovered_mix.location = (2110, 80)

    emission = nodes.new("ShaderNodeEmission")
    emission.name = "Endfield Recovered Color Carrier"
    emission.location = (2320, 80)
    emission.inputs["Strength"].default_value = 1.0

    scene_lit = nodes.new("ShaderNodeBsdfDiffuse")
    scene_lit.name = "Endfield Blender Shadow Receiver"
    scene_lit.label = "Blender 场景灯光/阴影兼容层"
    scene_lit.location = (2320, -120)
    scene_lit.inputs["Roughness"].default_value = 0.75

    scene_influence = nodes.new("ShaderNodeValue")
    scene_influence.name = "Endfield Blender Shadow Influence"
    scene_influence.label = "兼容层占比（非游戏 CharacterParams）"
    scene_influence.outputs["Value"].default_value = blender_shadow_influence
    scene_influence.location = (2320, -300)

    hybrid = nodes.new("ShaderNodeMixShader")
    hybrid.name = "Endfield Character Surface"
    hybrid.location = (2560, 80)

    links.new(
        recovered_base_color if recovered_base_color is not None else base_color,
        multiply.inputs[1],
    )
    links.new(ramp_color, multiply.inputs[2])
    links.new(base_color, recovered_mix.inputs[1])
    links.new(multiply.outputs["Color"], recovered_mix.inputs[2])
    links.new(recovered_mix.outputs["Color"], emission.inputs["Color"])
    links.new(base_color, scene_lit.inputs["Color"])
    links.new(scene_influence.outputs["Value"], hybrid.inputs[0])
    links.new(emission.outputs["Emission"], hybrid.inputs[1])
    links.new(scene_lit.outputs["BSDF"], hybrid.inputs[2])
    links.new(hybrid.outputs["Shader"], output.inputs["Surface"])


def build_character_face_nodes(
    material: bpy.types.Material,
    main_light_direction: Vector,
    metadata: dict,
    base_color,
    output,
) -> bool:
    """Build the recovered face SDF carrier from the selected Skin variant."""

    tree = material.node_tree
    nodes = tree.nodes
    links = tree.links
    sdf_image = find_imported_image(metadata.get("sdfLightmapTextureId"))
    mask_image = find_imported_image(metadata.get("sdfMaskTextureId"))
    ramp_image = find_imported_image(metadata.get("diffuseRampTextureId"))
    shadow_lut_image = find_imported_image(metadata.get("shadowLutTextureId"))
    if (
        sdf_image is None
        or mask_image is None
        or ramp_image is None
        or shadow_lut_image is None
    ):
        material["endfieldPreviewDiagnostic"] = (
            "face SDF requires sdfLightmap, sdfMask, diffuseRamp and shadowLut"
        )
        return False

    for link in list(output.inputs["Surface"].links):
        links.remove(link)

    coordinates = nodes.new("ShaderNodeTexCoord")
    coordinates.name = "Endfield Face Coordinates"
    coordinates.location = (400, -700)
    separate_uv = nodes.new("ShaderNodeSeparateXYZ")
    separate_uv.name = "Endfield Face UV"
    separate_uv.location = (590, -700)
    links.new(coordinates.outputs["UV"], separate_uv.inputs["Vector"])

    horizontal = Vector((main_light_direction.x, main_light_direction.y))
    if horizontal.length_squared == 0:
        horizontal = Vector((0.0, 1.0))
    horizontal.normalize()
    light_x, light_forward = horizontal

    sample_x = separate_uv.outputs["X"]
    if light_x <= 0:
        sample_x = scalar_math(
            nodes,
            links,
            "SUBTRACT",
            "Endfield Face Mirror SDF U",
            1.0,
            sample_x,
        )
    sdf_uv = nodes.new("ShaderNodeCombineXYZ")
    sdf_uv.name = "Endfield Face SDF UV"
    sdf_uv.location = (800, -700)
    links.new(sample_x, sdf_uv.inputs["X"])
    links.new(separate_uv.outputs["Y"], sdf_uv.inputs["Y"])

    sdf = nodes.new("ShaderNodeTexImage")
    sdf.name = "Endfield Face SDF Lightmap"
    sdf.image = sdf_image
    sdf.interpolation = "Closest"
    sdf.extension = "MIRROR"
    sdf.location = (990, -700)
    links.new(sdf_uv.outputs["Vector"], sdf.inputs["Vector"])
    sdf_channels = nodes.new("ShaderNodeSeparateColor")
    sdf_channels.name = "Endfield Face SDF Channels"
    sdf_channels.mode = "RGB"
    sdf_channels.location = (1190, -700)
    links.new(sdf.outputs["Color"], sdf_channels.inputs["Color"])

    mask = nodes.new("ShaderNodeTexImage")
    mask.name = "Endfield Face SDF Mask"
    mask.label = "R Rim / G SDF / B FlatSH"
    mask.image = mask_image
    mask.interpolation = "Closest"
    mask.extension = "MIRROR"
    mask.location = (800, -960)
    links.new(coordinates.outputs["UV"], mask.inputs["Vector"])
    mask_channels = nodes.new("ShaderNodeSeparateColor")
    mask_channels.name = "Endfield Face SDF Mask Channels"
    mask_channels.mode = "RGB"
    mask_channels.location = (1010, -960)
    links.new(mask.outputs["Color"], mask_channels.inputs["Color"])

    sdf_sum = scalar_math(
        nodes,
        links,
        "ADD",
        "Endfield Face SDF R Plus G",
        sdf_channels.outputs["Red"],
        sdf_channels.outputs["Green"],
    )
    sdf_half = scalar_math(
        nodes,
        links,
        "MULTIPLY",
        "Endfield Face SDF Half Sum",
        sdf_sum,
        0.5,
    )

    # These equations are the SDF core shared by the inspected face variants,
    # including the material-matched b225. Camera-side compensation and live
    # CharacterParams are not yet recovered, so this preview keeps those terms
    # neutral and records the boundary on the material.
    threshold = max(0.001, min(0.999, 0.5 - light_forward * 0.5))
    lower = max(threshold * 2.0 - 1.0, 0.0)
    upper = min(threshold * 2.0, 1.0)
    denominator = (1.0 - lower) + upper
    subtract_lower = scalar_math(
        nodes,
        links,
        "SUBTRACT",
        "Endfield Face Remove SDF Lower Bound",
        sdf_half,
        lower,
    )
    normalize_sdf = scalar_math(
        nodes,
        links,
        "DIVIDE",
        "Endfield Face Normalize SDF",
        subtract_lower,
        denominator,
    )
    normalize_node = normalize_sdf.node
    normalize_node.use_clamp = True

    squared = scalar_math(
        nodes,
        links,
        "MULTIPLY",
        "Endfield Face SDF Squared",
        normalize_sdf,
        normalize_sdf,
    )
    two_t = scalar_math(
        nodes,
        links,
        "MULTIPLY",
        "Endfield Face SDF Twice",
        normalize_sdf,
        2.0,
    )
    three_minus_two_t = scalar_math(
        nodes,
        links,
        "SUBTRACT",
        "Endfield Face SDF Three Minus Twice",
        3.0,
        two_t,
    )
    smooth_sdf = scalar_math(
        nodes,
        links,
        "MULTIPLY",
        "Endfield Face SDF Smooth Polynomial",
        squared,
        three_minus_two_t,
    )
    phase = (light_forward * 0.5) * math.ceil(light_forward * 0.5)
    add_phase = scalar_math(
        nodes,
        links,
        "ADD",
        "Endfield Face Add Direction Phase",
        smooth_sdf,
        phase,
    )
    absolute = scalar_math(
        nodes,
        links,
        "ABSOLUTE",
        "Endfield Face Absolute SDF",
        add_phase,
    )
    twice_absolute = scalar_math(
        nodes,
        links,
        "MULTIPLY",
        "Endfield Face Twice Absolute SDF",
        absolute,
        2.0,
    )
    sdf_signal = scalar_math(
        nodes,
        links,
        "SUBTRACT",
        "Endfield Face Signed SDF Signal",
        twice_absolute,
        1.0,
    )

    normal_signal = face_pseudo_normal_signal(
        nodes,
        links,
        sdf_channels.outputs["Blue"],
        light_x,
        main_light_direction,
    )
    mix_signal = nodes.new("ShaderNodeMix")
    mix_signal.name = "Endfield Face Mix SDF And Pseudo Normal"
    mix_signal.data_type = "FLOAT"
    mix_signal.clamp_factor = True
    mix_signal.location = (1280, -360)
    links.new(mask_channels.outputs["Green"], mix_signal.inputs["Factor"])
    links.new(sdf_signal, mix_signal.inputs[2])
    links.new(normal_signal, mix_signal.inputs[3])

    ramp_color = sample_diffuse_ramp(
        nodes,
        links,
        ramp_image,
        mix_signal.outputs["Result"],
    )
    shadow_lut_color = sample_face_shadow_lut(
        nodes,
        links,
        shadow_lut_image,
        base_color,
    )
    # The SDF carrier remains connected for inspection, but it is not a final
    # face color until Shadow LUT and runtime CharacterParams are restored.
    connect_character_surface(
        nodes,
        links,
        output,
        base_color,
        ramp_color,
        recovered_shading_influence=0.0,
        blender_shadow_influence=0.0,
        recovered_base_color=shadow_lut_color,
    )
    material["endfieldShaderBackend"] = "blender-eevee-character-face-sdf-v1"
    material["endfieldPreviewDiagnostic"] = (
        "source carrier: b225 SDF core plus Shadow LUT; normal/emotion/highlight "
        "and unresolved live CharacterParams remain incomplete; final face output "
        "uses BaseMap fallback while the recovered intermediate branch stays connected"
    )
    return True


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
        "metallicGlossTextureId",
        "baseColorTextureId",
        "sdfLightmapTextureId",
        "sdfMaskTextureId",
        "shadowLutTextureId",
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


def build_character_npr_nodes(
    material: bpy.types.Material,
    main_light_direction: Vector,
) -> bool:
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

    role = metadata["materialRole"]
    if (
        role == "skin"
        and metadata.get("sdfLightmapTextureId")
        and metadata.get("sdfMaskTextureId")
    ):
        return build_character_face_nodes(
            material,
            main_light_direction,
            metadata,
            base_color,
            output,
        )

    for link in list(output.inputs["Surface"].links):
        links.remove(link)

    ramp_image = find_imported_image(metadata.get("diffuseRampTextureId"))
    if ramp_image is not None:
        signal = character_normal_signal(nodes, links, main_light_direction)
        ramp_output = sample_diffuse_ramp(nodes, links, ramp_image, signal)
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
        signal = character_normal_signal(nodes, links, main_light_direction)
        links.new(signal, ramp.inputs["Fac"])
        ramp_output = ramp.outputs["Color"]
    connect_character_surface(nodes, links, output, base_color, ramp_output)

    material["endfieldShaderBackend"] = (
        "blender-eevee-character-body-skin-v1"
        if role == "skin"
        else "blender-eevee-character-hair-v1"
    )
    return True


def configure_silk_stockings_nodes(material: bpy.types.Material) -> bool:
    metadata = material_preview_metadata(material)
    source_material = material_source_metadata(material)
    plan = material_plan_metadata(material)
    plan_inputs = silk_plan_inputs(plan)
    silk = metadata.get("silkStockings")
    if not isinstance(silk, dict) or not material.node_tree:
        return False

    tree = material.node_tree
    nodes = tree.nodes
    links = tree.links
    output = next(
        (node for node in nodes if node.bl_idname == "ShaderNodeOutputMaterial"),
        None,
    )
    principled = next(
        (node for node in nodes if node.bl_idname == "ShaderNodeBsdfPrincipled"),
        None,
    )
    if output is None or principled is None:
        material["endfieldPreviewDiagnostic"] = "silk stockings missing imported Principled/output"
        return False

    # The standard glTF preview remains neutral. This specialized preview applies
    # the original Endfield parameters once, directly from endfieldSourceMaterial.
    base_color = image_color_source(
        nodes,
        find_imported_image(metadata.get("baseColorTextureId")),
    ) or linked_source(principled.inputs.get("Base Color"))
    normal = linked_source(principled.inputs.get("Normal"))
    if base_color is None:
        material["endfieldPreviewDiagnostic"] = "silk stockings missing base-color source"
        return False

    group = nodes.new("ShaderNodeGroup")
    group.name = "Endfield Silk Stockings"
    group.label = "终末地丝袜"
    group.node_tree = create_silk_stockings_group()
    group.location = (principled.location.x + 260, principled.location.y)
    links.new(base_color, group.inputs["Skin Base Color"])
    if normal is not None:
        links.new(normal, group.inputs["Normal"])

    tangent = nodes.new("ShaderNodeTangent")
    tangent.name = "Endfield Silk Tangent"
    tangent.direction_type = "UV_MAP"
    tangent.location = (group.location.x - 220, group.location.y - 460)
    links.new(tangent.outputs["Tangent"], group.inputs["Tangent"])

    source_colors = source_material.get("colors", {})
    source_floats = source_material.get("floats", {})
    dry_color = plan_value(plan_inputs, "Dry Color") if plan_inputs is not None else None
    if isinstance(dry_color, list) and len(dry_color) in {3, 4}:
        group.inputs["Dry Tint"].default_value = (*map(float, dry_color[:3]), 1.0)
    color = (
        plan_value(plan_inputs, "Edge Color")
        if plan_inputs is not None
        else source_colors.get("_SilkStockingsColor", silk.get("color"))
    )
    if isinstance(color, list) and len(color) in {3, 4}:
        group.inputs["Edge Color"].default_value = (*map(float, color[:3]), 1.0)
    min_affect = (
        plan_value(plan_inputs, "Minimum Affect")
        if plan_inputs is not None
        else source_floats.get("_SilkStockingsMinAffect")
    )
    if isinstance(min_affect, (int, float)):
        group.inputs["Min Affect"].default_value = max(0.0, min(0.49, float(min_affect)))
    max_affect = (
        plan_value(plan_inputs, "Maximum Affect")
        if plan_inputs is not None
        else source_floats.get("_SilkStockingsMaxAffect", silk.get("maxAffect"))
    )
    if isinstance(max_affect, (int, float)):
        group.inputs["Max Affect"].default_value = max(0.5, min(1.0, float(max_affect)))

    mask_texture_id = (
        plan_texture_id(plan_inputs, "Silk Mask")
        if plan_inputs is not None
        else metadata.get("silkStockingsMaskTextureId")
    )
    mask_image = find_imported_image(mask_texture_id)
    if mask_image is not None:
        mask = nodes.new("ShaderNodeTexImage")
        mask.name = "Endfield Silk Stockings Mask"
        mask.label = "R 各向异性 / G 方向 / B 湿润 / A 覆盖"
        mask.image = mask_image
        mask.image.colorspace_settings.name = "Non-Color"
        mask.location = (group.location.x - 460, group.location.y - 240)
        links.new(mask.outputs["Color"], group.inputs["Stocking Mask RGB"])
        links.new(mask.outputs["Alpha"], group.inputs["Stocking Coverage"])
        channels = nodes.new("ShaderNodeSeparateColor")
        channels.name = "Endfield Silk Mask Channels"
        channels.mode = "RGB"
        channels.location = (group.location.x - 220, group.location.y - 250)
        links.new(mask.outputs["Color"], channels.inputs["Color"])
        links.new(channels.outputs["Green"], group.inputs["Anisotropic Rotation"])

    for link in list(output.inputs["Surface"].links):
        links.remove(link)
    links.new(group.outputs["Shader"], output.inputs["Surface"])
    nodes.remove(principled)
    material["endfieldShaderBackend"] = (
        f"blender-eevee-silk-stockings-v{MATERIAL_BACKEND_VERSION}"
    )
    if plan_inputs is not None:
        material["endfieldMaterialPlanApplied"] = plan.get("version")
    return True


def configure_character_cloth_nodes(material: bpy.types.Material) -> bool:
    metadata = material_preview_metadata(material)
    if (
        metadata.get("materialFamily") != "characterNpr"
        or metadata.get("materialRole") != "cloth"
        or isinstance(metadata.get("silkStockings"), dict)
        or not material.node_tree
    ):
        return False

    image = find_imported_image(metadata.get("metallicGlossTextureId"))
    principled = next(
        (
            node
            for node in material.node_tree.nodes
            if node.bl_idname == "ShaderNodeBsdfPrincipled"
        ),
        None,
    )
    specular = principled.inputs.get("Specular IOR Level") if principled else None
    if image is None or specular is None:
        material["endfieldPreviewDiagnostic"] = "missing cloth specular image or socket"
        return False

    texture = next(
        (
            node
            for node in material.node_tree.nodes
            if node.bl_idname == "ShaderNodeTexImage" and node.image == image
        ),
        None,
    )
    if texture is None:
        texture = material.node_tree.nodes.new("ShaderNodeTexImage")
        texture.image = image
        texture.location = (-420, -420)
    texture.name = "Endfield HGRP Metallic Gloss"
    texture.label = "HGRP Metal / Spec / Shadow / Smooth"
    material.node_tree.links.new(texture.outputs["Alpha"], specular)
    material["endfieldShaderBackend"] = "blender-eevee-character-cloth-v1"
    return True


def configure_overlay_shadow_nodes(material: bpy.types.Material) -> bool:
    metadata = material_preview_metadata(material)
    shadow = metadata.get("overlayShadow")
    if (
        metadata.get("materialRole") != "overlayShadow"
        or not isinstance(shadow, dict)
        or not material.node_tree
    ):
        return False

    color = shadow.get("color")
    image = find_imported_image(metadata.get("baseColorTextureId"))
    if not isinstance(color, list) or len(color) != 3 or image is None:
        material["endfieldPreviewDiagnostic"] = "missing overlay shadow color or mask"
        return False

    # The game multiplies the framebuffer by the overlay color. Eevee materials
    # cannot read that framebuffer, so a black layer with luminance-derived
    # opacity provides the equivalent grayscale attenuation.
    luminance = sum(
        float(value) * weight
        for value, weight in zip(color, (0.2126, 0.7152, 0.0722))
    )
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()
    texture = nodes.new("ShaderNodeTexImage")
    texture.name = "Endfield Overlay Shadow Mask"
    texture.image = image
    texture.location = (-520, 0)
    attenuation = nodes.new("ShaderNodeMath")
    attenuation.name = "Endfield Overlay Shadow Attenuation"
    attenuation.operation = "MULTIPLY"
    attenuation.inputs[1].default_value = 1.0 - max(0.0, min(1.0, luminance))
    attenuation.location = (-300, -40)
    transparent = nodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (-80, 100)
    black = nodes.new("ShaderNodeEmission")
    black.inputs["Color"].default_value = (0.0, 0.0, 0.0, 1.0)
    black.location = (-80, -100)
    mix = nodes.new("ShaderNodeMixShader")
    mix.location = (140, 0)
    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (360, 0)
    links.new(texture.outputs["Alpha"], attenuation.inputs[0])
    links.new(attenuation.outputs["Value"], mix.inputs[0])
    links.new(transparent.outputs["BSDF"], mix.inputs[1])
    links.new(black.outputs["Emission"], mix.inputs[2])
    links.new(mix.outputs["Shader"], output.inputs["Surface"])
    material["endfieldShaderBackend"] = "blender-eevee-overlay-shadow-v1"
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


def configure_world(lighting: CharacterLighting | None) -> None:
    scene = bpy.context.scene
    if scene.world is None:
        scene.world = bpy.data.worlds.new("Endfield Preview World")
    if lighting is None or lighting.cubemap.equirectangular_path is None:
        scene.world.use_nodes = False
        scene.world.color = (0.025, 0.03, 0.04)
        return

    panorama_path = lighting.cubemap.equirectangular_path
    if not panorama_path.is_file():
        raise FileNotFoundError(f"lighting panorama does not exist: {panorama_path}")
    scene.world.use_nodes = True
    nodes = scene.world.node_tree.nodes
    links = scene.world.node_tree.links
    nodes.clear()
    environment = nodes.new("ShaderNodeTexEnvironment")
    environment.name = "Endfield Character Cubemap"
    environment.label = f"角色 Cubemap（{lighting.cubemap.encoding}）"
    environment.image = bpy.data.images.load(str(panorama_path), check_existing=True)
    environment.image.pack()
    environment.interpolation = "Linear"
    environment.location = (-420, 0)
    background = nodes.new("ShaderNodeBackground")
    background.name = "Endfield Character Ambient"
    background.inputs["Strength"].default_value = lighting.ambient.base_intensity
    background.location = (-120, 0)
    output = nodes.new("ShaderNodeOutputWorld")
    output.location = (120, 0)
    links.new(environment.outputs["Color"], background.inputs["Color"])
    links.new(background.outputs["Background"], output.inputs["Surface"])


def configure_armature_viewport() -> int:
    armatures = [obj for obj in bpy.data.objects if obj.type == "ARMATURE"]
    for armature in armatures:
        # 骨架仍完整保留供动画编辑使用，但默认不遮挡角色模型。
        armature.data.display_type = "STICK"
        armature.show_in_front = False
        armature.select_set(False)
        armature.hide_set(True)
    return len(armatures)


def configure_animation_timeline() -> int:
    actions = list(bpy.data.actions)
    if not actions:
        return 0
    frame_starts = [action.frame_range[0] for action in actions]
    frame_ends = [action.frame_range[1] for action in actions]
    bpy.context.scene.frame_start = math.floor(min(frame_starts))
    bpy.context.scene.frame_end = math.ceil(max(frame_ends))
    bpy.context.scene.frame_set(bpy.context.scene.frame_start)
    return len(actions)


def configure_preview_scene(
    enable_outline: bool,
    lighting: CharacterLighting | None,
    framing: str,
    main_light_direction: Vector,
) -> None:
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x = 768
    scene.render.resolution_y = 1024
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Medium High Contrast"

    configure_world(lighting)
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
    if framing == "portrait":
        framing_height = size.z * 0.38
        camera_target = Vector((center.x, center.y, minimum.z + size.z * 0.79))
        distance = framing_height / (2 * math.tan(vertical_fov / 2)) * 1.16
        camera.location = camera_target + Vector((size.x * 0.04, -distance, 0.0))
    else:
        camera_target = center + Vector((0.0, 0.0, size.z * 0.02))
        distance = max(
            size.z / (2 * math.tan(vertical_fov / 2)) * 1.16,
            size.length * 0.8,
        )
        camera.location = center + Vector((size.x * 0.12, -distance, size.z * 0.02))
    point_camera(camera, camera_target)

    light_scale = max(size.length, 1.0)
    main_direction = main_light_direction.copy()
    # The recovered profile direction can be exactly horizontal. A small
    # elevation keeps the preview key light useful without changing its azimuth.
    if abs(main_direction.z) < 0.15:
        main_direction.z = 0.15
        main_direction.normalize()
    ambient = effective_ambient(lighting)
    base_intensity = ambient.base_intensity
    directional_intensity = ambient.directional_intensity
    add_area_light(
        "Endfield Key Light",
        center + main_direction * light_scale,
        260 * base_intensity * directional_intensity,
        light_scale,
        center,
    )
    add_area_light(
        "Endfield Fill Light",
        center
        - main_direction * light_scale * 0.6
        + Vector((0.0, 0.0, light_scale * 0.5)),
        110 * base_intensity * (1.0 - directional_intensity * 0.5),
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
    lighting = load_character_lighting(args.lighting) if args.lighting else None
    main_light_direction = normalized_main_light_direction(args.main_light_direction)
    loaded_preview_images = load_embedded_preview_images(input_path)
    converted = sum(
        build_character_npr_nodes(material, main_light_direction)
        for material in bpy.data.materials
    )
    configured_silk = sum(
        configure_silk_stockings_nodes(material) for material in bpy.data.materials
    )
    configured_cloth = sum(
        configure_character_cloth_nodes(material) for material in bpy.data.materials
    )
    configured_overlays = sum(
        configure_overlay_shadow_nodes(material) for material in bpy.data.materials
    )
    configured_armatures = configure_armature_viewport()
    configured_actions = configure_animation_timeline()
    configure_preview_scene(
        args.outline,
        lighting,
        args.framing,
        main_light_direction,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output_path))
    if args.render:
        render_path = args.render.resolve()
        render_path.parent.mkdir(parents=True, exist_ok=True)
        bpy.context.scene.render.filepath = str(render_path)
        bpy.ops.render.render(write_still=True)
    print(
        f"Imported {input_path.name}; loaded {loaded_preview_images} preview images; "
        f"converted {converted} CharacterNPR materials; "
        f"configured {configured_silk} silk-stockings materials; "
        f"configured {configured_cloth} Character cloth materials; "
        f"configured {configured_overlays} overlay shadows; "
        f"configured {configured_armatures} hidden armatures; "
        f"configured {configured_actions} animation actions; "
        f"lighting={'configured' if lighting is not None else 'fallback'}"
    )


if __name__ == "__main__":
    main()
