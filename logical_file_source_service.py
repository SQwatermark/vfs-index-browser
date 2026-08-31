"""从 VFS 索引选择一个逻辑文件的本地可读来源。"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Callable


class LogicalFileSourceService:
    def __init__(
        self,
        db_path: Path,
        source_ranker: Callable[[str, bool], tuple],
    ) -> None:
        self._db_path = db_path
        self._source_ranker = source_ranker

    def resolve(self, logical_id: str) -> tuple[dict, Path] | None:
        with closing(self._connect()) as connection:
            effective_ids = {
                int(row[0])
                for row in connection.execute(
                    """
                    SELECT file_id FROM entries
                    WHERE scope = 'effective' AND type = 'file' AND path = ?
                      AND file_id IS NOT NULL
                    """,
                    (logical_id,),
                )
            }
            candidates = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM files WHERE logical_id = ?",
                    (logical_id,),
                )
            ]
        candidates.sort(
            key=lambda row: (
                0 if int(row["id"]) in effective_ids else 1,
                *self._source_ranker(
                    str(row["source"]), bool(row["chunk_exists"])
                ),
            )
        )
        return next(
            (
                (candidate, Path(candidate["chunk_path"]))
                for candidate in candidates
                if Path(candidate["chunk_path"]).is_file()
            ),
            None,
        )

    def resolve_record(
        self,
        original: dict,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> tuple[dict, Path] | None:
        """保持指定记录优先；不可读时回退到同逻辑 ID 的最佳来源。"""

        original_path = Path(original["chunk_path"])
        if original_path.exists():
            return original, original_path
        if connection is not None:
            return self._resolve_record_fallback(connection, original)
        with closing(self._connect()) as owned_connection:
            return self._resolve_record_fallback(owned_connection, original)

    def find_record(
        self,
        file_id: int,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict | None:
        """按稳定文件 ID 读取原始 VFS 记录，不执行来源替换。"""

        if connection is not None:
            return self._find_record(connection, file_id)
        with closing(self._connect()) as owned_connection:
            return self._find_record(owned_connection, file_id)

    def resolve_file_id(self, file_id: int) -> tuple[dict, Path] | None:
        """在同一数据库视图中按 ID 读取记录并解析可读来源。"""

        with closing(self._connect()) as connection:
            original = self._find_record(connection, file_id)
            if original is None:
                return None
            return self.resolve_record(original, connection=connection)

    @staticmethod
    def _find_record(
        connection: sqlite3.Connection,
        file_id: int,
    ) -> dict | None:
        row = connection.execute(
            "SELECT * FROM files WHERE id = ?", (file_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    def _resolve_record_fallback(
        self,
        connection: sqlite3.Connection,
        original: dict,
    ) -> tuple[dict, Path] | None:
        candidates = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM files WHERE logical_id = ?",
                (original["logical_id"],),
            )
        ]
        candidates.sort(
            key=lambda row: self._source_ranker(
                str(row["source"]), bool(row["chunk_exists"])
            )
        )
        return next(
            (
                (candidate, Path(candidate["chunk_path"]))
                for candidate in candidates
                if Path(candidate["chunk_path"]).exists()
            ),
            None,
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection
