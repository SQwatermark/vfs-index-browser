import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from index_freshness import inspect_index_freshness


class IndexFreshnessTests(unittest.TestCase):
    def build_database(self, root: Path, rows: list[tuple]) -> Path:
        database = root / "index.sqlite"
        with closing(sqlite3.connect(database)) as conn:
            conn.executescript(
                """
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE files (
                    source TEXT NOT NULL,
                    block_hash TEXT NOT NULL,
                    chunk_file TEXT NOT NULL,
                    chunk_path TEXT NOT NULL,
                    chunk_exists INTEGER NOT NULL
                );
                INSERT INTO meta VALUES ('builtAtEpoch', '123');
                """
            )
            conn.executemany("INSERT INTO files VALUES (?, ?, ?, ?, ?)", rows)
            conn.commit()
        return database

    def test_reports_missing_previously_available_chunks_as_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing = root / "existing.chk"
            existing.write_bytes(b"chunk")
            missing = root / "missing.chk"
            database = self.build_database(
                root,
                [
                    ("StreamingAssets", "A", "existing.chk", str(existing), 1),
                    ("Persistent", "B", "missing.chk", str(missing), 1),
                    ("Persistent", "B", "missing.chk", str(missing), 1),
                ],
            )

            report = inspect_index_freshness(database)

            self.assertEqual("stale", report["status"])
            self.assertEqual(2, report["checkedChunkCount"])
            self.assertEqual(1, report["missingChunkCount"])
            self.assertEqual("missing.chk", report["examples"][0]["chunkFile"])
            self.assertNotIn(str(root), str(report["examples"]))
            self.assertEqual(123, report["builtAtEpoch"])

    def test_does_not_claim_current_without_blc_content_identities(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chunk = root / "existing.chk"
            chunk.write_bytes(b"chunk")
            database = self.build_database(
                root,
                [("StreamingAssets", "A", "existing.chk", str(chunk), 1)],
            )

            report = inspect_index_freshness(database)

            self.assertEqual("unverified", report["status"])
            self.assertEqual("blc_content_identity_not_recorded", report["reason"])

    def test_reports_current_only_when_every_blc_identity_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chunk = root / "existing.chk"
            chunk.write_bytes(b"chunk")
            blc = root / "VFS" / "A" / "A.blc"
            blc.parent.mkdir(parents=True)
            blc.write_bytes(b"metadata")
            database = self.build_database(
                root,
                [("StreamingAssets", "A", "existing.chk", str(chunk), 1)],
            )
            summary = {
                "sources": [
                    {
                        "source": "StreamingAssets",
                        "sourceRoot": str(root),
                        "blcIdentities": [
                            {
                                "blockHash": "A",
                                "relativePath": "VFS/A/A.blc",
                                "length": blc.stat().st_size,
                                "sha256": hashlib.sha256(blc.read_bytes()).hexdigest(),
                            }
                        ],
                    }
                ]
            }
            with closing(sqlite3.connect(database)) as conn:
                conn.execute(
                    "INSERT INTO meta VALUES ('summary', ?)",
                    (json.dumps(summary),),
                )
                conn.commit()

            report = inspect_index_freshness(database)

            self.assertEqual("current", report["status"])
            self.assertEqual(1, report["checkedBlcCount"])
            self.assertEqual(0, report["changedBlcCount"])

            blc.write_bytes(b"changed")
            changed = inspect_index_freshness(database)
            self.assertEqual("stale", changed["status"])
            self.assertEqual("blc_content_identity_changed", changed["reason"])
            self.assertEqual(1, changed["changedBlcCount"])
            self.assertNotIn(str(root), str(changed["examples"]))

    def test_reports_missing_or_invalid_database_without_raising(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(
                "unavailable",
                inspect_index_freshness(root / "missing.sqlite")["status"],
            )
            invalid = root / "invalid.sqlite"
            sqlite3.connect(invalid).close()
            self.assertEqual(
                "unavailable",
                inspect_index_freshness(invalid)["status"],
            )


if __name__ == "__main__":
    unittest.main()
