import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from blender_export import BlenderExportService


class BlenderExportServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.executable = self.root / "blender.exe"
        self.importer = self.root / "importer.py"
        self.backend = self.root / "backend.py"
        self.glb = self.root / "model.glb"
        for path in (self.executable, self.importer, self.backend, self.glb):
            path.write_bytes(b"source")
        self.service = BlenderExportService(
            self.executable,
            self.root,
            self.importer,
            (self.backend,),
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_publishes_blend_only_after_successful_export(self):
        def run(command, **options):
            self.assertEqual(self.root, options["cwd"])
            Path(command[-1]).write_bytes(b"blend")
            return subprocess.CompletedProcess(command, 0, stdout="done")

        with patch("blender_export.subprocess.run", side_effect=run):
            result = self.service.ensure_model_blend(self.glb)

        self.assertEqual(self.glb.with_suffix(".blend"), result)
        self.assertEqual(b"blend", result.read_bytes())
        self.assertFalse((self.root / "model.tmp.blend").exists())

    def test_reuses_a_blend_newer_than_all_inputs(self):
        blend = self.glb.with_suffix(".blend")
        blend.write_bytes(b"cached")
        newest = max(path.stat().st_mtime_ns for path in (self.glb, self.importer, self.backend))
        blend.touch()
        self.assertGreaterEqual(blend.stat().st_mtime_ns, newest)

        with patch("blender_export.subprocess.run") as run:
            result = self.service.ensure_model_blend(self.glb)

        self.assertEqual(blend, result)
        run.assert_not_called()

    def test_cancellation_before_launch_does_not_publish_or_start_blender(self):
        cancelled = threading.Event()
        cancelled.set()

        with patch("blender_export.subprocess.Popen") as popen:
            with self.assertRaisesRegex(RuntimeError, "worker_cancelled"):
                self.service.ensure_model_blend(self.glb, cancel_event=cancelled)

        popen.assert_not_called()
        self.assertFalse(self.glb.with_suffix(".blend").exists())

    def test_missing_temporary_output_is_an_explicit_failure(self):
        completed = subprocess.CompletedProcess([], 0, stdout="no output")
        with patch("blender_export.subprocess.run", return_value=completed):
            with self.assertRaisesRegex(
                RuntimeError,
                "without producing a file",
            ):
                self.service.ensure_model_blend(self.glb)

        self.assertFalse(self.glb.with_suffix(".blend").exists())


if __name__ == "__main__":
    unittest.main()
