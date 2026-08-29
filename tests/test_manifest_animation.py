import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class ManifestAnimationExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.chunk = self.root / "source.chk"
        self.chunk.write_bytes(b"bundle")
        self.record = {
            "id": 42,
            "length": 6,
            "offset": 0,
            "chunk_path": str(self.chunk),
        }
        self.asset = {
            "asset_index": 7,
            "path": "assets/animations/idle.fbx##idle_loop",
        }
        self.handler = object.__new__(server.BrowserHandler)

        def write_file_slice(_record, _chunk_path, target):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"bundle")

        self.handler.write_file_slice = write_file_slice
        self.calls = []

        class FakeWorker:
            def artifact_identity(inner_self):
                return [{"path": "worker.exe", "size": 1, "mtimeNs": 2}]

            def export_animation_clip_json(inner_self, **arguments):
                self.calls.append(arguments)
                relative_path = (
                    "AnimationClip/CAB-test/"
                    "Idle_Loop_p0000000000000011.animation.json"
                )
                payload = {
                    "format": "AnimeStudioAnimationClip",
                    "version": "1.1.0",
                    "name": "Idle_Loop",
                    "timelines": [],
                    "curves": [],
                }
                content = json.dumps(payload).encode("utf-8")
                target = arguments["output_directory"] / relative_path
                target.parent.mkdir(parents=True)
                target.write_bytes(content)
                return {
                    "artifactCount": 1,
                    "artifacts": [{
                        "relativePath": relative_path,
                        "sourceFile": "CAB-test",
                        "pathId": 17,
                        "name": "Idle_Loop",
                        "curveCount": 0,
                        "timelineCount": 0,
                        "duration": 0,
                        "byteCount": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }],
                }

        self.worker = FakeWorker()
        self.map_meta = {
            "selectedRun": "asset-map-run",
            "assetEntries": [{
                "Name": "Idle_Loop",
                "Container": "",
                "Source": "record:42",
                "PathID": 17,
                "Type": "AnimationClip",
            }],
        }
        self.handler.ensure_assetbundle_map = (
            lambda _record, _chunk_path, emit_errors=False: self.map_meta
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_exports_exact_clip_and_reuses_matching_cache(self):
        with (
            patch.object(server, "INTERNAL_CACHE_DIR", self.root / "cache"),
            patch.object(server, "UNITY_WORKER", self.worker),
        ):
            first = self.handler.ensure_animation_clip_export(
                self.record,
                self.chunk,
                self.asset,
            )
            second = self.handler.ensure_animation_clip_export(
                self.record,
                self.chunk,
                self.asset,
            )

        self.assertEqual(first, second)
        self.assertEqual(1, len(self.calls))
        self.assertEqual(17, self.calls[0]["path_id"])
        self.assertEqual("Idle_Loop", self.calls[0]["expected_name"])
        self.assertEqual("Idle_Loop", first[0]["name"])
        self.assertEqual("asset-map-run", self.map_meta["selectedRun"])
        self.assertIn("selectedRun", first[2])
        self.assertEqual(17, first[2]["source"]["pathId"])
        self.assertTrue(first[1].is_file())

    def test_rejects_missing_or_ambiguous_asset_map_identity(self):
        self.map_meta["assetEntries"].append({
            **self.map_meta["assetEntries"][0],
            "PathID": 18,
        })
        with (
            patch.object(server, "INTERNAL_CACHE_DIR", self.root / "cache"),
            patch.object(server, "UNITY_WORKER", self.worker),
        ):
            with self.assertRaisesRegex(RuntimeError, "found 0 for 'idle_loop'"):
                self.handler.ensure_animation_clip_export(
                    self.record,
                    self.chunk,
                    self.asset,
                )
        self.assertEqual([], self.calls)


if __name__ == "__main__":
    unittest.main()
