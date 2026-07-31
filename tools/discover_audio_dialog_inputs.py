#!/usr/bin/env python3
"""Query local AudioDialog inputs from an existing VFS SQLite index."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from audio_dialog_discovery import (
    AudioDialogDiscoveryError,
    discover_audio_dialog_inputs,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从本地 VFS SQLite 元数据发现 AudioDialog TableCfg 与四语 PCK。"
    )
    parser.add_argument("vfs_database", type=Path, help="VFS SQLite 索引")
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出机器可读 JSON，而不是摘要",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.vfs_database.is_file():
        print(f"error: VFS SQLite 不存在：{args.vfs_database}", file=sys.stderr)
        return 2

    try:
        uri = f"file:{args.vfs_database.resolve().as_posix()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            discovery = discover_audio_dialog_inputs(conn)
    except (sqlite3.Error, AudioDialogDiscoveryError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(discovery.to_dict(), ensure_ascii=False, indent=2))
        return 0

    preferred = discovery.preferred_tablecfg
    if preferred is None:
        print("AudioDialog TableCfg：未找到")
    else:
        availability = "索引时 chunk 存在" if preferred.chunk_exists else "索引时 chunk 缺失"
        print(
            "AudioDialog TableCfg："
            f"file_id={preferred.file_id}，source={preferred.source}，{availability}"
        )
        print(f"  {preferred.logical_id}")

    for language, packages in discovery.packages.items():
        print(f"{language}：{len(packages)} 个 PCK 候选")
        for package in packages:
            location = package.location
            flags = [package.role]
            if location.effective:
                flags.append("effective")
            flags.append(
                "索引时 chunk 存在"
                if location.chunk_exists
                else "索引时 chunk 缺失"
            )
            print(f"  [{', '.join(flags)}] {location.file_id} {location.file_name}")

    if discovery.unclassified_audio_pcks:
        print(f"未归类 Windows PCK：{len(discovery.unclassified_audio_pcks)} 个")
        for location in discovery.unclassified_audio_pcks:
            print(f"  {location.file_id} {location.file_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
