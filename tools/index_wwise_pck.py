#!/usr/bin/env python3
"""Build or replace one PCK's physical bank and HIRC relation index."""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from audio_package import parse_audio_package
from wwise_store import replace_wwise_package


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="索引一个本地 PCK 的 SoundBank、Media 与 HIRC 关系。"
    )
    parser.add_argument("pck", type=Path, help="已从 VFS 读取出的 PCK 文件")
    parser.add_argument("database", type=Path, help="目标 Wwise SQLite 索引")
    parser.add_argument("--pck-file-id", type=int, required=True, help="VFS files.id")
    parser.add_argument("--logical-path", help="PCK 在 VFS 中的逻辑路径")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.pck_file_id < 0:
        print("error: --pck-file-id must be non-negative", file=sys.stderr)
        return 2
    try:
        with args.pck.open("rb") as source:
            size = args.pck.stat().st_size
            content_md5 = hashlib.file_digest(source, "md5").hexdigest()

            def read_range(offset: int, length: int) -> bytes:
                source.seek(offset)
                data = source.read(length)
                if len(data) != length:
                    raise ValueError(f"PCK range {offset}+{length} is truncated")
                return data

            print(f"[1/3] 读取 PCK：{args.pck}")
            package = parse_audio_package(read_range, size, args.pck.name)
        print(
            f"[2/3] 解析完成：{len(package.banks)} banks，"
            f"{sum(len(bank.graph.objects) for bank in package.banks)} HIRC objects，"
            f"{sum(len(bank.graph.relations) for bank in package.banks)} relations，"
            f"{sum(len(bank.graph.diagnostics) for bank in package.banks)} diagnostics，"
            f"{len(package.media)} media"
        )
        args.database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(args.database) as conn:
            replace_wwise_package(
                conn,
                args.pck_file_id,
                package,
                logical_path=args.logical_path or args.pck.name,
                file_data_md5=content_md5,
            )
        print(f"[3/3] 已写入：{args.database}")
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
