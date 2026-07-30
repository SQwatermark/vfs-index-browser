import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import server


class ManifestCubemapExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.cli = self.root / "AnimeStudio.CLI.exe"
        self.cli.write_bytes(b"exe")
        self.cli.with_suffix(".dll").write_bytes(b"cli")
        (self.root / "AnimeStudio.dll").write_bytes(b"core")
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
            "path": "assets/example/character-light.exr",
        }
        self.handler = object.__new__(server.BrowserHandler)

        def write_file_slice(_record, _chunk_path, target):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"bundle")

        self.handler.write_file_slice = write_file_slice
        self.calls = []

    def tearDown(self):
        self.temporary.cleanup()

    def run_export(self, command, **_kwargs):
        self.calls.append(command)
        export_root = Path(command[2]) / "Cubemap"
        export_root.mkdir(parents=True)
        for face_name in server.CUBEMAP_FACE_NAMES:
            (export_root / f"character-light_{face_name}.png").write_bytes(face_name.encode())
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    def test_exports_all_faces_and_reuses_matching_cache(self):
        with (
            patch.object(server, "INTERNAL_CACHE_DIR", self.root / "cache"),
            patch.object(server, "ANIMESTUDIO_CUBEMAP_CLI", self.cli),
            patch.object(server.subprocess, "run", side_effect=self.run_export),
        ):
            first = self.handler.ensure_manifest_cubemap_export(
                self.record,
                self.chunk,
                self.asset,
            )
            second = self.handler.ensure_manifest_cubemap_export(
                self.record,
                self.chunk,
                self.asset,
            )

        self.assertIsNotNone(first)
        self.assertEqual(first, second)
        self.assertEqual(1, len(self.calls))
        self.assertIn("^assets/example/character\\-light\\.exr$", self.calls[0])
        self.assertEqual(set(server.CUBEMAP_FACE_NAMES), set(first[0]))
        self.assertEqual(
            ["AnimeStudio.CLI.exe", "AnimeStudio.CLI.dll", "AnimeStudio.dll"],
            [Path(item["path"]).name for item in first[1]["source"]["toolArtifacts"]],
        )

    def test_rebuilds_cache_when_tool_changes(self):
        with (
            patch.object(server, "INTERNAL_CACHE_DIR", self.root / "cache"),
            patch.object(server, "ANIMESTUDIO_CUBEMAP_CLI", self.cli),
            patch.object(server.subprocess, "run", side_effect=self.run_export),
        ):
            self.assertIsNotNone(
                self.handler.ensure_manifest_cubemap_export(
                    self.record,
                    self.chunk,
                    self.asset,
                )
            )
            self.cli.with_suffix(".dll").write_bytes(b"changed")
            self.assertIsNotNone(
                self.handler.ensure_manifest_cubemap_export(
                    self.record,
                    self.chunk,
                    self.asset,
                )
            )

        self.assertEqual(2, len(self.calls))

    def test_rejects_incomplete_export(self):
        def run_incomplete(command, **_kwargs):
            export_root = Path(command[2]) / "Cubemap"
            export_root.mkdir(parents=True)
            (export_root / "character-light_PositiveX.png").write_bytes(b"face")
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

        with (
            patch.object(server, "INTERNAL_CACHE_DIR", self.root / "cache"),
            patch.object(server, "ANIMESTUDIO_CUBEMAP_CLI", self.cli),
            patch.object(server.subprocess, "run", side_effect=run_incomplete),
        ):
            result = self.handler.ensure_manifest_cubemap_export(
                self.record,
                self.chunk,
                self.asset,
            )

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
