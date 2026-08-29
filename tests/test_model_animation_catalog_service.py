import unittest

from model_animation_catalog_service import ModelAnimationCatalogService


class FakeIndex:
    def __init__(self):
        self.calls = []

    def search_animation_assets(self, query, *, page, page_size):
        self.calls.append((query, page, page_size))
        return {
            "page": page,
            "pageSize": page_size,
            "total": 1,
            "files": [{"assetIndex": 22, "path": "assets/idle.animation"}],
        }


class ModelAnimationCatalogServiceTests(unittest.TestCase):
    def service(self):
        return ModelAnimationCatalogService(
            lambda path: f"default:{path}",
            animation_version=18,
            blend_version=12,
        )

    def test_searches_ordinary_model_with_defaults_and_stable_urls(self):
        index = FakeIndex()
        asset = {"asset_index": 11, "path": "assets/hero.prefab"}

        result = self.service().search(
            {
                "manifestId": ["5"],
                "page": ["2"],
                "pageSize": ["25"],
            },
            (index, asset, {}, None),
        )

        self.assertEqual([("default:assets/hero.prefab", 2, 25)], index.calls)
        self.assertEqual("default:assets/hero.prefab", result["defaultQuery"])
        self.assertEqual("modelAnimationCandidates", result["kind"])
        candidate = result["files"][0]
        self.assertEqual(
            "/api/manifest-asset/model-animation?manifestId=5&assetIndex=11"
            "&animationAssetIndex=22&v=18",
            candidate["previewUrl"],
        )
        self.assertEqual(
            "/api/manifest-asset/model-blend?manifestId=5&assetIndex=11"
            "&animationAssetIndex=22&v=12",
            candidate["blendUrl"],
        )
        self.assertNotIn("lod=", candidate["previewUrl"])

    def test_searches_avatar_with_trimmed_hint_query_and_lod(self):
        index = FakeIndex()
        asset = {
            "asset_index": 33,
            "path": (
                "assets/beyond/dynamicassets/gameplay/npc/avatarmesh/actor/"
                "data_npc_avatarmesh_sample.asset"
            ),
        }

        result = self.service().search(
            {
                "manifestId": ["7"],
                "lod": ["2"],
                "queryHint": ["  idle girl  "],
                "q": ["  run girl  "],
            },
            (index, asset, {}, None),
        )

        self.assertEqual([("run girl", 1, 50)], index.calls)
        self.assertEqual("idle girl", result["defaultQuery"])
        self.assertIn("&lod=2&animationAssetIndex=22", result["files"][0]["previewUrl"])

    def test_rejects_invalid_avatar_lod_before_search(self):
        index = FakeIndex()
        asset = {
            "asset_index": 33,
            "path": (
                "assets/beyond/dynamicassets/gameplay/npc/avatarmesh/actor/"
                "data_npc_avatarmesh_sample.asset"
            ),
        }

        with self.assertRaisesRegex(ValueError, "invalid AvatarMesh LOD"):
            self.service().search(
                {"manifestId": ["7"], "lod": ["4"]},
                (index, asset, {}, None),
            )

        self.assertEqual([], index.calls)


if __name__ == "__main__":
    unittest.main()
