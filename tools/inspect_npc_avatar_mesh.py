#!/usr/bin/env python3
"""将 AnimeStudio 导出的 NPC AvatarMesh TypeTree 转成可检查的 JSON。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from npc_avatar_config import (
    attach_resolved_paths,
    parse_avatar_mesh,
    summarize_avatar_mesh,
)
from string_path_hash import StringPathHashIndex


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="检查 NPC AvatarMesh TypeTree，并解析其中的运行时路径哈希。"
    )
    parser.add_argument("input", type=Path, help="AnimeStudio TypeTree 文本")
    parser.add_argument(
        "--string-path-hash",
        type=Path,
        required=True,
        help="游戏目录中的 StringPathHash.bin",
    )
    parser.add_argument("--output", type=Path, help="结构化 JSON 输出路径")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        document = parse_avatar_mesh(args.input.read_text(encoding="utf-8"))
        attach_resolved_paths(document, StringPathHashIndex(args.string_path_hash))
    except (OSError, UnicodeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    document["summary"] = summarize_avatar_mesh(document)
    output = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
        print(json.dumps(document["summary"], ensure_ascii=False))
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
