import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from model_build_session import ModelBuildSession


class ModelBuildSessionTests(unittest.TestCase):
    def test_ordinary_session_owns_paths_progress_steps_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "model_build_session.time.time_ns", return_value=123
        ), patch("model_build_session.uuid.uuid4") as uuid4:
            uuid4.return_value.hex = "abc"
            progress = []
            session = ModelBuildSession(Path(directory), "model", 7, 11, 4, progress=progress.append)
            session.report("cabMap", 1)
            session.add_step("buildCABMap", {"artifactCount": 1})
            meta = session.metadata(version=3, source={"id": 7}, scope="closure", extra=True)

            self.assertEqual("model-7-11-123-abc", session.request_id)
            self.assertEqual(session.run_root / "objects", session.object_root)
            self.assertEqual("model-cab-7-11-123", session.worker_request_id("cab"))
            self.assertEqual([{"stage": "cabMap", "completed": 1, "total": 4}], progress)
            self.assertEqual("buildCABMap", meta["steps"][0]["name"])
            self.assertTrue(meta["extra"])

    def test_avatar_session_requires_and_embeds_lod(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "model_build_session.time.time_ns", return_value=456
        ), patch("model_build_session.uuid.uuid4") as uuid4:
            uuid4.return_value.hex = "def"
            session = ModelBuildSession(Path(directory), "avatar", 8, 12, 5, lod=2)
            self.assertEqual("avatar-8-12-lod2-456-def", session.request_id)
            self.assertEqual("avatar-textures-8-12-lod2-456", session.worker_request_id("textures"))

        with self.assertRaisesRegex(ValueError, "requires LOD"):
            ModelBuildSession(Path("runs"), "avatar", 1, 2, 5)


if __name__ == "__main__":
    unittest.main()
