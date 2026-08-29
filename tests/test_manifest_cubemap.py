import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class ManifestCubemapExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.chunk = self.root / "source.chk"
        self.chunk.write_bytes(b"bundle")
        self.record = {"id": 42, "length": 6, "offset": 0, "chunk_path": str(self.chunk)}
        self.asset = {"asset_index": 7, "path": "assets/example/character-light.exr"}
        self.handler = object.__new__(server.BrowserHandler)
        self.calls = []

    def tearDown(self):
        self.temporary.cleanup()

    def export_faces(self, **arguments):
        self.calls.append(arguments)
        artifacts = []
        for face in server.CUBEMAP_FACE_NAMES:
            relative = f"Cubemap/character-light_{face}.png"
            path = arguments["output_directory"] / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = face.encode()
            path.write_bytes(payload)
            artifacts.append({
                "relativePath": relative,
                "face": face,
                "byteCount": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            })
        return {"artifactCount": len(artifacts), "artifacts": artifacts}

    def test_exports_all_faces_and_reuses_matching_atomic_run(self):
        with (
            patch.object(server, "INTERNAL_CACHE_DIR", self.root / "cache"),
            patch.object(server.UNITY_WORKER, "export_cubemap_faces", side_effect=self.export_faces),
        ):
            first = self.handler.ensure_manifest_cubemap_export(self.record, self.chunk, self.asset)
            second = self.handler.ensure_manifest_cubemap_export(self.record, self.chunk, self.asset)

        self.assertEqual(first, second)
        self.assertEqual(1, len(self.calls))
        self.assertEqual("assets/example/character-light.exr", self.calls[0]["container"])
        self.assertEqual(set(server.CUBEMAP_FACE_NAMES), set(first[0]))
        self.assertTrue(first[1]["selectedRun"].startswith("cubemap-7-"))

    def test_rebuilds_cache_when_worker_identity_changes(self):
        identities = [[{"path": "worker", "size": 1}], [{"path": "worker", "size": 2}]]
        with (
            patch.object(server, "INTERNAL_CACHE_DIR", self.root / "cache"),
            patch.object(server.UNITY_WORKER, "export_cubemap_faces", side_effect=self.export_faces),
            patch.object(server.UNITY_WORKER, "artifact_identity", side_effect=identities),
        ):
            self.handler.ensure_manifest_cubemap_export(self.record, self.chunk, self.asset)
            self.handler.ensure_manifest_cubemap_export(self.record, self.chunk, self.asset)

        self.assertEqual(2, len(self.calls))

    def test_rejects_incomplete_worker_face_set_without_publishing(self):
        def export_incomplete(**arguments):
            relative = "Cubemap/only_PositiveX.png"
            path = arguments["output_directory"] / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = b"face"
            path.write_bytes(payload)
            return {
                "artifactCount": 1,
                "artifacts": [{
                    "relativePath": relative,
                    "face": "PositiveX",
                    "byteCount": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }],
            }

        cache = self.root / "cache"
        with (
            patch.object(server, "INTERNAL_CACHE_DIR", cache),
            patch.object(server.UNITY_WORKER, "export_cubemap_faces", side_effect=export_incomplete),
            self.assertRaisesRegex(RuntimeError, "incomplete Cubemap"),
        ):
            self.handler.ensure_manifest_cubemap_export(self.record, self.chunk, self.asset)

        self.assertFalse((cache / "42/manifest-assets/7/cubemap/meta.json").exists())


if __name__ == "__main__":
    unittest.main()
