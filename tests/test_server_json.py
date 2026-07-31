import gzip
import json
import unittest
from io import BytesIO

from server import BrowserHandler


class JsonResponseTests(unittest.TestCase):
    def test_compresses_json_when_client_accepts_gzip(self):
        handler = object.__new__(BrowserHandler)
        handler.headers = {"Accept-Encoding": "br, gzip"}
        handler.wfile = BytesIO()
        response = {}
        headers = {}
        handler.send_response = lambda status: response.update(status=status)
        handler.send_header = lambda name, value: headers.update({name: value})
        handler.end_headers = lambda: None

        handler.send_json(
            {"tracks": [[1, 2, 3]] * 100},
            cache_control="private, max-age=3600",
            compress=True,
        )

        body = handler.wfile.getvalue()
        self.assertEqual(200, response["status"])
        self.assertEqual("gzip", headers["Content-Encoding"])
        self.assertEqual("Accept-Encoding", headers["Vary"])
        self.assertEqual("private, max-age=3600", headers["Cache-Control"])
        self.assertEqual(len(body), int(headers["Content-Length"]))
        self.assertEqual(
            {"tracks": [[1, 2, 3]] * 100},
            json.loads(gzip.decompress(body)),
        )


if __name__ == "__main__":
    unittest.main()
