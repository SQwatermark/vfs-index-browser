import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen

import server
from audio_dialog_index import AudioMediaEntry, build_audio_dialog_index
from audio_dialog_store import replace_audio_dialog_language


class QuietBrowserHandler(server.BrowserHandler):
    def log_message(self, _format, *_args):
        pass


class AudioDialogApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.audio_db = Path(self.temp.name) / "audio.sqlite"
        payload = {
            "1": {"path": "story/ready.wav"},
            "2": {"path": "story/missing.wav"},
            "3": {"path": "shared/duplicate.wav"},
            "4": {"path": "shared/duplicate.wav"},
        }
        preliminary = build_audio_dialog_index(payload, "chinese", [])
        media = [
            AudioMediaEntry(
                preliminary[0].record.media_id,
                pck_file_id=99,
                offset=128,
                size=64,
                source="sound",
                language="chinese",
            )
        ]
        with closing(sqlite3.connect(self.audio_db)) as conn:
            replace_audio_dialog_language(
                conn,
                build_audio_dialog_index(payload, "chinese", media),
            )

        self.original_audio_db = server.AUDIO_DIALOG_DB
        server.AUDIO_DIALOG_DB = self.audio_db
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), QuietBrowserHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        server.AUDIO_DIALOG_DB = self.original_audio_db
        self.temp.cleanup()

    def get_json(self, path):
        with urlopen(f"{self.base_url}{path}", timeout=5) as response:
            return response.status, json.load(response)

    def test_lists_logical_directory_with_paging(self):
        status, payload = self.get_json(
            "/api/audio-dialog/list?language=cn&path=story&page=1&pageSize=1"
        )

        self.assertEqual(200, status)
        self.assertEqual("story", payload["path"])
        self.assertEqual(2, payload["page"]["total"])
        self.assertEqual(1, len(payload["files"]))

    def test_preview_exposes_urls_only_for_unique_match(self):
        status, ready = self.get_json(
            "/api/audio-dialog/preview?language=chinese&path=story%2Fready.wav"
        )
        _, missing = self.get_json(
            "/api/audio-dialog/preview?language=chinese&path=story%2Fmissing.wav"
        )

        self.assertEqual(200, status)
        self.assertEqual("ready", ready["status"])
        self.assertIn("format=wav", ready["rawUrl"])
        self.assertEqual("missing", missing["status"])
        self.assertNotIn("rawUrl", missing)

    def test_duplicate_path_requires_dialog_key(self):
        path = quote("shared/duplicate.wav", safe="")
        with self.assertRaises(HTTPError) as caught:
            self.get_json(
                f"/api/audio-dialog/preview?language=chinese&path={path}"
            )
        self.assertEqual(409, caught.exception.code)

        status, payload = self.get_json(
            f"/api/audio-dialog/preview?language=chinese&path={path}&dialogKey=3"
        )
        self.assertEqual(200, status)
        self.assertEqual(3, payload["entry"]["dialog_key"])


if __name__ == "__main__":
    unittest.main()
