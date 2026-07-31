#!/usr/bin/env python3
"""Build the Wwise virtual-directory index directly from the local VFS index."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from audio_package import parse_audio_package
from server import decrypt_vfs_file
from wwise_store import create_wwise_schema, replace_wwise_package


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--vfs-index",
        type=Path,
        default=PROJECT_ROOT / "data" / "endfield-vfs-index.sqlite",
        help="vfs-index-browser 生成的 SQLite 文件索引",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "data" / "wwise-index.sqlite",
        help="输出的 Wwise SQLite 索引",
    )
    parser.add_argument(
        "--pck-file-id",
        type=int,
        action="append",
        default=[],
        help="只索引指定 files.id；可重复。省略时索引 effective 视图中的全部 PCK",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="开始前删除旧索引；完整重建时建议使用",
    )
    return parser.parse_args(argv)


def load_pck_records(
    conn: sqlite3.Connection,
    file_ids: list[int],
) -> list[dict]:
    conn.row_factory = sqlite3.Row
    if file_ids:
        placeholders = ",".join("?" for _ in file_ids)
        rows = conn.execute(
            f"SELECT *, source_logical_id AS browse_path FROM files WHERE id IN ({placeholders})",
            file_ids,
        ).fetchall()
        found = {int(row["id"]) for row in rows}
        missing = sorted(set(file_ids) - found)
        if missing:
            raise ValueError(f"VFS file id not found: {missing}")
        records = [dict(row) for row in rows]
        return [_resolve_physical_record(conn, record) for record in records]

    rows = conn.execute(
        """
        SELECT f.*, e.path AS browse_path
        FROM entries e
        JOIN files f ON f.id = e.file_id
        WHERE e.scope = 'effective' AND e.type = 'file'
          AND lower(e.name) LIKE '%.pck'
        ORDER BY e.path COLLATE NOCASE
        """
    ).fetchall()
    records = []
    for row in rows:
        record = dict(row)
        try:
            records.append(_resolve_physical_record(conn, record))
        except FileNotFoundError as error:
            print(f"[跳过] {error}", file=sys.stderr, flush=True)
    return records


def _resolve_physical_record(conn: sqlite3.Connection, original: dict) -> dict:
    original_id = int(original["id"])
    browse_path = str(original["browse_path"])
    if Path(original["chunk_path"]).is_file():
        return {**original, "pck_file_id": original_id}
    candidates = conn.execute(
        "SELECT * FROM files WHERE logical_id = ?",
        (original["logical_id"],),
    ).fetchall()
    source_priority = {"Persistent": 0, "StreamingAssets": 1}
    candidates = sorted(
        candidates,
        key=lambda row: (
            0 if Path(row["chunk_path"]).is_file() else 1,
            source_priority.get(row["source"], 9),
            int(row["id"]),
        ),
    )
    for row in candidates:
        if Path(row["chunk_path"]).is_file():
            return {
                **dict(row),
                "pck_file_id": original_id,
                "browse_path": browse_path,
            }
    raise FileNotFoundError(
        f"PCK chunk is unavailable for files.id={original_id}: {browse_path}"
    )


def parse_indexed_pck(record: dict):
    chunk_path = Path(record["chunk_path"])
    if not chunk_path.is_file():
        raise FileNotFoundError(
            f"PCK chunk is unavailable for files.id={record['id']}: {chunk_path}"
        )
    file_offset = int(record["offset"])
    file_size = int(record["length"])
    decrypted_payload: bytes | None = None

    with chunk_path.open("rb") as source:
        def read_range(offset: int, length: int) -> bytes:
            nonlocal decrypted_payload
            if offset < 0 or length < 0 or offset + length > file_size:
                raise ValueError("PCK read range exceeds its VFS record")
            if record["encrypted"]:
                if decrypted_payload is None:
                    source.seek(file_offset)
                    raw = source.read(file_size)
                    if len(raw) != file_size:
                        raise ValueError("encrypted PCK VFS record is truncated")
                    decrypted_payload = decrypt_vfs_file(raw, int(record["iv_seed"]))
                return decrypted_payload[offset : offset + length]
            source.seek(file_offset + offset)
            data = source.read(length)
            if len(data) != length:
                raise ValueError("PCK VFS record is truncated")
            return data

        return parse_audio_package(
            read_range,
            file_size,
            str(record["browse_path"]),
        )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        with sqlite3.connect(args.vfs_index) as conn:
            records = load_pck_records(conn, args.pck_file_id)
        if not records:
            raise ValueError("effective 视图中没有可索引的 PCK 文件")
        if args.reset and args.output.exists():
            args.output.unlink()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(args.output) as output:
            create_wwise_schema(output)
            for index, record in enumerate(records, 1):
                label = str(record["browse_path"])
                print(f"[{index}/{len(records)}] 解析 {label}", flush=True)
                package = parse_indexed_pck(record)
                replace_wwise_package(
                    output,
                    int(record["pck_file_id"]),
                    package,
                    logical_path=label,
                )
                print(
                    f"  {len(package.banks)} banks, "
                    f"{sum(len(bank.graph.objects) for bank in package.banks)} objects, "
                    f"{sum(len(bank.graph.relations) for bank in package.banks)} relations, "
                    f"{sum(len(bank.graph.diagnostics) for bank in package.banks)} diagnostics, "
                    f"{len(package.media)} media",
                    flush=True,
                )
    except (OSError, sqlite3.Error, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"Wwise 索引已写入：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
