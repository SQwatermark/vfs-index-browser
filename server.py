#!/usr/bin/env python3
"""Serve a small local browser for the Endfield VFS JSONL index."""

from __future__ import annotations

import argparse
import gzip
import io
import json
import mimetypes
import os
import re
import sqlite3
import struct
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable, Iterator
from urllib.parse import parse_qs, quote, unquote, urlparse


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_INDEX = (
    PROJECT_ROOT.parent
    / "Endaxis"
    / "zmd-research"
    / "analysis"
    / "database"
    / "facts"
    / "endfield-vfs-index-20260727-234026.jsonl.tgz"
)
DEFAULT_DB = PROJECT_ROOT / "data" / "endfield-vfs-index.sqlite"
PUBLIC_DIR = PROJECT_ROOT / "public"
INTERNAL_CACHE_DIR = Path(os.environ.get("VFS_BROWSER_INTERNAL_CACHE", PROJECT_ROOT / "data" / "internal-cache"))
ANIMESTUDIO_CLI = Path(
    os.environ.get(
        "ANIMESTUDIO_CLI",
        r"D:\Projects\AnimeStudio\AnimeStudio.CLI\bin\Release\net10.0-windows\AnimeStudio.CLI.exe",
    )
)
VGMSTREAM_CLI = Path(
    os.environ.get(
        "VGMSTREAM_CLI",
        PROJECT_ROOT / "tools" / "vgmstream" / "vgmstream-cli.exe",
    )
)

CHACHA_KEY = bytes.fromhex(
    "e95b317ac4f828569d23a86bf271dcb53e846fa75c924d671dba8e38f4ca52e1"
)
VFS_PROTO_VERSION = 3
ASSETBUNDLE_META_VERSION = 2
AUDIO_PACKAGE_META_VERSION = 1
PREVIEW_TEXT_LIMIT = 2 * 1024 * 1024
PREVIEW_BINARY_LIMIT = 256 * 1024
STREAM_CHUNK_SIZE = 1024 * 1024

SOURCE_PRIORITY = {
    "Persistent": 0,
    "StreamingAssets": 1,
}

TEXT_EXTENSIONS = {".json", ".lua", ".md", ".txt", ".csv", ".xml", ".yaml", ".yml"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".ogg", ".mov"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".m4a"}
CONTAINER_EXTENSIONS = {".ab", ".pck", ".usm", ".hgmmap"}
ASSETBUNDLE_EXPORT_TYPES = ("Texture2D", "Sprite", "TextAsset", "AudioClip", "VideoClip")
AUDIO_ENTRY_RE = re.compile(r"^(wem|wav)/([0-9a-f]{1,2})/([0-9]+)\.(wem|wav)$", re.IGNORECASE)


@dataclass(frozen=True)
class FileView:
    file_id: int
    path: str
    name: str
    source: str
    chunk_exists: bool
    length: int
    encrypted: bool


@dataclass(frozen=True)
class AudioEntry:
    wem_id: int
    offset: int
    size: int
    source: str
    language: str | None = None
    bank_id: int | None = None
    bank_offset: int | None = None
    bank_size: int | None = None
    bank_wem_offset: int | None = None
    bank_encrypted: bool = False

    def to_json(self) -> dict:
        return {
            "id": self.wem_id,
            "offset": self.offset,
            "size": self.size,
            "source": self.source,
            "language": self.language,
            "bankId": self.bank_id,
            "bankOffset": self.bank_offset,
            "bankSize": self.bank_size,
            "bankWemOffset": self.bank_wem_offset,
            "bankEncrypted": self.bank_encrypted,
        }

    @staticmethod
    def from_json(payload: dict) -> "AudioEntry":
        return AudioEntry(
            wem_id=int(payload["id"]),
            offset=int(payload["offset"]),
            size=int(payload["size"]),
            source=str(payload.get("source") or "unknown"),
            language=payload.get("language"),
            bank_id=payload.get("bankId"),
            bank_offset=payload.get("bankOffset"),
            bank_size=payload.get("bankSize"),
            bank_wem_offset=payload.get("bankWemOffset"),
            bank_encrypted=bool(payload.get("bankEncrypted")),
        )


def open_index(path: Path) -> Iterator[str]:
    name = path.name.lower()
    if name.endswith(".tgz") or name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            members = [member for member in archive.getmembers() if member.isfile()]
            if len(members) != 1:
                raise ValueError(f"expected one JSONL file in archive, found {len(members)}")
            raw = archive.extractfile(members[0])
            if raw is None:
                raise ValueError(f"cannot read {members[0].name} from {path}")
            with io.TextIOWrapper(raw, encoding="utf-8") as reader:
                yield from reader
        return

    if name.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as reader:
            yield from reader
        return

    with path.open("r", encoding="utf-8") as reader:
        yield from reader


def split_parent(path: str) -> tuple[str, str]:
    path = path.strip("/")
    if not path:
        return "", ""
    if "/" not in path:
        return "", path
    parent, name = path.rsplit("/", 1)
    return parent, name


def rotl32(value: int, bits: int) -> int:
    return ((value << bits) & 0xFFFFFFFF) | (value >> (32 - bits))


def quarter_round(state: list[int], a: int, b: int, c: int, d: int) -> None:
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] = rotl32(state[d] ^ state[a], 16)
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] = rotl32(state[b] ^ state[c], 12)
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] = rotl32(state[d] ^ state[a], 8)
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] = rotl32(state[b] ^ state[c], 7)


def chacha20_apply(key: bytes, nonce12: bytes, counter: int, data: bytes) -> bytes:
    if len(key) != 32:
        raise ValueError("ChaCha20 key must be 32 bytes")
    if len(nonce12) != 12:
        raise ValueError("ChaCha20 nonce must be 12 bytes")

    constants = (0x61707865, 0x3320646E, 0x79622D32, 0x6B206574)
    key_words = list(struct.unpack("<8I", key))
    nonce_words = list(struct.unpack("<3I", nonce12))
    output = bytearray(data)
    offset = 0
    block_counter = counter & 0xFFFFFFFF
    nonce0 = nonce_words[0]

    while offset < len(output):
        state = [
            *constants,
            *key_words,
            block_counter,
            nonce0,
            nonce_words[1],
            nonce_words[2],
        ]
        working = state.copy()
        for _ in range(10):
            quarter_round(working, 0, 4, 8, 12)
            quarter_round(working, 1, 5, 9, 13)
            quarter_round(working, 2, 6, 10, 14)
            quarter_round(working, 3, 7, 11, 15)
            quarter_round(working, 0, 5, 10, 15)
            quarter_round(working, 1, 6, 11, 12)
            quarter_round(working, 2, 7, 8, 13)
            quarter_round(working, 3, 4, 9, 14)
        block = struct.pack("<16I", *((working[i] + state[i]) & 0xFFFFFFFF for i in range(16)))
        for i, value in enumerate(block[: len(output) - offset]):
            output[offset + i] ^= value
        offset += 64
        block_counter = (block_counter + 1) & 0xFFFFFFFF
        if block_counter == 0:
            nonce0 = (nonce0 + 1) & 0xFFFFFFFF

    return bytes(output)


def decrypt_vfs_file(data: bytes, iv_seed: int) -> bytes:
    nonce = struct.pack("<iq", VFS_PROTO_VERSION, int(iv_seed))
    return chacha20_apply(CHACHA_KEY, nonce, 1, data)


def derive_audio_key(seed: int) -> int:
    key = ((seed & 0xFF) ^ 0x9C5A0B29) * 81861667
    key &= 0xFFFFFFFF
    for shift in (8, 16, 24):
        key = (key ^ ((seed >> shift) & 0xFF)) * 81861667
        key &= 0xFFFFFFFF
    return key


def decrypt_audio_vfs_bytes(data: bytearray, start: int, length: int, seed: int, data_offset: int = 0) -> None:
    if start < 0 or length < 0 or start > len(data) or length > len(data) - start:
        raise ValueError("invalid audio decrypt range")

    key_index = (seed + (data_offset >> 2)) & 0xFFFFFFFF
    pos = start
    remaining = length
    alignment = data_offset & 3
    if alignment:
        key = derive_audio_key(key_index)
        to_align = min(4 - alignment, remaining)
        for i in range(to_align):
            data[pos] ^= (key >> ((alignment + i) * 8)) & 0xFF
            pos += 1
        remaining -= to_align
        key_index = (key_index + 1) & 0xFFFFFFFF

    for _ in range(remaining // 4):
        key = derive_audio_key(key_index)
        value = int.from_bytes(data[pos : pos + 4], "little") ^ key
        data[pos : pos + 4] = value.to_bytes(4, "little")
        pos += 4
        key_index = (key_index + 1) & 0xFFFFFFFF

    trailing = remaining & 3
    if trailing:
        key = derive_audio_key(key_index)
        for i in range(trailing):
            data[pos + i] ^= (key >> (i * 8)) & 0xFF


def decrypt_wem_bytes(data: bytes, wem_id: int) -> bytes:
    output = bytearray(data)
    decrypt_audio_vfs_bytes(output, 0, len(output), wem_id)
    return bytes(output)


def parse_akpk_header(header: bytes, label: str) -> tuple[bytes, int]:
    if len(header) < 28:
        raise ValueError(f"invalid AKPK header: {label}")
    data = bytearray(header)
    if data[:4] == b":)xD":
        header_size = int.from_bytes(data[4:8], "little")
        if header_size < 4 or header_size + 8 > len(data):
            raise ValueError(f"invalid encrypted AKPK header size: {label}")
        # 终末地音频包的包头会用同一套轻量异或流加密；解开后才是标准 AKPK 结构。
        decrypt_audio_vfs_bytes(data, 12, header_size - 4, header_size)
        data[:4] = b"AKPK"
        data[8:12] = (1).to_bytes(4, "little")
    if data[:4] != b"AKPK":
        raise ValueError(f"invalid AKPK magic: {label}")
    return bytes(data), int.from_bytes(data[4:8], "little")


def parse_akpk_languages(data: bytes, start: int, sector_size: int) -> dict[int, str]:
    languages = {}
    if sector_size < 4 or start + sector_size > len(data):
        return languages
    count = int.from_bytes(data[start : start + 4], "little")
    pos = start + 4
    for _ in range(count):
        if pos + 8 > start + sector_size:
            break
        name_offset = int.from_bytes(data[pos : pos + 4], "little")
        lang_id = int.from_bytes(data[pos + 4 : pos + 8], "little")
        name_start = start + name_offset
        name_end = min(start + sector_size, name_start + 32)
        raw = data[name_start:name_end]
        if len(raw) >= 2 and (raw[0] == 0 or raw[1] == 0):
            name = raw.decode("utf-16-le", errors="ignore").split("\x00", 1)[0]
        else:
            name = raw.decode("utf-8", errors="ignore").split("\x00", 1)[0]
        if name:
            languages[lang_id] = name
        pos += 8
    return languages


def parse_bnk_wem_ranges(payload: bytes) -> list[tuple[int, int, int]]:
    if len(payload) < 16 or payload[:4] != b"BKHD":
        return []
    bkhd_size = int.from_bytes(payload[4:8], "little")
    pos = 8 + bkhd_size
    end = len(payload)
    if pos + 8 > end or payload[pos : pos + 4] != b"DIDX":
        return []
    didx_size = int.from_bytes(payload[pos + 4 : pos + 8], "little")
    pos += 8
    rows = []
    for _ in range(didx_size // 12):
        if pos + 12 > end:
            return []
        wem_id = int.from_bytes(payload[pos : pos + 4], "little")
        wem_offset = int.from_bytes(payload[pos + 4 : pos + 8], "little")
        wem_size = int.from_bytes(payload[pos + 8 : pos + 12], "little")
        rows.append((wem_id, wem_offset, wem_size))
        pos += 12
    if pos + 8 > end or payload[pos : pos + 4] != b"DATA":
        return []
    data_offset = pos + 8
    return [(wem_id, data_offset + wem_offset, wem_size) for wem_id, wem_offset, wem_size in rows]


def audio_entry_prefix(wem_id: int) -> str:
    return f"{wem_id:x}"[:2].rjust(2, "0")


def parse_audio_internal_path(raw_path: str) -> tuple[str, int] | None:
    normalized = unquote(raw_path).replace("\\", "/").strip("/")
    match = AUDIO_ENTRY_RE.match(normalized)
    if not match or match.group(1).lower() != match.group(4).lower():
        return None
    return match.group(1).lower(), int(match.group(3))


def iter_ancestor_dirs(file_path: str) -> Iterator[str]:
    yield ""
    parts = [part for part in file_path.split("/") if part]
    current: list[str] = []
    for part in parts[:-1]:
        current.append(part)
        yield "/".join(current)


def add_dir_stats(
    dirs: dict[tuple[str, str], dict[str, int]],
    scope: str,
    file_path: str,
    length: int,
    encrypted: bool,
    chunk_exists: bool,
) -> None:
    for dir_path in iter_ancestor_dirs(file_path):
        stats = dirs.setdefault(
            (scope, dir_path),
            {"file_count": 0, "total_bytes": 0, "encrypted_count": 0, "missing_chunk_count": 0},
        )
        stats["file_count"] += 1
        stats["total_bytes"] += length
        if encrypted:
            stats["encrypted_count"] += 1
        if not chunk_exists:
            stats["missing_chunk_count"] += 1


def queue_file_entry(
    batch: list[tuple],
    scope: str,
    view: FileView,
) -> None:
    parent, name = split_parent(view.path)
    batch.append(
        (
            scope,
            parent,
            "file",
            name,
            view.path,
            view.file_id,
            view.length,
            int(view.encrypted),
            int(not view.chunk_exists),
        )
    )


def flush_file_entries(conn: sqlite3.Connection, batch: list[tuple]) -> None:
    if not batch:
        return
    conn.executemany(
        """
        INSERT INTO entries (
            scope, parent, type, name, path, file_id, total_bytes, encrypted_count, missing_chunk_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        batch,
    )
    batch.clear()


def source_rank(source: str, chunk_exists: bool) -> tuple[int, int]:
    return (0 if chunk_exists else 1, SOURCE_PRIORITY.get(source, 99))


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
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


def create_indexes(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE INDEX idx_entries_lookup ON entries(scope, parent, type, name);
        CREATE INDEX idx_entries_path ON entries(scope, path);
        CREATE INDEX idx_files_logical ON files(logical_id);
        CREATE INDEX idx_files_source_logical ON files(source_logical_id);
        CREATE INDEX idx_files_file_name ON files(file_name);
        """
    )


def insert_directories(conn: sqlite3.Connection, dirs: dict[tuple[str, str], dict[str, int]]) -> None:
    child_counts: dict[tuple[str, str], int] = {}
    for scope, path in dirs:
        if not path:
            continue
        parent, _ = split_parent(path)
        child_counts[(scope, parent)] = child_counts.get((scope, parent), 0) + 1

    directory_rows = []
    entry_rows = []
    for (scope, path), stats in dirs.items():
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

    conn.executemany(
        """
        INSERT INTO directories (
            scope, path, parent, name, file_count, total_bytes, encrypted_count,
            missing_chunk_count, child_dir_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        directory_rows,
    )
    conn.executemany(
        """
        INSERT INTO entries (
            scope, parent, type, name, path, file_id, file_count,
            total_bytes, encrypted_count, missing_chunk_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        entry_rows,
    )


def build_database(index_path: Path, db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    started = time.time()
    conn = sqlite3.connect(db_path)
    create_schema(conn)

    dirs: dict[tuple[str, str], dict[str, int]] = {}
    file_batch: list[tuple] = []
    entry_batch: list[tuple] = []
    effective: dict[str, tuple[tuple[int, int], FileView]] = {}
    header = None
    summary = None
    file_count = 0

    for line in open_index(index_path):
        record = json.loads(line)
        record_type = record.get("recordType")
        if record_type == "header":
            header = record
            continue
        if record_type == "summary":
            summary = record
            continue
        if record_type != "file":
            continue

        file_count += 1
        file_row = (
            record["source"],
            record.get("sourceRoot", ""),
            record["blockHash"],
            record["blockName"],
            record["logicalId"],
            record["sourceLogicalId"],
            record["fileName"],
            str(record.get("fileNameHash", "")),
            record["chunkFile"],
            record.get("chunkPath", ""),
            int(record.get("chunkExists", False)),
            record.get("chunkMd5Name"),
            record.get("chunkContentMd5"),
            record.get("fileChunkMd5"),
            record.get("fileDataMd5"),
            record["offset"],
            record["length"],
            int(record.get("encrypted", False)),
            record.get("ivSeed") or 0,
        )
        file_batch.append(file_row)
        if len(file_batch) >= 5000:
            conn.executemany(
                """
                INSERT INTO files (
                    source, source_root, block_hash, block_name, logical_id, source_logical_id,
                    file_name, file_name_hash, chunk_file, chunk_path, chunk_exists,
                    chunk_md5_name, chunk_content_md5, file_chunk_md5, file_data_md5,
                    offset, length, encrypted, iv_seed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                file_batch,
            )
            first_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0] - len(file_batch) + 1
            for i, row in enumerate(file_batch):
                file_id = first_id + i
                source = row[0]
                block_name = row[3]
                logical_id = row[4]
                length = int(row[16])
                encrypted = bool(row[17])
                chunk_exists = bool(row[10])
                source_path = logical_id
                all_path = f"{source}/{logical_id}"

                source_view = FileView(file_id, source_path, split_parent(source_path)[1], source, chunk_exists, length, encrypted)
                all_view = FileView(file_id, all_path, split_parent(all_path)[1], source, chunk_exists, length, encrypted)
                queue_file_entry(entry_batch, source, source_view)
                queue_file_entry(entry_batch, "all", all_view)
                add_dir_stats(dirs, source, source_path, length, encrypted, chunk_exists)
                add_dir_stats(dirs, "all", all_path, length, encrypted, chunk_exists)

                rank = source_rank(source, chunk_exists)
                current = effective.get(logical_id)
                if current is None or rank < current[0]:
                    effective[logical_id] = (
                        rank,
                        FileView(file_id, logical_id, split_parent(logical_id)[1], source, chunk_exists, length, encrypted),
                    )
            flush_file_entries(conn, entry_batch)
            file_batch.clear()

        if file_count % 100000 == 0:
            print(f"indexed {file_count:,} file records", flush=True)

    if file_batch:
        conn.executemany(
            """
            INSERT INTO files (
                source, source_root, block_hash, block_name, logical_id, source_logical_id,
                file_name, file_name_hash, chunk_file, chunk_path, chunk_exists,
                chunk_md5_name, chunk_content_md5, file_chunk_md5, file_data_md5,
                offset, length, encrypted, iv_seed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            file_batch,
        )
        first_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0] - len(file_batch) + 1
        for i, row in enumerate(file_batch):
            file_id = first_id + i
            source = row[0]
            logical_id = row[4]
            length = int(row[16])
            encrypted = bool(row[17])
            chunk_exists = bool(row[10])
            all_path = f"{source}/{logical_id}"
            queue_file_entry(entry_batch, source, FileView(file_id, logical_id, split_parent(logical_id)[1], source, chunk_exists, length, encrypted))
            queue_file_entry(entry_batch, "all", FileView(file_id, all_path, split_parent(all_path)[1], source, chunk_exists, length, encrypted))
            add_dir_stats(dirs, source, logical_id, length, encrypted, chunk_exists)
            add_dir_stats(dirs, "all", all_path, length, encrypted, chunk_exists)
            rank = source_rank(source, chunk_exists)
            current = effective.get(logical_id)
            if current is None or rank < current[0]:
                effective[logical_id] = (
                    rank,
                    FileView(file_id, logical_id, split_parent(logical_id)[1], source, chunk_exists, length, encrypted),
                )
        flush_file_entries(conn, entry_batch)

    for _, view in effective.values():
        queue_file_entry(entry_batch, "effective", view)
        add_dir_stats(dirs, "effective", view.path, view.length, view.encrypted, view.chunk_exists)
        if len(entry_batch) >= 5000:
            flush_file_entries(conn, entry_batch)
    flush_file_entries(conn, entry_batch)

    insert_directories(conn, dirs)
    create_indexes(conn)

    meta = {
        "indexPath": str(index_path),
        "builtAtEpoch": int(time.time()),
        "elapsedSeconds": round(time.time() - started, 3),
        "sourceFileCount": file_count,
        "effectiveFileCount": len(effective),
        "header": header,
        "summary": summary,
    }
    conn.executemany(
        "INSERT INTO meta(key, value) VALUES (?, ?)",
        [(key, json.dumps(value, ensure_ascii=False)) for key, value in meta.items()],
    )
    conn.commit()
    conn.close()
    print(f"database built: {db_path} ({file_count:,} source records, {len(effective):,} effective records)")


def row_to_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


def file_suffix(file_name: str) -> str:
    return Path(file_name).suffix.lower()


def tablecfg_name_for_file(file_name: str) -> str | None:
    normalized = file_name.replace("\\", "/").strip("/")
    prefix = "Data/TableCfg/"
    suffix = ".bytes"
    if not normalized.startswith(prefix) or not normalized.endswith(suffix):
        return None
    name = normalized[len(prefix) : -len(suffix)]
    if "/" in name or not name:
        return None
    return name


def guess_content_type(file_name: str, data: bytes | None = None) -> str:
    suffix = file_suffix(file_name)
    if suffix == ".wem":
        return "audio/x-wem"
    if data:
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            return "image/webp"
        if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
            return "image/gif"
        if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
            return "audio/wav"
    if suffix == ".json":
        return "application/json; charset=utf-8"
    if suffix in {".lua", ".md", ".txt", ".csv", ".xml", ".yaml", ".yml"}:
        return "text/plain; charset=utf-8"
    return mimetypes.guess_type(file_name)[0] or "application/octet-stream"


def decode_text(data: bytes) -> tuple[str | None, str | None]:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return None, None


def looks_like_text(value: str) -> bool:
    if not value:
        return True
    sample = value[:8192]
    controls = sum(1 for char in sample if ord(char) < 32 and char not in "\r\n\t")
    return controls <= max(2, len(sample) // 100)


def hex_preview(data: bytes, max_bytes: int = PREVIEW_BINARY_LIMIT) -> str:
    data = data[:max_bytes]
    lines = []
    for offset in range(0, len(data), 16):
        row = data[offset : offset + 16]
        hex_part = " ".join(f"{value:02x}" for value in row)
        ascii_part = "".join(chr(value) if 32 <= value < 127 else "." for value in row)
        lines.append(f"{offset:08x}  {hex_part:<47}  {ascii_part}")
    return "\n".join(lines)


def internal_preview_kind(path: Path) -> str:
    suffix = file_suffix(path.name)
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    if suffix in TEXT_EXTENSIONS:
        return "text"
    return "binary"


def safe_relative_path(root: Path, raw_path: str) -> Path | None:
    normalized = unquote(raw_path).replace("\\", "/").strip("/")
    if not normalized:
        return root
    candidate = (root / normalized).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def posix_relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def folder_stats(path: Path) -> tuple[int, int]:
    file_count = 0
    total_bytes = 0
    for child in path.rglob("*"):
        if child.is_file():
            file_count += 1
            total_bytes += child.stat().st_size
    return file_count, total_bytes


class BrowserHandler(BaseHTTPRequestHandler):
    db_path: Path

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def send_json(self, payload: object, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_error_json(self, status: int, message: str) -> None:
        self.send_json({"error": message}, status=status)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/manifest":
            self.handle_manifest()
            return
        if parsed.path == "/api/list":
            self.handle_list(parse_qs(parsed.query))
            return
        if parsed.path == "/api/search":
            self.handle_search(parse_qs(parsed.query))
            return
        if parsed.path == "/api/file":
            self.handle_file(parse_qs(parsed.query))
            return
        if parsed.path == "/api/preview":
            self.handle_preview(parse_qs(parsed.query))
            return
        if parsed.path == "/api/raw":
            self.handle_raw(parse_qs(parsed.query))
            return
        if parsed.path == "/api/internal/list":
            self.handle_internal_list(parse_qs(parsed.query))
            return
        if parsed.path == "/api/internal/preview":
            self.handle_internal_preview(parse_qs(parsed.query))
            return
        if parsed.path == "/api/internal/raw":
            self.handle_internal_raw(parse_qs(parsed.query))
            return
        self.serve_static(parsed.path)

    def handle_manifest(self) -> None:
        with self.connect() as conn:
            meta = {row["key"]: json.loads(row["value"]) for row in conn.execute("SELECT key, value FROM meta")}
            scopes = []
            for scope in ["effective", "Persistent", "StreamingAssets", "all"]:
                row = conn.execute(
                    "SELECT * FROM directories WHERE scope = ? AND path = ''",
                    (scope,),
                ).fetchone()
                if row:
                    scopes.append(row_to_dict(row))
            self.send_json({"meta": meta, "scopes": scopes})

    def handle_list(self, query: dict[str, list[str]]) -> None:
        scope = query.get("scope", ["effective"])[0]
        path = unquote(query.get("path", [""])[0]).strip("/")
        page = max(int(query.get("page", ["1"])[0]), 1)
        page_size = min(max(int(query.get("pageSize", ["100"])[0]), 10), 500)
        offset = (page - 1) * page_size

        with self.connect() as conn:
            current = conn.execute(
                "SELECT * FROM directories WHERE scope = ? AND path = ?",
                (scope, path),
            ).fetchone()
            if current is None:
                self.send_error_json(404, "directory not found")
                return
            dirs = [
                row_to_dict(row)
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
            total_files = conn.execute(
                "SELECT COUNT(*) AS count FROM entries WHERE scope = ? AND parent = ? AND type = 'file'",
                (scope, path),
            ).fetchone()["count"]
            files = [
                row_to_dict(row)
                for row in conn.execute(
                    """
                    SELECT e.path, e.name, f.id, f.source, f.block_name, f.block_hash,
                           f.file_name, f.logical_id, f.source_logical_id, f.chunk_file,
                           f.chunk_exists, f.offset, f.length, f.encrypted, f.iv_seed,
                           f.file_data_md5
                    FROM entries e
                    JOIN files f ON f.id = e.file_id
                    WHERE e.scope = ? AND e.parent = ? AND e.type = 'file'
                    ORDER BY e.name COLLATE NOCASE
                    LIMIT ? OFFSET ?
                    """,
                    (scope, path, page_size, offset),
                )
            ]
            self.send_json(
                {
                    "scope": scope,
                    "path": path,
                    "directory": row_to_dict(current),
                    "dirs": dirs,
                    "files": files,
                    "filePage": {
                        "page": page,
                        "pageSize": page_size,
                        "total": total_files,
                        "pages": max((total_files + page_size - 1) // page_size, 1),
                    },
                }
            )

    def handle_search(self, query: dict[str, list[str]]) -> None:
        scope = query.get("scope", ["effective"])[0]
        term = query.get("q", [""])[0].strip()
        limit = min(max(int(query.get("limit", ["100"])[0]), 1), 500)
        if not term:
            self.send_json({"items": []})
            return
        pattern = f"%{term.replace('%', r'\\%').replace('_', r'\\_')}%"
        with self.connect() as conn:
            rows = [
                row_to_dict(row)
                for row in conn.execute(
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
            ]
        self.send_json({"items": rows, "limit": limit})

    def handle_file(self, query: dict[str, list[str]]) -> None:
        try:
            file_id = int(query.get("id", [""])[0])
        except ValueError:
            self.send_error_json(400, "invalid file id")
            return
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
            if row is None:
                self.send_error_json(404, "file not found")
                return
            self.send_json(row_to_dict(row))

    def original_file_record(self, conn: sqlite3.Connection, file_id: int) -> dict | None:
        row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        if row is None:
            self.send_error_json(404, "file not found")
            return None
        return row_to_dict(row)

    def file_id_from_query(self, query: dict[str, list[str]]) -> int | None:
        try:
            return int(query.get("id", [""])[0])
        except ValueError:
            self.send_error_json(400, "invalid file id")
            return None

    def resolve_file_record(self, conn: sqlite3.Connection, file_id: int) -> tuple[dict, dict, Path] | None:
        original_dict = self.original_file_record(conn, file_id)
        if original_dict is None:
            return None
        original_path = Path(original_dict["chunk_path"])
        if original_path.exists():
            return original_dict, original_dict, original_path

        candidates = [
            row_to_dict(row)
            for row in conn.execute(
                """
                SELECT * FROM files
                WHERE logical_id = ?
                """,
                (original_dict["logical_id"],),
            )
        ]
        candidates.sort(key=lambda row: source_rank(row["source"], bool(row["chunk_exists"])))
        for candidate in candidates:
            candidate_path = Path(candidate["chunk_path"])
            if candidate_path.exists():
                return original_dict, candidate, candidate_path

        self.send_error_json(
            404,
            "chunk not found; this record likely requires a source fallback that is unavailable on this host",
        )
        return None

    def read_file_slice(self, record: dict, chunk_path: Path, limit: int | None = None) -> bytes:
        length = int(record["length"])
        if limit is not None:
            length = min(length, limit)
        with chunk_path.open("rb") as file:
            file.seek(int(record["offset"]))
            data = file.read(length)
        if record.get("encrypted"):
            data = decrypt_vfs_file(data, int(record["iv_seed"]))
        return data

    def read_file_range(self, record: dict, chunk_path: Path, relative_offset: int, length: int) -> bytes:
        file_length = int(record["length"])
        if relative_offset < 0 or length < 0 or relative_offset + length > file_length:
            raise ValueError("file range is outside the VFS record")
        if record.get("encrypted"):
            return self.read_file_slice(record, chunk_path)[relative_offset : relative_offset + length]
        with chunk_path.open("rb") as file:
            file.seek(int(record["offset"]) + relative_offset)
            return file.read(length)

    def write_file_slice(self, record: dict, chunk_path: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        expected_size = int(record["length"])
        if target.exists() and target.stat().st_size == expected_size:
            return

        tmp = target.with_suffix(target.suffix + ".tmp")
        if record.get("encrypted"):
            tmp.write_bytes(self.read_file_slice(record, chunk_path))
        else:
            with chunk_path.open("rb") as source, tmp.open("wb") as output:
                source.seek(int(record["offset"]))
                remaining = expected_size
                while remaining > 0:
                    data = source.read(min(STREAM_CHUNK_SIZE, remaining))
                    if not data:
                        break
                    output.write(data)
                    remaining -= len(data)
        os.replace(tmp, target)

    def assetbundle_cache_paths(self, record: dict) -> tuple[Path, Path, Path, Path]:
        cache_root = INTERNAL_CACHE_DIR / str(record["id"])
        source_path = cache_root / "source.ab"
        export_root = cache_root / "exported"
        meta_path = cache_root / "meta.json"
        map_path = cache_root / "maps" / "asset_map.json"
        return source_path, export_root, meta_path, map_path

    def ensure_assetbundle_export(self, record: dict, chunk_path: Path) -> tuple[Path, dict] | None:
        source_path, export_root, meta_path, map_path = self.assetbundle_cache_paths(record)
        if export_root.exists() and meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if meta.get("version") == ASSETBUNDLE_META_VERSION and meta.get("returncode") == 0:
                    return export_root, meta
            except (OSError, json.JSONDecodeError):
                pass

        if not ANIMESTUDIO_CLI.exists():
            self.send_json(
                {
                    "kind": "assetBundle",
                    "status": "toolMissing",
                    "message": f"AnimeStudio.CLI not found: {ANIMESTUDIO_CLI}",
                }
            )
            return None

        self.write_file_slice(record, chunk_path, source_path)
        export_root.mkdir(parents=True, exist_ok=True)
        map_path.parent.mkdir(parents=True, exist_ok=True)
        map_command = [
            str(ANIMESTUDIO_CLI),
            str(source_path),
            str(map_path.parent),
            "--game",
            "ArknightsEndfield",
            "--types",
            *ASSETBUNDLE_EXPORT_TYPES,
            "--map_op",
            "AssetMap",
            "--map_type",
            "JSON",
            "--map_name",
            map_path.with_suffix("").name,
            "--logger_flags",
            "Error",
            "Warning",
            "Info",
        ]
        map_completed = subprocess.run(
            map_command,
            cwd=str(ANIMESTUDIO_CLI.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )

        export_command = [
            str(ANIMESTUDIO_CLI),
            str(source_path),
            str(export_root),
            "--game",
            "ArknightsEndfield",
            "--types",
            *ASSETBUNDLE_EXPORT_TYPES,
            "--export_type",
            "Convert",
            "--logger_flags",
            "Error",
            "Warning",
            "Info",
        ]
        export_completed = subprocess.run(
            export_command,
            cwd=str(ANIMESTUDIO_CLI.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
        asset_entries = []
        if map_path.exists():
            try:
                asset_map = json.loads(map_path.read_text(encoding="utf-8"))
                asset_entries = asset_map.get("AssetEntries") or []
            except (OSError, json.JSONDecodeError):
                asset_entries = []
        meta = {
            "version": ASSETBUNDLE_META_VERSION,
            "command": export_command,
            "mapCommand": map_command,
            "returncode": export_completed.returncode,
            "mapReturncode": map_completed.returncode,
            "stdout": export_completed.stdout,
            "stderr": export_completed.stderr,
            "mapStdout": map_completed.stdout,
            "mapStderr": map_completed.stderr,
            "builtAtEpoch": int(time.time()),
            "exportTypes": ASSETBUNDLE_EXPORT_TYPES,
            "assetEntries": asset_entries,
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        if export_completed.returncode != 0:
            self.send_json(
                {
                    "kind": "assetBundle",
                    "status": "exportFailed",
                    "message": "AnimeStudio failed to export this AssetBundle.",
                    "meta": meta,
                },
                status=500,
            )
            return None
        return export_root, meta

    def asset_metadata_by_export_name(self, meta: dict) -> dict[tuple[str, str], dict]:
        out = {}
        for entry in meta.get("assetEntries") or []:
            name = str(entry.get("Name") or "").lower()
            asset_type = str(entry.get("Type") or "").lower()
            if name and asset_type:
                out[(asset_type, name)] = entry
        return out

    def metadata_for_internal_file(self, child: Path, export_root: Path, metadata_by_name: dict[tuple[str, str], dict]) -> dict | None:
        try:
            asset_type = child.relative_to(export_root).parts[0].lower()
        except (ValueError, IndexError):
            return None
        name = child.stem.lower()
        return metadata_by_name.get((asset_type, name))

    def list_internal_export(self, export_root: Path, raw_path: str, meta: dict) -> dict:
        current = safe_relative_path(export_root, raw_path)
        if current is None or not current.exists() or not current.is_dir():
            raise FileNotFoundError("internal directory not found")

        dirs = []
        files = []
        metadata_by_name = self.asset_metadata_by_export_name(meta)
        for child in sorted(current.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            rel_path = posix_relative(child, export_root)
            if child.is_dir():
                file_count, total_bytes = folder_stats(child)
                dirs.append(
                    {
                        "name": child.name,
                        "path": rel_path,
                        "fileCount": file_count,
                        "totalBytes": total_bytes,
                    }
                )
            elif child.is_file():
                asset_meta = self.metadata_for_internal_file(child, export_root, metadata_by_name)
                files.append(
                    {
                        "name": child.name,
                        "path": rel_path,
                        "size": child.stat().st_size,
                        "kind": internal_preview_kind(child),
                        "asset": asset_meta,
                    }
                )
        return {"path": posix_relative(current, export_root) if current != export_root else "", "dirs": dirs, "files": files}

    def audio_cache_paths(self, record: dict) -> tuple[Path, Path, Path]:
        cache_root = INTERNAL_CACHE_DIR / str(record["id"])
        meta_path = cache_root / "audio_meta.json"
        wem_root = cache_root / "audio" / "wem"
        wav_root = cache_root / "audio" / "wav"
        return meta_path, wem_root, wav_root

    def read_pck_header(self, record: dict, chunk_path: Path) -> bytes:
        probe = self.read_file_range(record, chunk_path, 0, min(28, int(record["length"])))
        if len(probe) < 8:
            raise ValueError("PCK is too small")
        magic = probe[:4]
        if magic not in {b":)xD", b"AKPK"}:
            raise ValueError("invalid AKPK magic")
        header_size = int.from_bytes(probe[4:8], "little")
        read_size = header_size + 8 if magic == b":)xD" else header_size
        if read_size < 28 or read_size > int(record["length"]):
            raise ValueError("invalid AKPK header size")
        return self.read_file_range(record, chunk_path, 0, read_size)

    def read_pck_payload(self, record: dict, chunk_path: Path, offset: int, size: int) -> bytes:
        return self.read_file_range(record, chunk_path, offset, size)

    def parse_pck_index(self, record: dict, chunk_path: Path) -> list[AudioEntry]:
        header, header_size = parse_akpk_header(self.read_pck_header(record, chunk_path), record["file_name"])
        language_size = int.from_bytes(header[12:16], "little")
        banks_size = int.from_bytes(header[16:20], "little")
        sounds_size = int.from_bytes(header[20:24], "little")
        has_externals = language_size + banks_size + sounds_size + 0x10 < header_size
        externals_size = int.from_bytes(header[24:28], "little") if has_externals else 0
        start = 28 if has_externals else 24
        languages = parse_akpk_languages(header, start, language_size)
        entries: list[AudioEntry] = []
        pos = start + language_size

        def parse_sector(sector_start: int, sector_size: int, is_sounds: bool, is_externals: bool) -> None:
            if sector_size == 0 or sector_start + 4 > len(header):
                return
            count = int.from_bytes(header[sector_start : sector_start + 4], "little")
            if count == 0:
                return
            entry_size = (sector_size - 4) // count
            alt_mode = entry_size == 0x18
            p = sector_start + 4
            for _ in range(count):
                if p + entry_size > len(header):
                    break
                file_id_low = int.from_bytes(header[p : p + 4], "little")
                q = p + 4
                file_id_high = None
                if alt_mode and is_externals:
                    file_id_high = int.from_bytes(header[q : q + 4], "little")
                    q += 4
                block_size = int.from_bytes(header[q : q + 4], "little")
                q += 4
                if alt_mode and is_externals:
                    size = int.from_bytes(header[q : q + 4], "little")
                    q += 4
                elif alt_mode:
                    size = int.from_bytes(header[q : q + 8], "little")
                    q += 8
                else:
                    size = int.from_bytes(header[q : q + 4], "little")
                    q += 4
                offset = int.from_bytes(header[q : q + 4], "little")
                lang_id = int.from_bytes(header[q + 4 : q + 8], "little") if q + 8 <= p + entry_size else 0
                if block_size:
                    offset *= block_size
                language = languages.get(lang_id)
                final_id = ((file_id_high << 32) | file_id_low) if file_id_high is not None else file_id_low
                if is_sounds:
                    entries.append(AudioEntry(final_id, offset, size, "external" if is_externals else "sound", language))
                else:
                    self.append_bank_wem_entries(record, chunk_path, entries, file_id_low, offset, size, language)
                p += entry_size

        parse_sector(pos, banks_size, False, False)
        pos += banks_size
        parse_sector(pos, sounds_size, True, False)
        pos += sounds_size
        if externals_size:
            parse_sector(pos, externals_size, True, True)
        return entries

    def append_bank_wem_entries(
        self,
        record: dict,
        chunk_path: Path,
        entries: list[AudioEntry],
        bank_id: int,
        bank_offset: int,
        bank_size: int,
        language: str | None,
    ) -> None:
        if bank_size <= 0:
            return
        payload = self.read_pck_payload(record, chunk_path, bank_offset, bank_size)
        bank_encrypted = False
        ranges = parse_bnk_wem_ranges(payload)
        if not ranges:
            decrypted = bytearray(payload)
            decrypt_audio_vfs_bytes(decrypted, 0, len(decrypted), bank_id)
            payload = bytes(decrypted)
            ranges = parse_bnk_wem_ranges(payload)
            bank_encrypted = bool(ranges)
        for wem_id, wem_offset, wem_size in ranges:
            if wem_offset + wem_size > len(payload):
                continue
            entries.append(
                AudioEntry(
                    wem_id=wem_id,
                    offset=bank_offset + wem_offset,
                    size=wem_size,
                    source="bank",
                    language=language,
                    bank_id=bank_id,
                    bank_offset=bank_offset,
                    bank_size=bank_size,
                    bank_wem_offset=wem_offset,
                    bank_encrypted=bank_encrypted,
                )
            )

    def ensure_audio_package_index(self, record: dict, chunk_path: Path) -> dict:
        meta_path, _, _ = self.audio_cache_paths(record)
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if (
                    meta.get("version") == AUDIO_PACKAGE_META_VERSION
                    and meta.get("fileLength") == int(record["length"])
                    and isinstance(meta.get("entries"), list)
                ):
                    return meta
            except (OSError, json.JSONDecodeError):
                pass

        entries = self.parse_pck_index(record, chunk_path)
        meta = {
            "version": AUDIO_PACKAGE_META_VERSION,
            "fileLength": int(record["length"]),
            "builtAtEpoch": int(time.time()),
            "entryCount": len(entries),
            "entries": [entry.to_json() for entry in entries],
        }
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return meta

    def list_audio_package(self, meta: dict, raw_path: str) -> dict:
        normalized = unquote(raw_path).replace("\\", "/").strip("/")
        entries = [AudioEntry.from_json(item) for item in meta.get("entries") or []]
        total_size = sum(entry.size for entry in entries)
        dirs = []
        files = []
        if not normalized:
            dirs = [
                {"name": "wem", "path": "wem", "fileCount": len(entries), "totalBytes": total_size},
                {"name": "wav", "path": "wav", "fileCount": len(entries), "totalBytes": total_size},
            ]
            return {"path": "", "dirs": dirs, "files": files}

        parts = normalized.split("/")
        if len(parts) == 1 and parts[0] in {"wem", "wav"}:
            groups: dict[str, tuple[int, int]] = {}
            for entry in entries:
                prefix = audio_entry_prefix(entry.wem_id)
                count, size = groups.get(prefix, (0, 0))
                groups[prefix] = (count + 1, size + entry.size)
            for prefix, (count, size) in sorted(groups.items()):
                dirs.append({"name": prefix, "path": f"{parts[0]}/{prefix}", "fileCount": count, "totalBytes": size})
            return {"path": normalized, "dirs": dirs, "files": files}

        if len(parts) == 2 and parts[0] in {"wem", "wav"}:
            mode, prefix = parts
            for entry in sorted(entries, key=lambda item: item.wem_id):
                if audio_entry_prefix(entry.wem_id) != prefix.lower():
                    continue
                name = f"{entry.wem_id}.{mode}"
                files.append(
                    {
                        "name": name,
                        "path": f"{mode}/{prefix}/{name}",
                        "size": entry.size,
                        "kind": "audio" if mode == "wav" else "wem",
                        "asset": {
                            "Name": str(entry.wem_id),
                            "Type": "WEM",
                            "Container": f"wwise/{entry.wem_id}.wem",
                            "Source": entry.source,
                            "PathID": entry.wem_id,
                            "Language": entry.language,
                            "BankID": entry.bank_id,
                        },
                    }
                )
            return {"path": normalized, "dirs": dirs, "files": files}

        raise FileNotFoundError("audio package directory not found")

    def audio_entries_by_id(self, meta: dict) -> dict[int, AudioEntry]:
        return {AudioEntry.from_json(item).wem_id: AudioEntry.from_json(item) for item in meta.get("entries") or []}

    def extract_wem_entry(self, record: dict, chunk_path: Path, entry: AudioEntry) -> bytes:
        if entry.bank_encrypted:
            if entry.bank_id is None or entry.bank_offset is None or entry.bank_size is None or entry.bank_wem_offset is None:
                raise ValueError("encrypted bank entry is missing bank metadata")
            bank_payload = bytearray(self.read_pck_payload(record, chunk_path, entry.bank_offset, entry.bank_size))
            decrypt_audio_vfs_bytes(bank_payload, 0, len(bank_payload), entry.bank_id)
            data = bytes(bank_payload[entry.bank_wem_offset : entry.bank_wem_offset + entry.size])
        else:
            data = self.read_pck_payload(record, chunk_path, entry.offset, entry.size)
        if len(data) >= 4 and data[:4] not in {b"RIFF", b"RIFX"}:
            data = decrypt_wem_bytes(data, entry.wem_id)
        return data

    def ensure_audio_entry_file(self, record: dict, chunk_path: Path, internal_path: str) -> tuple[Path, AudioEntry]:
        parsed = parse_audio_internal_path(internal_path)
        if parsed is None:
            raise FileNotFoundError("audio entry not found")
        mode, wem_id = parsed
        meta = self.ensure_audio_package_index(record, chunk_path)
        entry = self.audio_entries_by_id(meta).get(wem_id)
        if entry is None:
            raise FileNotFoundError("audio entry not found")

        _, wem_root, wav_root = self.audio_cache_paths(record)
        prefix = audio_entry_prefix(wem_id)
        wem_path = wem_root / prefix / f"{wem_id}.wem"
        if not wem_path.exists() or wem_path.stat().st_size != entry.size:
            wem_path.parent.mkdir(parents=True, exist_ok=True)
            wem_path.write_bytes(self.extract_wem_entry(record, chunk_path, entry))
        if mode == "wem":
            return wem_path, entry

        wav_path = wav_root / prefix / f"{wem_id}.wav"
        if wav_path.exists() and wav_path.stat().st_size > 0:
            return wav_path, entry
        if not VGMSTREAM_CLI.exists():
            raise FileNotFoundError(f"vgmstream-cli.exe not found: {VGMSTREAM_CLI}")
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = wav_path.with_name(f".{wav_path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        command = [str(VGMSTREAM_CLI), "-o", str(tmp), str(wem_path)]
        try:
            completed = subprocess.run(
                command,
                cwd=str(VGMSTREAM_CLI.parent),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
            if completed.returncode != 0 or not tmp.exists():
                raise RuntimeError(f"vgmstream conversion failed: {completed.stderr or completed.stdout}")
            os.replace(tmp, wav_path)
        finally:
            if tmp.exists():
                tmp.unlink()
        return wav_path, entry

    def resolve_assetbundle_internal_file(self, query: dict[str, list[str]]) -> tuple[dict, Path, dict | None] | None:
        file_id = self.file_id_from_query(query)
        if file_id is None:
            return None
        internal_path = query.get("path", [""])[0]
        with self.connect() as conn:
            resolved = self.resolve_file_record(conn, file_id)
            if resolved is None:
                return None
            _, record, chunk_path = resolved

        if file_suffix(record["file_name"]) != ".ab":
            self.send_error_json(400, "AssetBundle internal file preview expected an .ab record")
            return None

        ensured = self.ensure_assetbundle_export(record, chunk_path)
        if ensured is None:
            return None
        export_root, meta = ensured
        target = safe_relative_path(export_root, internal_path)
        if target is None or not target.is_file():
            self.send_error_json(404, "internal file not found")
            return None
        asset_meta = self.metadata_for_internal_file(target, export_root, self.asset_metadata_by_export_name(meta))
        return record, target, asset_meta

    def resolve_audio_internal_file(self, query: dict[str, list[str]]) -> tuple[dict, Path, AudioEntry] | None:
        file_id = self.file_id_from_query(query)
        if file_id is None:
            return None
        internal_path = query.get("path", [""])[0]
        with self.connect() as conn:
            resolved = self.resolve_file_record(conn, file_id)
            if resolved is None:
                return None
            _, record, chunk_path = resolved

        if file_suffix(record["file_name"]) != ".pck":
            self.send_error_json(400, "audio internal file preview expected a .pck record")
            return None
        try:
            target, entry = self.ensure_audio_entry_file(record, chunk_path, internal_path)
        except FileNotFoundError as error:
            self.send_error_json(404, str(error))
            return None
        except (ValueError, RuntimeError) as error:
            self.send_error_json(500, str(error))
            return None
        return record, target, entry

    def handle_preview(self, query: dict[str, list[str]]) -> None:
        file_id = self.file_id_from_query(query)
        if file_id is None:
            return

        with self.connect() as conn:
            resolved = self.resolve_file_record(conn, file_id)
            if resolved is None:
                return
            original, record, chunk_path = resolved

        suffix = file_suffix(record["file_name"])
        raw_url = f"/api/raw?id={file_id}"
        download_url = f"/api/raw?id={file_id}&download=1"
        base = {
            "file": original,
            "resolvedFile": record,
            "usedFallback": original["id"] != record["id"],
            "rawUrl": raw_url,
            "downloadUrl": download_url,
        }

        if suffix in CONTAINER_EXTENSIONS:
            kind = "container"
            if suffix == ".ab":
                message = "这是 Unity/AssetBundle 容器。可以下载原始 .ab，也可以点击“查看内部结构”按需导出并预览 Texture2D、Sprite、TextAsset 等资源。"
            elif suffix == ".pck":
                message = "这是音频 PCK 容器。VFS 层可以下载原始 PCK；单条语音需要继续解析 PCK/AKPK/WEM。"
            elif suffix == ".usm":
                message = "这是 CRI/USM 视频容器。浏览器通常不能直接播放 .usm；可以先下载，后续接入 CRI 转码/抽流。"
            else:
                message = "这是二级容器文件，可以下载；内部解析尚未接入。"
            self.send_json({**base, "kind": kind, "message": message})
            return

        if suffix in IMAGE_EXTENSIONS:
            self.send_json({**base, "kind": "image", "contentType": guess_content_type(record["file_name"])})
            return
        if suffix in VIDEO_EXTENSIONS:
            self.send_json({**base, "kind": "video", "contentType": guess_content_type(record["file_name"])})
            return
        if suffix in AUDIO_EXTENSIONS:
            self.send_json({**base, "kind": "audio", "contentType": guess_content_type(record["file_name"])})
            return

        limit = PREVIEW_TEXT_LIMIT if suffix in TEXT_EXTENSIONS else PREVIEW_BINARY_LIMIT
        data = self.read_file_slice(record, chunk_path, limit=limit)
        text, encoding = decode_text(data)
        truncated = int(record["length"]) > len(data)
        if suffix == ".json" and text is not None:
            try:
                text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                pass
        if text is not None and (suffix in TEXT_EXTENSIONS or looks_like_text(text)):
            self.send_json(
                {
                    **base,
                    "kind": "text",
                    "encoding": encoding,
                    "text": text,
                    "truncated": truncated,
                }
            )
            return

        tablecfg_name = tablecfg_name_for_file(record["file_name"])
        message = (
            f"`{tablecfg_name}` 是本地 VFS 解密后的 TableCfg 二进制表，后续需要接入 SparkBuffer 解析后才能显示为 JSON。"
            if tablecfg_name
            else "该文件不是可直接显示的文本，当前展示解密后的前段十六进制内容。"
        )
        self.send_json(
            {
                **base,
                "kind": "hex",
                "hex": hex_preview(data),
                "truncated": truncated,
                "message": message,
            }
        )

    def handle_raw(self, query: dict[str, list[str]]) -> None:
        file_id = self.file_id_from_query(query)
        if file_id is None:
            return
        download = query.get("download", ["0"])[0] in {"1", "true", "yes"}
        with self.connect() as conn:
            resolved = self.resolve_file_record(conn, file_id)
            if resolved is None:
                return
            original, record, chunk_path = resolved

        file_name = Path(original["file_name"]).name or "vfs-file.bin"
        content_type = guess_content_type(original["file_name"])
        disposition = "attachment" if download else "inline"
        encoded_name = quote(file_name)

        if record.get("encrypted"):
            data = self.read_file_slice(record, chunk_path)
            content_type = guess_content_type(original["file_name"], data[:32])
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header(
                "Content-Disposition",
                f"{disposition}; filename*=UTF-8''{encoded_name}",
            )
            self.end_headers()
            self.wfile.write(data)
            return

        length = int(record["length"])
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header(
            "Content-Disposition",
            f"{disposition}; filename*=UTF-8''{encoded_name}",
        )
        self.end_headers()
        with chunk_path.open("rb") as file:
            file.seek(int(record["offset"]))
            remaining = length
            while remaining > 0:
                data = file.read(min(STREAM_CHUNK_SIZE, remaining))
                if not data:
                    break
                self.wfile.write(data)
                remaining -= len(data)

    def handle_internal_list(self, query: dict[str, list[str]]) -> None:
        file_id = self.file_id_from_query(query)
        if file_id is None:
            return
        path = query.get("path", [""])[0]
        with self.connect() as conn:
            resolved = self.resolve_file_record(conn, file_id)
            if resolved is None:
                return
            original, record, chunk_path = resolved

        suffix = file_suffix(record["file_name"])
        if suffix == ".ab":
            ensured = self.ensure_assetbundle_export(record, chunk_path)
            if ensured is None:
                return
            export_root, meta = ensured
            try:
                listing = self.list_internal_export(export_root, path, meta)
            except FileNotFoundError:
                self.send_error_json(404, "internal directory not found")
                return
            self.send_json(
                {
                    "kind": "assetBundle",
                    "status": "ready",
                    "file": original,
                    "resolvedFile": record,
                    "path": listing["path"],
                    "dirs": listing["dirs"],
                    "files": listing["files"],
                    "meta": {
                        "returncode": meta.get("returncode"),
                        "builtAtEpoch": meta.get("builtAtEpoch"),
                        "exportTypes": meta.get("exportTypes"),
                    },
                }
            )
            return
        if suffix == ".pck":
            try:
                meta = self.ensure_audio_package_index(record, chunk_path)
                listing = self.list_audio_package(meta, path)
            except (FileNotFoundError, ValueError) as error:
                self.send_error_json(404, str(error))
                return
            self.send_json(
                {
                    "kind": "audioPackage",
                    "status": "ready",
                    "file": original,
                    "resolvedFile": record,
                    "path": listing["path"],
                    "dirs": listing["dirs"],
                    "files": listing["files"],
                    "meta": {
                        "builtAtEpoch": meta.get("builtAtEpoch"),
                        "entryCount": meta.get("entryCount"),
                        "wavPreviewAvailable": VGMSTREAM_CLI.exists(),
                        "vgmstreamCli": str(VGMSTREAM_CLI),
                    },
                }
            )
            return
        if suffix == ".usm":
            self.send_json(
                {
                    "kind": "criVideo",
                    "status": "notConnected",
                    "message": "USM 内部流尚未接入。下一步需要解析 CRI UTF 表或转码为浏览器可播放格式。",
                }
            )
            return
        self.send_json({"kind": "plainFile", "status": "notContainer", "message": "该文件不是当前识别的二级容器。"})

    def handle_internal_preview(self, query: dict[str, list[str]]) -> None:
        file_id = self.file_id_from_query(query)
        if file_id is None:
            return
        with self.connect() as conn:
            resolved_record = self.resolve_file_record(conn, file_id)
            if resolved_record is None:
                return
            _, record, _ = resolved_record

        suffix = file_suffix(record["file_name"])
        asset_meta = None
        audio_entry = None
        if suffix == ".ab":
            resolved = self.resolve_assetbundle_internal_file(query)
            if resolved is None:
                return
            record, target, asset_meta = resolved
        elif suffix == ".pck":
            resolved = self.resolve_audio_internal_file(query)
            if resolved is None:
                return
            record, target, audio_entry = resolved
        else:
            self.send_error_json(400, "unsupported internal preview container")
            return
        rel_path = query.get("path", [""])[0]
        raw_url = f"/api/internal/raw?id={record['id']}&path={quote(rel_path, safe='')}"
        download_url = f"{raw_url}&download=1"
        internal_suffix = file_suffix(target.name)
        base = {
            "file": record,
            "path": rel_path,
            "name": target.name,
            "size": target.stat().st_size,
            "rawUrl": raw_url,
            "downloadUrl": download_url,
            "asset": asset_meta,
            "audioEntry": audio_entry.to_json() if audio_entry else None,
        }
        if internal_suffix in IMAGE_EXTENSIONS:
            self.send_json({**base, "kind": "image", "contentType": guess_content_type(target.name)})
            return
        if internal_suffix in VIDEO_EXTENSIONS:
            self.send_json({**base, "kind": "video", "contentType": guess_content_type(target.name)})
            return
        if internal_suffix in AUDIO_EXTENSIONS:
            self.send_json({**base, "kind": "audio", "contentType": guess_content_type(target.name)})
            return

        data = target.read_bytes()[:PREVIEW_TEXT_LIMIT if internal_suffix in TEXT_EXTENSIONS else PREVIEW_BINARY_LIMIT]
        text, encoding = decode_text(data)
        truncated = target.stat().st_size > len(data)
        if text is not None and (internal_suffix in TEXT_EXTENSIONS or looks_like_text(text)):
            self.send_json({**base, "kind": "text", "encoding": encoding, "text": text, "truncated": truncated})
            return
        self.send_json({**base, "kind": "hex", "hex": hex_preview(data), "truncated": truncated})

    def handle_internal_raw(self, query: dict[str, list[str]]) -> None:
        file_id = self.file_id_from_query(query)
        if file_id is None:
            return
        with self.connect() as conn:
            resolved_record = self.resolve_file_record(conn, file_id)
            if resolved_record is None:
                return
            _, record, _ = resolved_record
        suffix = file_suffix(record["file_name"])
        if suffix == ".ab":
            resolved = self.resolve_assetbundle_internal_file(query)
            if resolved is None:
                return
            _, target, _ = resolved
        elif suffix == ".pck":
            resolved = self.resolve_audio_internal_file(query)
            if resolved is None:
                return
            _, target, _ = resolved
        else:
            self.send_error_json(400, "unsupported internal raw container")
            return
        download = query.get("download", ["0"])[0] in {"1", "true", "yes"}
        disposition = "attachment" if download else "inline"
        encoded_name = quote(target.name)
        with target.open("rb") as file:
            sniff = file.read(32)
        self.send_response(200)
        self.send_header("Content-Type", guess_content_type(target.name, sniff))
        self.send_header("Content-Length", str(target.stat().st_size))
        self.send_header("Content-Disposition", f"{disposition}; filename*=UTF-8''{encoded_name}")
        self.end_headers()
        with target.open("rb") as file:
            while True:
                data = file.read(STREAM_CHUNK_SIZE)
                if not data:
                    break
                self.wfile.write(data)

    def serve_static(self, request_path: str) -> None:
        request_path = unquote(request_path)
        if request_path in ("", "/"):
            request_path = "/index.html"
        normalized = os.path.normpath(request_path.lstrip("/"))
        if normalized.startswith(".."):
            self.send_error(403)
            return
        file_path = PUBLIC_DIR / normalized
        if not file_path.exists() or not file_path.is_file():
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX, help="Path to JSONL/TGZ VFS index")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database path")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--rebuild", action="store_true", help="Rebuild SQLite database before serving")
    parser.add_argument("--build-only", action="store_true", help="Rebuild SQLite database and exit")
    return parser.parse_args(list(argv))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.rebuild or not args.db.exists():
        if not args.index.exists():
            raise SystemExit(f"index not found: {args.index}")
        build_database(args.index, args.db)
    if args.build_only:
        return 0

    BrowserHandler.db_path = args.db
    server = ThreadingHTTPServer((args.host, args.port), BrowserHandler)
    print(f"VFS index browser: http://{args.host}:{args.port}")
    print(f"database: {args.db}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
