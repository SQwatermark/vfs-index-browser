import unittest

from request_router import dispatch_get, dispatch_post


class RequestRouterTests(unittest.TestCase):
    def test_health_bypasses_index_gate(self):
        calls = []

        class Handler:
            def require_current_index(self):
                raise AssertionError("health must bypass the index gate")

            def handle_health(self):
                calls.append("health")

        self.assertTrue(dispatch_get(Handler(), "/api/health", {}))
        self.assertEqual(["health"], calls)

    def test_query_route_runs_after_index_gate(self):
        calls = []

        class Handler:
            def require_current_index(self):
                calls.append("gate")
                return True

            def handle_list(self, query):
                calls.append(query)

        query = {"path": ["assets"]}
        self.assertTrue(dispatch_get(Handler(), "/api/list", query))
        self.assertEqual(["gate", query], calls)

    def test_static_path_is_not_consumed(self):
        self.assertFalse(dispatch_get(object(), "/index.html", {}))

    def test_stale_index_consumes_unknown_api_route(self):
        class Handler:
            def require_current_index(self):
                return False

        self.assertTrue(dispatch_get(Handler(), "/api/unknown", {}))

    def test_post_route_and_unknown_route(self):
        calls = []

        class Handler:
            def require_current_index(self):
                return True

            def handle_start_model_task(self):
                calls.append("model")

        handler = Handler()
        self.assertTrue(dispatch_post(handler, "/api/tasks/model"))
        self.assertFalse(dispatch_post(handler, "/not-api"))
        self.assertEqual(["model"], calls)


if __name__ == "__main__":
    unittest.main()
