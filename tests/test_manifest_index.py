import sqlite3
import struct
import tempfile
import unittest
from pathlib import Path

from manifest_index import ManifestIndex, _ref_int_array


class ManifestDependencyTests(unittest.TestCase):
    def test_reads_reference_integer_array(self):
        data = b"head" + struct.pack("<i3i", 3, 1, 4, 2)
        self.assertEqual([1, 4, 2], _ref_int_array(data, 4, 0, 5))

    def test_rejects_out_of_range_dependency(self):
        data = struct.pack("<i2i", 2, 1, 5)
        with self.assertRaises(ValueError):
            _ref_int_array(data, 0, 0, 5)

    def test_queries_direct_and_transitive_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.sqlite"
            conn = sqlite3.connect(path)
            try:
                conn.executescript("""
                    CREATE TABLE bundles (bundle_index INTEGER PRIMARY KEY, name TEXT NOT NULL);
                    CREATE TABLE bundle_dependencies (
                        bundle_index INTEGER NOT NULL,
                        dependency_index INTEGER NOT NULL,
                        kind TEXT NOT NULL,
                        PRIMARY KEY (bundle_index, dependency_index, kind)
                    );
                """)
                conn.executemany(
                    "INSERT INTO bundles VALUES (?, ?)",
                    [(0, "root.ab"), (1, "body.ab"), (2, "shared.ab"), (3, "unused.ab")],
                )
                conn.executemany(
                    "INSERT INTO bundle_dependencies VALUES (?, ?, 'direct')",
                    [(0, 1), (1, 2)],
                )
                conn.commit()
            finally:
                conn.close()
            index = ManifestIndex(path)
            self.assertEqual(["body.ab"], [row["name"] for row in index.bundle_dependencies(0, transitive=False)])
            self.assertEqual(["body.ab", "shared.ab"], [row["name"] for row in index.bundle_dependencies(0)])


if __name__ == "__main__":
    unittest.main()
