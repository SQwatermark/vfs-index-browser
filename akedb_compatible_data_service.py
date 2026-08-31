"""从本地 VFS 构建 AKEDB-compatible 数据文件与清单。"""

from __future__ import annotations

import sqlite3
import struct
from contextlib import closing
from pathlib import Path
from typing import Callable

from akedb_compatible_route import is_safe_akedb_json_file
from sparkbuffer import SparkBufferError
from memorypack_value_decoder import (
    MemoryPackValueDecodeError,
    MemoryPackValueDecoder,
)


class AkedbCompatibleDataError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class AkedbCompatibleDataService:
    def __init__(
        self,
        db_path: Path,
        logical_file_resolver: Callable[[str], tuple[dict, Path] | None],
        table_parser: Callable[[dict, Path], tuple[dict, bytes]],
        memorypack_decoder: MemoryPackValueDecoder,
    ) -> None:
        self._db_path = db_path
        self._resolve_file = logical_file_resolver
        self._parse_table = table_parser
        self._memorypack = memorypack_decoder

    def table(self, table_name: str) -> object:
        logical_id = f"Table/Data/TableCfg/{table_name}.bytes"
        record, chunk_path = self._required_file(logical_id)
        try:
            parsed, _ = self._parse_table(record, chunk_path)
            return parsed["data"]
        except (SparkBufferError, struct.error, UnicodeDecodeError, ValueError) as error:
            raise AkedbCompatibleDataError(
                422, f"SparkBuffer parse failed: {error}"
            ) from error
        except OSError as error:
            raise AkedbCompatibleDataError(503, str(error)) from error

    def collection_manifest(self, collection: str) -> list[dict]:
        logical_parent = f"JsonData/Data/Json/{collection}"
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT name FROM entries
                    WHERE scope = 'effective' AND type = 'file' AND parent = ?
                    ORDER BY name
                    """,
                    (logical_parent,),
                ).fetchall()
        except (OSError, sqlite3.Error) as error:
            raise AkedbCompatibleDataError(503, str(error)) from error
        files = sorted(
            {
                str(row["name"])
                for row in rows
                if is_safe_akedb_json_file(str(row["name"]))
            }
        )
        return [
            {"contentFile": f"/api/akedb-compatible/{collection}/{file_name}"}
            for file_name in files
        ]

    def collection_file(self, collection: str, file_name: str) -> object:
        logical_id = f"JsonData/Data/Json/{collection}/{file_name}"
        record, chunk_path = self._required_file(logical_id)
        try:
            decoded = self._memorypack.decode(logical_id, record, chunk_path)
        except MemoryPackValueDecodeError as error:
            raise AkedbCompatibleDataError(
                422, f"MemoryPack decode failed: {error}"
            ) from error
        except OSError as error:
            raise AkedbCompatibleDataError(503, str(error)) from error
        if decoded is None:
            raise AkedbCompatibleDataError(
                422, f"MemoryPack class is unknown: {logical_id}"
            )
        if not decoded.complete:
            raise AkedbCompatibleDataError(
                422,
                "MemoryPack decode was incomplete: "
                f"consumed {decoded.consumed} / {decoded.byte_count} bytes",
            )
        return decoded.value

    def _required_file(self, logical_id: str) -> tuple[dict, Path]:
        resolved = self._resolve_file(logical_id)
        if resolved is None:
            raise AkedbCompatibleDataError(
                404, f"local VFS resource is unavailable: {logical_id}"
            )
        return resolved

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection
