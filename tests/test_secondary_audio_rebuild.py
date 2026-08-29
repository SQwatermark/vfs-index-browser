import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from secondary_audio_rebuild import (
    SecondaryAudioRebuildError,
    rebuild_wwise_index_atomically,
)


class SecondaryAudioRebuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.vfs = self.root / "vfs.sqlite"
        self.audio = self.root / "missing-audio.sqlite"
        self.wwise = self.root / "wwise.sqlite"
        self.chunk = self.root / "package.chk"
        self.chunk.write_bytes(b"PCK")
        with closing(sqlite3.connect(self.vfs)) as conn:
            conn.execute(
                """
                CREATE TABLE files (
                    id INTEGER PRIMARY KEY,
                    source TEXT NOT NULL,
                    logical_id TEXT NOT NULL,
                    length INTEGER NOT NULL,
                    chunk_path TEXT NOT NULL
                    ,file_data_md5 TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO files VALUES (9, 'Persistent', 'Audio/a.pck', 1000, ?, 'PCKMD5')",
                (str(self.chunk),),
            )
            conn.commit()
        self.wwise.write_bytes(b"old database")

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _write_candidate(path: Path, *, size: int = 1000):
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("CREATE TABLE wwise_index_meta (key TEXT, value TEXT)")
            conn.execute("INSERT INTO wwise_index_meta VALUES ('schema_version', '3')")
            conn.execute(
                """
                CREATE TABLE wwise_packages (
                    pck_file_id INTEGER,
                    logical_path TEXT,
                    file_size INTEGER,
                    file_data_md5 TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO wwise_packages VALUES (7, 'Audio/a.pck', ?, 'pckmd5')",
                (size,),
            )
            conn.commit()

    def test_validates_candidate_backs_up_old_and_atomically_publishes(self):
        def runner(command, **_options):
            candidate = Path(command[command.index("--output") + 1])
            self._write_candidate(candidate)
            return subprocess.CompletedProcess(command, 0, "built", "")

        result = rebuild_wwise_index_atomically(
            self.vfs,
            self.audio,
            self.wwise,
            self.root,
            runner=runner,
        )

        self.assertEqual("rebuilt", result["status"])
        self.assertEqual(1, result["resolvedPackageCount"])
        self.assertEqual(b"old database", (self.root / "wwise.previous.sqlite").read_bytes())
        with closing(sqlite3.connect(self.wwise)) as conn:
            self.assertEqual(1, conn.execute("SELECT COUNT(1) FROM wwise_packages").fetchone()[0])
        self.assertEqual([], list(self.root.glob(".wwise-index-rebuild-*")))

    def test_builder_failure_preserves_active_database(self):
        def runner(command, **_options):
            return subprocess.CompletedProcess(command, 2, "", "parse failed")

        with self.assertRaisesRegex(SecondaryAudioRebuildError, "parse failed"):
            rebuild_wwise_index_atomically(
                self.vfs,
                self.audio,
                self.wwise,
                self.root,
                runner=runner,
            )

        self.assertEqual(b"old database", self.wwise.read_bytes())

    def test_stale_candidate_preserves_active_database(self):
        def runner(command, **_options):
            candidate = Path(command[command.index("--output") + 1])
            self._write_candidate(candidate, size=999)
            return subprocess.CompletedProcess(command, 0, "built", "")

        with self.assertRaisesRegex(SecondaryAudioRebuildError, "freshness"):
            rebuild_wwise_index_atomically(
                self.vfs,
                self.audio,
                self.wwise,
                self.root,
                runner=runner,
            )

        self.assertEqual(b"old database", self.wwise.read_bytes())


if __name__ == "__main__":
    unittest.main()
