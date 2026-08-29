import unittest

from manifest_asset_requests import (
    ManifestAssetReference,
    ManifestAssetRequestError,
    parse_animation_asset_indexes,
    parse_manifest_asset_reference,
    parse_manifest_id,
)


class ManifestAssetRequestTests(unittest.TestCase):
    def test_parses_default_and_named_asset_parameters(self):
        query = {
            "manifestId": ["12"],
            "assetIndex": ["34"],
            "animationAssetIndex": ["56"],
        }

        self.assertEqual(
            ManifestAssetReference(12, 34),
            parse_manifest_asset_reference(query),
        )
        self.assertEqual(
            ManifestAssetReference(12, 56),
            parse_manifest_asset_reference(query, asset_parameter="animationAssetIndex"),
        )

    def test_rejects_missing_or_invalid_resource_identity(self):
        for query in ({}, {"manifestId": ["x"], "assetIndex": ["2"]}):
            with self.subTest(query=query):
                with self.assertRaisesRegex(
                    ManifestAssetRequestError,
                    "Manifest 资源引用无效",
                ):
                    parse_manifest_asset_reference(query)

    def test_parses_manifest_id_without_requiring_an_asset(self):
        self.assertEqual(12, parse_manifest_id({"manifestId": ["12"]}))

    def test_animation_selection_is_stable_and_unique(self):
        self.assertEqual(
            (11, 22),
            parse_animation_asset_indexes(
                {"animationAssetIndex": ["22", "11,22"]},
                maximum=5,
            ),
        )

    def test_empty_animation_selection_is_optional(self):
        self.assertEqual((), parse_animation_asset_indexes({}, maximum=5))

    def test_rejects_invalid_or_oversized_animation_selection(self):
        cases = (
            ({"animationAssetIndex": ["x"]}, "animationAssetIndex is invalid"),
            (
                {"animationAssetIndex": ["1,2,3"]},
                "animation selection must contain 1 to 2 items",
            ),
            (
                {"animationAssetIndex": [","]},
                "animation selection must contain 1 to 2 items",
            ),
        )
        for query, message in cases:
            with self.subTest(query=query):
                with self.assertRaisesRegex(ManifestAssetRequestError, message):
                    parse_animation_asset_indexes(query, maximum=2)


if __name__ == "__main__":
    unittest.main()
