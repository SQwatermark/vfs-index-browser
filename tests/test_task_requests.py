import unittest

from task_requests import (
    ModelAnimationTaskRequest,
    ModelBlendTaskRequest,
    ModelTaskRequest,
    TaskInputError,
)


class TaskRequestTests(unittest.TestCase):
    def test_parses_model_request_with_optional_animation(self):
        request = ModelTaskRequest.parse({
            "manifestId": "3",
            "assetIndex": 11,
            "lod": "2",
            "animationAssetIndex": "21",
        })

        self.assertEqual((3, 11, 2, 21), (
            request.manifest_id,
            request.asset_index,
            request.lod,
            request.animation_asset_index,
        ))
        self.assertIsNone(ModelTaskRequest.parse({
            "manifestId": 3,
            "assetIndex": 11,
        }).animation_asset_index)

    def test_parses_blend_request_and_preserves_order(self):
        request = ModelBlendTaskRequest.parse(
            {
                "manifestId": 3,
                "assetIndex": 11,
                "animationAssetIndexes": [22, "21"],
            },
            max_animation_count=8,
        )

        self.assertEqual((22, 21), request.animation_asset_indexes)

    def test_parses_single_animation_request(self):
        request = ModelAnimationTaskRequest.parse({
            "manifestId": 3,
            "assetIndex": 11,
            "animationAssetIndex": 21,
            "lod": 1,
        })

        self.assertEqual(21, request.animation_asset_index)
        self.assertEqual(1, request.lod)

    def test_rejects_missing_negative_or_out_of_range_values(self):
        invalid = (
            lambda: ModelTaskRequest.parse({"manifestId": 3}),
            lambda: ModelTaskRequest.parse({"manifestId": 3, "assetIndex": 11, "lod": 4}),
            lambda: ModelBlendTaskRequest.parse(
                {"manifestId": 3, "assetIndex": 11, "animationAssetIndexes": [1, 2]},
                max_animation_count=1,
            ),
            lambda: ModelAnimationTaskRequest.parse({
                "manifestId": 3,
                "assetIndex": 11,
                "animationAssetIndex": -1,
            }),
        )
        for operation in invalid:
            with self.assertRaises(TaskInputError):
                operation()
