import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from urllib.request import urlopen

import server
from audio_package import AudioPackageBank, AudioPackageIndex, AudioPackageMedia
from tests.test_wwise_hirc import EVENT, MEDIA, bank_fixture
from wwise_hirc import parse_soundbank
from wwise_store import replace_wwise_package


class QuietBrowserHandler(server.BrowserHandler):
    def log_message(self, _format, *_args):
        pass


class WwiseApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.wwise_db = Path(self.temp.name) / "wwise.sqlite"
        graph = parse_soundbank(EVENT, bank_fixture())
        package = AudioPackageIndex(
            file_size=4096,
            banks=(AudioPackageBank(EVENT, 100, 500, "sfx", False, graph),),
            media=tuple(
                AudioPackageMedia(media_id, 1000 + index * 100, 64, "sound", "sfx")
                for index, media_id in enumerate(MEDIA)
            ),
        )
        with closing(sqlite3.connect(self.wwise_db)) as conn:
            replace_wwise_package(
                conn,
                99,
                package,
                logical_path="Audio/test.pck",
            )

        self.original_wwise_db = server.WWISE_DB
        self.original_index_freshness_report = server.INDEX_FRESHNESS_REPORT
        server.WWISE_DB = self.wwise_db
        server.INDEX_FRESHNESS_REPORT = {"status": "current"}
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), QuietBrowserHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        server.WWISE_DB = self.original_wwise_db
        server.INDEX_FRESHNESS_REPORT = self.original_index_freshness_report
        self.temp.cleanup()

    def get_json(self, path):
        with urlopen(f"{self.base_url}{path}", timeout=5) as response:
            return response.status, json.load(response)

    def test_lists_virtual_roots_and_events(self):
        status, root = self.get_json("/api/wwise/list?path=")
        _, events = self.get_json("/api/wwise/list?path=Events&page=1&pageSize=10")

        self.assertEqual(200, status)
        self.assertEqual(["Events", "Banks", "Media"], [item["name"] for item in root["dirs"]])
        self.assertEqual(1, events["page"]["total"])
        self.assertEqual("wwiseEvent", events["files"][0]["virtualKind"])

    def test_event_preview_exposes_relation_graph_and_media_urls(self):
        status, payload = self.get_json(
            f"/api/wwise/preview?kind=event&pckFileId=99&bankId={EVENT}&eventId={EVENT}"
        )

        self.assertEqual(200, status)
        self.assertEqual("wwiseEvent", payload["kind"])
        self.assertEqual(list(MEDIA), payload["event"]["media_ids"])
        self.assertEqual(3, len(payload["event"]["media"]))
        self.assertIn("/api/wwise/raw", payload["event"]["media"][0]["rawUrl"])

    def test_lists_banks_and_media_and_previews_media(self):
        _, banks = self.get_json("/api/wwise/list?path=Banks")
        _, media_root = self.get_json(
            "/api/wwise/list?path=%5CMedia%5C&page=0&pageSize=999999"
        )
        _, media = self.get_json(
            f"/api/wwise/list?path={media_root['dirs'][0]['path']}"
        )
        _, preview = self.get_json(media["files"][0]["previewUrl"])

        self.assertEqual("wwiseBank", banks["files"][0]["virtualKind"])
        self.assertEqual("Media", media_root["path"])
        self.assertEqual(1, media_root["page"]["page"])
        self.assertEqual(server.PAGE_SIZE_MAX, media_root["page"]["pageSize"])
        self.assertEqual("wwiseMedia", preview["kind"])
        self.assertIn("format=wav", preview["rawUrl"])
        self.assertIn("download=1", preview["wemDownloadUrl"])


if __name__ == "__main__":
    unittest.main()
