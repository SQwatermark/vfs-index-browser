"""Audit secondary audio indexes against the current VFS package identities."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3


def inspect_secondary_audio_indexes(
    vfs_database: Path,
    audio_dialog_database: Path,
    wwise_database: Path,
) -> dict:
    with closing(sqlite3.connect(vfs_database)) as vfs:
        audio_dialog = _inspect_audio_dialog(vfs, audio_dialog_database)
        wwise = _inspect_wwise(vfs, wwise_database)
    children = (audio_dialog, wwise)
    if any(child["status"] == "stale" for child in children):
        status = "stale"
    elif any(child["status"] == "unavailable" for child in children):
        status = "unavailable"
    else:
        status = "current"
    return {
        "status": status,
        "audioDialog": audio_dialog,
        "wwise": wwise,
    }


def _inspect_audio_dialog(vfs: sqlite3.Connection, database: Path) -> dict:
    if not database.is_file():
        return _unavailable(database, "AudioDialog index is missing")
    try:
        with closing(sqlite3.connect(database)) as conn:
            version = _schema_version(conn, "audio_index_meta")
            if version < 2:
                return {
                    "status": "stale",
                    "database": str(database),
                    "schemaVersion": version,
                    "packageCount": 0,
                    "resolvedPackageCount": 0,
                    "issues": ["schema does not preserve stable PCK identity"],
                }
            rows = conn.execute(
                """
                SELECT DISTINCT pck_file_id, pck_logical_path, pck_file_size
                FROM audio_media
                ORDER BY pck_file_id, pck_logical_path
                """
            ).fetchall()
    except (sqlite3.Error, ValueError) as error:
        return _unavailable(database, str(error))
    return _audit_packages(vfs, database, version, rows)


def _inspect_wwise(vfs: sqlite3.Connection, database: Path) -> dict:
    if not database.is_file():
        return _unavailable(database, "Wwise index is missing")
    try:
        with closing(sqlite3.connect(database)) as conn:
            version = _schema_version(conn, "wwise_index_meta")
            rows = conn.execute(
                """
                SELECT pck_file_id, logical_path, file_size
                FROM wwise_packages
                ORDER BY pck_file_id
                """
            ).fetchall()
    except (sqlite3.Error, ValueError) as error:
        return _unavailable(database, str(error))
    return _audit_packages(vfs, database, version, rows)


def _audit_packages(
    vfs: sqlite3.Connection,
    database: Path,
    schema_version: int,
    rows: list[tuple],
) -> dict:
    issues = []
    resolved = 0
    for pck_file_id, raw_path, raw_size in rows:
        logical_path = str(raw_path or "")
        if not logical_path or raw_size is None:
            issues.append(f"VFS file id {pck_file_id} has no stable PCK identity")
            continue
        current = _resolve_readable_vfs_package(vfs, logical_path)
        if current is None:
            issues.append(f"PCK is unavailable: {logical_path}")
            continue
        current_id, current_size = current
        if int(raw_size) != current_size:
            issues.append(
                f"PCK size changed: {logical_path} ({raw_size} -> {current_size})"
            )
            continue
        resolved += 1
    return {
        "status": "current" if not issues else "stale",
        "database": str(database),
        "schemaVersion": schema_version,
        "packageCount": len(rows),
        "resolvedPackageCount": resolved,
        "verification": "logicalPathAndSize",
        "issues": issues[:10],
    }


def _resolve_readable_vfs_package(
    conn: sqlite3.Connection, logical_path: str
) -> tuple[int, int] | None:
    rows = conn.execute(
        """
        SELECT id, length, chunk_path
        FROM files
        WHERE logical_id = ?
        ORDER BY CASE source WHEN 'Persistent' THEN 0 WHEN 'StreamingAssets' THEN 1 ELSE 9 END,
                 id
        """,
        (logical_path,),
    ).fetchall()
    for file_id, length, chunk_path in rows:
        if Path(chunk_path).is_file():
            return int(file_id), int(length)
    return None


def _schema_version(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(
        f"SELECT value FROM {table} WHERE key = 'schema_version'"
    ).fetchone()
    if row is None:
        raise ValueError("secondary index schema version is missing")
    return int(row[0])


def _unavailable(database: Path, message: str) -> dict:
    return {
        "status": "unavailable",
        "database": str(database),
        "packageCount": 0,
        "resolvedPackageCount": 0,
        "issues": [message],
    }
