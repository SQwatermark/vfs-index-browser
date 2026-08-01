import json
import unittest
from copy import deepcopy
from pathlib import Path

from jsonschema import Draft202012Validator

from experiments.material_semantic_ir.validate_ir import (
    MaterialSemanticIrError,
    validate_references,
)


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "experiments" / "material_semantic_ir"


class MaterialSemanticIrSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(
            (EXPERIMENT / "material-semantic-ir.schema.json").read_text(
                encoding="utf-8"
            )
        )
        cls.example = json.loads(
            (EXPERIMENT / "examples" / "silk-stockings-b391.json").read_text(
                encoding="utf-8"
            )
        )

    def test_silk_stockings_example_matches_schema(self):
        Draft202012Validator.check_schema(self.schema)
        errors = sorted(
            Draft202012Validator(self.schema).iter_errors(self.example),
            key=lambda error: list(error.absolute_path),
        )
        self.assertEqual([], [error.message for error in errors])

    def test_graph_references_and_acyclicity(self):
        validate_references(self.example)

    def test_unknown_node_reference_is_rejected(self):
        document = deepcopy(self.example)
        document["graph"]["outputs"]["surface"]["node"] = "missing"
        with self.assertRaisesRegex(MaterialSemanticIrError, "unknown node"):
            validate_references(document)

    def test_cycle_is_rejected(self):
        document = deepcopy(self.example)
        first_node = document["graph"]["nodes"][0]
        last_node = document["graph"]["nodes"][-1]
        first_node["inputs"]["cycle"] = {
            "node": last_node["id"],
            "output": next(iter(last_node["outputs"])),
        }
        with self.assertRaisesRegex(MaterialSemanticIrError, "contains a cycle"):
            validate_references(document)


if __name__ == "__main__":
    unittest.main()
