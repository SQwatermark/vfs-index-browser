import hashlib
import tempfile
import threading
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

    def test_same_pointer_concurrent_requests_build_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = WorkerRunService()
            entered = threading.Event()
            release = threading.Event()
            calls = []
            results = []

            def invoke(_run, exported, request_id, _cancel):
                calls.append(request_id)
                entered.set()
                self.assertTrue(release.wait(2))
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
                "version": 1,
                "source_identity": {},
                "invoke": invoke,
            }
            first = threading.Thread(target=lambda: results.append(service.ensure(**options)))
            second = threading.Thread(target=lambda: results.append(service.ensure(**options)))
            first.start()
            self.assertTrue(entered.wait(2))
            second.start()
            release.set()
            first.join(2)
            second.join(2)

            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertEqual(1, len(calls))
            self.assertEqual(2, len(results))
            self.assertEqual(results[0][2]["selectedRun"], results[1][2]["selectedRun"])

    def test_different_pointers_can_build_concurrently(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = WorkerRunService()
            both_entered = threading.Event()
            release = threading.Event()
            entered_guard = threading.Lock()
            entered = []
            errors = []

            def invoke(_run, exported, request_id, _cancel):
                with entered_guard:
                    entered.append(request_id)
                    if len(entered) == 2:
                        both_entered.set()
                if not release.wait(2):
                    raise RuntimeError("unrelated publication was serialized")
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

            def run(name):
                try:
                    service.ensure(
                        runs_root=root / name / "runs",
                        meta_path=root / name / "meta.json",
                        request_prefix=name,
                        version=1,
                        source_identity={},
                        invoke=invoke,
                    )
                except Exception as error:
                    errors.append(error)

            threads = [threading.Thread(target=run, args=(name,)) for name in ("a", "b")]
            for thread in threads:
                thread.start()
            concurrent = both_entered.wait(2)
            release.set()
            for thread in threads:
                thread.join(2)

            self.assertTrue(concurrent)
            self.assertEqual([], errors)
            self.assertTrue(all(not thread.is_alive() for thread in threads))


if __name__ == "__main__":
    unittest.main()
