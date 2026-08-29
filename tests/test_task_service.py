import json
import unittest
from pathlib import Path

from task_registry import TaskNotFoundError
from task_service import TaskApplicationService


class TaskApplicationServiceTests(unittest.TestCase):
    def test_maps_status_cancel_and_artifact_results(self):
        class Registry:
            def snapshot(self, task_id):
                return {"taskId": task_id, "state": "running"}

            def cancel(self, task_id):
                return {"taskId": task_id, "state": "cancelling"}

            def artifact(self, _task_id):
                return Path("model.blend"), "角色.blend", "application/x-blender"

        service = TaskApplicationService(Registry())

        self.assertEqual("running", service.snapshot("a" * 32)["state"])
        cancellation = service.cancel("a" * 32)
        self.assertEqual(202, cancellation.http_status)
        self.assertEqual("cancelling", cancellation.snapshot["state"])
        artifact = service.artifact("a" * 32)
        self.assertEqual(Path("model.blend"), artifact.path)
        self.assertEqual("角色.blend", artifact.name)

    def test_terminal_cancel_uses_success_status(self):
        class Registry:
            def cancel(self, task_id):
                return {"taskId": task_id, "state": "succeeded"}

        result = TaskApplicationService(Registry()).cancel("b" * 32)

        self.assertEqual(200, result.http_status)

    def test_storage_failures_become_task_not_found(self):
        class Registry:
            def snapshot(self, _task_id):
                raise json.JSONDecodeError("bad status", "{", 0)

            def cancel(self, _task_id):
                raise OSError("missing task directory")

            def artifact(self, task_id):
                raise TaskNotFoundError(task_id)

        service = TaskApplicationService(Registry())

        for operation in (service.snapshot, service.cancel, service.artifact):
            with self.assertRaises(TaskNotFoundError):
                operation("c" * 32)
