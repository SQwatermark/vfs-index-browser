import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from audio_dialog_index import build_audio_dialog_records
from audio_dialog_rebuild import (
    AudioDialogRebuildError,
    rebuild_audio_dialog_index_atomically,
)


class FakePackageService:
    def ensure_index(self, record, _read_range):
        entries = []
        if "stream" in record["file_name"]:
            media_id = build_audio_dialog_records(
                {"1": {"path": "story/line.wav"}},
                "chinese",
            )[0].media_id
            entries.append({
                "id": media_id,
                "offset": 4,
                "size": 8,
                "source": "external",
                "language": "chinese",
            })
        return {
            "identity": {
                "version": 2,
                "recordId": record["id"],
                "length": record["length"],
                "logicalId": record["logical_id"],
                "fileName": record["file_name"],
            },
            "entryCount": len(entries),
            "entries": entries,
        }


class AudioDialogRebuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.vfs = self.root / "vfs.sqlite"
        self.audio = self.root / "audio.sqlite"
        self.wwise = self.root / "missing-wwise.sqlite"
        self.audio.write_bytes(b"old database")
        self._create_vfs()

    def tearDown(self):
        self.temp.cleanup()

    def _create_vfs(self):
        with closing(sqlite3.connect(self.vfs)) as conn:
            conn.executescript(
                """
                CREATE TABLE files (
                    id INTEGER PRIMARY KEY,
                    source TEXT NOT NULL,
                    block_name TEXT NOT NULL,
                    logical_id TEXT NOT NULL,
                    source_logical_id TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    chunk_path TEXT NOT NULL,
                    chunk_exists INTEGER NOT NULL,
                    offset INTEGER NOT NULL,
                    length INTEGER NOT NULL,
                    encrypted INTEGER NOT NULL,
                    iv_seed INTEGER,
                    file_data_md5 TEXT,
                    file_chunk_md5 TEXT
                );
                CREATE TABLE entries (
                    scope TEXT NOT NULL,
                    type TEXT NOT NULL,
                    path TEXT NOT NULL,
                    file_id INTEGER
                );
                """
            )
            records = [
                (1, "Table/Data/TableCfg/AudioDialog.bytes", "Data/TableCfg/AudioDialog.bytes", 32),
                (2, "AudioChinese/Data/Audio/PCK/Windows/Chinese/default_chinese_banks.pck", "Data/Audio/PCK/Windows/Chinese/default_chinese_banks.pck", 100),
                (3, "AudioChinese/Data/Audio/PCK/Windows/Chinese/default_chinese_stream.pck", "Data/Audio/PCK/Windows/Chinese/default_chinese_stream.pck", 200),
            ]
            for file_id, logical_id, file_name, length in records:
                chunk = self.root / f"{file_id}.chk"
                chunk.write_bytes(b"x" * length)
                conn.execute(
                    """
                    INSERT INTO files VALUES (
                        ?, 'Persistent', 'Test', ?, ?, ?, ?, 1, 0, ?, 0,
                        NULL, 'CONTENTMD5', ''
                    )
                    """,
                    (file_id, logical_id, f"Persistent/{logical_id}", file_name, str(chunk), length),
                )
                conn.execute(
                    "INSERT INTO entries VALUES ('effective', 'file', ?, ?)",
                    (logical_id, file_id),
                )
            conn.commit()

    @patch("audio_dialog_rebuild.parse_sparkbuffer")
    def test_builds_installed_language_and_atomically_publishes(self, parse):
        parse.return_value = {"data": {"1": {"path": "story/line.wav"}}}

        result = rebuild_audio_dialog_index_atomically(
            self.vfs,
            self.audio,
            self.wwise,
            FakePackageService(),
            lambda data, _seed: data,
        )

        self.assertEqual("rebuilt", result["status"])
        self.assertEqual(["chinese"], list(result["languages"]))
        self.assertEqual(1, result["languages"]["chinese"]["matchedCount"])
        self.assertEqual(b"old database", (self.root / "audio.previous.sqlite").read_bytes())
        with closing(sqlite3.connect(self.audio)) as conn:
            self.assertEqual(
                [("chinese", "matched")],
                conn.execute(
                    "SELECT language, match_status FROM audio_dialog"
                ).fetchall(),
            )
        self.assertEqual([], list(self.root.glob(".audio-dialog-index-rebuild-*")))

    @patch("audio_dialog_rebuild.parse_sparkbuffer")
    def test_missing_stream_preserves_active_database(self, parse):
        parse.return_value = {"data": {"1": {"path": "story/line.wav"}}}
        (self.root / "3.chk").unlink()

        with self.assertRaisesRegex(AudioDialogRebuildError, "both readable"):
            rebuild_audio_dialog_index_atomically(
                self.vfs,
                self.audio,
                self.wwise,
                FakePackageService(),
                lambda data, _seed: data,
            )

        self.assertEqual(b"old database", self.audio.read_bytes())


if __name__ == "__main__":
    unittest.main()
