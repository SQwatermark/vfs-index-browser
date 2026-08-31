import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from bundle_source_service import BundleSourceService


class BundleSourceServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "vfs.sqlite"
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """
                CREATE TABLE files (
                    id INTEGER PRIMARY KEY, file_name TEXT, source TEXT,
                    chunk_path TEXT, chunk_exists INTEGER
                )
                """
            )
            connection.commit()
        self.service = BundleSourceService(
            self.database,
            lambda source, exists: (
                0 if exists else 1,
                0 if source == "Persistent" else 1,
            ),
        )

    def tearDown(self):
        self.temporary.cleanup()

    def insert(self, rows):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executemany("INSERT INTO files VALUES (?, ?, ?, ?, ?)", rows)
            connection.commit()

    def test_resolves_in_input_order_and_reports_missing(self):
        first = self.root / "first.ab"
        second = self.root / "second.ab"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        self.insert([
            (1, "Data/Bundles/Windows/a.ab", "StreamingAssets", str(first), 1),
            (2, "Data/Bundles/Windows/b.ab", "Persistent", str(second), 1),
        ])
        bundles = [{"name": "b.ab"}, {"name": "missing.ab"}, {"name": "a.ab"}]

        resolved, missing = self.service.resolve_many(bundles)

        self.assertEqual([2, 1], [record["id"] for record, _ in resolved])
        self.assertEqual([bundles[1]], missing)

    def test_uses_ranked_readable_fallback(self):
        fallback = self.root / "fallback.ab"
        fallback.write_bytes(b"fallback")
        self.insert([
            (
                1,
                "Data/Bundles/Windows/a.ab",
                "Persistent",
                str(self.root / "missing.ab"),
                1,
            ),
            (
                2,
                "Data/Bundles/Windows/a.ab",
                "StreamingAssets",
                str(fallback),
                1,
            ),
        ])

        resolved, missing = self.service.resolve_many([{"name": "a.ab"}])

        self.assertEqual([], missing)
        self.assertEqual(2, resolved[0][0]["id"])
        self.assertEqual(fallback, resolved[0][1])

    def test_empty_request_does_not_require_database(self):
        service = BundleSourceService(
            self.root / "missing.sqlite", lambda *_args: (0,)
        )
        self.assertEqual(([], []), service.resolve_many([]))


if __name__ == "__main__":
    unittest.main()
