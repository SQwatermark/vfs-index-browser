"""SQLite persistence and directory queries for the AudioDialog index."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from collections.abc import Iterable

from audio_dialog_index import AudioDialogMatch, AudioMediaEntry, normalize_audio_language


AUDIO_DIALOG_SCHEMA_VERSION = 3


def create_audio_dialog_schema(conn: sqlite3.Connection) -> None:
    existing_version = _existing_schema_version(conn)
    if (
        existing_version is not None
        and existing_version not in {1, 2, AUDIO_DIALOG_SCHEMA_VERSION}
    ):
        raise RuntimeError(
            "unsupported AudioDialog index schema "
            f"{existing_version}; rebuild with schema {AUDIO_DIALOG_SCHEMA_VERSION}"
        )
    conn.execute("PRAGMA foreign_keys = ON")
    if existing_version == 1:
        conn.execute("ALTER TABLE audio_media ADD COLUMN pck_logical_path TEXT")
        conn.execute("ALTER TABLE audio_media ADD COLUMN pck_file_size INTEGER")
    if existing_version in {1, 2}:
        conn.execute(
            "UPDATE audio_index_meta SET value = ? WHERE key = 'schema_version'",
            (str(AUDIO_DIALOG_SCHEMA_VERSION),),
        )
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS audio_index_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS audio_media (
            physical_key TEXT PRIMARY KEY,
            media_id TEXT NOT NULL,
            pck_file_id INTEGER NOT NULL,
            offset INTEGER NOT NULL,
            size INTEGER NOT NULL,
            source TEXT NOT NULL,
            language TEXT,
            bank_id INTEGER,
            bank_offset INTEGER,
            bank_size INTEGER,
            bank_wem_offset INTEGER,
            bank_encrypted INTEGER NOT NULL,
            pck_logical_path TEXT,
            pck_file_size INTEGER
        );

        CREATE TABLE IF NOT EXISTS audio_dialog (
            language TEXT NOT NULL,
            dialog_key INTEGER NOT NULL,
            logical_path TEXT NOT NULL,
            parent TEXT NOT NULL,
            name TEXT NOT NULL,
            normalized_hash_input TEXT NOT NULL,
            media_id TEXT NOT NULL,
            match_status TEXT NOT NULL CHECK (
                match_status IN ('matched', 'missing', 'ambiguous', 'collision')
            ),
            media_match_count INTEGER NOT NULL,
            PRIMARY KEY (language, dialog_key)
        );

        CREATE TABLE IF NOT EXISTS audio_dialog_media (
            language TEXT NOT NULL,
            dialog_key INTEGER NOT NULL,
            ordinal INTEGER NOT NULL,
            physical_key TEXT NOT NULL,
            PRIMARY KEY (language, dialog_key, ordinal),
            FOREIGN KEY (language, dialog_key)
                REFERENCES audio_dialog(language, dialog_key) ON DELETE CASCADE,
            FOREIGN KEY (physical_key)
                REFERENCES audio_media(physical_key)
        );

        CREATE TABLE IF NOT EXISTS audio_dialog_directories (
            language TEXT NOT NULL,
            path TEXT NOT NULL,
            parent TEXT,
            name TEXT NOT NULL,
            file_count INTEGER NOT NULL,
            matched_count INTEGER NOT NULL,
            missing_count INTEGER NOT NULL,
            ambiguous_count INTEGER NOT NULL,
            collision_count INTEGER NOT NULL,
            PRIMARY KEY (language, path)
        );

        CREATE INDEX IF NOT EXISTS idx_audio_media_id
            ON audio_media(media_id, language);
        CREATE INDEX IF NOT EXISTS idx_audio_dialog_parent
            ON audio_dialog(language, parent, name);
        CREATE INDEX IF NOT EXISTS idx_audio_dialog_directories_parent
            ON audio_dialog_directories(language, parent, name);
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO audio_index_meta(key, value)
        VALUES ('schema_version', ?)
        """,
        (str(AUDIO_DIALOG_SCHEMA_VERSION),),
    )


def replace_audio_dialog_language(
    conn: sqlite3.Connection,
    matches: Iterable[AudioDialogMatch],
) -> None:
    match_list = tuple(matches)
    if not match_list:
        raise ValueError("cannot replace an AudioDialog language with no records")
    languages = {match.record.language for match in match_list}
    if len(languages) != 1:
        raise ValueError("AudioDialog replacement must contain exactly one language")
    language = normalize_audio_language(next(iter(languages)))

    directories = _build_directory_rows(match_list)
    with conn:
        create_audio_dialog_schema(conn)
        conn.execute(
            "DELETE FROM audio_dialog_media WHERE language = ?",
            (language,),
        )
        conn.execute("DELETE FROM audio_dialog WHERE language = ?", (language,))
        conn.execute(
            "DELETE FROM audio_dialog_directories WHERE language = ?",
            (language,),
        )

        for match in match_list:
            record = match.record
            parent, _, name = record.logical_path.rpartition("/")
            conn.execute(
                """
                INSERT INTO audio_dialog (
                    language, dialog_key, logical_path, parent, name,
                    normalized_hash_input, media_id, match_status,
                    media_match_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    language,
                    record.dialog_key,
                    record.logical_path,
                    parent,
                    name,
                    record.normalized_hash_input,
                    record.media_id_hex,
                    match.status,
                    match.media_match_count,
                ),
            )
            for ordinal, media in enumerate(match.media_entries):
                physical_key = _store_media(conn, media)
                conn.execute(
                    """
                    INSERT INTO audio_dialog_media (
                        language, dialog_key, ordinal, physical_key
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (language, record.dialog_key, ordinal, physical_key),
                )

        conn.executemany(
            """
            INSERT INTO audio_dialog_directories (
                language, path, parent, name, file_count, matched_count,
                missing_count, ambiguous_count, collision_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            directories,
        )
        conn.execute(
            """
            DELETE FROM audio_media
            WHERE physical_key NOT IN (
                SELECT DISTINCT physical_key FROM audio_dialog_media
            )
            """
        )


def list_audio_dialog_directory(
    conn: sqlite3.Connection,
    language: str,
    path: str = "",
    *,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    normalized_language = normalize_audio_language(language)
    normalized_path = _normalize_directory_path(path)
    if not 1 <= limit <= 500:
        raise ValueError("limit must be in 1..500")
    if offset < 0:
        raise ValueError("offset must be non-negative")

    directory = conn.execute(
        """
        SELECT path, file_count, matched_count, missing_count,
               ambiguous_count, collision_count
        FROM audio_dialog_directories
        WHERE language = ? AND path = ?
        """,
        (normalized_language, normalized_path),
    ).fetchone()
    if directory is None:
        raise FileNotFoundError("AudioDialog directory not found")

    child_directories = _fetch_dicts(
        conn,
        """
        SELECT name, path, file_count, matched_count, missing_count,
               ambiguous_count, collision_count
        FROM audio_dialog_directories
        WHERE language = ? AND parent = ?
        ORDER BY name COLLATE NOCASE
        """,
        (normalized_language, normalized_path),
    )
    total_files = conn.execute(
        "SELECT COUNT(*) FROM audio_dialog WHERE language = ? AND parent = ?",
        (normalized_language, normalized_path),
    ).fetchone()[0]
    files = _fetch_dicts(
        conn,
        """
        SELECT dialog_key, name, logical_path, media_id, match_status,
               media_match_count
        FROM audio_dialog
        WHERE language = ? AND parent = ?
        ORDER BY name COLLATE NOCASE, dialog_key
        LIMIT ? OFFSET ?
        """,
        (normalized_language, normalized_path, limit, offset),
    )
    return {
        "language": normalized_language,
        "path": normalized_path,
        "summary": _row_to_dict(directory, (
            "path",
            "file_count",
            "matched_count",
            "missing_count",
            "ambiguous_count",
            "collision_count",
        )),
        "directories": child_directories,
        "files": files,
        "page": {
            "offset": offset,
            "limit": limit,
            "total": total_files,
        },
    }


def get_audio_dialog_entry(
    conn: sqlite3.Connection,
    language: str,
    logical_path: str,
) -> list[dict]:
    normalized_language = normalize_audio_language(language)
    rows = _fetch_dicts(
        conn,
        """
        SELECT dialog_key, logical_path, media_id, match_status,
               media_match_count
        FROM audio_dialog
        WHERE language = ? AND logical_path = ?
        ORDER BY dialog_key
        """,
        (normalized_language, logical_path),
    )
    for row in rows:
        row["media"] = _fetch_dicts(
            conn,
            """
            SELECT m.media_id, m.pck_file_id, m.offset, m.size, m.source,
                   m.language, m.bank_id, m.bank_offset, m.bank_size,
                   m.bank_wem_offset, m.bank_encrypted,
                   m.pck_logical_path, m.pck_file_size
            FROM audio_dialog_media dm
            JOIN audio_media m ON m.physical_key = dm.physical_key
            WHERE dm.language = ? AND dm.dialog_key = ?
            ORDER BY dm.ordinal
            """,
            (normalized_language, row["dialog_key"]),
        )
    return rows


def _build_directory_rows(matches: tuple[AudioDialogMatch, ...]) -> list[tuple]:
    stats: dict[str, Counter] = {}
    language = matches[0].record.language
    for match in matches:
        parent, _, _name = match.record.logical_path.rpartition("/")
        paths = [""]
        if parent:
            parts = parent.split("/")
            paths.extend("/".join(parts[:index]) for index in range(1, len(parts) + 1))
        for path in paths:
            counter = stats.setdefault(path, Counter())
            counter["file_count"] += 1
            counter[f"{match.status}_count"] += 1

    rows = []
    for path, counter in sorted(stats.items()):
        parent, _, name = path.rpartition("/")
        rows.append((
            language,
            path,
            parent if path else None,
            name,
            counter["file_count"],
            counter["matched_count"],
            counter["missing_count"],
            counter["ambiguous_count"],
            counter["collision_count"],
        ))
    return rows


def _store_media(conn: sqlite3.Connection, media: AudioMediaEntry) -> str:
    record = media.sqlite_record()
    encoded = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
    physical_key = hashlib.sha256(encoded).hexdigest()
    conn.execute(
        """
        INSERT OR IGNORE INTO audio_media (
            physical_key, media_id, pck_file_id, offset, size, source,
            language, bank_id, bank_offset, bank_size, bank_wem_offset,
            bank_encrypted, pck_logical_path, pck_file_size
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            physical_key,
            record["media_id"],
            record["pck_file_id"],
            record["offset"],
            record["size"],
            record["source"],
            record["language"],
            record["bank_id"],
            record["bank_offset"],
            record["bank_size"],
            record["bank_wem_offset"],
            int(record["bank_encrypted"]),
            record["pck_logical_path"],
            record["pck_file_size"],
        ),
    )
    return physical_key


def _normalize_directory_path(path: str) -> str:
    if not isinstance(path, str):
        raise TypeError("AudioDialog directory path must be a string")
    normalized = path.replace("\\", "/").strip("/")
    if normalized and any(part in {"", ".", ".."} for part in normalized.split("/")):
        raise ValueError("AudioDialog directory path contains a relative segment")
    return normalized


def _existing_schema_version(conn: sqlite3.Connection) -> int | None:
    exists = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'audio_index_meta'
        """
    ).fetchone()
    if exists is None:
        return None
    row = conn.execute(
        "SELECT value FROM audio_index_meta WHERE key = 'schema_version'"
    ).fetchone()
    if row is None:
        raise RuntimeError("AudioDialog index has no schema version")
    try:
        return int(row[0])
    except (TypeError, ValueError) as error:
        raise RuntimeError("AudioDialog index has an invalid schema version") from error


def _fetch_dicts(
    conn: sqlite3.Connection,
    statement: str,
    parameters: tuple,
) -> list[dict]:
    cursor = conn.execute(statement, parameters)
    names = tuple(column[0] for column in cursor.description)
    return [_row_to_dict(row, names) for row in cursor.fetchall()]


def _row_to_dict(row, names: tuple[str, ...]) -> dict:
    return {name: row[index] for index, name in enumerate(names)}
