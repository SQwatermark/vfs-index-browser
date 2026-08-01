import json
import unittest
from copy import deepcopy
from pathlib import Path

from jsonschema import Draft202012Validator

from experiments.material_binding_resolver import (
    ShaderSource,
    resolve_material_bindings,
)
from experiments.material_semantic_ir.character_npr_mapping import (
    CharacterNprMappingError,
    VariantIdentity,
    build_blender_parameter_plan,
    build_character_npr_silk_ir,
)
from experiments.material_semantic_ir.validate_ir import (
    MaterialSemanticIrError,
    validate_references,
)


ROOT = Path(__file__).resolve().parents[2]
BINDING_EXAMPLES = ROOT / "experiments" / "material_binding_resolver" / "examples"
SCHEMA_PATH = ROOT / "experiments" / "material_semantic_ir" / "material-semantic-ir.schema.json"


def load_json(name):
    return json.loads((BINDING_EXAMPLES / name).read_text(encoding="utf-8"))


class CharacterNprSemanticMappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shader_text = (
            BINDING_EXAMPLES / "character-npr-subset.shader"
        ).read_text(encoding="utf-8")
        cls.material = load_json("stockings-material.json")
        cls.texture_metadata = load_json("texture-metadata.json")
        cls.texture_rules = load_json("texture-rules.json")
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    def resolve(self, material=None):
        return resolve_material_bindings(
            self.shader_text,
            ShaderSource(
                archive_version="1.4.4",
                uri="archive://AllShader_1.4.4/characternpr.shader",
            ),
            self.material if material is None else material,
            texture_metadata=self.texture_metadata,
            texture_rules=self.texture_rules,
        )

    def variant(self, *, keywords=None):
        return VariantIdentity(
            pass_name="Sub0_Pass0",
            blob=391,
            hlsl_uri="shader-archive://1.4.4/characternpr/Sub0_Pass0_Fragment_b391.hlsl",
            material_keywords=tuple(
                keywords
                if keywords is not None
                else {
                    "_METALLICSPECGLOSSMAP",
                    "_NORMALMAP",
                    "_SILK_STOCKINGS",
                }
            ),
        )

    def build_ir(self, material=None, *, keywords=None):
        return build_character_npr_silk_ir(
            self.resolve(material),
            material_id="sample:pelica:stockings",
            variant=self.variant(keywords=keywords),
        )

    def test_generated_ir_matches_schema_and_reference_rules(self):
        ir = self.build_ir()
        Draft202012Validator.check_schema(self.schema)
        errors = sorted(
            Draft202012Validator(self.schema).iter_errors(ir),
            key=lambda error: list(error.absolute_path),
        )
        self.assertEqual([], [error.message for error in errors])
        validate_references(ir)

        resolver_names = {item["name"] for item in self.resolve()["bindings"]}
        ir_names = {
            item["sourceName"]
            for item in ir["bindings"]
            if item["kind"] in {"material", "texture"}
        }
        self.assertEqual(resolver_names, ir_names)

    def test_texture_uv_color_space_and_sampler_survive_into_ir(self):
        ir = self.build_ir()
        base = next(
            item for item in ir["bindings"] if item["sourceName"] == "_BaseMap"
        )
        self.assertEqual("texture:base", base["resourceId"])
        self.assertEqual(
            {
                "set": "uv0",
                "scale": [2.0, 1.0],
                "offset": [0.25, 0.0],
            },
            base["textureBinding"]["uv"],
        )
        self.assertEqual("srgb", base["textureBinding"]["colorSpace"])
        self.assertEqual("shaderRule", base["textureBinding"]["sampler"]["source"])
        self.assertEqual(
            "mirror",
            base["textureBinding"]["sampler"]["effective"]["wrapU"],
        )

    def test_inactive_advanced_mode_never_samples_mask(self):
        ir = self.build_ir()
        domain = next(
            node
            for node in ir["graph"]["nodes"]
            if node["op"] == "endfield.silkStockings.materialState.v1"
        )
        self.assertFalse(domain["parameters"]["advanced"])
        self.assertNotIn("mask", domain["inputs"])
        self.assertNotIn(
            "sample:silkMask", {node["id"] for node in ir["graph"]["nodes"]}
        )

        plan = build_blender_parameter_plan(ir)
        mask = plan["nodeGroups"][0]["inputs"]["Silk Mask"]
        self.assertEqual("disabled", mask["kind"])
        self.assertEqual("_SilkStockingsAdvance == 0", mask["reason"])

    def test_active_advanced_mode_maps_mask_texture(self):
        material = deepcopy(self.material)
        material["floats"]["_SilkStockingsAdvance"] = 1.0
        ir = self.build_ir(material)
        domain = next(
            node
            for node in ir["graph"]["nodes"]
            if node["op"] == "endfield.silkStockings.materialState.v1"
        )
        self.assertTrue(domain["parameters"]["advanced"])
        self.assertEqual(
            {"node": "sample:silkMask", "output": "rgba"},
            domain["inputs"]["mask"],
        )

        plan = build_blender_parameter_plan(ir)
        mask = plan["nodeGroups"][0]["inputs"]["Silk Mask"]
        self.assertEqual("texture", mask["kind"])
        self.assertEqual("texture:silk-mask", mask["resource"]["id"])
        self.assertEqual("uv1", mask["uv"]["set"])

    def test_blender_plan_maps_values_defaults_and_runtime_inputs(self):
        plan = build_blender_parameter_plan(self.build_ir())
        group = plan["nodeGroups"][0]
        self.assertEqual("EF_SilkStockings_MaterialState_v1", group["name"])
        self.assertEqual(
            [1.0, 1.0, 1.0, 1.0],
            group["inputs"]["Dry Color"]["value"],
        )
        self.assertEqual(
            "shaderDefault",
            group["inputs"]["Dry Color"]["valueSource"],
        )
        self.assertEqual(0.82, group["inputs"]["Maximum Affect"]["value"])
        self.assertEqual(
            "materialInstance",
            group["inputs"]["Maximum Affect"]["valueSource"],
        )
        self.assertEqual("runtimeInput", group["inputs"]["Wetness"]["kind"])
        self.assertEqual(
            "material:BaseColor",
            group["inputs"]["Base Color"]["sourceBinding"],
        )
        self.assertNotIn("Rain Wet Mask Scale", group["inputs"])
        self.assertNotIn("Wet Albedo Mode", group["inputs"])
        self.assertIn(
            "BLENDER_SILK_WETNESS_PARAMETERS_NOT_LOWERED",
            {item["code"] for item in plan["diagnostics"]},
        )

    def test_plan_inputs_exactly_cover_semantic_graph_binding_dependencies(self):
        ir = self.build_ir()
        plan = build_blender_parameter_plan(ir)
        graph_binding_ids = {
            node["parameters"]["bindingId"]
            for node in ir["graph"]["nodes"]
            if "bindingId" in node["parameters"]
        }
        plan_binding_ids = {
            value["sourceBinding"]
            for value in plan["nodeGroups"][0]["inputs"].values()
            if value["kind"] != "disabled"
        }
        self.assertEqual(graph_binding_ids, plan_binding_ids)

    def test_packed_map_and_smoothness_plan_inputs_are_mutually_exclusive(self):
        packed_plan = build_blender_parameter_plan(self.build_ir())
        packed_inputs = packed_plan["nodeGroups"][0]["inputs"]
        self.assertIn("Packed Material Map", packed_inputs)
        self.assertNotIn("Smoothness", packed_inputs)

        material = deepcopy(self.material)
        material["floats"]["_UseMetallicGlossMap"] = 0.0
        scalar_ir = self.build_ir(
            material,
            keywords={"_NORMALMAP", "_SILK_STOCKINGS"},
        )
        scalar_plan = build_blender_parameter_plan(scalar_ir)
        scalar_inputs = scalar_plan["nodeGroups"][0]["inputs"]
        self.assertNotIn("Packed Material Map", scalar_inputs)
        self.assertIn("Smoothness", scalar_inputs)
        self.assertEqual(
            "material:Smoothness",
            scalar_inputs["Smoothness"]["sourceBinding"],
        )
        self.assertNotIn(
            "sample:metallicGloss",
            {node["id"] for node in scalar_ir["graph"]["nodes"]},
        )

    def test_variant_keyword_disagreement_is_rejected(self):
        with self.assertRaisesRegex(CharacterNprMappingError, "_SILK_STOCKINGS"):
            self.build_ir(keywords={"_METALLICSPECGLOSSMAP", "_NORMALMAP"})

    def test_resolution_errors_are_rejected(self):
        material = deepcopy(self.material)
        material["shader"] = "HGRP/Wrong"
        with self.assertRaisesRegex(CharacterNprMappingError, "error diagnostics"):
            self.build_ir(material)

    def test_diagnostic_unknown_output_is_rejected(self):
        ir = self.build_ir()
        ir["diagnostics"][0]["affectedOutputs"].append("missing")
        with self.assertRaisesRegex(MaterialSemanticIrError, "unknown outputs"):
            validate_references(ir)


if __name__ == "__main__":
    unittest.main()
