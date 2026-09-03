"""Build Endaxis source documents directly from the effective local VFS."""

from __future__ import annotations

import json
import math
import sqlite3
import struct
from contextlib import closing
from pathlib import Path
from typing import Callable

from endaxis_data_route import is_safe_endaxis_json_file
from memorypack_value_decoder import MemoryPackValueDecodeError, MemoryPackValueDecoder
from sparkbuffer import SparkBufferError


class EndaxisDataError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class EndaxisDataService:
    def __init__(
        self,
        db_path: Path,
        logical_file_resolver: Callable[[str], tuple[dict, Path] | None],
        table_parser: Callable[[dict, Path], tuple[dict, bytes]],
        memorypack_decoder: MemoryPackValueDecoder,
        file_reader: Callable[[dict, Path], bytes],
    ) -> None:
        self._db_path = db_path
        self._resolve_file = logical_file_resolver
        self._parse_table = table_parser
        self._memorypack = memorypack_decoder
        self._read_file = file_reader

    def table(self, table_name: str) -> object:
        record, chunk_path = self._required_file(f"Table/Data/TableCfg/{table_name}.bytes")
        try:
            parsed, _ = self._parse_table(record, chunk_path)
            return make_endaxis_json_value(parsed["data"])
        except (SparkBufferError, struct.error, UnicodeDecodeError, ValueError) as error:
            raise EndaxisDataError(422, f"SparkBuffer parse failed: {error}") from error
        except OSError as error:
            raise EndaxisDataError(503, str(error)) from error

    def collection_manifest(self, collection: str) -> list[dict]:
        logical_parent = f"JsonData/Data/Json/{collection}"
        try:
            with closing(sqlite3.connect(self._db_path)) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    """
                    SELECT name FROM entries
                    WHERE scope = 'effective' AND type = 'file' AND parent = ?
                    ORDER BY name
                    """,
                    (logical_parent,),
                ).fetchall()
        except (OSError, sqlite3.Error) as error:
            raise EndaxisDataError(503, str(error)) from error
        files = sorted(
            {str(row["name"]) for row in rows if is_safe_endaxis_json_file(str(row["name"]))}
        )
        return [
            {"contentFile": f"/api/endaxis-data/{collection}/{file_name}"}
            for file_name in files
        ]

    def collection_file(self, collection: str, file_name: str) -> object:
        logical_id = f"JsonData/Data/Json/{collection}/{file_name}"
        record, chunk_path = self._required_file(logical_id)
        try:
            raw = self._read_file(record, chunk_path)
            if raw.lstrip().startswith((b"{", b"[")):
                return make_endaxis_json_value(json.loads(raw.decode("utf-8-sig")))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise EndaxisDataError(422, f"JSON parse failed: {error}") from error
        except OSError as error:
            raise EndaxisDataError(503, str(error)) from error
        try:
            decoded = self._memorypack.decode(logical_id, record, chunk_path)
        except MemoryPackValueDecodeError as error:
            raise EndaxisDataError(422, f"MemoryPack decode failed: {error}") from error
        except OSError as error:
            raise EndaxisDataError(503, str(error)) from error
        if decoded is None:
            raise EndaxisDataError(422, f"MemoryPack class is unknown: {logical_id}")
        if not decoded.complete:
            raise EndaxisDataError(
                422,
                "MemoryPack decode was incomplete: "
                f"consumed {decoded.consumed} / {decoded.byte_count} bytes",
            )
        return make_endaxis_json_value(decoded.value)

    def _required_file(self, logical_id: str) -> tuple[dict, Path]:
        resolved = self._resolve_file(logical_id)
        if resolved is None:
            raise EndaxisDataError(404, f"local VFS resource is unavailable: {logical_id}")
        return resolved


def make_endaxis_json_value(value: object) -> object:
    """Preserve Unity non-finite float semantics in strict JSON form."""
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            return "NaN"
        return "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, list):
        return [make_endaxis_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: make_endaxis_json_value(item) for key, item in value.items()}
    return value
