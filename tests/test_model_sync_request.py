import unittest

from model_sync_request import ModelSyncRequest


class ModelSyncRequestTests(unittest.TestCase):
    def test_parses_defaults_and_existing_truthy_values(self):
        self.assertEqual(ModelSyncRequest(0, False, False), ModelSyncRequest.parse({}))
        self.assertEqual(
            ModelSyncRequest(2, True, True),
            ModelSyncRequest.parse(
                {"lod": ["2"], "download": ["yes"], "prepare": ["true"]}
            ),
        )

    def test_preserves_false_semantics_for_unknown_boolean_text(self):
        request = ModelSyncRequest.parse(
            {"download": ["on"], "prepare": ["TRUE"]}
        )

        self.assertFalse(request.download)
        self.assertFalse(request.prepare)

    def test_rejects_non_integer_lod(self):
        with self.assertRaises(ValueError):
            ModelSyncRequest.parse({"lod": ["bad"]})

    def test_builds_download_url_without_prepare_parameter(self):
        url = ModelSyncRequest.download_url(
            "/api/manifest-asset/model-blend",
            {
                "manifestId": ["12"],
                "assetIndex": ["34"],
                "animationAssetIndex": ["56", "78"],
                "prepare": ["1"],
            },
        )

        self.assertNotIn("prepare=", url)
        self.assertIn("animationAssetIndex=56", url)
        self.assertIn("animationAssetIndex=78", url)


if __name__ == "__main__":
    unittest.main()
