import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from vfs_database_builder import VfsDatabaseBuilder, source_rank


class VfsDatabaseBuilderTests(unittest.TestCase):
    def test_builds_scopes_and_prefers_readable_persistent_source_across_batches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = root / "index.jsonl"
            database = root / "index.sqlite"
            records = [
                {"recordType": "header", "version": 3},
                self._file("StreamingAssets", "Data/shared.bin", True, 10),
                self._file("Persistent", "Data/shared.bin", True, 20),
                self._file("Persistent", "Data/missing.bin", False, 30),
                {"recordType": "summary", "selectedFileCount": 3},
            ]
            index.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )

            VfsDatabaseBuilder(batch_size=2, progress_every=0).build(index, database)

            connection = sqlite3.connect(database)
            try:
                effective = list(
                    connection.execute(
                        """
                        SELECT entries.path, files.source, files.length
                        FROM entries JOIN files ON files.id = entries.file_id
                        WHERE entries.scope='effective' AND entries.type='file'
                        ORDER BY entries.path
                        """
                    )
                )
                scopes = dict(
                    connection.execute(
                        "SELECT scope, file_count FROM directories WHERE path=''"
                    )
                )
                metadata = {
                    key: json.loads(value)
                    for key, value in connection.execute("SELECT key, value FROM meta")
                }
            finally:
                connection.close()

        self.assertEqual(
            [
                ("Data/missing.bin", "Persistent", 30),
                ("Data/shared.bin", "Persistent", 20),
            ],
            effective,
        )
        self.assertEqual(3, scopes["all"])
        self.assertEqual(2, scopes["Persistent"])
        self.assertEqual(1, scopes["StreamingAssets"])
        self.assertEqual(2, scopes["effective"])
        self.assertEqual(3, metadata["sourceFileCount"])
        self.assertEqual(2, metadata["effectiveFileCount"])
        self.assertEqual(3, metadata["header"]["version"])
        self.assertEqual(3, metadata["summary"]["selectedFileCount"])

    def test_source_rank_prefers_readability_then_source_priority(self):
        self.assertLess(source_rank("Persistent", True), source_rank("StreamingAssets", True))
        self.assertLess(source_rank("StreamingAssets", True), source_rank("Persistent", False))

    @staticmethod
    def _file(source: str, logical_id: str, exists: bool, length: int) -> dict:
        return {
            "recordType": "file",
            "source": source,
            "sourceRoot": f"/{source}",
            "blockHash": "ABC",
            "blockName": "Bundle",
            "logicalId": logical_id,
            "sourceLogicalId": logical_id,
            "fileName": logical_id.split("/", 1)[-1],
            "fileNameHash": "1",
            "chunkFile": "chunk.chk",
            "chunkPath": "/chunk.chk",
            "chunkExists": exists,
            "offset": 0,
            "length": length,
            "encrypted": source == "Persistent",
            "ivSeed": 7,
        }


if __name__ == "__main__":
    unittest.main()
