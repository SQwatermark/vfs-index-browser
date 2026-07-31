"""SQLite persistence and virtual-directory queries for Wwise audio data."""

from __future__ import annotations

import sqlite3

from audio_package import AudioPackageIndex
from wwise_hirc import normalize_wwise_id


WWISE_SCHEMA_VERSION = 2


def create_wwise_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS wwise_index_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS wwise_packages (
            pck_file_id INTEGER PRIMARY KEY,
            logical_path TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            bank_count INTEGER NOT NULL,
            media_count INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audio_banks (
            pck_file_id INTEGER NOT NULL,
            bank_id INTEGER NOT NULL,
            offset INTEGER NOT NULL,
            size INTEGER NOT NULL,
            language TEXT,
            encrypted INTEGER NOT NULL,
            PRIMARY KEY (pck_file_id, bank_id)
        );
        CREATE TABLE IF NOT EXISTS wwise_media (
            pck_file_id INTEGER NOT NULL,
            ordinal INTEGER NOT NULL,
            media_id TEXT NOT NULL,
            offset INTEGER NOT NULL,
            size INTEGER NOT NULL,
            source TEXT NOT NULL,
            language TEXT,
            bank_id INTEGER,
            bank_offset INTEGER,
            bank_size INTEGER,
            bank_media_offset INTEGER,
            bank_encrypted INTEGER NOT NULL,
            PRIMARY KEY (pck_file_id, ordinal),
            FOREIGN KEY (pck_file_id)
                REFERENCES wwise_packages(pck_file_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS wwise_objects (
            pck_file_id INTEGER NOT NULL,
            bank_id INTEGER NOT NULL,
            object_type INTEGER NOT NULL,
            object_kind TEXT NOT NULL,
            object_id INTEGER NOT NULL,
            payload_offset INTEGER NOT NULL,
            payload_size INTEGER NOT NULL,
            PRIMARY KEY (pck_file_id, bank_id, object_type, object_id),
            FOREIGN KEY (pck_file_id, bank_id)
                REFERENCES audio_banks(pck_file_id, bank_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS wwise_relations (
            pck_file_id INTEGER NOT NULL,
            bank_id INTEGER NOT NULL,
            ordinal INTEGER NOT NULL,
            source_kind TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            relation TEXT NOT NULL,
            target_kind TEXT NOT NULL,
            target_id INTEGER NOT NULL,
            confidence TEXT NOT NULL,
            evidence TEXT NOT NULL,
            PRIMARY KEY (pck_file_id, bank_id, ordinal),
            FOREIGN KEY (pck_file_id, bank_id)
                REFERENCES audio_banks(pck_file_id, bank_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS wwise_diagnostics (
            pck_file_id INTEGER NOT NULL,
            bank_id INTEGER NOT NULL,
            ordinal INTEGER NOT NULL,
            object_type INTEGER NOT NULL,
            object_kind TEXT NOT NULL,
            object_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            PRIMARY KEY (pck_file_id, bank_id, ordinal),
            FOREIGN KEY (pck_file_id, bank_id)
                REFERENCES audio_banks(pck_file_id, bank_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_wwise_object_identity
            ON wwise_objects(object_kind, object_id);
        CREATE INDEX IF NOT EXISTS idx_wwise_relation_source
            ON wwise_relations(source_kind, source_id);
        CREATE INDEX IF NOT EXISTS idx_wwise_relation_target
            ON wwise_relations(target_kind, target_id);
        CREATE INDEX IF NOT EXISTS idx_wwise_media_identity
            ON wwise_media(media_id, language);
        """
    )
    row = conn.execute(
        "SELECT value FROM wwise_index_meta WHERE key = 'schema_version'"
    ).fetchone()
    if row is not None and int(row[0]) != WWISE_SCHEMA_VERSION:
        raise RuntimeError(
            f"unsupported Wwise schema {row[0]}; rebuild with schema {WWISE_SCHEMA_VERSION}"
        )
    if row is None:
        conn.execute(
            "INSERT INTO wwise_index_meta VALUES ('schema_version', ?)",
            (str(WWISE_SCHEMA_VERSION),),
        )


def validate_wwise_schema(conn: sqlite3.Connection) -> None:
    try:
        row = conn.execute(
            "SELECT value FROM wwise_index_meta WHERE key = 'schema_version'"
        ).fetchone()
    except sqlite3.OperationalError as error:
        raise RuntimeError("Wwise index schema is missing; rebuild the index") from error
    if row is None or int(row[0]) != WWISE_SCHEMA_VERSION:
        version = None if row is None else row[0]
        raise RuntimeError(
            f"unsupported Wwise schema {version}; rebuild with schema {WWISE_SCHEMA_VERSION}"
        )


def replace_wwise_package(
    conn: sqlite3.Connection,
    pck_file_id: int,
    package: AudioPackageIndex,
    *,
    logical_path: str = "",
) -> None:
    if pck_file_id < 0:
        raise ValueError("pck_file_id must be non-negative")
    with conn:
        create_wwise_schema(conn)
        conn.execute("DELETE FROM audio_banks WHERE pck_file_id = ?", (pck_file_id,))
        conn.execute("DELETE FROM wwise_packages WHERE pck_file_id = ?", (pck_file_id,))
        conn.execute(
            "INSERT INTO wwise_packages VALUES (?, ?, ?, ?, ?)",
            (
                pck_file_id,
                logical_path,
                package.file_size,
                len(package.banks),
                len(package.media),
            ),
        )
        conn.executemany(
            "INSERT INTO wwise_media VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    pck_file_id,
                    ordinal,
                    encode_wwise_media_id(item.media_id),
                    item.offset,
                    item.size,
                    item.source,
                    item.language,
                    item.bank_id,
                    item.bank_offset,
                    item.bank_size,
                    item.bank_media_offset,
                    int(item.bank_encrypted),
                )
                for ordinal, item in enumerate(package.media)
            ],
        )
        for bank in package.banks:
            conn.execute(
                "INSERT INTO audio_banks VALUES (?, ?, ?, ?, ?, ?)",
                (
                    pck_file_id,
                    bank.bank_id,
                    bank.offset,
                    bank.size,
                    bank.language,
                    int(bank.encrypted),
                ),
            )
            conn.executemany(
                "INSERT INTO wwise_objects VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        pck_file_id,
                        bank.bank_id,
                        item.object_type,
                        item.kind,
                        item.object_id,
                        item.payload_offset,
                        item.payload_size,
                    )
                    for item in bank.graph.objects
                ],
            )
            conn.executemany(
                "INSERT INTO wwise_relations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        pck_file_id,
                        bank.bank_id,
                        ordinal,
                        edge.source_kind,
                        edge.source_id,
                        edge.relation,
                        edge.target_kind,
                        edge.target_id,
                        edge.confidence,
                        edge.evidence,
                    )
                    for ordinal, edge in enumerate(bank.graph.relations)
                ],
            )
            conn.executemany(
                "INSERT INTO wwise_diagnostics VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        pck_file_id,
                        bank.bank_id,
                        ordinal,
                        item.object_type,
                        item.object_kind,
                        item.object_id,
                        item.message,
                    )
                    for ordinal, item in enumerate(bank.graph.diagnostics)
                ],
            )


def event_media_ids(
    conn: sqlite3.Connection,
    event_id: int,
    *,
    pck_file_id: int | None = None,
    bank_id: int | None = None,
) -> tuple[int, ...]:
    """Traverse persisted relations without assuming that IDs imply filenames."""

    validate_wwise_schema(conn)
    normalized_event_id = normalize_wwise_id(event_id)
    package_filter = "AND pck_file_id = ?" if pck_file_id is not None else ""
    bank_filter = "AND bank_id = ?" if bank_id is not None else ""
    parameters = [normalized_event_id]
    if pck_file_id is not None:
        parameters.append(pck_file_id)
    if bank_id is not None:
        parameters.append(bank_id)
    rows = conn.execute(
        f"""
        WITH RECURSIVE graph(pck_file_id, bank_id, kind, id) AS (
            SELECT pck_file_id, bank_id, object_kind, object_id
            FROM wwise_objects
            WHERE object_kind = 'event' AND object_id = ? {package_filter} {bank_filter}
            UNION
            SELECT r.pck_file_id, r.bank_id, r.target_kind, r.target_id
            FROM wwise_relations r
            JOIN graph g ON g.pck_file_id = r.pck_file_id
                        AND g.bank_id = r.bank_id
                        AND g.kind = r.source_kind
                        AND g.id = r.source_id
        )
        SELECT DISTINCT id FROM graph WHERE kind = 'media' ORDER BY id
        """,
        parameters,
    ).fetchall()
    return tuple(int(row[0]) for row in rows)


def wwise_summary(conn: sqlite3.Connection) -> dict:
    validate_wwise_schema(conn)
    row = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM wwise_packages),
            (SELECT COUNT(*) FROM audio_banks),
            (SELECT COUNT(*) FROM wwise_objects WHERE object_kind = 'event'),
            (SELECT COUNT(*) FROM wwise_media),
            (SELECT COALESCE(SUM(size), 0) FROM wwise_media)
        """
    ).fetchone()
    return {
        "packageCount": int(row[0]),
        "bankCount": int(row[1]),
        "eventCount": int(row[2]),
        "mediaCount": int(row[3]),
        "mediaBytes": int(row[4]),
    }


def list_wwise_banks(
    conn: sqlite3.Connection,
    *,
    limit: int,
    offset: int,
) -> tuple[int, list[dict]]:
    validate_wwise_schema(conn)
    total = int(conn.execute("SELECT COUNT(*) FROM audio_banks").fetchone()[0])
    rows = _fetch_dicts(
        conn,
        """
        SELECT b.pck_file_id, p.logical_path, b.bank_id, b.offset, b.size,
               b.language, b.encrypted,
               (SELECT COUNT(*) FROM wwise_objects o
                WHERE o.pck_file_id = b.pck_file_id AND o.bank_id = b.bank_id)
                   AS object_count,
               (SELECT COUNT(*) FROM wwise_relations r
                WHERE r.pck_file_id = b.pck_file_id AND r.bank_id = b.bank_id)
                   AS relation_count,
               (SELECT COUNT(*) FROM wwise_diagnostics d
                WHERE d.pck_file_id = b.pck_file_id AND d.bank_id = b.bank_id)
                   AS diagnostic_count
        FROM audio_banks b
        JOIN wwise_packages p ON p.pck_file_id = b.pck_file_id
        ORDER BY b.bank_id, b.pck_file_id
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    )
    return total, rows


def list_wwise_events(
    conn: sqlite3.Connection,
    *,
    limit: int,
    offset: int,
) -> tuple[int, list[dict]]:
    validate_wwise_schema(conn)
    total = int(
        conn.execute(
            "SELECT COUNT(*) FROM wwise_objects WHERE object_kind = 'event'"
        ).fetchone()[0]
    )
    rows = _fetch_dicts(
        conn,
        """
        SELECT o.pck_file_id, p.logical_path, o.bank_id, o.object_id AS event_id,
               o.payload_offset, o.payload_size,
               (SELECT COUNT(*) FROM wwise_relations r
                WHERE r.pck_file_id = o.pck_file_id AND r.bank_id = o.bank_id
                  AND r.source_kind = 'event' AND r.source_id = o.object_id)
                   AS direct_relation_count
        FROM wwise_objects o
        JOIN wwise_packages p ON p.pck_file_id = o.pck_file_id
        WHERE o.object_kind = 'event'
        ORDER BY o.object_id, o.pck_file_id, o.bank_id
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    )
    return total, rows


def list_wwise_media_prefixes(conn: sqlite3.Connection) -> list[dict]:
    validate_wwise_schema(conn)
    return _fetch_dicts(
        conn,
        """
        SELECT substr(media_id, -2) AS prefix,
               COUNT(*) AS file_count, SUM(size) AS total_bytes
        FROM wwise_media
        GROUP BY prefix
        ORDER BY prefix
        """,
        (),
    )


def list_wwise_media(
    conn: sqlite3.Connection,
    prefix: str,
    *,
    limit: int,
    offset: int,
) -> tuple[int, list[dict]]:
    validate_wwise_schema(conn)
    if len(prefix) != 2 or any(char not in "0123456789abcdef" for char in prefix):
        raise ValueError("Wwise media prefix must contain two hexadecimal digits")
    total = int(
        conn.execute(
            "SELECT COUNT(*) FROM wwise_media WHERE substr(media_id, -2) = ?",
            (prefix,),
        ).fetchone()[0]
    )
    rows = _fetch_dicts(
        conn,
        """
        SELECT m.*, p.logical_path
        FROM wwise_media m
        JOIN wwise_packages p ON p.pck_file_id = m.pck_file_id
        WHERE substr(m.media_id, -2) = ?
        ORDER BY m.media_id, m.pck_file_id, m.ordinal
        LIMIT ? OFFSET ?
        """,
        (prefix, limit, offset),
    )
    return total, rows


def get_wwise_event(
    conn: sqlite3.Connection,
    pck_file_id: int,
    bank_id: int,
    event_id: int,
) -> dict | None:
    validate_wwise_schema(conn)
    event = conn.execute(
        """
        SELECT o.pck_file_id, p.logical_path, o.bank_id, o.object_id AS event_id,
               o.payload_offset, o.payload_size
        FROM wwise_objects o
        JOIN wwise_packages p ON p.pck_file_id = o.pck_file_id
        WHERE o.pck_file_id = ? AND o.bank_id = ?
          AND o.object_kind = 'event' AND o.object_id = ?
        """,
        (pck_file_id, bank_id, normalize_wwise_id(event_id)),
    ).fetchone()
    if event is None:
        return None
    media_ids = event_media_ids(
        conn,
        event_id,
        pck_file_id=pck_file_id,
        bank_id=bank_id,
    )
    relations = _reachable_relations(conn, pck_file_id, bank_id, event_id)
    media = []
    for media_id in media_ids:
        media.extend(
            _fetch_dicts(
                conn,
                """
                SELECT m.*, p.logical_path
                FROM wwise_media m
                JOIN wwise_packages p ON p.pck_file_id = m.pck_file_id
                WHERE m.media_id = ?
                ORDER BY CASE WHEN m.language = 'sfx' THEN 0 ELSE 1 END,
                         m.pck_file_id, m.ordinal
                """,
                (encode_wwise_media_id(media_id),),
            )
        )
    names = ("pck_file_id", "logical_path", "bank_id", "event_id", "payload_offset", "payload_size")
    return {
        **{name: event[index] for index, name in enumerate(names)},
        "relations": relations,
        "media_ids": list(media_ids),
        "media": media,
    }


def get_wwise_bank(
    conn: sqlite3.Connection,
    pck_file_id: int,
    bank_id: int,
) -> dict | None:
    validate_wwise_schema(conn)
    rows = _fetch_dicts(
        conn,
        """
        SELECT b.*, p.logical_path,
               (SELECT COUNT(*) FROM wwise_objects o
                WHERE o.pck_file_id = b.pck_file_id AND o.bank_id = b.bank_id)
                   AS object_count,
               (SELECT COUNT(*) FROM wwise_relations r
                WHERE r.pck_file_id = b.pck_file_id AND r.bank_id = b.bank_id)
                   AS relation_count,
               (SELECT COUNT(*) FROM wwise_diagnostics d
                WHERE d.pck_file_id = b.pck_file_id AND d.bank_id = b.bank_id)
                   AS diagnostic_count
        FROM audio_banks b
        JOIN wwise_packages p ON p.pck_file_id = b.pck_file_id
        WHERE b.pck_file_id = ? AND b.bank_id = ?
        """,
        (pck_file_id, bank_id),
    )
    if not rows:
        return None
    rows[0]["object_kinds"] = _fetch_dicts(
        conn,
        """
        SELECT object_kind AS kind, COUNT(*) AS count
        FROM wwise_objects
        WHERE pck_file_id = ? AND bank_id = ?
        GROUP BY object_kind ORDER BY object_kind
        """,
        (pck_file_id, bank_id),
    )
    rows[0]["diagnostics"] = _fetch_dicts(
        conn,
        """
        SELECT object_kind, object_id, message
        FROM wwise_diagnostics
        WHERE pck_file_id = ? AND bank_id = ?
        ORDER BY ordinal
        """,
        (pck_file_id, bank_id),
    )
    return rows[0]


def get_wwise_media(
    conn: sqlite3.Connection,
    pck_file_id: int,
    ordinal: int,
) -> dict | None:
    validate_wwise_schema(conn)
    rows = _fetch_dicts(
        conn,
        """
        SELECT m.*, p.logical_path
        FROM wwise_media m
        JOIN wwise_packages p ON p.pck_file_id = m.pck_file_id
        WHERE m.pck_file_id = ? AND m.ordinal = ?
        """,
        (pck_file_id, ordinal),
    )
    return rows[0] if rows else None


def _reachable_relations(
    conn: sqlite3.Connection,
    pck_file_id: int,
    bank_id: int,
    event_id: int,
) -> list[dict]:
    return _fetch_dicts(
        conn,
        """
        WITH RECURSIVE nodes(kind, id) AS (
            VALUES ('event', ?)
            UNION
            SELECT r.target_kind, r.target_id
            FROM wwise_relations r
            JOIN nodes n ON n.kind = r.source_kind AND n.id = r.source_id
            WHERE r.pck_file_id = ? AND r.bank_id = ?
        )
        SELECT r.source_kind, r.source_id, r.relation,
               r.target_kind, r.target_id, r.confidence, r.evidence
        FROM wwise_relations r
        JOIN nodes n ON n.kind = r.source_kind AND n.id = r.source_id
        WHERE r.pck_file_id = ? AND r.bank_id = ?
        ORDER BY r.ordinal
        """,
        (normalize_wwise_id(event_id), pck_file_id, bank_id, pck_file_id, bank_id),
    )


def _fetch_dicts(
    conn: sqlite3.Connection,
    statement: str,
    parameters: tuple,
) -> list[dict]:
    cursor = conn.execute(statement, parameters)
    names = tuple(column[0] for column in cursor.description)
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def encode_wwise_media_id(value: int) -> str:
    if not isinstance(value, int) or not 0 <= value < (1 << 64):
        raise ValueError("Wwise media ID must fit uint64")
    return f"{value:016x}"
