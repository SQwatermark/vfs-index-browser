import hashlib
import tempfile
import unittest
from pathlib import Path

from worker_run_service import WorkerRunService


class WorkerRunServiceTests(unittest.TestCase):
    def test_publishes_and_reuses_verified_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = WorkerRunService()
            calls = []

            def invoke(_run, exported, request_id, _cancel):
                calls.append(request_id)
                target = exported / "item.bin"
                target.parent.mkdir(parents=True)
                target.write_bytes(b"artifact")
                return {
                    "artifactCount": 1,
                    "artifacts": [{
                        "relativePath": "item.bin",
                        "byteCount": 8,
                        "sha256": hashlib.sha256(b"artifact").hexdigest(),
                    }],
                }

            options = {
                "runs_root": root / "runs",
                "meta_path": root / "meta.json",
                "request_prefix": "fixture",
                "version": 2,
                "source_identity": {"source": "a"},
                "invoke": invoke,
            }
            first = service.ensure(**options)
            second = service.ensure(**options)

            self.assertEqual(1, len(calls))
            self.assertEqual(first[0], second[0])
            self.assertEqual(first[2]["selectedRun"], second[2]["selectedRun"])

    def test_failed_run_is_removed_without_publishing_pointer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def fail(run, _exported, _request_id, _cancel):
                run.mkdir(parents=True)
                (run / "partial.bin").write_bytes(b"partial")
                raise RuntimeError("worker failed")

            with self.assertRaisesRegex(RuntimeError, "worker failed"):
                WorkerRunService().ensure(
                    runs_root=root / "runs",
                    meta_path=root / "meta.json",
                    request_prefix="fixture",
                    version=1,
                    source_identity={},
                    invoke=fail,
                )

            self.assertFalse((root / "meta.json").exists())
            self.assertEqual([], list((root / "runs").glob("*")))

    def test_failed_rebuild_preserves_previous_published_pointer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = WorkerRunService()

            def publish(_run, exported, _request_id, _cancel):
                target = exported / "item.bin"
                target.parent.mkdir(parents=True)
                target.write_bytes(b"published")
                return {
                    "artifactCount": 1,
                    "artifacts": [{
                        "relativePath": "item.bin",
                        "byteCount": 9,
                        "sha256": hashlib.sha256(b"published").hexdigest(),
                    }],
                }

            common = {
                "runs_root": root / "runs",
                "meta_path": root / "meta.json",
                "request_prefix": "fixture",
                "version": 1,
            }
            published = service.ensure(
                **common,
                source_identity={"source": "old"},
                invoke=publish,
            )
            pointer_before = (root / "meta.json").read_bytes()

            def fail(run, _exported, _request_id, _cancel):
                run.mkdir(parents=True)
                (run / "partial.bin").write_bytes(b"partial")
                raise RuntimeError("worker failed")

            with self.assertRaisesRegex(RuntimeError, "worker failed"):
                service.ensure(
                    **common,
                    source_identity={"source": "new"},
                    invoke=fail,
                )

            self.assertEqual(pointer_before, (root / "meta.json").read_bytes())
            self.assertTrue(published[0].is_dir())
            self.assertEqual(
                [published[2]["selectedRun"]],
                [path.name for path in (root / "runs").iterdir()],
            )

    def test_rejects_worker_path_escape_and_cleans_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def escape(run, exported, _request_id, _cancel):
                exported.mkdir(parents=True)
                (run / "outside.bin").write_bytes(b"bad")
                return {
                    "artifactCount": 1,
                    "artifacts": [{
                        "relativePath": "../outside.bin",
                        "byteCount": 3,
                        "sha256": hashlib.sha256(b"bad").hexdigest(),
                    }],
                }

            with self.assertRaisesRegex(RuntimeError, "invalid"):
                WorkerRunService().ensure(
                    runs_root=root / "runs",
                    meta_path=root / "meta.json",
                    request_prefix="fixture",
                    version=1,
                    source_identity={},
                    invoke=escape,
                )
            self.assertEqual([], list((root / "runs").glob("*")))


if __name__ == "__main__":
    unittest.main()
