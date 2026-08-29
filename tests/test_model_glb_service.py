import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from model_glb_service import ModelGlbService


class ModelGlbServiceTests(unittest.TestCase):
    avatar_path = (
        "assets/beyond/dynamicassets/gameplay/npc/avatarmesh/actor/"
        "data_npc_avatarmesh_sample.asset"
    )

    def create_run(self, root: Path, uri: str) -> Path:
        run = root / "avatar-run"
        (run / "textures").mkdir(parents=True)
        (run / "textures" / "body.png").write_bytes(b"png")
        model = run / "model.json"
        model.write_text(
            json.dumps({"images": [{"id": "body", "uri": uri}]}),
            encoding="utf-8",
        )
        (run / "geometry.bin").write_bytes(b"geometry")
        return model

    def service(self, builds: list) -> ModelGlbService:
        def build(document, geometry, image_loader, material_plans):
            builds.append((document, geometry, image_loader(document["images"][0]), material_plans))
            return b"glb"

        return ModelGlbService(
            build,
            lambda *_args: {"material": 1},
            lambda: {"revision": 2},
            version=4,
            exporter_path=Path(__file__),
            shader_archive_root=Path(__file__).parent / "missing-shaders",
            character_shader_path=Path("character.shader"),
        )

    def test_validates_inputs_and_reuses_matching_glb(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            uri = (
                "/api/manifest-asset/model-texture?recordId=7&assetIndex=11"
                "&lod=2&run=avatar-run&path=body.png"
            )
            model = self.create_run(root, uri)
            builds = []
            service = self.service(builds)

            document, geometry, images = service.load_inputs(
                {"path": self.avatar_path, "asset_index": 11},
                {"id": 7},
                model,
                lod=2,
            )
            first = service.ensure(
                {"path": self.avatar_path, "asset_index": 11},
                {"id": 7},
                model,
                lod=2,
            )
            second = service.ensure(
                {"path": self.avatar_path, "asset_index": 11},
                {"id": 7},
                model,
                lod=2,
            )

            self.assertEqual(b"geometry", geometry)
            self.assertEqual(model.parent / "textures" / "body.png", images["body"])
            self.assertEqual(first, second)
            self.assertEqual(b"glb", first.read_bytes())
            self.assertEqual(1, len(builds))
            self.assertEqual(b"png", builds[0][2])
            self.assertEqual(
                {"version": 4, "materialPlan": {"revision": 2}},
                json.loads(first.with_suffix(".glb.meta.json").read_text(encoding="utf-8")),
            )

    def test_rejects_texture_identity_mismatch_and_escape(self):
        cases = [
            (
                "/api/manifest-asset/model-texture?recordId=8&assetIndex=11"
                "&lod=2&run=avatar-run&path=body.png",
                "recordId",
            ),
            (
                "/api/manifest-asset/model-texture?recordId=7&assetIndex=11"
                "&lod=1&run=avatar-run&path=body.png",
                "LOD",
            ),
            (
                "/api/manifest-asset/model-texture?recordId=7&assetIndex=11"
                "&lod=2&run=avatar-run&path=../body.png",
                "texture not found",
            ),
        ]
        for uri, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as directory:
                model = self.create_run(Path(directory), uri)
                with self.assertRaisesRegex((ValueError, FileNotFoundError), message):
                    self.service([]).load_inputs(
                        {"path": self.avatar_path, "asset_index": 11},
                        {"id": 7},
                        model,
                        lod=2,
                    )

    def test_cancellation_prevents_rebuild(self):
        with tempfile.TemporaryDirectory() as directory:
            model = self.create_run(
                Path(directory),
                "/api/manifest-asset/model-texture?recordId=7&assetIndex=11"
                "&lod=2&run=avatar-run&path=body.png",
            )
            with self.assertRaisesRegex(RuntimeError, "worker_cancelled"):
                self.service([]).ensure(
                    {"path": self.avatar_path, "asset_index": 11},
                    {"id": 7},
                    model,
                    lod=2,
                    cancel_event=SimpleNamespace(is_set=lambda: True),
                )


if __name__ == "__main__":
    unittest.main()
