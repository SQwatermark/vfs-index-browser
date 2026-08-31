import unittest

from manifest_asset_service import ManifestAssetResolutionError
from manifest_request_resolver_service import (
    ManifestRequestResolutionError,
    ManifestRequestResolverService,
)


class FakeAssets:
    def __init__(self):
        self.calls = []

    def resolve(self, manifest_id, asset_index):
        self.calls.append(("asset", manifest_id, asset_index))
        return ("asset", manifest_id, asset_index)

    def resolve_model(self, manifest_id, asset_index):
        self.calls.append(("model", manifest_id, asset_index))
        return ("model", manifest_id, asset_index)

    def resolve_many(self, manifest_id, indexes):
        self.calls.append(("many", manifest_id, list(indexes)))
        return [(manifest_id, index) for index in indexes]


class ManifestRequestResolverServiceTests(unittest.TestCase):
    def setUp(self):
        self.assets = FakeAssets()
        self.service = ManifestRequestResolverService(self.assets)

    def test_resolves_asset_and_model_references(self):
        query = {"manifestId": ["12"], "assetIndex": ["34"]}

        asset = self.service.resolve_asset(query)
        model = self.service.resolve_model(query)

        self.assertEqual(("asset", 12, 34), asset)
        self.assertEqual(("model", 12, 34), model)

    def test_optional_animation_distinguishes_absent_and_present(self):
        self.assertIsNone(
            self.service.resolve_optional_animation(
                {"manifestId": ["12"], "assetIndex": ["34"]}
            )
        )

        result = self.service.resolve_optional_animation(
            {"manifestId": ["12"], "animationAssetIndex": ["56"]}
        )

        self.assertEqual(("asset", 12, 56), result)

    def test_batch_deduplicates_and_sorts_before_resolving(self):
        result = self.service.resolve_animations(
            {
                "manifestId": ["12"],
                "animationAssetIndex": ["22", "11,22"],
            },
            maximum=10,
        )

        self.assertEqual([(12, 11), (12, 22)], result)
        self.assertEqual(("many", 12, [11, 22]), self.assets.calls[-1])

    def test_maps_query_errors_to_400(self):
        with self.assertRaises(ManifestRequestResolutionError) as raised:
            self.service.resolve_asset({"manifestId": ["bad"], "assetIndex": ["1"]})

        self.assertEqual(400, raised.exception.status)

    def test_preserves_asset_service_status(self):
        self.assets.resolve_model = lambda *_args: (_ for _ in ()).throw(
            ManifestAssetResolutionError(404, "model missing")
        )

        with self.assertRaisesRegex(
            ManifestRequestResolutionError,
            "model missing",
        ) as raised:
            self.service.resolve_model(
                {"manifestId": ["12"], "assetIndex": ["34"]}
            )

        self.assertEqual(404, raised.exception.status)


if __name__ == "__main__":
    unittest.main()
