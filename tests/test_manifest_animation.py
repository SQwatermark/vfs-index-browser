import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import server


class ManifestAnimationExportTests(unittest.TestCase):
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
            "path": "assets/animations/idle.fbx##idle_loop",
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
        export_root = Path(command[2]) / "AnimationClip"
        export_root.mkdir(parents=True)
        payload = {
            "format": "AnimeStudioAnimationClip",
            "version": "1.0.0",
            "name": "Idle_Loop",
            "timelines": [],
            "curves": [],
        }
        (export_root / "idle_loop.animation.json").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    def test_exports_exact_clip_and_reuses_matching_cache(self):
        with (
            patch.object(server, "INTERNAL_CACHE_DIR", self.root / "cache"),
            patch.object(server, "ANIMESTUDIO_CLI", self.cli),
            patch.object(server.subprocess, "run", side_effect=self.run_export),
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
        self.assertNotIn("--names", self.calls[0])
        self.assertIn("AnimationJSON", self.calls[0])
        self.assertEqual("Idle_Loop", first[0]["name"])
        self.assertEqual(
            ["AnimeStudio.CLI.exe", "AnimeStudio.CLI.dll", "AnimeStudio.dll"],
            [Path(item["path"]).name for item in first[2]["source"]["toolArtifacts"]],
        )

    def test_rejects_ambiguous_export(self):
        def run_ambiguous(command, **_kwargs):
            export_root = Path(command[2]) / "AnimationClip"
            export_root.mkdir(parents=True)
            for name in ("first", "second"):
                (export_root / f"{name}.animation.json").write_text(
                    json.dumps({"name": name}),
                    encoding="utf-8",
                )
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

        with (
            patch.object(server, "INTERNAL_CACHE_DIR", self.root / "cache"),
            patch.object(server, "ANIMESTUDIO_CLI", self.cli),
            patch.object(server.subprocess, "run", side_effect=run_ambiguous),
        ):
            with self.assertRaisesRegex(RuntimeError, "0 match 'idle_loop'"):
                self.handler.ensure_animation_clip_export(
                    self.record,
                    self.chunk,
                    self.asset,
                )


if __name__ == "__main__":
    unittest.main()
