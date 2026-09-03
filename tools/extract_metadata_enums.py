"""用同版本 metadata 常量表与 AI dump 批量生成枚举目录，并可嵌入 MemoryPack schema。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

try:
    from .metadata_enum_catalog import EnumMetadata, build_catalog, read_dump_enums
except ImportError:
    from metadata_enum_catalog import EnumMetadata, build_catalog, read_dump_enums


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--dump-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--schema", type=Path, help="可选：输出携带枚举目录的 schema，而不是单独目录")
    args = parser.parse_args()
    raw = args.metadata.read_bytes()
    enums, hashes = read_dump_enums(args.dump_root)
    catalog = {"format": "VfsNativeEnumCatalog", "version": 1,
               "metadataSha256": hashlib.sha256(raw).hexdigest(),
               "dumpSha256": hashes, "enums": build_catalog(EnumMetadata(raw), enums)}
    output = catalog
    if args.schema:
        output = json.loads(args.schema.read_text(encoding="utf-8"))
        output["enumCatalog"] = catalog
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(catalog['enums'])} enum types to {args.output}")


if __name__ == "__main__":
    main()
