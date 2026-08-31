import sqlite3
import unittest

from vfs_database_schema import create_indexes, create_schema, insert_directories


class VfsDatabaseSchemaTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.addCleanup(self.connection.close)
        create_schema(self.connection)

    def test_creates_expected_tables_and_indexes(self):
        tables = {
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertEqual({"meta", "files", "directories", "entries"}, tables)

        create_indexes(self.connection)
        indexes = {
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        self.assertTrue(
            {
                "idx_entries_lookup",
                "idx_entries_path",
                "idx_files_logical",
                "idx_files_source_logical",
                "idx_files_file_name",
            }.issubset(indexes)
        )

    def test_recreates_schema_without_preserving_old_rows(self):
        self.connection.execute("INSERT INTO meta VALUES ('old', 'value')")

        create_schema(self.connection)

        self.assertEqual(0, self.connection.execute("SELECT COUNT(*) FROM meta").fetchone()[0])

    def test_publishes_directory_rows_and_parent_entries(self):
        insert_directories(
            self.connection,
            {
                ("effective", ""): {
                    "file_count": 2,
                    "total_bytes": 30,
                    "encrypted_count": 1,
                    "missing_chunk_count": 0,
                },
                ("effective", "Data"): {
                    "file_count": 2,
                    "total_bytes": 30,
                    "encrypted_count": 1,
                    "missing_chunk_count": 0,
                },
                ("effective", "Data/Json"): {
                    "file_count": 1,
                    "total_bytes": 20,
                    "encrypted_count": 1,
                    "missing_chunk_count": 0,
                },
            },
        )

        root = self.connection.execute(
            "SELECT child_dir_count FROM directories WHERE scope=? AND path=?",
            ("effective", ""),
        ).fetchone()
        data = self.connection.execute(
            "SELECT child_dir_count FROM directories WHERE scope=? AND path=?",
            ("effective", "Data"),
        ).fetchone()
        entries = list(
            self.connection.execute(
                "SELECT parent, name, path FROM entries ORDER BY path"
            )
        )

        self.assertEqual((1,), root)
        self.assertEqual((1,), data)
        self.assertEqual(
            [("", "Data", "Data"), ("Data", "Json", "Data/Json")],
            entries,
        )


if __name__ == "__main__":
    unittest.main()
