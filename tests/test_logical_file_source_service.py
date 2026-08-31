import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from logical_file_source_service import LogicalFileSourceService


class LogicalFileSourceServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "vfs.sqlite"
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executescript(
                """
                CREATE TABLE files (
                    id INTEGER PRIMARY KEY, source TEXT, logical_id TEXT,
                    chunk_path TEXT, chunk_exists INTEGER
                );
                CREATE TABLE entries (
                    scope TEXT, type TEXT, path TEXT, file_id INTEGER
                );
                """
            )
            connection.commit()
        self.service = LogicalFileSourceService(
            self.database,
            lambda source, exists: (
                0 if exists else 1,
                0 if source == "Persistent" else 1,
            ),
        )

    def tearDown(self):
        self.temporary.cleanup()

    def insert(self, rows, *, effective_id=None):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executemany("INSERT INTO files VALUES (?, ?, ?, ?, ?)", rows)
            if effective_id is not None:
                connection.execute(
                    "INSERT INTO entries VALUES ('effective', 'file', 'data.bin', ?)",
                    (effective_id,),
                )
            connection.commit()

    def test_prefers_readable_effective_entry_over_source_rank(self):
        effective = self.root / "streaming.chk"
        preferred_source = self.root / "persistent.chk"
        effective.write_bytes(b"effective")
        preferred_source.write_bytes(b"persistent")
        self.insert(
            [
                (1, "StreamingAssets", "data.bin", str(effective), 1),
                (2, "Persistent", "data.bin", str(preferred_source), 1),
            ],
            effective_id=1,
        )

        record, path = self.service.resolve("data.bin")

        self.assertEqual(1, record["id"])
        self.assertEqual(effective, path)

    def test_falls_back_when_effective_entry_is_unreadable(self):
        fallback = self.root / "persistent.chk"
        fallback.write_bytes(b"fallback")
        self.insert(
            [
                (1, "StreamingAssets", "data.bin", str(self.root / "missing"), 1),
                (2, "Persistent", "data.bin", str(fallback), 1),
            ],
            effective_id=1,
        )

        record, path = self.service.resolve("data.bin")

        self.assertEqual(2, record["id"])
        self.assertEqual(fallback, path)

    def test_returns_none_without_readable_candidate(self):
        self.insert(
            [(1, "Persistent", "data.bin", str(self.root / "missing"), 0)]
        )
        self.assertIsNone(self.service.resolve("data.bin"))

    def test_record_resolution_keeps_readable_original(self):
        original = self.root / "original.chk"
        preferred = self.root / "preferred.chk"
        original.write_bytes(b"original")
        preferred.write_bytes(b"preferred")
        self.insert([
            (1, "StreamingAssets", "data.bin", str(original), 1),
            (2, "Persistent", "data.bin", str(preferred), 1),
        ])
        record = {
            "id": 1,
            "source": "StreamingAssets",
            "logical_id": "data.bin",
            "chunk_path": str(original),
            "chunk_exists": 1,
        }

        resolved_record, path = self.service.resolve_record(record)

        self.assertIs(record, resolved_record)
        self.assertEqual(original, path)

    def test_record_resolution_reuses_supplied_connection_for_fallback(self):
        fallback = self.root / "fallback.chk"
        fallback.write_bytes(b"fallback")
        self.insert([
            (1, "StreamingAssets", "data.bin", str(self.root / "missing"), 1),
            (2, "Persistent", "data.bin", str(fallback), 1),
        ])
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            original = dict(
                connection.execute("SELECT * FROM files WHERE id = 1").fetchone()
            )
            resolved = self.service.resolve_record(original, connection=connection)

        self.assertEqual(2, resolved[0]["id"])
        self.assertEqual(fallback, resolved[1])

    def test_finds_original_record_without_resolving_its_source(self):
        self.insert([
            (1, "Persistent", "data.bin", str(self.root / "missing"), 0),
        ])

        record = self.service.find_record(1)

        self.assertEqual(1, record["id"])
        self.assertEqual(str(self.root / "missing"), record["chunk_path"])
        self.assertIsNone(self.service.find_record(99))

    def test_resolves_file_id_and_its_fallback_in_one_operation(self):
        fallback = self.root / "fallback.chk"
        fallback.write_bytes(b"fallback")
        self.insert([
            (1, "StreamingAssets", "data.bin", str(self.root / "missing"), 1),
            (2, "Persistent", "data.bin", str(fallback), 1),
        ])

        resolved = self.service.resolve_file_id(1)

        self.assertEqual(2, resolved[0]["id"])
        self.assertEqual(fallback, resolved[1])
        self.assertIsNone(self.service.resolve_file_id(99))


if __name__ == "__main__":
    unittest.main()
