"""Headless validation for endfield_shader_nodes.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import bpy


HERE = Path(__file__).resolve().parent
MODULE_PATH = HERE / "endfield_shader_nodes.py"


def load_module():
    spec = importlib.util.spec_from_file_location("endfield_shader_nodes", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def socket_names(tree, in_out):
    return {
        item.name
        for item in tree.interface.items_tree
        if getattr(item, "in_out", None) == in_out
    }


def validate_group(tree, expected_inputs):
    actual_inputs = socket_names(tree, "INPUT")
    missing = expected_inputs - actual_inputs
    assert not missing, f"{tree.name} is missing inputs: {sorted(missing)}"
    assert "Shader" in socket_names(tree, "OUTPUT")
    assert any(node.bl_idname == "ShaderNodeBsdfPrincipled" for node in tree.nodes)
    assert any(node.bl_idname == "ShaderNodeMixShader" for node in tree.nodes)
    assert len(tree.links) > 0


module = load_module()
character, stockings = module.install()

validate_group(
    character,
    {
        "Base Color",
        "Metallic",
        "Roughness",
        "Normal",
        "Character Ambient Tint",
        "Character Ambient Floor",
    },
)
validate_group(
    stockings,
    {
        "Skin Base Color",
        "Stocking Mask RGB",
        "Stocking Coverage",
        "Dry Tint",
        "Edge Color",
        "Wetness",
        "Anisotropy Strength",
        "Normal",
        "Tangent",
    },
)

# Idempotency matters when the script is run repeatedly in one .blend file.
before = set(bpy.data.node_groups.keys())
module.install()
after = set(bpy.data.node_groups.keys())
assert before == after

output = HERE / "endfield_shader_nodes.blend"
bpy.ops.wm.save_as_mainfile(filepath=str(output))
print(f"Validation passed; wrote {output}")
