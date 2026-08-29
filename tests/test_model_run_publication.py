import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


def complete_run(root: Path, name: str, *, geometry: bytes = b"") -> Path:
    run = root / "runs" / name
    run.mkdir(parents=True)
    (run / "run.json").write_text(
        json.dumps({"selectedRun": name}),
        encoding="utf-8",
    )
    if geometry:
        (run / "geometry.bin").write_bytes(geometry)
    return run


class ModelRunPublicationTests(unittest.TestCase):
    def test_resolves_only_completed_runs_from_pointer_or_explicit_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = complete_run(root, "old")
            current = complete_run(root, "current")
            incomplete = root / "runs" / "incomplete"
            incomplete.mkdir(parents=True)
            (root / "run.json").write_text(
                json.dumps({"selectedRun": "current"}),
                encoding="utf-8",
            )

            self.assertEqual(current.resolve(), server.resolve_published_model_run(root))
            self.assertEqual(old.resolve(), server.resolve_published_model_run(root, "old"))
            self.assertIsNone(server.resolve_published_model_run(root, "incomplete"))
            self.assertIsNone(server.resolve_published_model_run(root, "../outside"))

    def test_buffer_and_texture_urls_stay_bound_to_requested_run(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            root = cache / "7" / "models" / "11"
            old = complete_run(root, "old", geometry=b"old-geometry")
            current = complete_run(root, "current", geometry=b"new-geometry")
            old_texture = old / "textures" / "Texture2D" / "CAB-a" / "old.png"
            old_texture.parent.mkdir(parents=True)
            old_texture.write_bytes(b"old-texture")
            (root / "run.json").write_text(
                json.dumps({"selectedRun": "current"}),
                encoding="utf-8",
            )

            handler = object.__new__(server.BrowserHandler)
            handler.wfile = io.BytesIO()
            handler.send_response = lambda _status: None
            handler.send_header = lambda _name, _value: None
            handler.end_headers = lambda: None
            errors = []
            handler.send_error_json = lambda status, message: errors.append((status, message))

            with patch.object(server, "INTERNAL_CACHE_DIR", cache):
                handler.handle_manifest_asset_model_buffer({
                    "recordId": ["7"], "assetIndex": ["11"], "run": ["old"],
                })
                self.assertEqual(b"old-geometry", handler.wfile.getvalue())
                handler.wfile = io.BytesIO()
                handler.handle_manifest_asset_model_texture({
                    "recordId": ["7"],
                    "assetIndex": ["11"],
                    "run": ["old"],
                    "path": ["Texture2D/CAB-a/old.png"],
                })

            self.assertEqual([], errors)
            self.assertEqual(b"old-texture", handler.wfile.getvalue())
            self.assertEqual(b"new-geometry", (current / "geometry.bin").read_bytes())


if __name__ == "__main__":
    unittest.main()
