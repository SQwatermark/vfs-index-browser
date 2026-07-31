import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class ManifestModelBlendTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.blender = self.root / "blender.exe"
        self.importer = self.root / "blender_import_model.py"
        self.backend = self.root / "blender_materials.py"
        self.lighting = self.root / "character_lighting.py"
        for path in (self.blender, self.importer, self.backend, self.lighting):
            path.write_bytes(b"tool")

    def tearDown(self):
        self.temporary.cleanup()

    def make_handler(self, asset_path):
        model_root = self.root / "model"
        model_root.mkdir(exist_ok=True)
        model_document = model_root / "model-document.json"
        glb = model_root / "model.glb"
        model_document.write_text("{}", encoding="utf-8")
        glb.write_bytes(b"glb")

        handler = object.__new__(server.BrowserHandler)
        handler.wfile = io.BytesIO()
        handler.resolve_manifest_asset_source = lambda _query: (
            object(),
            {"asset_index": 7, "path": asset_path},
            {},
            self.root / "source.chk",
        )
        lods = []

        def ensure_model(resolved, *, lod=0):
            lods.append(lod)
            return resolved[1], model_document, glb

        handler.ensure_manifest_asset_model_glb = ensure_model
        handler.send_response = lambda _status: None
        handler.send_header = lambda _name, _value: None
        handler.end_headers = lambda: None
        handler.send_error_json = lambda status, message: self.fail(
            f"unexpected HTTP {status}: {message}"
        )
        return handler, model_root, lods

    def run_blender(self, command, **_kwargs):
        Path(command[-1]).write_bytes(b"BLENDER-v404")

    def test_exports_avatar_mesh_with_requested_lod(self):
        handler, model_root, lods = self.make_handler(
            "assets/beyond/dynamicassets/gameplay/npc/avatarmesh/actor/"
            "data_npc_avatarmesh_qinjc.asset"
        )
        with (
            patch.object(server, "BLENDER_EXE", self.blender),
            patch.object(server, "BLENDER_MODEL_IMPORTER", self.importer),
            patch.object(server, "PROJECT_ROOT", self.root),
            patch.object(server.subprocess, "run", side_effect=self.run_blender),
        ):
            handler.handle_manifest_asset_model_blend({"lod": ["2"]})

        self.assertEqual([2], lods)
        self.assertEqual(b"BLENDER-v404", handler.wfile.getvalue())
        self.assertEqual(b"BLENDER-v404", (model_root / "model.blend").read_bytes())

    def test_rejects_non_model_asset(self):
        handler, _model_root, _lods = self.make_handler(
            "assets/beyond/arts/effects/commonassets/vat/softbody/sample.exr"
        )
        errors = []
        handler.send_error_json = lambda status, message: errors.append((status, message))
        with patch.object(server, "BLENDER_EXE", self.blender):
            handler.handle_manifest_asset_model_blend({})

        self.assertEqual([(400, "resource is not a supported model entry")], errors)


if __name__ == "__main__":
    unittest.main()
