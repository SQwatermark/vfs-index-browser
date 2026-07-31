import unittest

from tools.inspect_npc_avatar_mesh import parse_avatar_mesh


SAMPLE = """\
MonoBehaviour Base
\tstring m_Name = "data_npc_avatarmesh_sample"
\tstring mainPrefabPath = "Assets/Beyond/Designer/PostModels/Npcs/sample.prefab"
\tSInt64 mainPrefabPathHash = 100
\tNPCAvatarLodMeshAssets avatarSlotMeshDatas
\t\tArray Array
\t\t\tint size = 1
\t\t\t\t[0]
\t\t\t\tNPCAvatarLodMeshAssets data
\t\t\t\t\tstring name = "sample"
\t\t\t\t\tint partType = 2
\t\t\t\t\tSubMeshInfo partSubMeshsLOD0
\t\t\t\t\t\tArray Array
\t\t\t\t\t\t\tint size = 1
\t\t\t\t\t\t\t\t[0]
\t\t\t\t\t\t\t\tSubMeshInfo data
\t\t\t\t\t\t\t\t\tSInt64 meshPathHash = 101
\t\t\t\t\t\t\t\t\tstring meshName = "sample_body_lod0"
\t\t\t\t\t\t\t\t\tvector materialPathHashes
\t\t\t\t\t\t\t\t\t\tArray Array
\t\t\t\t\t\t\t\t\t\t\tint size = 1
\t\t\t\t\t\t\t\t\t\t\t\t[0]
\t\t\t\t\t\t\t\t\t\t\t\tSInt64 data = 201
\t\t\t\t\t\t\t\t\tvector backupMaterialPathHashes
\t\t\t\t\t\t\t\t\t\tArray Array
\t\t\t\t\t\t\t\t\t\t\tint size = 0
\t\t\t\t\t\t\t\t\tUInt8 isActive = 1
\t\t\t\t\t\t\t\t\tstring rootBoneName = "Pelvis"
\t\t\t\t\t\t\t\t\tint rootBoneID = 3
\t\t\t\t\t\t\t\t\tUInt8 realTimeShadowCaster = 1
\t\t\t\t\t\t\t\t\tUInt8 isRendererDisabled = 0
\t\t\t\t\t\t\t\t\tint platformAvailability = 7
"""


class NpcAvatarMeshParserTests(unittest.TestCase):
    def test_parses_slot_lod_and_submesh_references(self):
        document = parse_avatar_mesh(SAMPLE)
        self.assertEqual("data_npc_avatarmesh_sample", document["name"])
        self.assertEqual(100, document["mainPrefabPathHash"])
        self.assertEqual(1, len(document["slots"]))
        mesh = document["slots"][0]["lods"]["0"][0]
        self.assertEqual("sample_body_lod0", mesh["meshName"])
        self.assertEqual([201], mesh["materialPathHashes"])
        self.assertEqual("Pelvis", mesh["rootBoneName"])
        self.assertTrue(mesh["isActive"])

    def test_rejects_declared_lod_size_mismatch(self):
        with self.assertRaisesRegex(ValueError, "LOD 声明 2 项"):
            parse_avatar_mesh(SAMPLE.replace("int size = 1\n\t\t\t\t\t\t\t\t[0]", "int size = 2\n\t\t\t\t\t\t\t\t[0]"))


if __name__ == "__main__":
    unittest.main()
