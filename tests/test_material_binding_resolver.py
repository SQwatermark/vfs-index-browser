import json
import unittest
from copy import deepcopy
from pathlib import Path

from experiments.material_binding_resolver import (
    MaterialBindingError,
    ShaderLabParseError,
    ShaderSource,
    parse_shaderlab_properties,
    resolve_material_bindings,
)


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "experiments" / "material_binding_resolver" / "examples"


def load_json(name):
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


class MaterialBindingResolverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shader_text = (EXAMPLES / "character-npr-subset.shader").read_text(
            encoding="utf-8"
        )
        cls.material = load_json("stockings-material.json")
        cls.texture_metadata = load_json("texture-metadata.json")
        cls.texture_rules = load_json("texture-rules.json")

    def resolve(self, material=None, **kwargs):
        return resolve_material_bindings(
            self.shader_text,
            ShaderSource(
                archive_version="1.4.4",
                uri="archive://AllShader_1.4.4/characternpr.shader",
            ),
            material or self.material,
            texture_metadata=kwargs.get("texture_metadata", self.texture_metadata),
            texture_rules=kwargs.get("texture_rules", self.texture_rules),
        )

    def test_parses_supported_properties_defaults_and_attributes(self):
        shader = parse_shaderlab_properties(self.shader_text)
        properties = {prop["name"]: prop for prop in shader["properties"]}

        self.assertEqual("HGRP/CharacterNPR", shader["name"])
        self.assertEqual("color", properties["_BaseColor"]["kind"])
        self.assertEqual([1.0, 1.0, 1.0, 1.0], properties["_BaseColor"]["defaultValue"])
        self.assertEqual("texture2d", properties["_BaseMap"]["kind"])
        self.assertEqual("white", properties["_BaseMap"]["defaultValue"])
        self.assertEqual("_NORMALMAP", properties["_UseBumpMap"]["toggleKeyword"])
        self.assertEqual("integer", properties["_Mode"]["kind"])
        self.assertEqual("Range(0.5, 0.9)", properties["_SilkStockingsMaxAffect"]["declaredType"])

    def test_resolves_defaults_overrides_and_keeps_their_sources(self):
        result = self.resolve()
        bindings = {binding["name"]: binding for binding in result["bindings"]}

        self.assertEqual("1.4.4", result["shader"]["archiveVersion"])
        self.assertEqual(
            {"value": [1.0, 1.0, 1.0, 1.0], "source": "shaderDefault"},
            bindings["_SilkStockingsDryColor"]["effective"],
        )
        self.assertEqual(
            {"value": 0.82, "source": "materialInstance"},
            bindings["_SilkStockingsMaxAffect"]["effective"],
        )
        self.assertEqual(
            {"value": 1, "source": "materialInstance"},
            bindings["_Mode"]["effective"],
        )

    def test_mask_presence_does_not_override_advanced_mode_value(self):
        result = self.resolve()
        bindings = {binding["name"]: binding for binding in result["bindings"]}

        self.assertEqual("resource", bindings["_SilkStockingsMask"]["effective"]["kind"])
        self.assertEqual(
            {"value": 0.0, "source": "materialInstance"},
            bindings["_SilkStockingsAdvance"]["effective"],
        )

    def test_resolves_uv_color_space_and_sampler_precedence(self):
        result = self.resolve()
        bindings = {binding["name"]: binding for binding in result["bindings"]}
        base = bindings["_BaseMap"]
        mask = bindings["_SilkStockingsMask"]

        self.assertEqual(
            {
                "set": "uv0",
                "scale": [2.0, 1.0],
                "offset": [0.25, 0.0],
                "transformSource": "materialInstance",
            },
            base["uv"],
        )
        self.assertEqual("srgb", base["colorSpace"])
        self.assertEqual("shaderRule", base["sampling"]["effectiveSource"])
        self.assertEqual("mirror", base["sampling"]["effective"]["wrapU"])
        self.assertEqual("uv1", mask["uv"]["set"])
        self.assertEqual("textureAsset", mask["sampling"]["effectiveSource"])

    def test_preserves_undeclared_instance_properties_with_diagnostic(self):
        result = self.resolve()
        self.assertEqual(
            4.0,
            result["unmatchedInstanceProperties"]["floats"]["_LegacyStockingsValue"],
        )
        self.assertIn(
            ("MATERIAL_PROPERTY_UNDECLARED", "_LegacyStockingsValue"),
            {(item["code"], item.get("property")) for item in result["diagnostics"]},
        )

    def test_reports_shader_mismatch_without_discarding_bindings(self):
        material = deepcopy(self.material)
        material["shader"] = "HGRP/Other"
        result = self.resolve(material)

        self.assertTrue(result["bindings"])
        self.assertIn(
            "MATERIAL_SHADER_MISMATCH",
            {item["code"] for item in result["diagnostics"]},
        )

    def test_invalid_instance_value_falls_back_with_diagnostic(self):
        material = deepcopy(self.material)
        material["floats"]["_SilkStockingsMaxAffect"] = "high"
        result = self.resolve(material)
        binding = next(
            item for item in result["bindings"] if item["name"] == "_SilkStockingsMaxAffect"
        )

        self.assertEqual({"value": 0.9, "source": "shaderDefault"}, binding["effective"])
        self.assertIn(
            "INSTANCE_VALUE_TYPE_MISMATCH",
            {item["code"] for item in result["diagnostics"]},
        )

    def test_rejects_unknown_shaderlab_property_type(self):
        shader = 'Shader "Test" { Properties {\n_M ("Matrix", Matrix) = 0\n} }'
        with self.assertRaisesRegex(ShaderLabParseError, "unsupported property type"):
            parse_shaderlab_properties(shader)

    def test_rejects_unknown_sampler_fields(self):
        rules = deepcopy(self.texture_rules)
        rules["_BaseMap"]["sampler"]["guess"] = True
        with self.assertRaisesRegex(MaterialBindingError, "unknown fields"):
            self.resolve(texture_rules=rules)


if __name__ == "__main__":
    unittest.main()
