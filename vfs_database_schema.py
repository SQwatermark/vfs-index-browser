"""SQLite schema and directory publication for the derived VFS index."""

from __future__ import annotations

import sqlite3


def split_parent(path: str) -> tuple[str, str]:
    path = path.strip("/")
    if not path:
        return "", ""
    if "/" not in path:
        return "", path
    return path.rsplit("/", 1)


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode = OFF;
        PRAGMA synchronous = OFF;
        PRAGMA temp_store = MEMORY;

        DROP TABLE IF EXISTS meta;
        DROP TABLE IF EXISTS files;
        DROP TABLE IF EXISTS directories;
        DROP TABLE IF EXISTS entries;

        CREATE TABLE meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE files (
            id INTEGER PRIMARY KEY,
            source TEXT NOT NULL,
            source_root TEXT NOT NULL,
            block_hash TEXT NOT NULL,
            block_name TEXT NOT NULL,
            logical_id TEXT NOT NULL,
            source_logical_id TEXT NOT NULL,
            file_name TEXT NOT NULL,
            file_name_hash TEXT,
            chunk_file TEXT NOT NULL,
            chunk_path TEXT NOT NULL,
            chunk_exists INTEGER NOT NULL,
            chunk_md5_name TEXT,
            chunk_content_md5 TEXT,
            file_chunk_md5 TEXT,
            file_data_md5 TEXT,
            offset INTEGER NOT NULL,
            length INTEGER NOT NULL,
            encrypted INTEGER NOT NULL,
            iv_seed INTEGER NOT NULL
        );

        CREATE TABLE directories (
            scope TEXT NOT NULL,
            path TEXT NOT NULL,
            parent TEXT NOT NULL,
            name TEXT NOT NULL,
            file_count INTEGER NOT NULL,
            total_bytes INTEGER NOT NULL,
            encrypted_count INTEGER NOT NULL,
            missing_chunk_count INTEGER NOT NULL,
            child_dir_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (scope, path)
        );

        CREATE TABLE entries (
            scope TEXT NOT NULL,
            parent TEXT NOT NULL,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            path TEXT NOT NULL,
            file_id INTEGER,
            file_count INTEGER,
            total_bytes INTEGER,
            encrypted_count INTEGER,
            missing_chunk_count INTEGER
        );
        """
    )


def create_indexes(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE INDEX idx_entries_lookup ON entries(scope, parent, type, name);
        CREATE INDEX idx_entries_path ON entries(scope, path);
        CREATE INDEX idx_files_logical ON files(logical_id);
        CREATE INDEX idx_files_source_logical ON files(source_logical_id);
        CREATE INDEX idx_files_file_name ON files(file_name);
        """
    )


def insert_directories(
    connection: sqlite3.Connection,
    directories: dict[tuple[str, str], dict[str, int]],
) -> None:
    child_counts: dict[tuple[str, str], int] = {}
    for scope, path in directories:
        if not path:
            continue
        parent, _ = split_parent(path)
        child_counts[(scope, parent)] = child_counts.get((scope, parent), 0) + 1

    directory_rows = []
    entry_rows = []
    for (scope, path), stats in directories.items():
        parent, name = split_parent(path)
        child_dir_count = child_counts.get((scope, path), 0)
        directory_rows.append(
            (
                scope,
                path,
                parent,
                name,
                stats["file_count"],
                stats["total_bytes"],
                stats["encrypted_count"],
                stats["missing_chunk_count"],
                child_dir_count,
            )
        )
        if path:
            entry_rows.append(
                (
                    scope,
                    parent,
                    "dir",
                    name,
                    path,
                    None,
                    stats["file_count"],
                    stats["total_bytes"],
                    stats["encrypted_count"],
                    stats["missing_chunk_count"],
                )
            )

    connection.executemany(
        """
        INSERT INTO directories (
            scope, path, parent, name, file_count, total_bytes, encrypted_count,
            missing_chunk_count, child_dir_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        directory_rows,
    )
    connection.executemany(
        """
        INSERT INTO entries (
            scope, parent, type, name, path, file_id, file_count,
            total_bytes, encrypted_count, missing_chunk_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        entry_rows,
    )
