"""解析 VFS 中的 TableCfg 记录与 SparkBuffer 内容。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from logical_file_source_service import LogicalFileSourceError


class TableCfgResolutionError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ResolvedTableCfg:
    original: dict
    record: dict
    chunk_path: Path
    table_name: str


class TableCfgService:
    def __init__(
        self,
        source_service: object,
        file_reader: Callable[[dict, Path], bytes],
        parser: Callable[[bytes], dict],
        name_resolver: Callable[[str], str | None],
    ) -> None:
        self._sources = source_service
        self._read_file = file_reader
        self._parse = parser
        self._table_name = name_resolver

    def resolve(
        self,
        connection: sqlite3.Connection,
        file_id: int,
    ) -> ResolvedTableCfg:
        original = self._sources.find_record(file_id, connection=connection)
        if original is None:
            raise TableCfgResolutionError(404, "file not found")
        resolved = self._sources.resolve_record(original, connection=connection)
        if resolved is None:
            raise TableCfgResolutionError(
                404,
                "chunk not found; this record likely requires a source fallback "
                "that is unavailable on this host",
            )
        record, chunk_path = resolved
        table_name = self._table_name(str(record["file_name"]))
        if table_name is None:
            raise TableCfgResolutionError(
                400, "TableCfg JSON expected a Data/TableCfg/*.bytes record"
            )
        return ResolvedTableCfg(original, record, chunk_path, table_name)

    def resolve_file_id(self, file_id: int) -> ResolvedTableCfg:
        try:
            original, record, chunk_path = self._sources.resolve_file_id_required(
                file_id
            )
        except LogicalFileSourceError as error:
            raise TableCfgResolutionError(error.status, str(error)) from error
        table_name = self._table_name(str(record["file_name"]))
        if table_name is None:
            raise TableCfgResolutionError(
                400, "TableCfg JSON expected a Data/TableCfg/*.bytes record"
            )
        return ResolvedTableCfg(original, record, chunk_path, table_name)

    def parse(self, record: dict, chunk_path: Path) -> tuple[dict, bytes]:
        parsed = self._parse(self._read_file(record, chunk_path))
        data = json.dumps(parsed["data"], ensure_ascii=False, indent=2).encode("utf-8")
        return parsed, data
