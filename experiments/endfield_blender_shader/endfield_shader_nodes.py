"""Portable Blender node groups inspired by Endfield character shading.

This module is intentionally standalone. Run it from Blender's Text Editor or:

    blender --background scene.blend --python endfield_shader_nodes.py

It only creates versioned node groups. Existing materials and node groups are
never replaced.
"""

from __future__ import annotations

import bpy


VERSION = "v0_1"
CHARACTER_GROUP = f"EF_CharacterSurface_{VERSION}"
STOCKINGS_GROUP = f"EF_SilkStockings_{VERSION}"


def _socket(
    tree: bpy.types.NodeTree,
    name: str,
    socket_type: str,
    *,
    in_out: str = "INPUT",
    default=None,
    minimum=None,
    maximum=None,
):
    socket = tree.interface.new_socket(
        name=name,
        in_out=in_out,
        socket_type=socket_type,
    )
    if default is not None:
        socket.default_value = default
    if minimum is not None:
        socket.min_value = minimum
    if maximum is not None:
        socket.max_value = maximum
    return socket


def _math(nodes, operation: str, x: float | None = None, y: float | None = None):
    node = nodes.new("ShaderNodeMath")
    node.operation = operation
    if x is not None:
        node.inputs[0].default_value = x
    if y is not None:
        node.inputs[1].default_value = y
    return node


def _multiply_color(nodes):
    node = nodes.new("ShaderNodeMixRGB")
    node.blend_type = "MULTIPLY"
    node.inputs["Fac"].default_value = 1.0
    return node


def _mix_color(nodes):
    node = nodes.new("ShaderNodeMixRGB")
    node.blend_type = "MIX"
    return node


def _connect_principled_inputs(links, group_input, principled):
    names = (
        "Metallic",
        "Roughness",
        "IOR",
        "Specular IOR Level",
        "Anisotropic",
        "Anisotropic Rotation",
        "Coat Weight",
        "Coat Roughness",
        "Sheen Weight",
        "Normal",
        "Tangent",
    )
    for name in names:
        if name in group_input.outputs and name in principled.inputs:
            links.new(group_input.outputs[name], principled.inputs[name])


def create_character_surface_group() -> bpy.types.ShaderNodeTree:
    """Create a scene-lit PBR surface with a small character ambient floor."""
    existing = bpy.data.node_groups.get(CHARACTER_GROUP)
    if existing is not None:
        return existing

    tree = bpy.data.node_groups.new(CHARACTER_GROUP, "ShaderNodeTree")
    _socket(tree, "Base Color", "NodeSocketColor", default=(0.5, 0.5, 0.5, 1.0))
    _socket(tree, "Metallic", "NodeSocketFloat", default=0.0, minimum=0.0, maximum=1.0)
    _socket(tree, "Roughness", "NodeSocketFloat", default=0.45, minimum=0.0, maximum=1.0)
    _socket(tree, "IOR", "NodeSocketFloat", default=1.5, minimum=1.0, maximum=4.0)
    _socket(
        tree,
        "Specular IOR Level",
        "NodeSocketFloat",
        default=0.5,
        minimum=0.0,
        maximum=1.0,
    )
    _socket(tree, "Anisotropic", "NodeSocketFloat", default=0.0, minimum=0.0, maximum=1.0)
    _socket(
        tree,
        "Anisotropic Rotation",
        "NodeSocketFloat",
        default=0.0,
        minimum=0.0,
        maximum=1.0,
    )
    _socket(tree, "Coat Weight", "NodeSocketFloat", default=0.0, minimum=0.0, maximum=1.0)
    _socket(
        tree,
        "Coat Roughness",
        "NodeSocketFloat",
        default=0.2,
        minimum=0.0,
        maximum=1.0,
    )
    _socket(tree, "Sheen Weight", "NodeSocketFloat", default=0.0, minimum=0.0, maximum=1.0)
    _socket(tree, "Normal", "NodeSocketVector", default=(0.0, 0.0, 0.0))
    _socket(tree, "Tangent", "NodeSocketVector", default=(0.0, 0.0, 0.0))
    _socket(
        tree,
        "Character Ambient Tint",
        "NodeSocketColor",
        default=(0.72, 0.78, 0.9, 1.0),
    )
    _socket(
        tree,
        "Character Ambient Floor",
        "NodeSocketFloat",
        default=0.08,
        minimum=0.0,
        maximum=0.35,
    )
    _socket(tree, "Shader", "NodeSocketShader", in_out="OUTPUT")

    nodes = tree.nodes
    links = tree.links
    group_input = nodes.new("NodeGroupInput")
    group_input.location = (-680, 40)
    group_output = nodes.new("NodeGroupOutput")
    group_output.location = (420, 40)

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.name = "Scene Lit Character Surface"
    principled.location = (-100, 150)
    links.new(group_input.outputs["Base Color"], principled.inputs["Base Color"])
    _connect_principled_inputs(links, group_input, principled)

    ambient_color = _multiply_color(nodes)
    ambient_color.name = "Character Ambient Tint"
    ambient_color.location = (-330, -220)
    links.new(group_input.outputs["Base Color"], ambient_color.inputs[1])
    links.new(group_input.outputs["Character Ambient Tint"], ambient_color.inputs[2])

    ambient = nodes.new("ShaderNodeEmission")
    ambient.name = "Character Ambient Floor"
    ambient.location = (-80, -180)
    ambient.inputs["Strength"].default_value = 1.0
    links.new(ambient_color.outputs["Color"], ambient.inputs["Color"])

    blend = nodes.new("ShaderNodeMixShader")
    blend.name = "Blend Scene Light And Character Ambient"
    blend.location = (180, 40)
    links.new(group_input.outputs["Character Ambient Floor"], blend.inputs[0])
    links.new(principled.outputs["BSDF"], blend.inputs[1])
    links.new(ambient.outputs["Emission"], blend.inputs[2])
    links.new(blend.outputs["Shader"], group_output.inputs["Shader"])

    tree["endfieldResearchVersion"] = VERSION
    tree["endfieldResearchRole"] = "hybrid-pbr-character"
    return tree


def create_silk_stockings_group() -> bpy.types.ShaderNodeTree:
    """Create an opaque, skin-preserving approximation of Endfield stockings.

    Stocking Mask channels follow the observed game convention:
    R anisotropy strength, G anisotropy direction/sharpness,
    B wet smoothness, A skin-through coverage.
    """
    existing = bpy.data.node_groups.get(STOCKINGS_GROUP)
    if existing is not None:
        return existing

    tree = bpy.data.node_groups.new(STOCKINGS_GROUP, "ShaderNodeTree")
    _socket(tree, "Skin Base Color", "NodeSocketColor", default=(0.55, 0.32, 0.24, 1.0))
    _socket(tree, "Stocking Mask RGB", "NodeSocketColor", default=(1.0, 0.5, 0.0, 1.0))
    _socket(tree, "Stocking Coverage", "NodeSocketFloat", default=1.0, minimum=0.0, maximum=1.0)
    _socket(tree, "Dry Tint", "NodeSocketColor", default=(0.22, 0.18, 0.22, 1.0))
    _socket(tree, "Edge Color", "NodeSocketColor", default=(0.012, 0.01, 0.016, 1.0))
    _socket(tree, "Min Affect", "NodeSocketFloat", default=0.05, minimum=0.0, maximum=0.49)
    _socket(tree, "Max Affect", "NodeSocketFloat", default=0.9, minimum=0.5, maximum=1.0)
    _socket(tree, "Coverage Scale", "NodeSocketFloat", default=1.0, minimum=0.0, maximum=2.0)
    _socket(tree, "Wetness", "NodeSocketFloat", default=0.0, minimum=0.0, maximum=1.0)
    _socket(tree, "Base Roughness", "NodeSocketFloat", default=0.42, minimum=0.0, maximum=1.0)
    _socket(tree, "Wet Roughness", "NodeSocketFloat", default=0.08, minimum=0.0, maximum=1.0)
    _socket(
        tree,
        "Anisotropy Strength",
        "NodeSocketFloat",
        default=0.75,
        minimum=0.0,
        maximum=1.0,
    )
    _socket(
        tree,
        "Anisotropic Rotation",
        "NodeSocketFloat",
        default=0.0,
        minimum=0.0,
        maximum=1.0,
    )
    _socket(
        tree,
        "Specular IOR Level",
        "NodeSocketFloat",
        default=0.5,
        minimum=0.0,
        maximum=1.0,
    )
    _socket(tree, "Normal", "NodeSocketVector", default=(0.0, 0.0, 0.0))
    _socket(tree, "Tangent", "NodeSocketVector", default=(0.0, 0.0, 0.0))
    _socket(
        tree,
        "Character Ambient Tint",
        "NodeSocketColor",
        default=(0.68, 0.74, 0.88, 1.0),
    )
    _socket(
        tree,
        "Character Ambient Floor",
        "NodeSocketFloat",
        default=0.06,
        minimum=0.0,
        maximum=0.25,
    )
    _socket(tree, "Shader", "NodeSocketShader", in_out="OUTPUT")

    nodes = tree.nodes
    links = tree.links
    group_input = nodes.new("NodeGroupInput")
    group_input.location = (-1240, 80)
    group_output = nodes.new("NodeGroupOutput")
    group_output.location = (620, 80)

    separate = nodes.new("ShaderNodeSeparateColor")
    separate.name = "Stocking Mask RGB"
    separate.mode = "RGB"
    separate.location = (-1040, -420)
    links.new(group_input.outputs["Stocking Mask RGB"], separate.inputs["Color"])

    layer_weight = nodes.new("ShaderNodeLayerWeight")
    layer_weight.name = "View Facing"
    layer_weight.location = (-1040, 300)
    links.new(group_input.outputs["Normal"], layer_weight.inputs["Normal"])

    edge = _math(nodes, "SUBTRACT", x=1.05)
    edge.name = "1.05 Minus NdotV"
    edge.location = (-820, 300)
    edge.use_clamp = True
    links.new(layer_weight.outputs["Facing"], edge.inputs[1])

    coverage = _math(nodes, "MULTIPLY")
    coverage.name = "Mask Coverage"
    coverage.location = (-820, -300)
    links.new(group_input.outputs["Stocking Coverage"], coverage.inputs[0])
    links.new(group_input.outputs["Coverage Scale"], coverage.inputs[1])

    exponent = _math(nodes, "MULTIPLY", y=2.0)
    exponent.name = "Coverage Exponent"
    exponent.location = (-610, -220)
    links.new(coverage.outputs[0], exponent.inputs[0])

    exponent_floor = _math(nodes, "MAXIMUM", y=0.02)
    exponent_floor.location = (-410, -220)
    links.new(exponent.outputs[0], exponent_floor.inputs[0])

    view_power = _math(nodes, "POWER")
    view_power.name = "View Dependent Silk Affect"
    view_power.location = (-200, 280)
    links.new(edge.outputs[0], view_power.inputs[0])
    links.new(exponent_floor.outputs[0], view_power.inputs[1])

    affect_range = _math(nodes, "SUBTRACT")
    affect_range.location = (-410, 120)
    links.new(group_input.outputs["Max Affect"], affect_range.inputs[0])
    links.new(group_input.outputs["Min Affect"], affect_range.inputs[1])

    affect_scaled = _math(nodes, "MULTIPLY")
    affect_scaled.location = (0, 210)
    links.new(view_power.outputs[0], affect_scaled.inputs[0])
    links.new(affect_range.outputs[0], affect_scaled.inputs[1])

    affect = _math(nodes, "ADD")
    affect.name = "Silk Affect"
    affect.location = (190, 210)
    affect.use_clamp = True
    links.new(affect_scaled.outputs[0], affect.inputs[0])
    links.new(group_input.outputs["Min Affect"], affect.inputs[1])

    tinted_skin = _multiply_color(nodes)
    tinted_skin.name = "Skin Through Dry Silk"
    tinted_skin.location = (-580, 520)
    links.new(group_input.outputs["Skin Base Color"], tinted_skin.inputs[1])
    links.new(group_input.outputs["Dry Tint"], tinted_skin.inputs[2])

    silk_color = _mix_color(nodes)
    silk_color.name = "View Dependent Stocking Color"
    silk_color.location = (10, 500)
    links.new(affect.outputs[0], silk_color.inputs["Fac"])
    links.new(tinted_skin.outputs["Color"], silk_color.inputs[1])
    links.new(group_input.outputs["Edge Color"], silk_color.inputs[2])

    final_color = _mix_color(nodes)
    final_color.name = "Apply Stocking Coverage"
    final_color.location = (220, 500)
    links.new(coverage.outputs[0], final_color.inputs["Fac"])
    links.new(group_input.outputs["Skin Base Color"], final_color.inputs[1])
    links.new(silk_color.outputs["Color"], final_color.inputs[2])

    wet_mask = _math(nodes, "MULTIPLY")
    wet_mask.location = (-580, -500)
    links.new(separate.outputs["Blue"], wet_mask.inputs[0])
    links.new(group_input.outputs["Wetness"], wet_mask.inputs[1])

    wet_facing = _math(nodes, "MULTIPLY")
    wet_facing.name = "Wet View Gloss"
    wet_facing.location = (-360, -500)
    links.new(wet_mask.outputs[0], wet_facing.inputs[0])
    links.new(layer_weight.outputs["Facing"], wet_facing.inputs[1])

    roughness = nodes.new("ShaderNodeMapRange")
    roughness.name = "Wet Roughness"
    roughness.clamp = True
    roughness.location = (-100, -390)
    roughness.inputs["From Min"].default_value = 0.0
    roughness.inputs["From Max"].default_value = 1.0
    links.new(wet_facing.outputs[0], roughness.inputs["Value"])
    links.new(group_input.outputs["Base Roughness"], roughness.inputs["To Min"])
    links.new(group_input.outputs["Wet Roughness"], roughness.inputs["To Max"])

    anisotropy = _math(nodes, "MULTIPLY")
    anisotropy.name = "Mask Anisotropy"
    anisotropy.location = (-100, -590)
    links.new(separate.outputs["Red"], anisotropy.inputs[0])
    links.new(group_input.outputs["Anisotropy Strength"], anisotropy.inputs[1])

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.name = "Scene Lit Silk Stockings"
    principled.location = (380, 180)
    principled.inputs["IOR"].default_value = 1.45
    principled.inputs["Sheen Weight"].default_value = 0.08
    principled.inputs["Sheen Roughness"].default_value = 0.35
    links.new(final_color.outputs["Color"], principled.inputs["Base Color"])
    links.new(roughness.outputs["Result"], principled.inputs["Roughness"])
    links.new(anisotropy.outputs[0], principled.inputs["Anisotropic"])
    links.new(
        group_input.outputs["Anisotropic Rotation"],
        principled.inputs["Anisotropic Rotation"],
    )
    links.new(
        group_input.outputs["Specular IOR Level"],
        principled.inputs["Specular IOR Level"],
    )
    links.new(group_input.outputs["Normal"], principled.inputs["Normal"])
    links.new(group_input.outputs["Tangent"], principled.inputs["Tangent"])

    ambient_color = _multiply_color(nodes)
    ambient_color.name = "Stocking Ambient Tint"
    ambient_color.location = (100, -40)
    links.new(final_color.outputs["Color"], ambient_color.inputs[1])
    links.new(group_input.outputs["Character Ambient Tint"], ambient_color.inputs[2])

    ambient = nodes.new("ShaderNodeEmission")
    ambient.name = "Stocking Ambient Floor"
    ambient.location = (360, -100)
    ambient.inputs["Strength"].default_value = 1.0
    links.new(ambient_color.outputs["Color"], ambient.inputs["Color"])

    blend = nodes.new("ShaderNodeMixShader")
    blend.name = "Blend Scene Light And Character Ambient"
    blend.location = (560, 80)
    links.new(group_input.outputs["Character Ambient Floor"], blend.inputs[0])
    links.new(principled.outputs["BSDF"], blend.inputs[1])
    links.new(ambient.outputs["Emission"], blend.inputs[2])
    links.new(blend.outputs["Shader"], group_output.inputs["Shader"])

    tree["endfieldResearchVersion"] = VERSION
    tree["endfieldResearchRole"] = "silk-stockings"
    tree["maskChannels"] = "R anisotropy, G direction/sharpness, B wet smoothness, A coverage"
    return tree


def install():
    groups = (
        create_character_surface_group(),
        create_silk_stockings_group(),
    )
    print("Installed Endfield research node groups:")
    for group in groups:
        print(f"  {group.name}")
    return groups


if __name__ == "__main__":
    install()
