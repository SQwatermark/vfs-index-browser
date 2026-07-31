"""Reusable Blender node groups for Endfield material reconstruction."""

from __future__ import annotations

import bpy


MATERIAL_BACKEND_VERSION = 1
SILK_STOCKINGS_GROUP = f"Endfield_SilkStockings_v{MATERIAL_BACKEND_VERSION}"


def _socket(
    tree: bpy.types.ShaderNodeTree,
    name: str,
    socket_type: str,
    *,
    in_out: str = "INPUT",
    default=None,
    minimum: float | None = None,
    maximum: float | None = None,
):
    socket = tree.interface.new_socket(name=name, in_out=in_out, socket_type=socket_type)
    if default is not None:
        socket.default_value = default
    if minimum is not None:
        socket.min_value = minimum
    if maximum is not None:
        socket.max_value = maximum
    return socket


def _math(nodes, operation: str, *, value=None):
    node = nodes.new("ShaderNodeMath")
    node.operation = operation
    if value is not None:
        node.inputs[1].default_value = value
    return node


def _mix_color(nodes, blend_type: str = "MIX"):
    node = nodes.new("ShaderNodeMixRGB")
    node.blend_type = blend_type
    node.inputs[0].default_value = 1.0
    return node


def create_silk_stockings_group() -> bpy.types.ShaderNodeTree:
    """Build an opaque, skin-preserving approximation of Endfield stockings."""

    existing = bpy.data.node_groups.get(SILK_STOCKINGS_GROUP)
    if existing is not None:
        return existing

    tree = bpy.data.node_groups.new(SILK_STOCKINGS_GROUP, "ShaderNodeTree")
    _socket(tree, "Skin Base Color", "NodeSocketColor", default=(0.55, 0.32, 0.24, 1.0))
    _socket(tree, "Stocking Mask RGB", "NodeSocketColor", default=(0.75, 0.0, 0.0, 1.0))
    _socket(tree, "Stocking Coverage", "NodeSocketFloat", default=1.0, minimum=0.0, maximum=1.0)
    _socket(tree, "Dry Tint", "NodeSocketColor", default=(0.34, 0.27, 0.31, 1.0))
    _socket(tree, "Edge Color", "NodeSocketColor", default=(0.008, 0.006, 0.01, 1.0))
    _socket(tree, "Min Affect", "NodeSocketFloat", default=0.05, minimum=0.0, maximum=0.49)
    _socket(tree, "Max Affect", "NodeSocketFloat", default=0.9, minimum=0.5, maximum=1.0)
    _socket(tree, "Wetness", "NodeSocketFloat", default=0.0, minimum=0.0, maximum=1.0)
    _socket(tree, "Base Roughness", "NodeSocketFloat", default=0.38, minimum=0.0, maximum=1.0)
    _socket(tree, "Wet Roughness", "NodeSocketFloat", default=0.08, minimum=0.0, maximum=1.0)
    _socket(tree, "Anisotropy Strength", "NodeSocketFloat", default=0.72, minimum=0.0, maximum=1.0)
    _socket(tree, "Anisotropic Rotation", "NodeSocketFloat", default=0.0, minimum=0.0, maximum=1.0)
    _socket(tree, "Specular IOR Level", "NodeSocketFloat", default=0.55, minimum=0.0, maximum=1.0)
    _socket(tree, "Normal", "NodeSocketVector", default=(0.0, 0.0, 0.0))
    _socket(tree, "Tangent", "NodeSocketVector", default=(0.0, 0.0, 0.0))
    _socket(tree, "Ambient Tint", "NodeSocketColor", default=(0.7, 0.75, 0.86, 1.0))
    _socket(tree, "Ambient Floor", "NodeSocketFloat", default=0.04, minimum=0.0, maximum=0.2)
    _socket(tree, "Shader", "NodeSocketShader", in_out="OUTPUT")

    nodes = tree.nodes
    links = tree.links
    group_input = nodes.new("NodeGroupInput")
    group_input.location = (-1100, 80)
    group_output = nodes.new("NodeGroupOutput")
    group_output.location = (840, 80)

    separate = nodes.new("ShaderNodeSeparateColor")
    separate.name = "Stocking Mask Channels"
    separate.mode = "RGB"
    separate.location = (-900, -500)
    links.new(group_input.outputs["Stocking Mask RGB"], separate.inputs["Color"])

    facing = nodes.new("ShaderNodeLayerWeight")
    facing.name = "View Facing"
    facing.location = (-900, 340)
    links.new(group_input.outputs["Normal"], facing.inputs["Normal"])

    edge = _math(nodes, "SUBTRACT")
    edge.name = "Grazing Angle"
    edge.inputs[0].default_value = 1.05
    edge.use_clamp = True
    edge.location = (-700, 340)
    links.new(facing.outputs["Facing"], edge.inputs[1])

    exponent = _math(nodes, "MULTIPLY", value=2.0)
    exponent.name = "Coverage Exponent"
    exponent.location = (-900, 160)
    links.new(group_input.outputs["Stocking Coverage"], exponent.inputs[0])
    exponent_floor = _math(nodes, "MAXIMUM", value=0.02)
    exponent_floor.location = (-700, 160)
    links.new(exponent.outputs[0], exponent_floor.inputs[0])

    view_power = _math(nodes, "POWER")
    view_power.name = "View Dependent Affect"
    view_power.location = (-500, 300)
    links.new(edge.outputs[0], view_power.inputs[0])
    links.new(exponent_floor.outputs[0], view_power.inputs[1])

    affect_range = _math(nodes, "SUBTRACT")
    affect_range.name = "Affect Range"
    affect_range.location = (-500, 100)
    links.new(group_input.outputs["Max Affect"], affect_range.inputs[0])
    links.new(group_input.outputs["Min Affect"], affect_range.inputs[1])
    affect_scaled = _math(nodes, "MULTIPLY")
    affect_scaled.location = (-300, 260)
    links.new(view_power.outputs[0], affect_scaled.inputs[0])
    links.new(affect_range.outputs[0], affect_scaled.inputs[1])
    affect = _math(nodes, "ADD")
    affect.name = "Silk Affect"
    affect.use_clamp = True
    affect.location = (-100, 260)
    links.new(affect_scaled.outputs[0], affect.inputs[0])
    links.new(group_input.outputs["Min Affect"], affect.inputs[1])

    tinted_skin = _mix_color(nodes, "MULTIPLY")
    tinted_skin.name = "Skin Through Dry Silk"
    tinted_skin.location = (-500, 560)
    links.new(group_input.outputs["Skin Base Color"], tinted_skin.inputs[1])
    links.new(group_input.outputs["Dry Tint"], tinted_skin.inputs[2])
    silk_color = _mix_color(nodes)
    silk_color.name = "View Dependent Stocking Color"
    silk_color.location = (-100, 540)
    links.new(affect.outputs[0], silk_color.inputs[0])
    links.new(tinted_skin.outputs["Color"], silk_color.inputs[1])
    links.new(group_input.outputs["Edge Color"], silk_color.inputs[2])
    final_color = _mix_color(nodes)
    final_color.name = "Apply Stocking Coverage"
    final_color.location = (120, 500)
    links.new(group_input.outputs["Stocking Coverage"], final_color.inputs[0])
    links.new(group_input.outputs["Skin Base Color"], final_color.inputs[1])
    links.new(silk_color.outputs["Color"], final_color.inputs[2])

    wet_mask = _math(nodes, "MULTIPLY")
    wet_mask.location = (-500, -380)
    links.new(separate.outputs["Blue"], wet_mask.inputs[0])
    links.new(group_input.outputs["Wetness"], wet_mask.inputs[1])
    roughness = nodes.new("ShaderNodeMapRange")
    roughness.name = "Wet Roughness"
    roughness.clamp = True
    roughness.location = (-100, -320)
    roughness.inputs["From Min"].default_value = 0.0
    roughness.inputs["From Max"].default_value = 1.0
    links.new(wet_mask.outputs[0], roughness.inputs["Value"])
    links.new(group_input.outputs["Base Roughness"], roughness.inputs["To Min"])
    links.new(group_input.outputs["Wet Roughness"], roughness.inputs["To Max"])

    anisotropy = _math(nodes, "MULTIPLY")
    anisotropy.name = "Mask Anisotropy"
    anisotropy.location = (-100, -500)
    links.new(separate.outputs["Red"], anisotropy.inputs[0])
    links.new(group_input.outputs["Anisotropy Strength"], anisotropy.inputs[1])

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.name = "Scene Lit Silk"
    principled.location = (350, 180)
    principled.inputs["IOR"].default_value = 1.45
    principled.inputs["Sheen Weight"].default_value = 0.06
    links.new(final_color.outputs["Color"], principled.inputs["Base Color"])
    links.new(roughness.outputs["Result"], principled.inputs["Roughness"])
    links.new(anisotropy.outputs[0], principled.inputs["Anisotropic"])
    links.new(group_input.outputs["Anisotropic Rotation"], principled.inputs["Anisotropic Rotation"])
    links.new(group_input.outputs["Specular IOR Level"], principled.inputs["Specular IOR Level"])
    links.new(group_input.outputs["Normal"], principled.inputs["Normal"])
    links.new(group_input.outputs["Tangent"], principled.inputs["Tangent"])

    ambient_color = _mix_color(nodes, "MULTIPLY")
    ambient_color.name = "Character Ambient Tint"
    ambient_color.location = (100, -80)
    links.new(final_color.outputs["Color"], ambient_color.inputs[1])
    links.new(group_input.outputs["Ambient Tint"], ambient_color.inputs[2])
    ambient = nodes.new("ShaderNodeEmission")
    ambient.name = "Character Ambient Floor"
    ambient.location = (350, -80)
    links.new(ambient_color.outputs["Color"], ambient.inputs["Color"])
    blend = nodes.new("ShaderNodeMixShader")
    blend.name = "Scene Light With Ambient Floor"
    blend.location = (610, 100)
    links.new(group_input.outputs["Ambient Floor"], blend.inputs[0])
    links.new(principled.outputs["BSDF"], blend.inputs[1])
    links.new(ambient.outputs["Emission"], blend.inputs[2])
    links.new(blend.outputs["Shader"], group_output.inputs["Shader"])

    tree["endfieldMaterialBackendVersion"] = MATERIAL_BACKEND_VERSION
    tree["endfieldMaterialRole"] = "silkStockings"
    return tree
