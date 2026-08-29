import io
import unittest
from contextlib import redirect_stderr

import server


class ServerCliTests(unittest.TestCase):
    def test_explicit_service_and_logging_options_are_parsed(self):
        args = server.parse_args(
            [
                "--host",
                "0.0.0.0",
                "--port",
                "9000",
                "--log-level",
                "debug",
                "--log-format",
                "text",
            ]
        )

        self.assertEqual("0.0.0.0", args.host)
        self.assertEqual(9000, args.port)
        self.assertEqual("debug", args.log_level)
        self.assertEqual("text", args.log_format)

    def test_rejects_out_of_range_port(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                server.parse_args(["--port", "65536"])


if __name__ == "__main__":
    unittest.main()
