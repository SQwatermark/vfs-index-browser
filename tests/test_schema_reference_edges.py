from __future__ import annotations

import unittest

from tools.validate_schema_reference_edges import validate_reference_graph


class SchemaReferenceEdgeTests(unittest.TestCase):
    def test_validates_source_identity_field_owner_and_evidence(self) -> None:
        summary = validate_reference_graph(ownership(), graph())
        self.assertEqual(summary, {"nodeCount": 1, "edgeCount": 1, "sourceTypeCount": 1})

    def test_rejects_field_name_inference_without_real_schema_field(self) -> None:
        value = graph()
        value["edges"][0]["source"]["field"] = "looksLikeSkillId"
        with self.assertRaisesRegex(ValueError, "has no field"):
            validate_reference_graph(ownership(), value)

    def test_rejects_edge_without_explicit_evidence(self) -> None:
        value = graph()
        value["edges"][0]["evidence"] = []
        with self.assertRaisesRegex(ValueError, "explicit evidence"):
            validate_reference_graph(ownership(), value)


def ownership() -> dict:
    return {
        "format": "SparkBufferSchemaOwnership",
        "revision": "1.4.4@test",
        "types": [
            {
                "typeHashHex": "0x00000001",
                "kind": "bean",
                "name": "WeaponData",
                "owners": ["weapon"],
                "definition": {"fields": [{"name": "skillId", "fieldType": "STRING"}]},
            }
        ],
    }


def graph() -> dict:
    return {
        "format": "GameDataReferenceGraph",
        "version": 1,
        "revision": "1.4.4@test",
        "nodes": [{"id": "memorypack:Beyond.Gameplay.SkillData"}],
        "edges": [
            {
                "source": {"typeHashHex": "0x00000001", "field": "skillId"},
                "target": "memorypack:Beyond.Gameplay.SkillData",
                "owners": ["weapon"],
                "evidence": [
                    {"basis": "native-code", "reference": "SkillUtil.CreateSkillData"}
                ],
            }
        ],
    }


if __name__ == "__main__":
    unittest.main()
