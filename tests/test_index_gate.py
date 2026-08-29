import unittest
from unittest.mock import patch

import server


class IndexGateTests(unittest.TestCase):
    def make_handler(self):
        handler = object.__new__(server.BrowserHandler)
        responses = []
        handler.send_json = lambda payload, **options: responses.append((payload, options))
        return handler, responses

    def test_stale_index_returns_structured_retryable_service_error(self):
        handler, responses = self.make_handler()
        freshness = {
            "status": "stale",
            "reason": "expected_chunks_missing",
            "missingChunkCount": 2,
        }
        with (
            patch.object(server, "INDEX_FRESHNESS_REPORT", freshness),
            patch.object(server, "INDEX_REBUILD_REPORT", {"status": "failed"}),
        ):
            allowed = handler.require_current_index()

        self.assertFalse(allowed)
        payload, options = responses[0]
        self.assertEqual("index_stale", payload["code"])
        self.assertEqual(freshness, payload["indexFreshness"])
        self.assertEqual(503, options["status"])
        self.assertEqual("no-store", options["cache_control"])

    def test_current_index_allows_request_without_response(self):
        handler, responses = self.make_handler()
        with patch.object(server, "INDEX_FRESHNESS_REPORT", {"status": "current"}):
            self.assertTrue(handler.require_current_index())
        self.assertEqual([], responses)

    def test_health_and_task_observation_bypass_index_gate(self):
        for path, method_name in (
            ("/api/health", "handle_health"),
            ("/api/task?taskId=1", "handle_task_status"),
            ("/api/task-artifact?taskId=1", "handle_task_artifact"),
        ):
            with self.subTest(path=path):
                handler, responses = self.make_handler()
                handler.path = path
                called = []
                setattr(handler, method_name, lambda *_args: called.append(True))
                with patch.object(server, "INDEX_FRESHNESS_REPORT", {"status": "stale"}):
                    handler.do_GET()
                self.assertEqual([True], called)
                self.assertEqual([], responses)

    def test_data_get_and_task_creation_are_blocked_before_handler(self):
        handler, responses = self.make_handler()
        handler.path = "/api/manifest"
        handler.handle_manifest = lambda: self.fail("data handler should not run")
        with patch.object(server, "INDEX_FRESHNESS_REPORT", {"status": "stale"}):
            handler.do_GET()
        self.assertEqual("index_stale", responses[0][0]["code"])

        handler, responses = self.make_handler()
        handler.path = "/api/tasks/model"
        handler.handle_start_model_task = lambda: self.fail("task handler should not run")
        with patch.object(server, "INDEX_FRESHNESS_REPORT", {"status": "stale"}):
            handler.do_POST()
        self.assertEqual("index_stale", responses[0][0]["code"])


if __name__ == "__main__":
    unittest.main()
