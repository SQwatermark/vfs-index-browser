import unittest

from shader_variants import (
    ShaderVariant,
    ShaderVariantSelectionError,
    active_material_keywords,
    fragment_variants,
    select_preview_fragment_variant,
)


SHADER = """
Shader "Test" {
    Properties {
        [Toggle(_FEATURE_A)] _UseFeatureA ("A", Float) = 0
        [Toggle(_FEATURE_B)] _UseFeatureB ("B", Float) = 0
    }
    // Stage: Fragment
    #ifdef SHADER_STAGE_FRAGMENT
    #if defined(_FEATURE_A) && !defined(_FEATURE_B) && defined(RUNTIME_SHADOW)
    #include "sample/Sub0_Pass0_Fragment_b1.hlsl"
    #elif defined(_FEATURE_A) && !defined(_FEATURE_B) && !defined(RUNTIME_SHADOW)
    #include "sample/Sub0_Pass0_Fragment_b2.hlsl"
    #endif
    #endif
    // Stage: Fragment
    #ifdef SHADER_STAGE_FRAGMENT
    #if !defined(_FEATURE_A) && !defined(_FEATURE_B)
    #include "sample/Sub0_Pass1_Fragment_b3.hlsl"
    #endif
    #endif
}
"""


class ShaderVariantTests(unittest.TestCase):
    def test_matches_material_keywords_and_preserves_runtime_choices(self):
        active, local = active_material_keywords(
            SHADER, {"_UseFeatureA": 1.0, "_UseFeatureB": 0.0}
        )
        self.assertEqual({"_FEATURE_A"}, active)
        variants = fragment_variants(SHADER, active, local)
        self.assertEqual(
            [
                ("sample/Sub0_Pass0_Fragment_b1.hlsl", ("RUNTIME_SHADOW",), ()),
                ("sample/Sub0_Pass0_Fragment_b2.hlsl", (), ("RUNTIME_SHADOW",)),
            ],
            [
                (
                    variant.include,
                    variant.enabled_runtime_keywords,
                    variant.disabled_runtime_keywords,
                )
                for variant in variants
            ],
        )

    def test_rejects_incompatible_local_keyword_branch(self):
        active, local = active_material_keywords(
            SHADER, {"_UseFeatureA": 0.0, "_UseFeatureB": 0.0}
        )
        self.assertEqual([], fragment_variants(SHADER, active, local))

    def test_selects_non_transient_preview_variant_and_parses_identity(self):
        variants = [
            ShaderVariant(
                "sample/Sub0_Pass0_Fragment_b604.hlsl",
                ("VFX_CHARACTER_DISSOLVE",),
                ("_ALPHABLEND_ON",),
            ),
            ShaderVariant(
                "sample/Sub0_Pass0_Fragment_b391.hlsl",
                (),
                ("VFX_CHARACTER_DISSOLVE", "_ALPHABLEND_ON"),
            ),
        ]

        selected = select_preview_fragment_variant(variants)

        self.assertEqual("Sub0_Pass0", selected.pass_name)
        self.assertEqual(391, selected.blob)

    def test_rejects_ambiguous_preview_variants(self):
        variants = [
            ShaderVariant("sample/Sub0_Pass0_Fragment_b1.hlsl", (), ()),
            ShaderVariant("sample/Sub0_Pass1_Fragment_b2.hlsl", (), ()),
        ]

        with self.assertRaises(ShaderVariantSelectionError):
            select_preview_fragment_variant(variants)


if __name__ == "__main__":
    unittest.main()
