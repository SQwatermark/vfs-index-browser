#!/usr/bin/env python3
"""Build the AudioDialog SQLite index from parsed TableCfg and PCK metadata."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from audio_dialog_index import build_audio_dialog_index_from_packages
from audio_dialog_store import replace_audio_dialog_language


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从 AudioDialog JSON 和 PCK audio_meta.json 构建逻辑音频索引。"
    )
    parser.add_argument("audio_dialog", type=Path, help="解析后的 AudioDialog JSON")
    parser.add_argument("output", type=Path, help="输出 SQLite；已有库会替换指定语言")
    parser.add_argument(
        "--language",
        required=True,
        help="chinese、english、japanese 或 korean",
    )
    parser.add_argument(
        "--package",
        action="append",
        default=[],
        metavar="PCK_FILE_ID=PATH",
        help="现有 PCK audio_meta.json，可重复指定",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="构建前删除整个输出数据库",
    )
    return parser.parse_args(argv)


def parse_package_spec(value: str) -> tuple[int, Path]:
    raw_id, separator, raw_path = value.partition("=")
    if not separator or not raw_id or not raw_path:
        raise ValueError(f"invalid package specification: {value!r}")
    try:
        pck_file_id = int(raw_id)
    except ValueError as error:
        raise ValueError(f"invalid PCK file id: {raw_id!r}") from error
    if pck_file_id < 0:
        raise ValueError("PCK file id must be non-negative")
    return pck_file_id, Path(raw_path)


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        package_specs = [parse_package_spec(value) for value in args.package]
        packages = []
        for index, (pck_file_id, path) in enumerate(package_specs, 1):
            print(f"[{index}/{len(package_specs)}] 读取 PCK 元数据：{path}")
            packages.append((pck_file_id, load_json(path)))
        matches = build_audio_dialog_index_from_packages(
            load_json(args.audio_dialog),
            args.language,
            packages,
        )
        if args.reset and args.output.exists():
            args.output.unlink()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(args.output)) as conn:
            replace_audio_dialog_language(conn, matches)
    except (OSError, UnicodeError, json.JSONDecodeError, sqlite3.Error, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    counts = Counter(match.status for match in matches)
    print(
        f"已写入 {args.output}：{len(matches)} 条；"
        + "，".join(f"{status}={counts[status]}" for status in (
            "matched", "missing", "ambiguous", "collision"
        ))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
