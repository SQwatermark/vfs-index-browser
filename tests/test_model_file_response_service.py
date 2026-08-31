import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from model_file_response_service import ModelFileResponseService


class ModelFileResponseServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.service = ModelFileResponseService(4)

    def test_prepares_glb_name_type_disposition_and_cache(self):
        target = self.root / "cached.glb"
        target.write_bytes(b"abcdefgh")

        prepared = self.service.glb(
            {"path": "assets/actor/sample.prefab"},
            target,
            download=True,
        )

        self.assertEqual("model/gltf-binary", prepared.response.content_type)
        self.assertEqual(
            "attachment; filename*=UTF-8''sample.glb",
            prepared.response.content_disposition,
        )
        self.assertEqual(
            "private, max-age=3600",
            prepared.headers["Cache-Control"],
        )
        self.assertEqual(b"abcdefgh", b"".join(prepared.response.chunks()))

    def test_prepares_blend_as_download_with_skip_diagnostic(self):
        target = self.root / "cached.blend"
        target.write_bytes(b"blend")
        artifact = SimpleNamespace(
            path=target,
            name="sample.blend",
            skipped_animation_count=3,
        )

        prepared = self.service.blend(artifact)

        self.assertEqual("application/x-blender", prepared.response.content_type)
        self.assertEqual(
            "attachment; filename*=UTF-8''sample.blend",
            prepared.response.content_disposition,
        )
        self.assertEqual(
            "3",
            prepared.headers["X-Endfield-Skipped-Animation-Count"],
        )


if __name__ == "__main__":
    unittest.main()
