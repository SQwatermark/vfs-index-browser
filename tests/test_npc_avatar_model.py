import unittest

from model_document import validate_model_document
from npc_avatar_model import (
    _complete_skeleton_world_matrices,
    build_static_avatar_mesh_document,
)


def triangle(name):
    return {
        "m_Name": name,
        "m_VertexCount": 3,
        "m_Vertices": [0, 0, 0, 1, 0, 0, 0, 1, 0],
        "m_Normals": [0, 0, 1] * 3,
        "m_UV0": [0, 0, 1, 0, 0, 1],
        "m_Indices": [0, 1, 2],
        "m_SubMeshes": [{"indexCount": 3, "topology": "Triangles"}],
        "m_Skin": [],
    }


class NpcAvatarModelTests(unittest.TestCase):
    def test_ambiguous_parent_bind_pose_falls_back_to_avatar_pose(self):
        identity = [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ]
        child_a = [row[:] for row in identity]
        child_b = [row[:] for row in identity]
        child_a[0][3] = 1.0
        child_b[0][3] = 2.0
        worlds = [None, child_a, child_b]

        ambiguous = _complete_skeleton_world_matrices(
            worlds,
            [
                {"m_ParentId": -1},
                {"m_ParentId": 0},
                {"m_ParentId": 0},
            ],
            [identity, identity, identity],
            [100, 101, 102],
        )

        self.assertEqual([100], ambiguous)
        self.assertEqual(identity, worlds[0])
        self.assertEqual(child_a, worlds[1])
        self.assertEqual(child_b, worlds[2])

    def test_builds_static_document_from_selected_lod(self):
        avatar_mesh = {
            "name": "sample-avatar",
            "mainPrefabPath": "Assets/Npcs/sample.prefab",
            "slots": [
                {
                    "lods": {
                        "0": [
                            {
                                "meshName": "SampleBody",
                                "meshPathHash": 101,
                                "meshPaths": ["Assets/SampleBody.asset"],
                                "rootBoneName": "Pelvis",
                                "isRendererDisabled": False,
                            }
                        ]
                    }
                }
            ],
        }
        document, geometry = build_static_avatar_mesh_document(
            avatar_mesh,
            {"samplebody": triangle("SampleBody")},
            buffer_uri="/api/model-buffer?lod=0",
        )

        self.assertEqual([], validate_model_document(document))
        self.assertGreater(len(geometry), 0)
        self.assertEqual("/api/model-buffer?lod=0", document["buffers"][0]["uri"])
        self.assertEqual(1, len(document["meshes"]))
        self.assertEqual(2, len(document["nodes"]))
        root, mesh = document["nodes"]
        self.assertEqual(document["meshes"][0]["id"], mesh["meshId"])
        self.assertEqual([root["id"]], document["asset"]["rootNodeIds"])
        self.assertEqual([mesh["id"]], root["children"])
        self.assertEqual(root["id"], mesh["parentId"])
        self.assertAlmostEqual(-0.70710678, root["transform"]["rotation"][0])

    def test_reports_all_missing_meshes(self):
        avatar_mesh = {
            "name": "sample-avatar",
            "mainPrefabPath": "Assets/Npcs/sample.prefab",
            "slots": [{"lods": {"0": [{"meshName": "Body"}, {"meshName": "Hair"}]}}],
        }
        with self.assertRaisesRegex(ValueError, "Body, Hair"):
            build_static_avatar_mesh_document(avatar_mesh, {})

    def test_material_binding_is_optional(self):
        avatar_mesh = {
            "name": "sample-avatar",
            "mainPrefabPath": "Assets/Npcs/sample.prefab",
            "slots": [
                {
                    "lods": {
                        "0": [
                            {
                                "meshName": "SampleBody",
                                "materialPaths": [
                                    {
                                        "paths": [
                                            "Assets/Npcs/Materials/SampleBody.mat"
                                        ]
                                    }
                                ],
                            }
                        ]
                    }
                }
            ],
        }

        document, _geometry = build_static_avatar_mesh_document(
            avatar_mesh,
            {"samplebody": triangle("SampleBody")},
        )

        self.assertEqual([], document["materials"])
        self.assertNotIn("materialId", document["meshes"][0]["primitives"][0])

    def test_binds_materials_and_textures_in_submesh_order(self):
        avatar_mesh = {
            "name": "sample-avatar",
            "mainPrefabPath": "Assets/Npcs/sample.prefab",
            "slots": [
                {
                    "lods": {
                        "0": [
                            {
                                "meshName": "SampleBody",
                                "materialPaths": [
                                    {
                                        "paths": [
                                            "Assets/Npcs/Materials/SampleBody.mat"
                                        ]
                                    }
                                ],
                            }
                        ]
                    }
                }
            ],
        }
        material = {
            "m_Name": "SampleBody",
            "m_Shader": {
                "m_FileID": 1,
                "m_PathID": 77,
                "Name": "",
                "IsNull": False,
            },
            "m_SavedProperties": {
                "m_TexEnvs": {
                    "_BaseMap": {
                        "m_Texture": {
                            "m_FileID": 1,
                            "m_PathID": 42,
                            "Name": "SampleBody_D",
                            "IsNull": False,
                        },
                        "m_Scale": {"X": 1, "Y": 1},
                        "m_Offset": {"X": 0, "Y": 0},
                    }
                },
                "m_Floats": {"_characterRenderQueue": 2000.0},
                "m_Colors": {
                    "_BaseColor": {"r": 1, "g": 1, "b": 1, "a": 1}
                },
            },
        }

        document, _geometry = build_static_avatar_mesh_document(
            avatar_mesh,
            {"samplebody": triangle("SampleBody")},
            material_payloads={"samplebody": material},
            texture_uris={"samplebody_d": "textures/SampleBody_D.png"},
        )

        self.assertEqual([], validate_model_document(document))
        self.assertEqual(1, len(document["materials"]))
        self.assertEqual(1, len(document["textures"]))
        self.assertEqual(1, len(document["images"]))
        primitive = document["meshes"][0]["primitives"][0]
        self.assertEqual(document["materials"][0]["id"], primitive["materialId"])
        self.assertEqual(
            document["textures"][0]["id"],
            document["materials"][0]["previewPbr"]["baseColorTextureId"],
        )
        self.assertEqual("textures/SampleBody_D.png", document["images"][0]["uri"])
        self.assertEqual(
            "MATERIAL_SHADER_UNRESOLVED",
            document["diagnostics"][0]["code"],
        )

    def test_material_logical_name_may_differ_from_unity_object_name(self):
        avatar_mesh = {
            "name": "sample-avatar",
            "mainPrefabPath": "Assets/Npcs/sample.prefab",
            "slots": [
                {
                    "lods": {
                        "0": [
                            {
                                "meshName": "SampleBody",
                                "materialPaths": [
                                    {"paths": ["Assets/Npcs/Materials/SampleBody.mat"]}
                                ],
                            }
                        ]
                    }
                }
            ],
        }
        material = {
            "m_Name": "ReusedInternalName",
            "m_SavedProperties": {"m_TexEnvs": {}, "m_Floats": {}, "m_Colors": {}},
        }

        document, _geometry = build_static_avatar_mesh_document(
            avatar_mesh,
            {"samplebody": triangle("SampleBody")},
            material_payloads={"samplebody": material},
        )

        self.assertEqual("ReusedInternalName", document["materials"][0]["name"])
        self.assertEqual(
            document["materials"][0]["id"],
            document["meshes"][0]["primitives"][0]["materialId"],
        )

    def test_attaches_flat_bind_skeleton_without_changing_mesh_contract(self):
        avatar_mesh = {
            "name": "sample-avatar",
            "mainPrefabPath": "Assets/Npcs/sample.prefab",
            "slots": [
                {
                    "lods": {
                        "0": [
                            {
                                "meshName": "SampleBody",
                                "meshPathHash": 101,
                                "rootBoneName": "Bone",
                                "isRendererDisabled": False,
                            }
                        ]
                    }
                }
            ],
        }
        mesh = triangle("SampleBody")
        mesh.update(
            {
                "m_Skin": [
                    {"weight": [1, 0, 0, 0], "boneIndex": [0, 0, 0, 0]}
                    for _ in range(3)
                ],
                "m_BoneNameHashes": [500],
                "m_BindPose": [
                    {
                        **{
                            f"M{row}{column}": 1.0 if row == column else 0.0
                            for row in range(4)
                            for column in range(4)
                        }
                    }
                ],
            }
        )
        document, geometry = build_static_avatar_mesh_document(
            avatar_mesh,
            {"samplebody": mesh},
            avatar={
                "m_TOS": {"100": "Root", "500": "Root/Bone"},
                "m_Avatar": {
                    "m_AvatarSkeleton": {
                        "m_Node": [
                            {"m_ParentId": -1, "m_AxesId": -1},
                            {"m_ParentId": 0, "m_AxesId": -1},
                        ],
                        "m_ID": [100, 500],
                    },
                    "m_DefaultPose": {
                        "m_X": [
                            {
                                "t": {"X": 0, "Y": 0, "Z": 0},
                                "q": {"X": 0, "Y": 0, "Z": 0, "W": 1},
                                "s": {"X": 1, "Y": 1, "Z": 1},
                            },
                            {
                                "t": {"X": 0, "Y": 1, "Z": 0},
                                "q": {"X": 0, "Y": 0, "Z": 0, "W": 1},
                                "s": {"X": 1, "Y": 1, "Z": 1},
                            },
                        ]
                    },
                },
            },
        )

        self.assertEqual([], validate_model_document(document))
        self.assertGreater(len(geometry), 0)
        self.assertEqual(1, len(document["skeletons"]))
        self.assertEqual(1, len(document["skins"]))
        mesh_node = next(node for node in document["nodes"] if node.get("meshId"))
        self.assertEqual(document["skins"][0]["id"], mesh_node["skinId"])
        root_bone, weighted_bone = document["skeletons"][0]["bones"]
        self.assertEqual(root_bone["id"], weighted_bone["parentId"])
        self.assertEqual([0.0, 1.0, 0.0], weighted_bone["transform"]["translation"])


if __name__ == "__main__":
    unittest.main()
