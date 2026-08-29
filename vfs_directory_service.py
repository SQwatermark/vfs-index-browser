"""Query one VFS directory and assemble its paginated API document."""

from __future__ import annotations

import sqlite3
from typing import Callable


MANIFEST_VIRTUAL_DIR = "__manifest_assets__"
MANIFEST_VIRTUAL_NAME = "Manifest 资源"


def split_manifest_virtual_path(path: str) -> tuple[str, str] | None:
    parts = [part for part in path.replace("\\", "/").strip("/").split("/") if part]
    if MANIFEST_VIRTUAL_DIR not in parts:
        return None
    marker = parts.index(MANIFEST_VIRTUAL_DIR)
    return "/".join(parts[:marker]), "/".join(parts[marker + 1 :])


def join_manifest_virtual_path(base_path: str, inner_path: str = "") -> str:
    return "/".join(
        part
        for part in (
            base_path.strip("/"),
            MANIFEST_VIRTUAL_DIR,
            inner_path.strip("/"),
        )
        if part
    )


class VfsDirectoryService:
    def __init__(
        self,
        manifest_asset_count: Callable[[sqlite3.Connection, int], int],
    ) -> None:
        self._manifest_asset_count = manifest_asset_count

    def list_directory(
        self,
        conn: sqlite3.Connection,
        scope: str,
        path: str,
        *,
        page: int,
        page_size: int,
    ) -> dict:
        current = conn.execute(
            "SELECT * FROM directories WHERE scope = ? AND path = ?",
            (scope, path),
        ).fetchone()
        if current is None:
            raise FileNotFoundError("directory not found")
        dirs = [
            _row_to_dict(row)
            for row in conn.execute(
                """
                SELECT path, name, file_count, total_bytes, encrypted_count,
                       missing_chunk_count
                FROM entries
                WHERE scope = ? AND parent = ? AND type = 'dir'
                ORDER BY name COLLATE NOCASE
                """,
                (scope, path),
            )
        ]
        manifest_entry = conn.execute(
            """
            SELECT e.file_id, f.length, f.chunk_exists
            FROM entries e JOIN files f ON f.id = e.file_id
            WHERE e.scope = ? AND e.parent = ? AND e.type = 'file'
              AND e.name = 'manifest.hgmmap'
            LIMIT 1
            """,
            (scope, path),
        ).fetchone()
        if manifest_entry is not None:
            manifest_count = 0
            if manifest_entry["chunk_exists"]:
                manifest_count = self._manifest_asset_count(
                    conn,
                    int(manifest_entry["file_id"]),
                )
            dirs.append({
                "path": join_manifest_virtual_path(path),
                "name": MANIFEST_VIRTUAL_NAME,
                "file_count": manifest_count,
                "total_bytes": int(manifest_entry["length"]),
                "encrypted_count": 0,
                "missing_chunk_count": (
                    0 if manifest_entry["chunk_exists"] else 1
                ),
                "virtualKind": "bundleManifest",
            })

        total_files = conn.execute(
            """
            SELECT COUNT(*) AS count FROM entries
            WHERE scope = ? AND parent = ? AND type = 'file'
            """,
            (scope, path),
        ).fetchone()["count"]
        offset = (page - 1) * page_size
        entry_rows = [
            _row_to_dict(row)
            for row in conn.execute(
                """
                SELECT path, name, file_id
                FROM entries
                WHERE scope = ? AND parent = ? AND type = 'file'
                ORDER BY name COLLATE NOCASE
                LIMIT ? OFFSET ?
                """,
                (scope, path, page_size, offset),
            )
        ]
        files_by_id = {}
        if entry_rows:
            placeholders = ",".join("?" for _ in entry_rows)
            files_by_id = {
                row["id"]: _row_to_dict(row)
                for row in conn.execute(
                    f"""
                    SELECT id, source, block_name, block_hash, file_name, logical_id,
                           source_logical_id, chunk_file, chunk_exists, offset, length,
                           encrypted, iv_seed, file_data_md5
                    FROM files
                    WHERE id IN ({placeholders})
                    """,
                    [row["file_id"] for row in entry_rows],
                )
            }
        files = [
            {**files_by_id[entry["file_id"]], "path": entry["path"], "name": entry["name"]}
            for entry in entry_rows
            if entry["file_id"] in files_by_id
        ]
        return {
            "scope": scope,
            "path": path,
            "directory": _row_to_dict(current),
            "dirs": dirs,
            "files": files,
            "filePage": {
                "page": page,
                "pageSize": page_size,
                "total": total_files,
                "pages": max((total_files + page_size - 1) // page_size, 1),
            },
        }


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}
