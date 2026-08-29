import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from avatar_model_build_service import AvatarModelBuildService


class FakeRuns:
    def __init__(self, root, cached=None):
        self.root, self.cached, self.published = root, cached, None

    def cache_paths(self, record_id, asset_index, *, lod=None):
        cache = self.root / str(record_id) / str(asset_index) / f"lod-{lod}"
        return cache, cache / "runs", cache / "run.json"

    def load_cached(self, *_args, **_kwargs):
        return self.cached

    def publish(self, *args, **kwargs):
        self.published = (args, kwargs)
        return args[2] / "model.json"


class FakeWorker:
    def __init__(self):
        self.object_options = None

    def stage_inputs(self, _root, sources):
        return [{"inputId": item.input_id, "inputPath": item.file_name} for item in sources]

    def build_cab_map(self, *_args, **_kwargs):
        return {"artifactCount": 1}

    def export_objects(self, *_args, **kwargs):
        self.object_options = kwargs
        return {"artifactCount": 2}

    def export_textures(self, *_args, **_kwargs):
        return {"artifactCount": 1, "artifacts": [{"relativePath": "body.png"}]}


class FakeDocuments:
    def __init__(self, *, with_texture=False):
        self.with_texture = with_texture
        self.texture_url = None

    def load(self, _root, _plan):
        selections = [{"sourceFile": "a", "pathId": 3}] if self.with_texture else []
        return SimpleNamespace(texture_selections=selections)

    def attach_exported_textures(self, _assembly, _result, url_builder):
        self.texture_url = url_builder("body.png")

    def build(self, _avatar_mesh, _assembly, *, lod, buffer_uri):
        return {"nodes": [], "lod": lod, "buffers": [{"uri": buffer_uri}]}, b"geometry"


class AvatarModelBuildServiceTests(unittest.TestCase):
    def service(self, runs, worker, documents, *, missing=None):
        missing = [] if missing is None else missing
        return AvatarModelBuildService(
            runs,
            worker,
            documents,
            lambda *_args, **_kwargs: (
                {"avatar": 1},
                {"bundles": [{"bundleIndex": 7}]},
                {"dump": {"selectedRun": "plan"}},
            ),
            lambda _index, _plan: [{"bundleIndex": 7, "name": "body.ab"}],
            lambda _bundles: (
                [
                    (
                        {
                            "id": 9,
                            "length": 1,
                            "offset": 0,
                            "chunk_path": __file__,
                        },
                        Path(__file__),
                    )
                ],
                missing,
            ),
            lambda _plan: ["assets/body.prefab"],
            lambda: [{"worker": 1}],
            version=4,
            builder_paths=[Path(__file__)],
        )

    def test_cache_hit_preserves_avatar_plan_and_reports_complete_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            cached = ({"nodes": [1]}, {"selectedRun": "old"}, Path("model.json"))
            runs = FakeRuns(Path(directory), cached)
            progress = []
            result = self.service(runs, FakeWorker(), FakeDocuments()).ensure(
                object(),
                {"asset_index": 2, "path": "assets/avatar.asset"},
                {"id": 1, "length": 1, "offset": 0, "chunk_path": __file__},
                Path(__file__),
                0,
                progress=progress.append,
            )
            self.assertEqual(cached, result)
            self.assertEqual(
                ["avatarPlan", "cache"], [entry["stage"] for entry in progress]
            )

    def test_coordinates_avatar_build_textures_and_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            runs = FakeRuns(Path(directory))
            worker = FakeWorker()
            documents = FakeDocuments(with_texture=True)
            progress = []
            document, meta, model_path = self.service(runs, worker, documents).ensure(
                object(),
                {"asset_index": 2, "path": "assets/avatar.asset"},
                {"id": 1, "length": 1, "offset": 0, "chunk_path": __file__},
                Path(__file__),
                3,
                progress=progress.append,
            )
            self.assertEqual(3, document["lod"])
            self.assertEqual("avatarMeshBundleClosure", meta["scope"])
            self.assertEqual(
                ["buildCABMap", "exportObjectSnapshots", "exportIdentifiedTextures"],
                [step["name"] for step in meta["steps"]],
            )
            self.assertEqual(["assets/body.prefab"], worker.object_options["containers"])
            self.assertIn("lod=3", documents.texture_url)
            self.assertEqual("model.json", model_path.name)
            runs.published[1]["before_pointer"]()
            self.assertEqual(
                ["avatarPlan", "cabMap", "objects", "textures", "publish"],
                [entry["stage"] for entry in progress],
            )

    def test_rejects_missing_bundle_before_cache_or_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(
                FakeRuns(Path(directory)),
                FakeWorker(),
                FakeDocuments(),
                missing=[{"name": "missing.ab"}],
            )
            with self.assertRaisesRegex(FileNotFoundError, "missing.ab"):
                service.ensure(
                    object(),
                    {"asset_index": 2, "path": "assets/avatar.asset"},
                    {"id": 1, "length": 1, "offset": 0},
                    Path(__file__),
                    0,
                )


if __name__ == "__main__":
    unittest.main()
