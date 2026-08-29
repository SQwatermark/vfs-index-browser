import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from secondary_audio_freshness import inspect_secondary_audio_indexes


class SecondaryAudioFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.vfs = self.root / "vfs.sqlite"
        self.audio = self.root / "audio.sqlite"
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
                    chunk_path TEXT NOT NULL,
                    file_data_md5 TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO files VALUES (99, 'Persistent', 'Audio/a.pck', 1000, ?, 'PCKMD5')",
                (str(self.chunk),),
            )
            conn.execute(
                "INSERT INTO files VALUES (100, 'Persistent', 'Table/AudioDialog.bytes', 50, ?, 'TABLEMD5')",
                (str(self.chunk),),
            )
            conn.commit()
        self._write_audio("Audio/a.pck", 1000)
        self._write_wwise("Audio/a.pck", 1000)

    def tearDown(self):
        self.temp.cleanup()

    def _write_audio(self, path, size):
        with closing(sqlite3.connect(self.audio)) as conn:
            conn.execute("CREATE TABLE audio_index_meta (key TEXT, value TEXT)")
            conn.executemany(
                "INSERT INTO audio_index_meta VALUES (?, ?)",
                [
                    ("schema_version", "3"),
                    ("tablecfg_logical_path", "Table/AudioDialog.bytes"),
                    ("tablecfg_file_size", "50"),
                    ("tablecfg_file_data_md5", "tablemd5"),
                    ("input_packages_json", '[{"logicalPath":"Audio/a.pck","fileSize":1000,"fileDataMd5":"pckmd5"}]'),
                ],
            )
            conn.execute(
                """
                CREATE TABLE audio_media (
                    pck_file_id INTEGER,
                    pck_logical_path TEXT,
                    pck_file_size INTEGER
                )
                """
            )
            conn.execute("INSERT INTO audio_media VALUES (7, ?, ?)", (path, size))
            conn.commit()

    def _write_wwise(self, path, size):
        with closing(sqlite3.connect(self.wwise)) as conn:
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
                "INSERT INTO wwise_packages VALUES (8, ?, ?, 'pckmd5')",
                (path, size),
            )
            conn.commit()

    def test_accepts_id_reordering_when_stable_path_and_size_match(self):
        result = inspect_secondary_audio_indexes(self.vfs, self.audio, self.wwise)

        self.assertEqual("current", result["status"])
        self.assertEqual(1, result["audioDialog"]["resolvedPackageCount"])
        self.assertEqual(1, result["wwise"]["resolvedPackageCount"])

    def test_reports_missing_identity_and_changed_package(self):
        with closing(sqlite3.connect(self.audio)) as conn:
            conn.execute(
                "UPDATE audio_media SET pck_logical_path = NULL, pck_file_size = NULL"
            )
            conn.commit()
        with closing(sqlite3.connect(self.wwise)) as conn:
            conn.execute("UPDATE wwise_packages SET file_size = 999")
            conn.commit()

        result = inspect_secondary_audio_indexes(self.vfs, self.audio, self.wwise)

        self.assertEqual("stale", result["status"])
        self.assertIn("no stable PCK identity", result["audioDialog"]["issues"][0])
        self.assertIn("PCK size changed", result["wwise"]["issues"][0])

    def test_reports_missing_secondary_database(self):
        self.audio.unlink()

        result = inspect_secondary_audio_indexes(self.vfs, self.audio, self.wwise)

        self.assertEqual("unavailable", result["status"])
        self.assertEqual("unavailable", result["audioDialog"]["status"])

    def test_reports_changed_audio_dialog_tablecfg(self):
        with closing(sqlite3.connect(self.vfs)) as conn:
            conn.execute(
                "UPDATE files SET file_data_md5 = 'NEWMD5' WHERE logical_id = 'Table/AudioDialog.bytes'"
            )
            conn.commit()

        result = inspect_secondary_audio_indexes(self.vfs, self.audio, self.wwise)

        self.assertEqual("stale", result["audioDialog"]["status"])
        self.assertIn("TableCfg content changed", result["audioDialog"]["issues"][0])

    def test_reports_same_size_changed_wwise_package(self):
        with closing(sqlite3.connect(self.vfs)) as conn:
            conn.execute(
                "UPDATE files SET file_data_md5 = 'NEWMD5' WHERE logical_id = 'Audio/a.pck'"
            )
            conn.commit()

        result = inspect_secondary_audio_indexes(self.vfs, self.audio, self.wwise)

        self.assertEqual("stale", result["wwise"]["status"])
        self.assertIn("PCK content changed", result["wwise"]["issues"][0])


if __name__ == "__main__":
    unittest.main()
