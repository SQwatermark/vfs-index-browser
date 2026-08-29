import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ordinary_model_build_service import OrdinaryModelBuildService


class FakeRuns:
    def __init__(self, root, cached=None):
        self.root, self.cached, self.published = root, cached, None
    def cache_paths(self, record_id, asset_index):
        cache = self.root / str(record_id) / str(asset_index)
        return cache, cache / "runs", cache / "run.json"
    def load_cached(self, *_args, **_kwargs):
        return self.cached
    def publish(self, *args, **kwargs):
        self.published = (args, kwargs)


class FakeWorker:
    def stage_inputs(self, _root, sources):
        return [{"inputId": item.input_id, "inputPath": item.file_name} for item in sources]
    def build_cab_map(self, *_args, **_kwargs):
        return {"artifactCount": 1}
    def export_objects(self, *_args, **_kwargs):
        return {"artifactCount": 2}
    def export_textures(self, *_args, **_kwargs):
        return {"artifactCount": 1, "artifacts": []}


class FakeDocuments:
    builder_mtime_ns = 5
    def __init__(self):
        self.finalized = False
    def assemble(self, *_args, **_kwargs):
        return SimpleNamespace(document={"nodes": []}, geometry=b"geometry", textures=[])
    def finalize(self, _assembly, _missing):
        self.finalized = True


class OrdinaryModelBuildServiceTests(unittest.TestCase):
    def options(self, root, runs, documents):
        return OrdinaryModelBuildService(
            runs, FakeWorker(), documents, lambda: [{"worker": 1}],
            version=3, snapshot_types=["GameObject", "Transform"],
        )

    def test_cache_hit_skips_build_and_reports_complete_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            cached = ({"nodes": [1]}, {"selectedRun": "old"}, Path("model.json"))
            runs = FakeRuns(Path(directory), cached)
            progress = []
            service = self.options(Path(directory), runs, FakeDocuments())
            result = service.ensure(
                {"id": 1, "length": 1, "offset": 0, "chunk_path": __file__}, Path(__file__),
                {"asset_index": 2, "path": "assets/a.prefab", "bundle_name": "a.ab"},
                [], [], [], progress=progress.append,
            )
            self.assertEqual(cached[:2], result)
            self.assertEqual([{"stage": "cache", "completed": 4, "total": 4}], progress)

    def test_coordinates_build_steps_and_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs, documents, progress = FakeRuns(root), FakeDocuments(), []
            service = self.options(root, runs, documents)
            document, meta = service.ensure(
                {"id": 1, "length": 1, "offset": 0, "chunk_path": __file__}, Path(__file__),
                {"asset_index": 2, "path": "assets/a.prefab", "bundle_name": "a.ab"},
                [], [], [], progress=progress.append,
            )
            self.assertEqual({"nodes": []}, document)
            self.assertEqual(["buildCABMap", "exportObjectSnapshots"], [x["name"] for x in meta["steps"]])
            self.assertTrue(documents.finalized)
            self.assertIsNotNone(runs.published)
            runs.published[1]["before_pointer"]()
            self.assertEqual(["cabMap", "objects", "textures", "publish"], [x["stage"] for x in progress])


if __name__ == "__main__":
    unittest.main()
