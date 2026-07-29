#!/usr/bin/env python3
"""Extract one logical file from a vfs-index-browser SQLite index."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server import decrypt_vfs_file


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("id", type=int, help="row id in the files table")
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--db", type=Path, default=PROJECT_ROOT / "data" / "endfield-vfs-index.sqlite"
    )
    args = parser.parse_args()

    with sqlite3.connect(args.db) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM files WHERE id = ?", (args.id,)).fetchone()
    if row is None:
        raise SystemExit(f"file id not found: {args.id}")

    source_path = Path(row["chunk_path"])
    with source_path.open("rb") as source:
        source.seek(int(row["offset"]))
        payload = source.read(int(row["length"]))
    if len(payload) != int(row["length"]):
        raise SystemExit(f"short read: expected {row['length']}, got {len(payload)}")
    if row["encrypted"]:
        payload = decrypt_vfs_file(payload, int(row["iv_seed"]))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(payload)
    print(f"extracted {len(payload)} bytes: {row['logical_id']} -> {args.output}")


if __name__ == "__main__":
    main()
