import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class FakePreviewWorker:
    def __init__(self):
        self.calls = 0

    def artifact_identity(self):
        return [{"kind": "test-worker", "version": 1}]

    def export_bundle_preview_media(
        self,
        *,
        output_directory,
        included_types,
        **_options,
    ):
        self.calls += 1
        self.included_types = list(included_types)
        payload = b"worker-png"
        relative = "Texture2D/CAB-test/icon_p0000000000000011.png"
        target = output_directory / relative
        target.parent.mkdir(parents=True)
        target.write_bytes(payload)
        return {
            "artifactCount": 1,
            "artifacts": [{
                "relativePath": relative,
                "type": "Texture2D",
                "sourceFile": "CAB-test",
                "pathId": 17,
                "name": "icon",
                "container": "assets/icon.png",
                "byteCount": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }],
            "skippedCount": 0,
            "skipped": [],
            "includedTypes": list(included_types),
        }


class AssetBundleRunPublicationTests(unittest.TestCase):
    def make_handler(self):
        handler = object.__new__(server.BrowserHandler)
        handler.send_json = lambda *_args, **_options: None
        return handler

    def make_record(self, source: Path):
        return {
            "id": 7,
            "length": source.stat().st_size,
            "offset": 0,
            "chunk_path": str(source),
            "logical_id": "fixture/bundle.ab",
        }

    def test_worker_media_is_published_once_and_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "bundle.ab"
            source.write_bytes(b"fixture-bundle")
            worker = FakePreviewWorker()
            map_meta = {
                "selectedRun": "map-1",
                "assetEntries": [{
                    "Type": "Texture2D",
                    "Name": "icon",
                    "PathID": 17,
                    "Container": "assets/icon.png",
                }],
            }
            handler = self.make_handler()
            handler.ensure_assetbundle_map = lambda *_args, **_options: map_meta

            with (
                patch.object(server, "INTERNAL_CACHE_DIR", root / "cache"),
                patch.object(server, "UNITY_WORKER", worker),
            ):
                first_root, first_meta = handler.ensure_assetbundle_export(
                    self.make_record(source), source,
                )
                second_root, second_meta = handler.ensure_assetbundle_export(
                    self.make_record(source), source,
                )

            self.assertEqual(1, worker.calls)
            self.assertEqual(["Texture2D"], worker.included_types)
            self.assertEqual(first_root, second_root)
            self.assertEqual(first_meta["selectedRun"], second_meta["selectedRun"])
            self.assertEqual(
                b"worker-png",
                (first_root / "Texture2D/CAB-test/icon_p0000000000000011.png").read_bytes(),
            )

    def test_remaining_legacy_type_is_hashed_before_run_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "bundle.ab"
            source.write_bytes(b"fixture-bundle")
            legacy_cli = root / "AnimeStudio.CLI.exe"
            legacy_cli.write_bytes(b"test-cli")
            worker = FakePreviewWorker()
            map_meta = {
                "selectedRun": "map-2",
                "assetEntries": [
                    {
                        "Type": "Texture2D",
                        "Name": "icon",
                        "PathID": 17,
                        "Container": "assets/icon.png",
                    },
                    {"Type": "AudioClip", "Name": "voice", "PathID": 23},
                ],
            }
            handler = self.make_handler()
            handler.ensure_assetbundle_map = lambda *_args, **_options: map_meta

            def run_legacy(command, **_options):
                export_root = Path(command[2])
                target = export_root / "AudioClip" / "voice.wav"
                target.parent.mkdir(parents=True)
                target.write_bytes(b"legacy-wav")
                return subprocess.CompletedProcess(command, 0, "ok", "")

            with (
                patch.object(server, "INTERNAL_CACHE_DIR", root / "cache"),
                patch.object(server, "UNITY_WORKER", worker),
                patch.object(server, "ANIMESTUDIO_CLI", legacy_cli),
                patch.object(server.subprocess, "run", run_legacy),
            ):
                export_root, meta = handler.ensure_assetbundle_export(
                    self.make_record(source), source,
                )

            identity = meta["derivedFiles"]["legacy:AudioClip/voice.wav"]
            self.assertEqual(len(b"legacy-wav"), identity["byteCount"])
            self.assertEqual(
                hashlib.sha256(b"legacy-wav").hexdigest(),
                identity["sha256"],
            )
            self.assertEqual(b"legacy-wav", (export_root / "AudioClip/voice.wav").read_bytes())


if __name__ == "__main__":
    unittest.main()
