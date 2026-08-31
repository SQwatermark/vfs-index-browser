"""Build the derived VFS SQLite browser database from index JSONL."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from service_logging import LOGGER
from vfs_database_schema import (
    create_indexes,
    create_schema,
    insert_directories,
    split_parent,
)
from vfs_index_jsonl import open_index


SOURCE_PRIORITY = {
    "Persistent": 0,
    "StreamingAssets": 1,
}


@dataclass(frozen=True)
class FileView:
    file_id: int
    path: str
    source: str
    chunk_exists: bool
    length: int
    encrypted: bool


def source_rank(source: str, chunk_exists: bool) -> tuple[int, int]:
    return (0 if chunk_exists else 1, SOURCE_PRIORITY.get(source, 99))


class VfsDatabaseBuilder:
    def __init__(self, *, batch_size: int = 5000, progress_every: int = 100000) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self._batch_size = batch_size
        self._progress_every = progress_every

    def build(self, index_path: Path, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        if database_path.exists():
            database_path.unlink()

        started = time.time()
        connection = sqlite3.connect(database_path)
        try:
            create_schema(connection)
            directories: dict[tuple[str, str], dict[str, int]] = {}
            file_batch: list[tuple] = []
            entry_batch: list[tuple] = []
            effective: dict[str, tuple[tuple[int, int], FileView]] = {}
            header = None
            summary = None
            file_count = 0

            for line in open_index(index_path):
                record = json.loads(line)
                record_type = record.get("recordType")
                if record_type == "header":
                    header = record
                    continue
                if record_type == "summary":
                    summary = record
                    continue
                if record_type != "file":
                    continue

                file_count += 1
                file_batch.append(self._file_row(record))
                if len(file_batch) >= self._batch_size:
                    self._insert_file_batch(
                        connection,
                        file_batch,
                        entry_batch,
                        directories,
                        effective,
                    )
                if self._progress_every > 0 and file_count % self._progress_every == 0:
                    LOGGER.info(
                        "database_index_progress",
                        extra={"fileCount": file_count},
                    )

            self._insert_file_batch(
                connection,
                file_batch,
                entry_batch,
                directories,
                effective,
            )
            for _, view in effective.values():
                self._queue_file_entry(entry_batch, "effective", view)
                self._add_directory_stats(
                    directories,
                    "effective",
                    view.path,
                    view.length,
                    view.encrypted,
                    view.chunk_exists,
                )
                if len(entry_batch) >= self._batch_size:
                    self._flush_file_entries(connection, entry_batch)
            self._flush_file_entries(connection, entry_batch)

            insert_directories(connection, directories)
            create_indexes(connection)
            metadata = {
                "indexPath": str(index_path),
                "builtAtEpoch": int(time.time()),
                "elapsedSeconds": round(time.time() - started, 3),
                "sourceFileCount": file_count,
                "effectiveFileCount": len(effective),
                "header": header,
                "summary": summary,
            }
            connection.executemany(
                "INSERT INTO meta(key, value) VALUES (?, ?)",
                [
                    (key, json.dumps(value, ensure_ascii=False))
                    for key, value in metadata.items()
                ],
            )
            connection.commit()
        finally:
            connection.close()

        LOGGER.info(
            "database_built",
            extra={
                "database": str(database_path),
                "sourceFileCount": file_count,
                "effectiveFileCount": len(effective),
            },
        )

    @staticmethod
    def _file_row(record: dict) -> tuple:
        return (
            record["source"],
            record.get("sourceRoot", ""),
            record["blockHash"],
            record["blockName"],
            record["logicalId"],
            record["sourceLogicalId"],
            record["fileName"],
            str(record.get("fileNameHash", "")),
            record["chunkFile"],
            record.get("chunkPath", ""),
            int(record.get("chunkExists", False)),
            record.get("chunkMd5Name"),
            record.get("chunkContentMd5"),
            record.get("fileChunkMd5"),
            record.get("fileDataMd5"),
            record["offset"],
            record["length"],
            int(record.get("encrypted", False)),
            record.get("ivSeed") or 0,
        )

    def _insert_file_batch(
        self,
        connection: sqlite3.Connection,
        file_batch: list[tuple],
        entry_batch: list[tuple],
        directories: dict[tuple[str, str], dict[str, int]],
        effective: dict[str, tuple[tuple[int, int], FileView]],
    ) -> None:
        if not file_batch:
            return
        connection.executemany(
            """
            INSERT INTO files (
                source, source_root, block_hash, block_name, logical_id,
                source_logical_id, file_name, file_name_hash, chunk_file,
                chunk_path, chunk_exists, chunk_md5_name, chunk_content_md5,
                file_chunk_md5, file_data_md5, offset, length, encrypted, iv_seed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            file_batch,
        )
        first_id = (
            int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
            - len(file_batch)
            + 1
        )
        for offset, row in enumerate(file_batch):
            file_id = first_id + offset
            source = str(row[0])
            logical_id = str(row[4])
            chunk_exists = bool(row[10])
            length = int(row[16])
            encrypted = bool(row[17])
            all_path = f"{source}/{logical_id}"
            source_view = FileView(
                file_id, logical_id, source, chunk_exists, length, encrypted
            )
            all_view = FileView(
                file_id, all_path, source, chunk_exists, length, encrypted
            )
            self._queue_file_entry(entry_batch, source, source_view)
            self._queue_file_entry(entry_batch, "all", all_view)
            self._add_directory_stats(
                directories,
                source,
                logical_id,
                length,
                encrypted,
                chunk_exists,
            )
            self._add_directory_stats(
                directories,
                "all",
                all_path,
                length,
                encrypted,
                chunk_exists,
            )
            rank = source_rank(source, chunk_exists)
            current = effective.get(logical_id)
            if current is None or rank < current[0]:
                effective[logical_id] = (rank, source_view)

        self._flush_file_entries(connection, entry_batch)
        file_batch.clear()

    @staticmethod
    def _ancestor_directories(file_path: str):
        yield ""
        parts = [part for part in file_path.split("/") if part]
        current: list[str] = []
        for part in parts[:-1]:
            current.append(part)
            yield "/".join(current)

    @classmethod
    def _add_directory_stats(
        cls,
        directories: dict[tuple[str, str], dict[str, int]],
        scope: str,
        file_path: str,
        length: int,
        encrypted: bool,
        chunk_exists: bool,
    ) -> None:
        for directory_path in cls._ancestor_directories(file_path):
            stats = directories.setdefault(
                (scope, directory_path),
                {
                    "file_count": 0,
                    "total_bytes": 0,
                    "encrypted_count": 0,
                    "missing_chunk_count": 0,
                },
            )
            stats["file_count"] += 1
            stats["total_bytes"] += length
            if encrypted:
                stats["encrypted_count"] += 1
            if not chunk_exists:
                stats["missing_chunk_count"] += 1

    @staticmethod
    def _queue_file_entry(batch: list[tuple], scope: str, view: FileView) -> None:
        parent, name = split_parent(view.path)
        batch.append(
            (
                scope,
                parent,
                "file",
                name,
                view.path,
                view.file_id,
                view.length,
                int(view.encrypted),
                int(not view.chunk_exists),
            )
        )

    @staticmethod
    def _flush_file_entries(
        connection: sqlite3.Connection,
        batch: list[tuple],
    ) -> None:
        if not batch:
            return
        connection.executemany(
            """
            INSERT INTO entries (
                scope, parent, type, name, path, file_id, total_bytes,
                encrypted_count, missing_chunk_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            batch,
        )
        batch.clear()


def build_database(index_path: Path, database_path: Path) -> None:
    VfsDatabaseBuilder().build(index_path, database_path)
