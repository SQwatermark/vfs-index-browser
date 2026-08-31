import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_resolves_published_model_path_from_asset_identity(self):
        _cache, _pointer, run = self.publish()
        self.assertEqual(run / "model.json", self.store.resolve_model_path(7, 11))
        self.assertIsNone(self.store.resolve_model_path(7, 11, "missing"))

    def test_publish_writes_completion_before_atomic_pointer(self):
        cache, runs, pointer = self.store.cache_paths(7, 11)
        run = runs / "next"
        meta = {"version": 3, "source": {"id": 7}, "selectedRun": "next"}
        observed = []
        model_path = self.store.publish(
            cache,
            pointer,
            run,
            document={"buffers": [], "images": []},
            meta=meta,
            geometry=b"",
            geometry_required=False,
            before_pointer=lambda: observed.append((run / "run.json").is_file()),
        )
        self.assertEqual([True], observed)
        self.assertTrue(model_path.is_file())
        self.assertFalse((run / "geometry.bin").exists())
        self.assertEqual(run.resolve(), resolve_published_model_run(cache))

    def test_failed_pointer_replace_preserves_old_pointer_and_cleans_temporary(self):
        cache, _runs, pointer = self.store.cache_paths(7, 11)
        cache.mkdir(parents=True)
        pointer.write_bytes(b"old-pointer")
        with (
            patch("model_run_store.os.replace", side_effect=OSError("replace failed")),
            self.assertRaisesRegex(OSError, "replace failed"),
        ):
            self.store.publish(
                cache,
                pointer,
                cache / "runs" / "next",
                document={},
                meta={"selectedRun": "next"},
                geometry=b"",
                geometry_required=False,
            )
        self.assertEqual(b"old-pointer", pointer.read_bytes())
        self.assertEqual([], list(cache.glob(".run.json.*.tmp")))

    def test_publish_rejects_mismatched_run_identity(self):
        cache, runs, pointer = self.store.cache_paths(7, 11)
        with self.assertRaisesRegex(ValueError, "target is inconsistent"):
            self.store.publish(
                cache,
                pointer,
                runs / "actual",
                document={},
                meta={"selectedRun": "different"},
                geometry=b"",
                geometry_required=False,
            )
        self.assertFalse((runs / "actual").exists())
