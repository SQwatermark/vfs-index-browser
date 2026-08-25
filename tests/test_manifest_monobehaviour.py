import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class ManifestMonoBehaviourDumpTests(unittest.TestCase):
    def test_exports_exact_container_and_reuses_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chunk = root / "source.chk"
            chunk.write_bytes(b"bundle")
            record = {
                "id": 42,
                "length": 6,
                "offset": 0,
                "chunk_path": str(chunk),
            }
            asset = {
                "asset_index": 7,
                "path": "assets/example/char.override.asset",
            }
            handler = object.__new__(server.BrowserHandler)

            def write_file_slice(_record, _chunk_path, target):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"bundle")

            handler.write_file_slice = write_file_slice
            calls = []

            class FakeWorker:
                def artifact_identity(self):
                    return [{"path": "worker.exe", "size": 1, "mtimeNs": 2}]

                def export_monobehaviour_typetree_dump(self, **arguments):
                    calls.append(arguments)
                    output = arguments["output_directory"]
                    artifacts = []
                    for ordinal, content in enumerate((b"profile", b"lighting")):
                        relative = f"objects/{ordinal:04d}-p{ordinal + 1:016X}.txt"
                        target = output / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(content)
                        artifacts.append({
                            "relativePath": relative,
                            "byteCount": len(content),
                            "sha256": hashlib.sha256(content).hexdigest(),
                            "complete": False,
                        })
                    return {"artifactCount": len(artifacts), "artifacts": artifacts}

            with (
                patch.object(server, "INTERNAL_CACHE_DIR", root / "cache"),
                patch.object(server, "UNITY_WORKER", FakeWorker()),
            ):
                first = handler.ensure_manifest_monobehaviour_dump(record, chunk, asset)
                second = handler.ensure_manifest_monobehaviour_dump(record, chunk, asset)
                first[0].write_text("damaged", encoding="utf-8")
                rebuilt = handler.ensure_manifest_monobehaviour_dump(record, chunk, asset)

            self.assertIsNotNone(first)
            self.assertEqual(first, second)
            self.assertNotEqual(first, rebuilt)
            self.assertEqual(len(calls), 2)
            self.assertEqual("assets/example/char.override.asset", calls[0]["container"])
            dump = rebuilt[0].read_text(encoding="utf-8")
            self.assertIn(
                "===== objects/0000-p0000000000000001.txt =====\nprofile",
                dump,
            )
            self.assertIn(
                "===== objects/0001-p0000000000000002.txt =====\nlighting",
                dump,
            )
            self.assertEqual(
                "combined-dump.txt",
                rebuilt[1]["derivedFiles"]["combinedDump"]["relativePath"],
            )

    def test_raw_export_uses_worker_atomic_cache_and_exact_container(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chunk = root / "source.chk"
            chunk.write_bytes(b"bundle")
            record = {
                "id": 42,
                "length": 6,
                "offset": 0,
                "chunk_path": str(chunk),
            }
            asset = {
                "asset_index": 7,
                "path": "assets/example/char.override.asset",
            }
            calls = []

            class FakeWorker:
                def artifact_identity(self):
                    return [{"path": "worker.exe", "size": 1, "mtimeNs": 2}]

                def export_monobehaviour_raw(self, **arguments):
                    calls.append(arguments)
                    content = b"raw-object"
                    output = arguments["output_directory"]
                    target = output / "objects" / "0000-p0000000000000001.dat"
                    target.parent.mkdir(parents=True)
                    target.write_bytes(content)
                    return {
                        "artifactCount": 1,
                        "artifacts": [{
                            "relativePath": "objects/0000-p0000000000000001.dat",
                            "byteCount": len(content),
                            "sha256": hashlib.sha256(content).hexdigest(),
                        }],
                    }

            handler = object.__new__(server.BrowserHandler)

            def write_slice(_record, _chunk, target):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"bundle")

            handler.write_file_slice = write_slice
            with (
                patch.object(server, "INTERNAL_CACHE_DIR", root / "cache"),
                patch.object(server, "UNITY_WORKER", FakeWorker()),
            ):
                first = handler.ensure_manifest_monobehaviour_raw(record, chunk, asset)
                second = handler.ensure_manifest_monobehaviour_raw(record, chunk, asset)

            self.assertEqual(first, second)
            self.assertEqual(1, len(calls))
            self.assertEqual("assets/example/char.override.asset", calls[0]["container"])
            self.assertEqual(b"raw-object", first[0].read_bytes())
            self.assertEqual(
                "objects/0000-p0000000000000001.dat",
                first[1]["exportedFile"],
            )


if __name__ == "__main__":
    unittest.main()
