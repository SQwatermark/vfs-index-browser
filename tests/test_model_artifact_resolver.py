import json
import tempfile
import unittest
from pathlib import Path

from model_artifact_resolver import (
    ModelArtifactReference,
    ModelArtifactResolver,
    ModelArtifactRunNotFound,
)


class FakeRuns:
    def __init__(self, root):
        self.root = root

    def cache_paths(self, record_id, asset_index, *, lod=None):
        root = self.root / str(record_id) / "models" / str(asset_index)
        if lod is not None:
            root /= f"avatar-lod-{lod}"
        return root, root / "runs", root / "run.json"


class ModelArtifactResolverTests(unittest.TestCase):
    def publish(self, root, *, lod=2):
        cache, runs, pointer = FakeRuns(root).cache_paths(7, 11, lod=lod)
        run = runs / "selected"
        (run / "textures" / "body").mkdir(parents=True)
        (run / "geometry.bin").write_bytes(b"geometry")
        (run / "textures" / "body" / "color.png").write_bytes(b"png")
        marker = {"selectedRun": "selected"}
        (run / "run.json").write_text(json.dumps(marker), encoding="utf-8")
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.write_text(json.dumps(marker), encoding="utf-8")
        return run

    def test_parses_reference_and_rejects_invalid_lod(self):
        reference = ModelArtifactReference.parse({
            "recordId": ["7"], "assetIndex": ["11"], "lod": ["2"], "run": ["selected"]
        })
        self.assertEqual((7, 11, 2, "selected"), (
            reference.record_id, reference.asset_index, reference.lod, reference.run
        ))
        with self.assertRaisesRegex(ValueError, "recordId, assetIndex or lod"):
            ModelArtifactReference.parse({
                "recordId": ["7"], "assetIndex": ["11"], "lod": ["4"]
            })

    def test_resolves_geometry_and_texture_from_completed_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = self.publish(root)
            resolver = ModelArtifactResolver(FakeRuns(root))
            reference = ModelArtifactReference(7, 11, 2, "")

            self.assertEqual(run / "geometry.bin", resolver.geometry(reference))
            self.assertEqual(
                run / "textures" / "body" / "color.png",
                resolver.texture(reference, "body%2Fcolor.png"),
            )

    def test_rejects_texture_escape_and_distinguishes_missing_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.publish(root)
            resolver = ModelArtifactResolver(FakeRuns(root))
            reference = ModelArtifactReference(7, 11, 2, "selected")

            self.assertIsNone(resolver.texture(reference, "../geometry.bin"))
            with self.assertRaisesRegex(ModelArtifactRunNotFound, "texture run"):
                resolver.texture(
                    ModelArtifactReference(7, 11, 2, "missing"), "body/color.png"
                )


if __name__ == "__main__":
    unittest.main()
