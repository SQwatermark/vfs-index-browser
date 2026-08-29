import sqlite3
import unittest

from vfs_directory_service import VfsDirectoryService


class VfsDirectoryServiceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript(
            """
            CREATE TABLE directories (
                scope TEXT, path TEXT, name TEXT, file_count INTEGER,
                total_bytes INTEGER, encrypted_count INTEGER,
                missing_chunk_count INTEGER
            );
            CREATE TABLE entries (
                scope TEXT, parent TEXT, type TEXT, path TEXT, name TEXT,
                file_id INTEGER, file_count INTEGER, total_bytes INTEGER,
                encrypted_count INTEGER, missing_chunk_count INTEGER
            );
            CREATE TABLE files (
                id INTEGER, source TEXT, block_name TEXT, block_hash TEXT,
                file_name TEXT, logical_id TEXT, source_logical_id TEXT,
                chunk_file TEXT, chunk_exists INTEGER, offset INTEGER,
                length INTEGER, encrypted INTEGER, iv_seed INTEGER,
                file_data_md5 TEXT
            );
            INSERT INTO directories VALUES ('effective', 'Root', 'Root', 3, 60, 0, 0);
            INSERT INTO entries VALUES (
                'effective', 'Root', 'dir', 'Root/Child', 'Child', NULL,
                1, 10, 0, 0
            );
            """
        )
        for file_id, name, size in (
            (1, "a.txt", 10),
            (2, "b.txt", 20),
            (3, "manifest.hgmmap", 30),
        ):
            self.conn.execute(
                "INSERT INTO entries VALUES ('effective', 'Root', 'file', ?, ?, ?, NULL, NULL, NULL, NULL)",
                (f"Root/{name}", name, file_id),
            )
            self.conn.execute(
                "INSERT INTO files VALUES (?, 'Persistent', 'Block', 'HASH', ?, ?, ?, 'x.chk', 1, 0, ?, 0, 0, 'MD5')",
                (file_id, name, f"Root/{name}", f"Persistent/Root/{name}", size),
            )
        self.conn.commit()

    def test_pages_files_in_one_batch_and_adds_manifest_virtual_directory(self):
        calls = []
        service = VfsDirectoryService(
            lambda _conn, file_id: (calls.append(file_id), 77)[1]
        )

        result = service.list_directory(
            self.conn,
            "effective",
            "Root",
            page=2,
            page_size=2,
        )

        self.assertEqual([3], calls)
        self.assertEqual(["manifest.hgmmap"], [item["name"] for item in result["files"]])
        self.assertEqual(3, result["filePage"]["total"])
        self.assertEqual(2, result["filePage"]["pages"])
        virtual = next(item for item in result["dirs"] if item.get("virtualKind"))
        self.assertEqual("Root/__manifest_assets__", virtual["path"])
        self.assertEqual(77, virtual["file_count"])

    def test_missing_directory_is_explicit(self):
        service = VfsDirectoryService(lambda *_args: 0)

        with self.assertRaisesRegex(FileNotFoundError, "directory not found"):
            service.list_directory(
                self.conn,
                "effective",
                "Missing",
                page=1,
                page_size=100,
            )


if __name__ == "__main__":
    unittest.main()
