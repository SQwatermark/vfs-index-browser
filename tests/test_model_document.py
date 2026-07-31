import unittest

from model_assembly import create_avatar_mesh_assembly, create_prefab_assembly
from model_document import create_model_document, validate_model_document


SOURCE = {"logicalPath": "assets/characters/sample.prefab", "bundle": "sample.ab"}


class ModelDocumentTests(unittest.TestCase):
    def test_empty_document_is_valid(self):
        document = create_model_document(
            "asset:sample",
            "sample",
            SOURCE,
            assembly=create_prefab_assembly(SOURCE),
        )
        self.assertEqual("2.0.0", document["version"])
        self.assertEqual([], validate_model_document(document))

    def test_schema_rejects_assembly_metadata_in_source_locator(self):
        document = create_model_document(
            "asset:sample",
            "sample",
            {**SOURCE, "kind": "prefab"},
        )
        errors = validate_model_document(document)
        self.assertTrue(
            any(
                item["code"] == "SCHEMA_VALIDATION_ERROR"
                and "kind" in item["message"]
                for item in errors
            )
        )

    def test_reports_assembly_part_without_a_document_node(self):
        part = {
            "id": "part:body",
            "nodeId": "node:missing",
            "slotIndex": 0,
            "meshIndex": 0,
            "lod": 0,
            "active": True,
            "meshName": "Body",
            "meshPathHash": 1,
            "meshPaths": ["assets/body.mesh"],
            "materialPaths": ["assets/body.mat"],
        }
        document = create_model_document(
            "asset:sample",
            "sample",
            SOURCE,
            assembly=create_avatar_mesh_assembly(
                entry_source=SOURCE,
                lod=0,
                parts=[part],
            ),
        )

        codes = {item["code"] for item in validate_model_document(document)}
        self.assertIn("UNRESOLVED_ASSEMBLY_NODE", codes)

    def test_valid_geometry_references_and_buffer_range(self):
        document = create_model_document("asset:sample", "sample", SOURCE, root_node_ids=["node:root"])
        document["buffers"].append({"id": "buffer:geometry", "uri": "geometry.bin", "byteLength": 36})
        document["bufferViews"].append(
            {"id": "view:positions", "bufferId": "buffer:geometry", "byteOffset": 0, "byteLength": 36}
        )
        document["accessors"].append(
            {"id": "accessor:positions", "bufferViewId": "view:positions", "componentType": "f32", "type": "vec3", "count": 3}
        )
        document["meshes"].append(
            {"id": "mesh:root", "name": "root", "primitives": [{"topology": "triangles", "attributes": {"POSITION": "accessor:positions"}}]}
        )
        document["nodes"].append(
            {"id": "node:root", "name": "root", "active": True, "children": [], "transform": {}, "meshId": "mesh:root"}
        )
        self.assertEqual([], validate_model_document(document))

    def test_reports_broken_tree_and_missing_reference(self):
        document = create_model_document("asset:sample", "sample", SOURCE, root_node_ids=["node:root"])
        document["nodes"].extend([
            {"id": "node:root", "name": "root", "active": True, "children": ["node:child"], "transform": {}, "meshId": "mesh:missing"},
            {"id": "node:child", "name": "child", "active": True, "children": [], "transform": {}, "parentId": "node:other"},
        ])
        codes = {item["code"] for item in validate_model_document(document)}
        self.assertIn("UNRESOLVED_REFERENCE", codes)
        self.assertIn("UNRESOLVED_PARENT", codes)
        self.assertIn("INCONSISTENT_NODE_TREE", codes)

    def test_reports_accessor_past_buffer_view(self):
        document = create_model_document("asset:sample", "sample", SOURCE)
        document["buffers"].append({"id": "buffer:geometry", "uri": "geometry.bin", "byteLength": 12})
        document["bufferViews"].append(
            {"id": "view:positions", "bufferId": "buffer:geometry", "byteOffset": 0, "byteLength": 12}
        )
        document["accessors"].append(
            {"id": "accessor:positions", "bufferViewId": "view:positions", "componentType": "f32", "type": "vec3", "count": 2}
        )
        self.assertEqual(["ACCESSOR_OUT_OF_RANGE"], [item["code"] for item in validate_model_document(document)])


if __name__ == "__main__":
    unittest.main()
