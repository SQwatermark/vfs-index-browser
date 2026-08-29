import io
import json
import unittest

from service_logging import configure_service_logging


class ServiceLoggingTests(unittest.TestCase):
    def test_json_logs_are_machine_readable_and_keep_event_fields(self):
        output = io.StringIO()
        logger = configure_service_logging("INFO", "json", stream=output)

        logger.info("server_started", extra={"host": "127.0.0.1", "port": 8765})

        payload = json.loads(output.getvalue())
        self.assertEqual("INFO", payload["level"])
        self.assertEqual("server_started", payload["event"])
        self.assertEqual("127.0.0.1", payload["host"])
        self.assertEqual(8765, payload["port"])
        self.assertTrue(payload["timestamp"].endswith("+00:00"))

    def test_level_filters_lower_priority_events(self):
        output = io.StringIO()
        logger = configure_service_logging("WARNING", "json", stream=output)

        logger.info("ignored")
        logger.warning("visible")

        self.assertEqual("visible", json.loads(output.getvalue())["event"])


if __name__ == "__main__":
    unittest.main()
