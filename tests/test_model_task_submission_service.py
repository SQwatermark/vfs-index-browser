import unittest

from model_task_submission_service import ModelTaskSubmissionService
from task_requests import TaskInputError


class FakeResolver:
    def __init__(self):
        self.calls = []

    def resolve_model(self, manifest_id, asset_index):
        self.calls.append(("model", manifest_id, asset_index))
        return f"model-{asset_index}"

    def resolve(self, manifest_id, asset_index):
        self.calls.append(("asset", manifest_id, asset_index))
        return f"asset-{asset_index}"

    def resolve_many(self, manifest_id, asset_indexes):
        self.calls.append(("many", manifest_id, asset_indexes))
        return tuple(f"asset-{index}" for index in asset_indexes)


class FakeOperations:
    def __init__(self):
        self.calls = []

    def start_model(self, manifest_id, model, animation, lod):
        self.calls.append(("model", manifest_id, model, animation, lod))
        return {"taskId": "model-task"}

    def start_model_blend(self, model, animations, lod):
        self.calls.append(("blend", model, animations, lod))
        return {"taskId": "blend-task"}

    def start_model_animation(self, model, animation, lod):
        self.calls.append(("animation", model, animation, lod))
        return {"taskId": "animation-task"}


class ModelTaskSubmissionServiceTests(unittest.TestCase):
    def setUp(self):
        self.resolver = FakeResolver()
        self.operations = FakeOperations()
        self.service = ModelTaskSubmissionService(
            self.resolver,
            self.operations,
            max_blend_animation_count=2,
        )

    def test_resolves_optional_animation_and_starts_model_task(self):
        result = self.service.start_model({
            "manifestId": 5,
            "assetIndex": 7,
            "animationAssetIndex": 9,
            "lod": 2,
        })

        self.assertEqual({"taskId": "model-task"}, result)
        self.assertEqual(
            [("model", 5, 7), ("asset", 5, 9)], self.resolver.calls
        )
        self.assertEqual(
            [("model", 5, "model-7", "asset-9", 2)], self.operations.calls
        )

    def test_model_without_animation_does_not_resolve_one(self):
        self.service.start_model({"manifestId": 5, "assetIndex": 7})

        self.assertEqual([("model", 5, 7)], self.resolver.calls)
        self.assertEqual(
            [("model", 5, "model-7", None, 0)], self.operations.calls
        )

    def test_resolves_blend_batch_before_submission(self):
        result = self.service.start_blend({
            "manifestId": 3,
            "assetIndex": 4,
            "animationAssetIndexes": [8, 9],
            "lod": 1,
        })

        self.assertEqual({"taskId": "blend-task"}, result)
        self.assertEqual(
            [("model", 3, 4), ("many", 3, (8, 9))], self.resolver.calls
        )
        self.assertEqual(
            [("blend", "model-4", ("asset-8", "asset-9"), 1)],
            self.operations.calls,
        )

    def test_resolves_model_and_animation_for_animation_task(self):
        result = self.service.start_animation({
            "manifestId": 2,
            "assetIndex": 6,
            "animationAssetIndex": 10,
            "lod": 3,
        })

        self.assertEqual({"taskId": "animation-task"}, result)
        self.assertEqual(
            [("model", 2, 6), ("asset", 2, 10)], self.resolver.calls
        )
        self.assertEqual(
            [("animation", "model-6", "asset-10", 3)], self.operations.calls
        )

    def test_invalid_blend_count_fails_before_resolution(self):
        with self.assertRaises(TaskInputError):
            self.service.start_blend({
                "manifestId": 1,
                "assetIndex": 2,
                "animationAssetIndexes": [3, 4, 5],
            })

        self.assertEqual([], self.resolver.calls)
        self.assertEqual([], self.operations.calls)


if __name__ == "__main__":
    unittest.main()
