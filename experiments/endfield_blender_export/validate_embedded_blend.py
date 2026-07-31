"""Validate the character .blend currently opened by Blender."""

from __future__ import annotations

import json

import bpy


meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
armatures = [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]
materials = [material for material in bpy.data.materials if material.users > 0]
referenced_images = {
    node.image
    for material in materials
    if material.use_nodes and material.node_tree
    for node in material.node_tree.nodes
    if node.bl_idname in {"ShaderNodeTexImage", "ShaderNodeTexEnvironment"}
    and node.image is not None
}
unpacked_images = sorted(
    image.name
    for image in referenced_images
    if image.packed_file is None and not image.packed_files
)
metadata_materials = [
    material for material in materials if material.get("endfieldPreview") is not None
]
source_materials = [
    material
    for material in materials
    if material.get("endfieldSourceMaterial") is not None
]
converted_materials = [
    material for material in materials if material.get("endfieldShaderBackend")
]
backend_materials: dict[str, list[bpy.types.Material]] = {}
for material in converted_materials:
    backend = str(material.get("endfieldShaderBackend"))
    backend_materials.setdefault(backend, []).append(material)

face_materials = backend_materials.get(
    "blender-eevee-character-face-sdf-v1",
    [],
)
body_skin_materials = backend_materials.get(
    "blender-eevee-character-body-skin-v1",
    [],
)
hair_materials = backend_materials.get(
    "blender-eevee-character-hair-v1",
    [],
)
silk_materials = [
    material
    for material in materials
    if isinstance(
        (
            material.get("endfieldPreview").to_dict()
            if hasattr(material.get("endfieldPreview"), "to_dict")
            else material.get("endfieldPreview")
        ),
        dict,
    )
    and "silkStockings"
    in (
        material.get("endfieldPreview").to_dict()
        if hasattr(material.get("endfieldPreview"), "to_dict")
        else material.get("endfieldPreview")
    )
]
disconnected_materials = [
    material.name
    for material in materials
    if material.use_nodes
    and material.node_tree
    and not any(
        output.inputs["Surface"].is_linked
        for output in material.node_tree.nodes
        if output.bl_idname == "ShaderNodeOutputMaterial"
    )
]

assert meshes, "Generated file contains no mesh objects"
assert armatures, "Generated character contains no armature"
assert materials, "Generated file contains no assigned materials"
assert len(source_materials) == len(materials), (
    "Some materials lost endfieldSourceMaterial during GLB import"
)
assert metadata_materials, "No Endfield material metadata survived GLB import"
assert converted_materials, "No Endfield material was converted by the Blender backend"
assert face_materials, "No Skin material selected the recovered face-SDF backend"
assert body_skin_materials, "No Skin material selected the body-skin backend"
assert hair_materials, "No Hair material selected the hair backend"
assert all(
    {
        "Endfield Face SDF Lightmap",
        "Endfield Face SDF Mask",
        "Endfield Face Pseudo Normal Light Dot",
        "Endfield Face Shadow LUT Slice 0",
        "Endfield Face Shadow LUT Slice 1",
        "Endfield Face Interpolate Shadow LUT",
        "Endfield Diffuse Ramp",
    }.issubset(material.node_tree.nodes.keys())
    for material in face_materials
), "Face-SDF material is missing its SDF, mask, or diffuse-ramp nodes"
assert all(
    material.node_tree.nodes["Endfield Face SDF Lightmap"].outputs["Color"].is_linked
    and material.node_tree.nodes["Endfield Face SDF Mask"].outputs["Color"].is_linked
    and material.node_tree.nodes["Endfield Face SDF Channels"].outputs["Blue"].is_linked
    and material.node_tree.nodes["Endfield Face Shadow LUT Slice 0"].outputs[
        "Color"
    ].is_linked
    and material.node_tree.nodes["Endfield Face Shadow LUT Slice 1"].outputs[
        "Color"
    ].is_linked
    and material.node_tree.nodes["Endfield Diffuse Ramp"].outputs["Color"].is_linked
    for material in face_materials
), "Face-SDF textures exist but are not connected to the recovered carrier"
assert all(
    "source carrier: b225 SDF core plus Shadow LUT"
    in str(material.get("endfieldPreviewDiagnostic", ""))
    for material in face_materials
), "Face-SDF material does not record its source shader carrier"
assert all(
    str(material.get("endfieldShaderBackend", "")).startswith(
        "blender-eevee-silk-stockings-v"
    )
    and any(
        node.bl_idname == "ShaderNodeGroup"
        and node.node_tree
        and node.node_tree.name.startswith("Endfield_SilkStockings_")
        for node in material.node_tree.nodes
    )
    for material in silk_materials
), "Silk-stockings metadata did not select the dedicated node group"
assert all(
    not any(
        node.bl_idname == "ShaderNodeBsdfPrincipled"
        for node in material.node_tree.nodes
    )
    for material in silk_materials
), "Silk-stockings material retained a disconnected imported Principled node"
assert not unpacked_images, f"Referenced images are not packed: {unpacked_images}"
assert not disconnected_materials, (
    f"Materials have no connected Surface output: {disconnected_materials}"
)

print(
    "Embedded Blender validation passed:\n"
    + json.dumps(
        {
            "meshes": len(meshes),
            "armatures": len(armatures),
            "materials": len(materials),
            "endfieldSourceMaterials": len(source_materials),
            "endfieldMetadataMaterials": len(metadata_materials),
            "convertedMaterials": len(converted_materials),
            "faceSdfMaterials": len(face_materials),
            "bodySkinMaterials": len(body_skin_materials),
            "hairMaterials": len(hair_materials),
            "silkStockingsMaterials": len(silk_materials),
            "referencedPackedImages": len(referenced_images),
        },
        ensure_ascii=False,
        indent=2,
    )
)
