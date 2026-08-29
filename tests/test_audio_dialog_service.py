import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from audio_dialog_index import AudioMediaEntry, build_audio_dialog_index
from audio_dialog_service import AudioDialogService
from audio_dialog_store import replace_audio_dialog_language


class AudioDialogServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "audio.sqlite"
        payload = {
            "1": {"path": "story/ready.wav"},
            "2": {"path": "story/missing.wav"},
        }
        preliminary = build_audio_dialog_index(payload, "chinese", [])
        media = AudioMediaEntry(
            preliminary[0].record.media_id,
            pck_file_id=99,
            offset=12,
            size=34,
            source="bank",
            language="chinese",
            bank_id=7,
            bank_offset=100,
            bank_size=200,
            bank_wem_offset=24,
            bank_encrypted=True,
        )
        with closing(sqlite3.connect(self.database)) as conn:
            replace_audio_dialog_language(
                conn, build_audio_dialog_index(payload, "chinese", [media])
            )

    def tearDown(self):
        self.temp.cleanup()

    def service(self, calls, *, source=True):
        return AudioDialogService(
            lambda: sqlite3.connect(self.database),
            lambda pck: (
                calls.append(("source", pck)),
                ({"id": pck}, Path("pck.chk")) if source else None,
            )[1],
            lambda record, chunk, entry, mode, namespace: (
                calls.append(("ensure", record, chunk, entry, mode, namespace)),
                Path(f"cached.{mode}"),
            )[1],
            page_size_max=500,
        )

    def test_lists_previews_and_resolves_bank_media(self):
        calls = []
        service = self.service(calls)

        listing = service.list(
            {"language": ["cn"], "path": ["story"], "pageSize": ["1"]}
        )
        preview = service.preview(
            {"language": ["chinese"], "path": ["story/ready.wav"]}
        )
        artifact = service.media(
            {
                "language": ["chinese"],
                "path": ["story/ready.wav"],
                "format": ["WEM"],
                "download": ["yes"],
            }
        )

        self.assertEqual(2, listing["page"]["total"])
        self.assertEqual(1, listing["page"]["pageSize"])
        self.assertEqual("ready", preview["status"])
        self.assertIn("dialogKey=1", preview["rawUrl"])
        self.assertEqual(Path("cached.wem"), artifact.target)
        self.assertEqual("story/ready.wav", artifact.logical_path)
        self.assertTrue(artifact.download)
        self.assertEqual(("source", 99), calls[0])
        entry = calls[1][3]
        self.assertEqual(24, entry.bank_wem_offset)
        self.assertTrue(entry.bank_encrypted)
        self.assertEqual("audio-dialog", calls[1][-1])

    def test_missing_pck_source_is_explicit(self):
        service = self.service([], source=False)

        with self.assertRaisesRegex(FileNotFoundError, "PCK source is unavailable"):
            service.media(
                {"language": ["chinese"], "path": ["story/ready.wav"]}
            )


if __name__ == "__main__":
    unittest.main()
