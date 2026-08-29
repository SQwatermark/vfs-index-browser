import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from index_rebuild import (
    IndexRebuildError,
    load_index_source_roots,
    rebuild_index_atomically,
)


class IndexRebuildTests(unittest.TestCase):
    @staticmethod
    def write_candidate(database: Path, source_root: Path) -> None:
        chunk = source_root / "VFS" / "A" / "chunk.chk"
        blc = source_root / "VFS" / "A" / "A.blc"
        chunk.parent.mkdir(parents=True, exist_ok=True)
        chunk.write_bytes(b"chunk")
        blc.write_bytes(b"metadata")
        summary = {
            "sources": [
                {
                    "source": "StreamingAssets",
                    "sourceRoot": str(source_root),
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
            conn.executescript(
                """
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE files (
                    source TEXT, block_hash TEXT, chunk_file TEXT,
                    chunk_path TEXT, chunk_exists INTEGER
                );
                """
            )
            conn.executemany(
                "INSERT INTO meta VALUES (?, ?)",
                [
                    ("sourceFileCount", json.dumps(1)),
                    ("effectiveFileCount", json.dumps(1)),
                    ("summary", json.dumps(summary)),
                ],
            )
            conn.execute(
                "INSERT INTO files VALUES (?, ?, ?, ?, ?)",
                ("StreamingAssets", "A", "chunk.chk", str(chunk), 1),
            )
            conn.commit()

    def test_builds_valid_candidate_then_atomically_replaces_old_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "active.sqlite"
            database.write_bytes(b"old database")
            source_root = root / "StreamingAssets"
            source_root.mkdir()

            def generator(args):
                args.output.write_text("generated", encoding="utf-8")
                return {
                    "totals": {
                        "selectedFileCount": 1,
                        "parseErrorCount": 0,
                        "missingChunkCount": 0,
                    }
                }

            def builder(index_path, candidate):
                self.assertEqual("generated", index_path.read_text(encoding="utf-8"))
                self.write_candidate(candidate, source_root)

            result = rebuild_index_atomically(
                database,
                {"StreamingAssets": source_root},
                builder,
                generator=generator,
            )

            self.assertEqual("rebuilt", result["status"])
            with closing(sqlite3.connect(database)) as conn:
                self.assertEqual("ok", conn.execute("PRAGMA integrity_check").fetchone()[0])
            self.assertEqual([], list(root.glob(".vfs-index-rebuild-*")))

    def test_failed_candidate_preserves_old_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "active.sqlite"
            database.write_bytes(b"old database")
            source_root = root / "StreamingAssets"
            source_root.mkdir()

            def generator(_args):
                return {"totals": {"selectedFileCount": 1, "parseErrorCount": 0}}

            def builder(_index_path, _candidate):
                raise RuntimeError("build failed")

            with self.assertRaisesRegex(IndexRebuildError, "build failed"):
                rebuild_index_atomically(
                    database,
                    {"StreamingAssets": source_root},
                    builder,
                    generator=generator,
                )

            self.assertEqual(b"old database", database.read_bytes())
            self.assertEqual([], list(root.glob(".vfs-index-rebuild-*")))

    def test_recovers_existing_source_roots_from_index_header(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            streaming = root / "StreamingAssets"
            streaming.mkdir()
            missing = root / "Persistent"
            database = root / "index.sqlite"
            header = {
                "sources": [
                    {"name": "StreamingAssets", "root": str(streaming)},
                    {"name": "Persistent", "root": str(missing)},
                ]
            }
            with closing(sqlite3.connect(database)) as conn:
                conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
                conn.execute("INSERT INTO meta VALUES ('header', ?)", (json.dumps(header),))
                conn.commit()

            self.assertEqual(
                {"StreamingAssets": streaming},
                load_index_source_roots(database),
            )


if __name__ == "__main__":
    unittest.main()
