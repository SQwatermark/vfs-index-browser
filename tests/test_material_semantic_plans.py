import json
import tempfile
import unittest
from pathlib import Path

from material_semantic_plans import CHARACTER_NPR_PATH, build_blender_material_plans


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "experiments" / "material_binding_resolver" / "examples"


class MaterialSemanticPlansTests(unittest.TestCase):
    def test_builds_plan_without_mutating_model_document(self):
        source_material = json.loads(
            (EXAMPLES / "stockings-material.json").read_text(encoding="utf-8")
        )
        shader_text = (
            EXAMPLES / "character-npr-subset.shader"
        ).read_text(encoding="utf-8")
        shader_text += """
// Stage: Fragment
#ifdef SHADER_STAGE_FRAGMENT
#if defined(_METALLICSPECGLOSSMAP) && defined(_NORMALMAP) && defined(_SILK_STOCKINGS) && !defined(VFX_CHARACTER_DISSOLVE) && !defined(_ALPHABLEND_ON)
#include "characternpr/Sub0_Pass0_Fragment_b391.hlsl"
#endif
#endif
"""
        document = {
            "materials": [
                {
                    "id": "material:silk",
                    "sourceMaterial": source_material,
                    "previewPbr": {"silkStockings": {}},
                }
            ]
        }
        original = json.loads(json.dumps(document))

        with tempfile.TemporaryDirectory() as directory:
            shader_path = Path(directory) / CHARACTER_NPR_PATH
            shader_path.parent.mkdir(parents=True)
            shader_path.write_text(shader_text, encoding="utf-8")
            plans = build_blender_material_plans(document, Path(directory))

        plan = plans["material:silk"]
        self.assertEqual("BlenderNodeParameterPlan", plan["format"])
        self.assertEqual(391, plan["source"]["variant"]["blob"])
        self.assertEqual(original, document)


if __name__ == "__main__":
    unittest.main()
