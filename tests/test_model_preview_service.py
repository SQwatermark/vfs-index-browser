import unittest
from pathlib import Path
from types import SimpleNamespace

from model_preview_service import ModelPreviewService


class ModelPreviewServiceTests(unittest.TestCase):
    def service(self, *, avatar_result=None, ordinary_result=None, blender=True, glb_calls=None):
        glb_calls = [] if glb_calls is None else glb_calls
        return ModelPreviewService(
            lambda *_args, **_kwargs: avatar_result,
            lambda *_args, **_kwargs: ordinary_result,
            lambda dependencies: ([("source", Path("chunk"))], []),
            lambda _path, _document: "idle girl",
            lambda: blender,
            lambda resolved, **kwargs: glb_calls.append((resolved, kwargs)),
            glb_version=4,
            blend_version=12,
            animation_version=18,
            max_blend_animation_count=100,
        )

    def test_builds_ordinary_preview_urls_and_status(self):
        document = {"images": [{}], "skins": [{}], "meshes": [{}]}
        service = self.service(
            ordinary_result=(
                document,
                {
                    "scope": "manifestDependencyClosure",
                    "builtAtEpoch": 7,
                    "dependencyBundles": [{"name": "a.ab"}],
                    "missingDependencyBundles": [],
                },
            )
        )
        index = SimpleNamespace(bundle_dependencies=lambda _index: [{"name": "a.ab"}])
        asset = {
            "asset_index": 11,
            "bundle_index": 3,
            "path": "assets/hero.prefab",
        }
        animation = (object(), {"asset_index": 22}, {}, Path("animation.chk"))

        result = service.build(
            5, (index, asset, {"id": 7}, Path("model.chk")), animation, 0
        )

        self.assertEqual("texturedSkinnedModel", result["status"])
        self.assertEqual(
            "/api/manifest-asset/model-glb?manifestId=5&assetIndex=11&v=4",
            result["glbUrl"],
        )
        self.assertIn("animationAssetIndex=22", result["blendUrl"])
        self.assertIn("queryHint=idle%20girl", result["animationCandidatesUrl"])
        self.assertEqual(100, result["maxBlendAnimationCount"])
        self.assertIsNone(result["run"]["lod"])

    def test_builds_avatar_preview_with_lod_and_no_blender(self):
        service = self.service(
            avatar_result=(
                {"meshes": [{}]},
                {"scope": "avatarMeshBundleClosure", "builtAtEpoch": 9},
                Path("model.json"),
            ),
            blender=False,
        )
        asset = {
            "asset_index": 33,
            "bundle_index": 4,
            "path": (
                "assets/beyond/dynamicassets/gameplay/npc/avatarmesh/actor/"
                "data_npc_avatarmesh_sample.asset"
            ),
        }

        result = service.build(
            5, (object(), asset, {"id": 7}, Path("model.chk")), None, 2
        )

        self.assertEqual("staticGeometry", result["status"])
        self.assertIn("&lod=2", result["glbUrl"])
        self.assertIsNone(result["blendUrl"])
        self.assertEqual(2, result["run"]["lod"])

    def test_task_maps_model_progress_and_ensures_glb(self):
        glb_calls = []
        service = self.service(
            ordinary_result=({"meshes": [{}]}, {"scope": "ordinary"}),
            glb_calls=glb_calls,
        )
        index = SimpleNamespace(bundle_dependencies=lambda _index: [])
        resolved = (
            index,
            {"asset_index": 11, "bundle_index": 3, "path": "assets/hero.prefab"},
            {"id": 7},
            Path("model.chk"),
        )
        reports = []
        cancelled = SimpleNamespace(is_set=lambda: False)

        result = service.build_task(
            5, resolved, None, 0, cancel_event=cancelled, progress=reports.append
        )

        self.assertEqual("modelDocument", result["kind"])
        self.assertEqual(["glb", "ready"], [entry["stage"] for entry in reports[-2:]])
        self.assertEqual(1, len(glb_calls))


if __name__ == "__main__":
    unittest.main()
