import tempfile
import threading
import time
import unittest
from pathlib import Path

from task_registry import BackgroundTaskRegistry, TaskNotFoundError


class BackgroundTaskRegistryTests(unittest.TestCase):
    def wait_terminal(self, registry, task_id):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            snapshot = registry.snapshot(task_id)
            if snapshot["state"] in {"succeeded", "failed", "cancelled"}:
                return snapshot
            time.sleep(0.01)
        self.fail("task did not reach a terminal state")

    def test_success_publishes_result_after_atomic_status_pointer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = BackgroundTaskRegistry(lambda: root)
            created = registry.submit("sample", lambda _cancel: {"value": 7})
            completed = self.wait_terminal(registry, created["taskId"])

            self.assertEqual("succeeded", completed["state"])
            self.assertEqual({"value": 7}, completed["result"])
            self.assertEqual("result.json", completed["resultFile"])

    def test_progress_task_publishes_latest_progress_with_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = BackgroundTaskRegistry(lambda: root)

            def operation(_cancel, report_progress):
                report_progress({"stage": "objects", "completed": 2, "total": 4})
                report_progress({"stage": "textures", "completed": 3, "total": 4})
                return {"value": 9}

            created = registry.submit_with_progress("sample", operation)
            completed = self.wait_terminal(registry, created["taskId"])

            self.assertEqual("succeeded", completed["state"])
            self.assertEqual({"value": 9}, completed["result"])
            self.assertEqual(
                {"stage": "textures", "completed": 3, "total": 4},
                completed["progress"],
            )

    def test_private_artifact_metadata_is_hidden_but_resolvable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "sample.blend"
            artifact.write_bytes(b"blend")
            registry = BackgroundTaskRegistry(lambda: root / "tasks")
            created = registry.submit(
                "sample",
                lambda _cancel: {
                    "kind": "artifact",
                    "_artifactPath": str(artifact),
                    "_artifactName": "model.blend",
                    "_artifactContentType": "application/x-blender",
                },
            )
            completed = self.wait_terminal(registry, created["taskId"])

            self.assertEqual({"kind": "artifact"}, completed["result"])
            self.assertEqual(
                (artifact, "model.blend", "application/x-blender"),
                registry.artifact(created["taskId"]),
            )

    def test_cancelled_task_discards_result_and_reaches_cancelled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = BackgroundTaskRegistry(lambda: root)
            entered = threading.Event()

            def operation(cancel_event):
                entered.set()
                cancel_event.wait(2)
                raise RuntimeError("worker_cancelled")

            created = registry.submit("sample", operation)
            self.assertTrue(entered.wait(1))
            cancelling = registry.cancel(created["taskId"])
            completed = self.wait_terminal(registry, created["taskId"])

            self.assertEqual("cancelling", cancelling["state"])
            self.assertEqual("cancelled", completed["state"])
            self.assertFalse((root / created["taskId"] / "result.json").exists())

    def test_non_terminal_task_from_previous_process_becomes_interrupted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_id = "0" * 32
            task_root = root / task_id
            task_root.mkdir(parents=True)
            (task_root / "status.json").write_text(
                '{"taskId":"' + task_id + '","kind":"sample","state":"running"}',
                encoding="utf-8",
            )
            registry = BackgroundTaskRegistry(lambda: root)

            snapshot = registry.snapshot(task_id)

            self.assertEqual("failed", snapshot["state"])
            self.assertEqual("task_interrupted", snapshot["error"]["code"])

    def test_rejects_invalid_or_unknown_task_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = BackgroundTaskRegistry(lambda: Path(directory))
            for task_id in ("../bad", "0" * 32):
                with self.subTest(task_id=task_id), self.assertRaises(TaskNotFoundError):
                    registry.snapshot(task_id)


if __name__ == "__main__":
    unittest.main()
