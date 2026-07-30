import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import server


class ManifestMonoBehaviourDumpTests(unittest.TestCase):
    def test_exports_exact_container_and_reuses_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cli = root / "AnimeStudio.CLI.exe"
            cli.write_bytes(b"")
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

            def run(command, **_kwargs):
                calls.append(command)
                export_root = Path(command[2]) / "MonoBehaviour"
                export_root.mkdir(parents=True)
                (export_root / "Profile.txt").write_text("profile", encoding="utf-8")
                (export_root / "Lighting.txt").write_text("lighting", encoding="utf-8")
                return SimpleNamespace(returncode=0, stdout="ok", stderr="")

            with (
                patch.object(server, "INTERNAL_CACHE_DIR", root / "cache"),
                patch.object(server, "ANIMESTUDIO_MONOBEHAVIOUR_CLI", cli),
                patch.object(server.subprocess, "run", side_effect=run),
            ):
                first = handler.ensure_manifest_monobehaviour_dump(record, chunk, asset)
                second = handler.ensure_manifest_monobehaviour_dump(record, chunk, asset)

            self.assertIsNotNone(first)
            self.assertEqual(first, second)
            self.assertEqual(len(calls), 1)
            self.assertIn("^assets/example/char\\.override\\.asset$", calls[0])
            dump = first[0].read_text(encoding="utf-8")
            self.assertIn("===== MonoBehaviour/Lighting.txt =====\nlighting", dump)
            self.assertIn("===== MonoBehaviour/Profile.txt =====\nprofile", dump)


if __name__ == "__main__":
    unittest.main()
