import unittest

from model_assembly import (
    create_avatar_mesh_assembly,
    create_prefab_assembly,
    validate_model_assembly,
)


SOURCE = {"logicalPath": "assets/characters/sample.prefab"}


def avatar_part(part_id="part:body", node_id="node:body"):
    return {
        "id": part_id,
        "nodeId": node_id,
        "slotIndex": 0,
        "meshIndex": 0,
        "lod": 0,
        "active": True,
        "meshName": "Body",
        "meshPathHash": 123,
        "meshPaths": ["assets/characters/body.mesh"],
        "materialPaths": ["assets/characters/body.mat"],
    }


class ModelAssemblyTests(unittest.TestCase):
    def test_prefab_assembly_uses_unity_reference_graph(self):
        assembly = create_prefab_assembly(SOURCE)

        self.assertEqual("prefab", assembly["kind"])
        self.assertEqual("unity-y-up", assembly["sourceBasis"])
        self.assertEqual([], assembly["parts"])
        self.assertEqual([], validate_model_assembly(assembly))

    def test_avatar_mesh_assembly_records_resolved_parts(self):
        assembly = create_avatar_mesh_assembly(
            entry_source=SOURCE,
            lod=0,
            parts=[avatar_part()],
        )

        self.assertEqual("avatarMesh", assembly["kind"])
        self.assertEqual("avatar-mesh-z-up", assembly["sourceBasis"])
        self.assertEqual("unity-y-up", assembly["documentBasis"])
        self.assertEqual([], validate_model_assembly(assembly))

    def test_avatar_mesh_rejects_duplicate_nodes_and_invalid_lod(self):
        assembly = create_avatar_mesh_assembly(
            entry_source=SOURCE,
            lod=4,
            parts=[
                avatar_part("part:body", "node:body"),
                avatar_part("part:hair", "node:body"),
            ],
        )

        codes = {item["code"] for item in validate_model_assembly(assembly)}
        self.assertIn("DUPLICATE_ASSEMBLY_NODE", codes)
        self.assertIn("INVALID_ASSEMBLY_LOD", codes)
        self.assertIn("INCONSISTENT_ASSEMBLY_LOD", codes)


if __name__ == "__main__":
    unittest.main()
