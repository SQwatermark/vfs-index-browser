import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from model_blend_service import ModelBlendService


class ModelBlendServiceTests(unittest.TestCase):
    def test_sync_preparation_does_not_start_blender_and_builds_http_document(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            glb = root / "model.glb"
            glb.write_bytes(b"glb")
            service = ModelBlendService(
                lambda resolved, **_kwargs: (
                    {"path": "assets/hero.prefab"},
                    root / "model.json",
                    glb,
                ),
                lambda *_args, **_kwargs: self.fail("animation path is unexpected"),
                lambda *_args, **_kwargs: self.fail("Blender must not start"),
            )

            bundle = service.prepare_bundle(object(), [], 0)
            document = service.preparation_document(bundle, "/download.blend")

            self.assertFalse(bundle.all_animations_failed)
            self.assertEqual("modelAnimationBundlePreparation", document["kind"])
            self.assertEqual(0, document["requestedCount"])
            self.assertEqual(0, document["exportedCount"])
            self.assertEqual("/download.blend", document["downloadUrl"])

    def test_prepares_base_model_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            glb = root / "model.glb"
            blend = root / "model.blend"
            glb.write_bytes(b"glb")
            blend.write_bytes(b"blend")
            reports = []
            service = ModelBlendService(
                lambda resolved, **_kwargs: (
                    {"path": "assets/hero.prefab"},
                    root / "model.json",
                    glb,
                ),
                lambda *_args, **_kwargs: self.fail("animation path is unexpected"),
                lambda path, **_kwargs: blend if path == glb else self.fail("wrong GLB"),
            )

            result = service.prepare(
                object(),
                [],
                0,
                cancel_event=SimpleNamespace(is_set=lambda: False),
                progress=reports.append,
            )

            self.assertTrue(result["artifactAvailable"])
            self.assertEqual("hero.blend", result["_artifactName"])
            self.assertEqual(["modelGlb", "blender", "blender"], [x["stage"] for x in reports])

    def test_prepares_animated_artifact_and_preserves_issues(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            glb = root / "animated.glb"
            blend = root / "animated.blend"
            glb.write_bytes(b"glb")
            blend.write_bytes(b"blend")
            issue = SimpleNamespace(as_json=lambda: {"stage": "clipExport"})
            bundle = SimpleNamespace(
                asset={"path": "assets/hero.prefab"},
                animations=[{"asset_index": 11}],
                glb_path=glb,
                issues=[issue],
            )
            service = ModelBlendService(
                lambda *_args, **_kwargs: self.fail("base path is unexpected"),
                lambda *_args, **_kwargs: bundle,
                lambda *_args, **_kwargs: blend,
            )

            result = service.prepare(
                object(),
                [(object(), {"asset_index": 11}, {}, root / "a.chk")],
                2,
                cancel_event=SimpleNamespace(is_set=lambda: False),
                progress=lambda _value: None,
            )

            self.assertEqual(1, result["exportedCount"])
            self.assertEqual([{"stage": "clipExport"}], result["issues"])
            self.assertEqual("hero-animations-1.blend", result["_artifactName"])

    def test_all_incompatible_animations_do_not_start_blender(self):
        issue = SimpleNamespace(as_json=lambda: {"stage": "modelBinding"})
        bundle = SimpleNamespace(
            asset={"path": "assets/hero.prefab"},
            animations=[],
            glb_path=Path("base.glb"),
            issues=[issue],
        )
        service = ModelBlendService(
            lambda *_args, **_kwargs: self.fail("base path is unexpected"),
            lambda *_args, **_kwargs: bundle,
            lambda *_args, **_kwargs: self.fail("Blender must not start"),
        )

        result = service.prepare(
            object(),
            [(object(), {"asset_index": 11}, {}, Path("a.chk"))],
            0,
            cancel_event=SimpleNamespace(is_set=lambda: False),
            progress=lambda _value: None,
        )

        self.assertFalse(result["artifactAvailable"])
        self.assertEqual(0, result["exportedCount"])
        self.assertNotIn("_artifactPath", result)

    def test_all_incompatible_bundle_builds_stable_failure_documents(self):
        issue = SimpleNamespace(as_json=lambda: {"stage": "modelBinding"})
        bundle = SimpleNamespace(
            asset={"path": "assets/hero.prefab"},
            animations=[],
            glb_path=Path("base.glb"),
            issues=[issue],
        )
        service = ModelBlendService(
            lambda *_args, **_kwargs: self.fail("base path is unexpected"),
            lambda *_args, **_kwargs: bundle,
            lambda *_args, **_kwargs: self.fail("Blender must not start"),
        )

        prepared = service.prepare_bundle(object(), [(object(),)], 0)

        self.assertTrue(prepared.all_animations_failed)
        self.assertIsNone(service.preparation_document(prepared, None)["downloadUrl"])
        self.assertEqual(
            {
                "error": "none of the selected animations could be exported",
                "issues": [{"stage": "modelBinding"}],
            },
            service.failure_document(prepared),
        )
        with self.assertRaisesRegex(ValueError, "all selected animations failed"):
            service.build_artifact(prepared)


if __name__ == "__main__":
    unittest.main()
