"""用同版本 metadata 常量表与 AI dump 批量生成枚举目录，并可嵌入 MemoryPack schema。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

try:
    from .metadata_enum_catalog import EnumMetadata, build_catalog, read_dump_enums
except ImportError:
    from metadata_enum_catalog import EnumMetadata, build_catalog, read_dump_enums


TYPE_REFERENCE_KEYS = frozenset({"type", "baseType"})
TYPE_NAME_PATTERN = re.compile(r"[A-Za-z_][\w`]*(?:[.+][A-Za-z_][\w`]*)+")


def referenced_schema_types(schema: dict) -> set[str]:
    """Collect native type names mentioned by schema field descriptors.

    The generated schema does not distinguish enum descriptors from ordinary object
    descriptors. Enum identity therefore remains owned by matching game metadata;
    this set is only an inclusion boundary for the metadata-backed catalog.
    """

    result: set[str] = set()

    def collect_type_expression(value: object) -> None:
        if not isinstance(value, str):
            return
        result.update(match.group(0).replace("+", ".") for match in TYPE_NAME_PATTERN.finditer(value))

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in TYPE_REFERENCE_KEYS:
                    collect_type_expression(child)
                elif key == "elementTypes" and isinstance(child, list):
                    for element in child:
                        collect_type_expression(element)
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(schema.get("classes", []))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--dump-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--schema", type=Path, help="可选：输出携带枚举目录的 schema，而不是单独目录")
    parser.add_argument(
        "--referenced-only",
        action="store_true",
        help="仅生成 --schema 字段实际引用的枚举；枚举身份仍由 metadata 确认",
    )
    args = parser.parse_args()
    if args.referenced_only and not args.schema:
        parser.error("--referenced-only requires --schema")
    raw = args.metadata.read_bytes()
    enums, hashes = read_dump_enums(args.dump_root)
    schema = json.loads(args.schema.read_text(encoding="utf-8")) if args.schema else None
    include_types = referenced_schema_types(schema) if args.referenced_only else None
    catalog = {"format": "VfsNativeEnumCatalog", "version": 1,
               "metadataSha256": hashlib.sha256(raw).hexdigest(),
               "dumpSha256": hashes,
               "enums": build_catalog(EnumMetadata(raw), enums, include_types)}
    output = catalog
    if schema is not None:
        output = schema
        output["enumCatalog"] = catalog
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(catalog['enums'])} enum types to {args.output}")


if __name__ == "__main__":
    main()
