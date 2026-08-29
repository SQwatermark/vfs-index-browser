import json
import tempfile
import unittest
from pathlib import Path

from model_run_store import ModelRunStore, resolve_published_model_run


class ModelRunStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = ModelRunStore(self.root, lambda document: document.get("errors", []))

    def tearDown(self):
        self.temporary.cleanup()

    def publish(self, *, document=None, source=None):
        cache, runs, pointer = self.store.cache_paths(7, 11)
        run = runs / "current"
        run.mkdir(parents=True)
        meta = {"version": 3, "source": source or {"id": 7}, "selectedRun": "current"}
        (run / "model.json").write_text(
            json.dumps(document or {"buffers": [], "images": []}), encoding="utf-8"
        )
        (run / "run.json").write_text(json.dumps(meta), encoding="utf-8")
        cache.mkdir(parents=True, exist_ok=True)
        pointer.write_text(json.dumps(meta), encoding="utf-8")
        return cache, pointer, run

    def test_loads_matching_completed_run(self):
        cache, pointer, run = self.publish()
        loaded = self.store.load_cached(
            cache,
            pointer,
            version=3,
            source_identity={"id": 7},
            geometry_required=False,
        )
        self.assertIsNotNone(loaded)
        self.assertEqual(run / "model.json", loaded[2])

    def test_rejects_missing_required_artifacts_or_invalid_document(self):
        cache, pointer, run = self.publish(document={"buffers": [{}], "images": []})
        self.assertIsNone(self.store.load_cached(
            cache, pointer, version=3, source_identity={"id": 7}, geometry_required=False,
        ))
        (run / "geometry.bin").write_bytes(b"geometry")
        (run / "model.json").write_text(json.dumps({"errors": ["invalid"]}), encoding="utf-8")
        self.assertIsNone(self.store.load_cached(
            cache, pointer, version=3, source_identity={"id": 7}, geometry_required=False,
        ))

    def test_avatar_mode_requires_geometry_even_without_buffer_declaration(self):
        cache, pointer, run = self.publish()
        self.assertIsNone(self.store.load_cached(
            cache, pointer, version=3, source_identity={"id": 7}, geometry_required=True,
        ))
        (run / "geometry.bin").write_bytes(b"geometry")
        self.assertIsNotNone(self.store.load_cached(
            cache, pointer, version=3, source_identity={"id": 7}, geometry_required=True,
        ))

    def test_rejects_source_change_and_missing_texture_directory(self):
        cache, pointer, run = self.publish(document={"buffers": [], "images": [{}]})
        self.assertIsNone(self.store.load_cached(
            cache, pointer, version=3, source_identity={"id": 8}, geometry_required=False,
        ))
        self.assertIsNone(self.store.load_cached(
            cache, pointer, version=3, source_identity={"id": 7}, geometry_required=False,
        ))
        (run / "textures").mkdir()
        self.assertIsNotNone(self.store.load_cached(
            cache, pointer, version=3, source_identity={"id": 7}, geometry_required=False,
        ))

    def test_resolver_rejects_escape_and_incomplete_run(self):
        cache, _pointer, _run = self.publish()
        incomplete = cache / "runs" / "incomplete"
        incomplete.mkdir()
        self.assertIsNone(resolve_published_model_run(cache, "../outside"))
        self.assertIsNone(resolve_published_model_run(cache, "incomplete"))
