import sqlite3
import unittest

from vfs_search_service import VfsSearchService


class VfsSearchServiceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript(
            """
            CREATE TABLE entries (
                scope TEXT, type TEXT, path TEXT, name TEXT, file_id INTEGER
            );
            CREATE TABLE files (
                id INTEGER, source TEXT, block_name TEXT, file_name TEXT,
                chunk_file TEXT, chunk_exists INTEGER, offset INTEGER,
                length INTEGER, encrypted INTEGER, iv_seed INTEGER
            );
            INSERT INTO files VALUES
                (1, 'Persistent', 'A', 'literal_%.txt', 'a.chk', 1, 2, 3, 0, 4),
                (2, 'Persistent', 'B', 'literal_ab.txt', 'b.chk', 1, 5, 6, 0, 7),
                (3, 'Persistent', 'C', 'other.txt', 'c.chk', 1, 8, 9, 0, 10);
            INSERT INTO entries VALUES
                ('effective', 'file', 'Root/literal_%.txt', 'literal_%.txt', 1),
                ('effective', 'file', 'Root/literal_ab.txt', 'literal_ab.txt', 2),
                ('raw', 'file', 'Root/literal_%.txt', 'literal_%.txt', 3);
            """
        )

    def test_searches_one_scope_and_returns_file_metadata(self):
        result = VfsSearchService().search(
            self.conn, "effective", "literal", limit=1
        )

        self.assertEqual(1, result["limit"])
        self.assertEqual(1, len(result["items"]))
        self.assertEqual("Root/literal_%.txt", result["items"][0]["path"])
        self.assertEqual("a.chk", result["items"][0]["chunk_file"])

    def test_treats_like_metacharacters_as_literals(self):
        underscore = VfsSearchService().search(
            self.conn, "effective", "literal_%", limit=10
        )
        percent = VfsSearchService().search(
            self.conn, "effective", "%", limit=10
        )

        self.assertEqual(["Root/literal_%.txt"], [x["path"] for x in underscore["items"]])
        self.assertEqual(["Root/literal_%.txt"], [x["path"] for x in percent["items"]])

    def test_empty_term_does_not_query_and_preserves_api_shape(self):
        result = VfsSearchService().search(
            self.conn, "effective", "   ", limit=100
        )

        self.assertEqual({"items": []}, result)


if __name__ == "__main__":
    unittest.main()
