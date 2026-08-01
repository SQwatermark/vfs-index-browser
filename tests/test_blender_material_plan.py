import unittest

from blender_material_plan import plan_texture_id, plan_value, silk_plan_inputs


class BlenderMaterialPlanTests(unittest.TestCase):
    def test_reads_valid_silk_plan_inputs(self):
        plan = {
            "format": "BlenderNodeParameterPlan",
            "version": "0.1.0",
            "nodeGroups": [
                {
                    "name": "EF_SilkStockings_MaterialState_v1",
                    "inputs": {
                        "Maximum Affect": {"kind": "value", "value": 0.9},
                        "Silk Mask": {
                            "kind": "texture",
                            "resource": {"kind": "resource", "id": "texture:mask"},
                        },
                    },
                }
            ],
        }

        inputs = silk_plan_inputs(plan)

        self.assertEqual(0.9, plan_value(inputs, "Maximum Affect"))
        self.assertEqual("texture:mask", plan_texture_id(inputs, "Silk Mask"))

    def test_rejects_unknown_plan_version(self):
        self.assertIsNone(
            silk_plan_inputs(
                {
                    "format": "BlenderNodeParameterPlan",
                    "version": "9.0.0",
                    "nodeGroups": [],
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
