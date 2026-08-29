"""Search indexed VFS files without exposing SQL details to HTTP handlers."""

from __future__ import annotations

import sqlite3


class VfsSearchService:
    def search(
        self,
        conn: sqlite3.Connection,
        scope: str,
        term: str,
        *,
        limit: int,
    ) -> dict:
        term = term.strip()
        if not term:
            return {"items": []}
        pattern = f"%{_escape_sql_like(term)}%"
        rows = conn.execute(
            """
            SELECT e.path, e.name, f.id, f.source, f.block_name, f.file_name,
                   f.chunk_file, f.chunk_exists, f.offset, f.length,
                   f.encrypted, f.iv_seed
            FROM entries e
            JOIN files f ON f.id = e.file_id
            WHERE e.scope = ? AND e.type = 'file' AND e.path LIKE ? ESCAPE '\\'
            ORDER BY e.path COLLATE NOCASE
            LIMIT ?
            """,
            (scope, pattern, limit),
        )
        return {
            "items": [{key: row[key] for key in row.keys()} for row in rows],
            "limit": limit,
        }


def _escape_sql_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
