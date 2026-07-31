import struct
import tempfile
import unittest
from pathlib import Path

from animestudio_model import (
    AnimeStudioObject,
    UnityObjectId,
    attach_mesh_geometry,
    attach_texture_images,
    build_standalone_material_objects,
    build_hierarchy_document,
    collect_material_textures,
    find_container_root_game_object,
    infer_character_material_role,
    load_standalone_material_payloads,
)
from model_document import validate_model_document


def make_object(source_file, path_id, type_name, name, payload, references=(), metadata=None):
    value = {
        "$animestudio": {
            "sourceFile": source_file,
            "pathId": path_id,
            "classId": 1 if type_name == "GameObject" else 4,
            "type": type_name,
            "name": name,
            "container": "assets/sample.prefab",
            "pptrReferences": list(references),
            **(metadata or {}),
        },
        **payload,
    }
    return AnimeStudioObject.from_payload(value)


def ref(path, source_file, path_id, type_name):
    return {
        "path": path,
        "fileId": 0,
        "pathId": path_id,
        "targetType": type_name,
        "targetPathId": path_id,
        "targetSourceFile": source_file,
    }


class AnimeStudioModelTests(unittest.TestCase):
    def test_skin_signature_takes_precedence_over_eye_highlight(self):
        role = infer_character_material_role(
            {"_SDFLightmap", "_EyeHighLight"},
            {"_characterRenderQueue": 2000.0},
        )

        self.assertEqual("skin", role)

    def test_adapts_standalone_material_texture_references(self):
        payload = {
            "m_Name": "Body",
            "m_SavedProperties": {
                "m_TexEnvs": {
                    "_BaseMap": {
                        "m_Texture": {
                            "m_PathID": 42,
                            "Name": "Body_D",
                            "IsNull": False,
                        }
                    }
                }
            },
        }

        objects = build_standalone_material_objects(
            {"body": payload},
            material_source_prefix="material",
            texture_source_prefix="texture",
        )

        material = next(iter(objects.values()))
        self.assertEqual("Body", material.name)
        self.assertEqual(
            {
                "path": "$.m_SavedProperties.m_TexEnvs._BaseMap.m_Texture",
                "targetType": "Texture2D",
                "targetSourceFile": "texture:body_d",
                "targetPathId": 42,
                "targetName": "Body_D",
            },
            material.metadata["pptrReferences"][0],
        )

    def test_standalone_material_loader_rejects_non_material_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "texture.json"
            path.write_text('{"m_Name":"Texture"}', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "m_SavedProperties"):
                load_standalone_material_payloads(Path(directory))

    def test_builds_transform_hierarchy_from_resolved_pptr_metadata(self):
        source_file = "CAB-sample"
        root_id = UnityObjectId(source_file, 1)
        objects = {
            root_id: make_object(
                source_file, 1, "GameObject", "Root", {"m_Name": "Root", "m_IsActive": 1},
                [ref("$.m_Components[0]", source_file, 2, "Transform")],
            ),
            UnityObjectId(source_file, 2): make_object(
                source_file, 2, "Transform", "Root", {
                    "m_LocalPosition": {"x": 1, "y": 2, "z": 3},
                    "m_LocalRotation": {"x": 0, "y": 0, "z": 0, "w": 1},
                    "m_LocalScale": {"x": 1, "y": 1, "z": 1},
                },
                [
                    ref("$.m_GameObject", source_file, 1, "GameObject"),
                    ref("$.m_Children[0]", source_file, 4, "Transform"),
                ],
            ),
            UnityObjectId(source_file, 3): make_object(
                source_file, 3, "GameObject", "Child", {"m_Name": "Child"},
                [ref("$.m_Components[0]", source_file, 4, "Transform")],
            ),
            UnityObjectId(source_file, 4): make_object(
                source_file, 4, "Transform", "Child", {
                    "m_LocalPosition": {"x": 0, "y": 1, "z": 0},
                    "m_LocalRotation": {"x": 0, "y": 0, "z": 0, "w": 1},
                    "m_LocalScale": {"x": 1, "y": 1, "z": 1},
                },
                [
                    ref("$.m_GameObject", source_file, 3, "GameObject"),
                    ref("$.m_Father", source_file, 2, "Transform"),
                ],
            ),
        }

        document = build_hierarchy_document(
            objects,
            root_id,
            logical_path="assets/sample.prefab",
            bundle="sample.ab",
        )

        self.assertEqual([], validate_model_document(document))
        self.assertEqual([root_id.document_id], document["asset"]["rootNodeIds"])
        self.assertEqual(2, len(document["nodes"]))
        root, child = document["nodes"]
        self.assertEqual([child["id"]], root["children"])
        self.assertEqual(root["id"], child["parentId"])
        self.assertEqual([1.0, 2.0, 3.0], root["transform"]["translation"])

    def test_finds_unique_container_root(self):
        source_file = "CAB-sample"
        root_id = UnityObjectId(source_file, 1)
        objects = {
            root_id: make_object(
                source_file, 1, "GameObject", "Root", {},
                [ref("$.m_Components[0]", source_file, 2, "Transform")],
            ),
            UnityObjectId(source_file, 2): make_object(
                source_file, 2, "Transform", "Root", {"m_Father": {"m_FileID": 0, "m_PathID": 0}}
            ),
            UnityObjectId(source_file, 3): make_object(
                source_file, 3, "GameObject", "Child", {},
                [ref("$.m_Components[0]", source_file, 4, "Transform")],
            ),
            UnityObjectId(source_file, 4): make_object(
                source_file, 4, "Transform", "Child", {"m_Father": {"m_FileID": 0, "m_PathID": 2}}
            ),
        }
        self.assertEqual(root_id, find_container_root_game_object(objects, "assets/sample.prefab"))

    def test_falls_back_to_entry_prefab_name_when_containers_are_absent(self):
        root_id = UnityObjectId("CAB-entry", 1)
        transform_id = UnityObjectId("CAB-entry", 2)
        objects = {
            root_id: make_object(
                root_id.source_file,
                root_id.path_id,
                "GameObject",
                "sample",
                {},
                references=[ref("$.m_Components[0]", transform_id.source_file, transform_id.path_id, "Transform")],
                metadata={"container": "", "sourceOriginalPath": "C:/cache/entry.ab"},
            ),
            transform_id: make_object(
                transform_id.source_file,
                transform_id.path_id,
                "Transform",
                "Transform",
                payload={"m_Father": {"m_PathID": 0}},
                metadata={"container": ""},
            ),
        }

        self.assertEqual(root_id, find_container_root_game_object(objects, "assets/sample.prefab"))

    def test_reports_missing_transform(self):
        entry = UnityObjectId("CAB-sample", 1)
        objects = {
            entry: make_object("CAB-sample", 1, "GameObject", "Root", {"m_Name": "Root"})
        }
        document = build_hierarchy_document(
            objects, entry, logical_path="assets/sample.prefab", bundle="sample.ab"
        )
        self.assertEqual("MISSING_TRANSFORM", document["diagnostics"][0]["code"])

    def test_preserves_renderer_component_and_its_asset_references(self):
        source_file = "CAB-sample"
        mesh_file = "CAB-mesh"
        entry = UnityObjectId(source_file, 1)
        renderer_id = UnityObjectId(source_file, 3)
        objects = {
            entry: make_object(
                source_file,
                1,
                "GameObject",
                "Body",
                {"m_Name": "Body"},
                [
                    ref("$.m_Components[0]", source_file, 2, "Transform"),
                    ref("$.m_Components[1]", source_file, 3, "SkinnedMeshRenderer"),
                ],
            ),
            UnityObjectId(source_file, 2): make_object(
                source_file,
                2,
                "Transform",
                "Body",
                {"m_Father": {"m_FileID": 0, "m_PathID": 0}},
            ),
            renderer_id: make_object(
                source_file,
                3,
                "SkinnedMeshRenderer",
                "Body",
                {"m_Enabled": 1},
                [ref("$.m_Mesh", mesh_file, 10, "Mesh")],
            ),
        }

        document = build_hierarchy_document(
            objects, entry, logical_path="assets/sample.prefab", bundle="sample.ab"
        )

        component = document["nodes"][0]["extras"]["unityComponents"][0]
        self.assertEqual("SkinnedMeshRenderer", component["type"])
        self.assertEqual("unity:CAB-mesh:10", component["references"][0]["target"])
        mesh_edge = next(edge for edge in document["dependencies"] if edge["kind"] == "$.m_Mesh")
        self.assertEqual("missing", mesh_edge["status"])
        self.assertEqual([], validate_model_document(document))

    def test_ignores_game_object_convenience_component_references(self):
        source_file = "CAB-sample"
        entry = UnityObjectId(source_file, 1)
        objects = {
            entry: make_object(
                source_file,
                1,
                "GameObject",
                "Body",
                {"m_Name": "Body"},
                [
                    ref("$.m_Components[0]", source_file, 2, "Transform"),
                    ref("$.m_SkinnedMeshRenderer.m_Mesh", "CAB-mesh", 10, "Mesh"),
                ],
            ),
            UnityObjectId(source_file, 2): make_object(
                source_file,
                2,
                "Transform",
                "Body",
                {"m_Father": {"m_FileID": 0, "m_PathID": 0}},
            ),
        }

        document = build_hierarchy_document(
            objects, entry, logical_path="assets/sample.prefab", bundle="sample.ab"
        )

        self.assertFalse(
            any(edge["kind"].startswith("$.m_SkinnedMeshRenderer") for edge in document["dependencies"])
        )

    def test_attaches_mesh_geometry_and_material(self):
        source_file = "CAB-sample"
        mesh_file = "CAB-mesh"
        material_file = "CAB-material"
        entry = UnityObjectId(source_file, 1)
        objects = {
            entry: make_object(
                source_file, 1, "GameObject", "Body", {"m_Name": "Body"},
                [
                    ref("$.m_Components[0]", source_file, 2, "Transform"),
                    ref("$.m_Components[1]", source_file, 3, "SkinnedMeshRenderer"),
                    ref("$.m_Components[2]", source_file, 4, "LODGroup"),
                ],
            ),
            UnityObjectId(source_file, 2): make_object(
                source_file,
                2,
                "Transform",
                "Body",
                {
                    "m_Father": {"m_PathID": 0},
                    "m_LocalPosition": {"X": 1, "Y": 2, "Z": 3},
                    "m_LocalRotation": {"X": 0, "Y": 0, "Z": 0, "W": 1},
                    "m_LocalScale": {"X": 1, "Y": 1, "Z": 1},
                },
                [ref("$.m_GameObject", source_file, 1, "GameObject")],
            ),
            UnityObjectId(source_file, 3): make_object(
                source_file, 3, "SkinnedMeshRenderer", "Body", {},
                [
                    ref("$.m_Mesh", mesh_file, 10, "Mesh"),
                    ref("$.m_Materials[0]", material_file, 20, "Material"),
                    ref("$.m_Bones[0]", source_file, 2, "Transform"),
                    ref("$.m_RootBone", source_file, 2, "Transform"),
                ],
            ),
            UnityObjectId(source_file, 4): make_object(
                source_file,
                4,
                "LODGroup",
                "Body LOD",
                {
                    "m_LocalReferencePoint": {"X": 0, "Y": 1, "Z": 0},
                    "m_Size": 2.0,
                    "m_FadeMode": 0,
                    "m_LODs": [{"screenRelativeHeight": 0.5, "fadeTransitionWidth": 0.1}],
                },
                [ref("$.m_LODs[0].renderers[0].renderer", source_file, 3, "SkinnedMeshRenderer")],
            ),
            UnityObjectId(mesh_file, 10): make_object(
                mesh_file, 10, "Mesh", "Triangle",
                {
                    "m_VertexCount": 3,
                    "m_Vertices": [0, 0, 0, 1, 0, 0, 0, 1, 0],
                    "m_Normals": [0, 0, 1] * 3,
                    "m_UV0": [0, 0, 1, 0, 0, 1],
                    "m_Tangents": [1, 0, 0, 1] * 3,
                    "m_Indices": [0, 1, 2],
                    "m_SubMeshes": [{"indexCount": 3, "topology": "Triangles"}],
                    "m_Skin": [],
                    "m_BindPose": [
                        {
                            **{f"M{row}{column}": float(row * 4 + column) for row in range(4) for column in range(4)}
                        }
                    ],
                },
                metadata={"container": ""},
            ),
            UnityObjectId(material_file, 20): make_object(
                material_file,
                20,
                "Material",
                "BodyMat",
                {
                    "m_SavedProperties": {
                        "m_TexEnvs": {
                            "_BaseMap": {
                                "m_Scale": {"X": 1, "Y": 1},
                                "m_Offset": {"X": 0, "Y": 0},
                            },
                            "_DiffRampMap": {
                                "m_Scale": {"X": 1, "Y": 1},
                                "m_Offset": {"X": 0, "Y": 0},
                            },
                            "_SDFMask": {
                                "m_Scale": {"X": 1, "Y": 1},
                                "m_Offset": {"X": 0, "Y": 0},
                            },
                            "_ShadowLutTex": {
                                "m_Scale": {"X": 1, "Y": 1},
                                "m_Offset": {"X": 0, "Y": 0},
                            },
                        },
                        "m_Ints": {},
                        "m_Floats": {
                            "_BumpScale": 1.0,
                            "_Cull": 2.0,
                            "_characterRenderQueue": 2000.0,
                            "_EnableRealisticLighting": 1.0,
                            "_Metallic": 0.2,
                            "_SilkStockings": 1.0,
                            "_SilkStockingsMaxAffect": 0.9,
                            "_Smoothness": 0.25,
                            "_UseDiffRampMap": 1.0,
                            "_UseSDFLightmap": 1.0,
                            "_UseShadowLutTex": 1.0,
                        },
                        "m_Colors": {
                            "_BaseColor": {"r": 1, "g": 1, "b": 1, "a": 1},
                            "_SilkStockingsColor": {"r": 0, "g": 0, "b": 0, "a": 1},
                        },
                    }
                },
                [
                    ref("$.m_SavedProperties.m_TexEnvs._BaseMap.m_Texture", "CAB-texture", 30, "Texture2D"),
                    ref("$.m_SavedProperties.m_TexEnvs._DiffRampMap.m_Texture", "CAB-texture", 30, "Texture2D"),
                    ref("$.m_SavedProperties.m_TexEnvs._SDFMask.m_Texture", "CAB-texture", 30, "Texture2D"),
                    ref("$.m_SavedProperties.m_TexEnvs._ShadowLutTex.m_Texture", "CAB-texture", 30, "Texture2D"),
                ],
                metadata={"container": ""},
            ),
        }
        document = build_hierarchy_document(
            objects, entry, logical_path="assets/sample.prefab", bundle="sample.ab"
        )

        geometry = attach_mesh_geometry(document, objects)

        self.assertGreater(len(geometry), 0)
        self.assertEqual(UnityObjectId(mesh_file, 10).document_id, document["nodes"][0]["meshId"])
        self.assertEqual([1.0, 2.0, 3.0], document["nodes"][0]["transform"]["translation"])
        self.assertEqual(1, len(document["meshes"]))
        self.assertEqual(1, len(document["materials"]))
        self.assertEqual(1, len(document["skeletons"]))
        self.assertEqual(1, len(document["skins"]))
        self.assertEqual(document["skins"][0]["id"], document["nodes"][0]["skinId"])
        self.assertEqual(0, document["nodes"][0]["extras"]["lodLevel"])
        lod_group = next(
            component
            for component in document["nodes"][0]["extras"]["unityComponents"]
            if component["type"] == "LODGroup"
        )
        self.assertEqual(0.5, lod_group["properties"]["levels"][0]["screenRelativeHeight"])
        self.assertEqual("mat4", document["accessors"][-1]["type"])
        bind_accessor = document["accessors"][-1]
        bind_view = next(
            view for view in document["bufferViews"] if view["id"] == bind_accessor["bufferViewId"]
        )
        bind_values = struct.unpack_from("<16f", geometry, bind_view["byteOffset"])
        self.assertEqual(tuple(float(value) for value in range(16)), bind_values)
        textures = collect_material_textures(document, objects)
        texture_id = UnityObjectId("CAB-texture", 30)
        self.assertIn(texture_id, textures)
        attach_texture_images(document, textures, {texture_id: "/texture.png"})
        self.assertEqual(texture_id.document_id, document["materials"][0]["previewPbr"]["baseColorTextureId"])
        self.assertEqual(texture_id.document_id, document["materials"][0]["previewPbr"]["diffuseRampTextureId"])
        self.assertEqual(texture_id.document_id, document["materials"][0]["previewPbr"]["sdfMaskTextureId"])
        self.assertEqual(texture_id.document_id, document["materials"][0]["previewPbr"]["shadowLutTextureId"])
        for expected, actual in zip(
            [0.1, 0.1, 0.1, 1.0],
            document["materials"][0]["previewPbr"]["baseColorFactor"],
        ):
            self.assertAlmostEqual(expected, actual)
        self.assertEqual(0.2, document["materials"][0]["previewPbr"]["metallicFactor"])
        self.assertEqual(0.75, document["materials"][0]["previewPbr"]["roughnessFactor"])
        self.assertEqual("characterNpr", document["materials"][0]["previewPbr"]["materialFamily"])
        self.assertEqual("cloth", document["materials"][0]["previewPbr"]["materialRole"])
        self.assertNotIn("unlit", document["materials"][0]["previewPbr"])
        self.assertEqual(
            {"color": [0.0, 0.0, 0.0], "maxAffect": 0.9},
            document["materials"][0]["previewPbr"]["silkStockings"],
        )
        self.assertFalse(document["materials"][0]["previewPbr"]["doubleSided"])
        self.assertEqual("/texture.png", document["images"][0]["uri"])
        self.assertEqual({"POSITION", "NORMAL", "TEXCOORD_0", "TANGENT"}, set(document["meshes"][0]["primitives"][0]["attributes"]))
        self.assertEqual([], validate_model_document(document))


if __name__ == "__main__":
    unittest.main()
