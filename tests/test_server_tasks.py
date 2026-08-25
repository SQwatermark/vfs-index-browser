import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class ServerTaskTests(unittest.TestCase):
    def test_start_projectile_task_uses_detached_service_and_returns_202(self):
        operations = []
        service_instances = []

        class FakeTasks:
            def submit(self, kind, operation):
                operations.append((kind, operation))
                return {"taskId": "a" * 32, "kind": kind, "state": "pending"}

        handler = object.__new__(server.BrowserHandler)
        handler.db_path = Path("index.sqlite")
        handler.read_json_body = lambda: {"projectileId": "projectile_sample"}
        responses = []
        handler.send_json = lambda payload, **options: responses.append((payload, options))
        handler.send_error_json = lambda status, message: self.fail(f"{status}: {message}")

        def build(service, projectile_id, *, cancel_event=None):
            service_instances.append(service)
            return {"projectileId": projectile_id, "cancelEvent": cancel_event is not None}

        with (
            patch.object(server, "TASKS", FakeTasks()),
            patch.object(server.BrowserHandler, "build_projectile_document", build),
        ):
            handler.handle_start_projectile_task()
            result = operations[0][1](threading.Event())

        self.assertEqual("projectile", operations[0][0])
        self.assertIsNot(handler, service_instances[0])
        self.assertEqual(handler.db_path, service_instances[0].db_path)
        self.assertEqual(
            {"projectileId": "projectile_sample", "cancelEvent": True},
            result,
        )
        self.assertEqual(202, responses[0][1]["status"])
        self.assertEqual("no-store", responses[0][1]["cache_control"])

    def test_task_status_and_cancel_map_registry_states(self):
        class FakeTasks:
            def snapshot(self, task_id):
                return {"taskId": task_id, "state": "running"}

            def cancel(self, task_id):
                return {"taskId": task_id, "state": "cancelling"}

        handler = object.__new__(server.BrowserHandler)
        responses = []
        handler.send_json = lambda payload, **options: responses.append((payload, options))
        handler.send_error_json = lambda status, message: self.fail(f"{status}: {message}")
        query = {"taskId": ["b" * 32]}

        with patch.object(server, "TASKS", FakeTasks()):
            handler.handle_task_status(query)
            handler.handle_cancel_task(query)

        self.assertEqual("running", responses[0][0]["state"])
        self.assertEqual(202, responses[1][1]["status"])


if __name__ == "__main__":
    unittest.main()
