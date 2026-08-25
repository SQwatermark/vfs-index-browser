import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class ManifestAssetMapTests(unittest.TestCase):
    def test_asset_map_uses_worker_atomic_cache_and_preserves_entry_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chunk = root / "bundle.chk"
            chunk.write_bytes(b"bundle")
            record = {
                "id": 81,
                "length": 6,
                "offset": 0,
                "chunk_path": str(chunk),
            }
            calls = []
            entry = {
                "Name": "example",
                "Container": "assets/example.asset",
                "Source": "record:81",
                "PathID": 7,
                "Type": "TextAsset",
                "Hash": "abc",
                "Offset": 0,
            }

            class FakeWorker:
                def artifact_identity(self):
                    return [{"path": "worker.exe", "size": 1, "mtimeNs": 2}]

                def build_asset_map(self, **arguments):
                    calls.append(arguments)
                    content = json.dumps({
                        "GameType": "ArknightsEndfield",
                        "AssetEntries": [entry],
                    }).encode("utf-8")
                    output = arguments["output_directory"]
                    output.mkdir(parents=True)
                    (output / "asset-map.json").write_bytes(content)
                    return {
                        "artifactCount": 1,
                        "artifacts": [{
                            "relativePath": "asset-map.json",
                            "byteCount": len(content),
                            "sha256": hashlib.sha256(content).hexdigest(),
                        }],
                        "entryCount": 1,
                        "includedTypes": list(arguments["included_types"]),
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
                first = handler.ensure_assetbundle_map(record, chunk, emit_errors=False)
                second = handler.ensure_assetbundle_map(record, chunk, emit_errors=False)

            self.assertEqual(first, second)
            self.assertEqual(1, len(calls))
            self.assertEqual(server.ASSETBUNDLE_EXPORT_TYPES, calls[0]["included_types"])
            self.assertEqual("record:81", calls[0]["source_label"])
            self.assertEqual([entry], first["assetEntries"])
            self.assertEqual("asset-map.json", first["assetMapFile"])


if __name__ == "__main__":
    unittest.main()
