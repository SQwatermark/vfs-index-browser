#!/usr/bin/env python3
"""按名义类型引用图分析多个 SparkBuffer schema 根的领域归属。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from vfs_crypto import decrypt_vfs_file
from sparkbuffer import (
    BeanField,
    BeanType,
    EnumType,
    RootDef,
    SparkType,
)

try:
    from sparkbuffer import parse_sparkbuffer_schema
except ImportError:
    # 允许把单文件工具通过 stdin 投送到尚未更新公共 API 的只读研究主机。
    from types import SimpleNamespace

    from sparkbuffer import SparkReader, TypeRegistry, _parse_root_def, _parse_type_definitions

    def parse_sparkbuffer_schema(data: bytes) -> Any:
        reader = SparkReader(data)
        type_def_offset = reader.read_int32()
        root_def_offset = reader.read_int32()
        data_offset = reader.read_int32()
        registry = TypeRegistry()
        reader.seek(type_def_offset)
        _parse_type_definitions(reader, registry)
        reader.seek(root_def_offset)
        return SimpleNamespace(
            root=_parse_root_def(reader), registry=registry, data_offset=data_offset
        )


@dataclass(frozen=True)
class SchemaRootInput:
    owner: str
    label: str
    payload: bytes


@dataclass(frozen=True)
class TypeIdentity:
    kind: str
    name: str
    signature: tuple[Any, ...]
    definition: Any


def analyze_schema_ownership(inputs: Iterable[SchemaRootInput]) -> dict[str, Any]:
    roots = list(inputs)
    if not roots:
        raise ValueError("expected at least one schema root")

    identities: dict[int, TypeIdentity] = {}
    references: dict[int, set[int]] = {}
    root_types: dict[tuple[str, str], set[int]] = {}
    root_names: dict[tuple[str, str], str] = {}
    root_digests: dict[tuple[str, str], str] = {}
    for item in roots:
        if not item.owner:
            raise ValueError(f"{item.label}: owner cannot be empty")
        key = (item.owner, item.label)
        if key in root_types:
            raise ValueError(f"duplicate schema root {item.owner}={item.label}")
        schema = parse_sparkbuffer_schema(item.payload)
        local_identities, local_references = describe_schema(schema)
        merge_type_graph(identities, references, local_identities, local_references, item.label)
        root_types[key] = reachable_types(schema.root, local_identities, local_references, item.label)
        root_names[key] = schema.root.name
        root_digests[key] = hashlib.sha256(item.payload).hexdigest()

    owners = sorted({item.owner for item in roots})
    type_roots: dict[int, list[tuple[str, str]]] = {}
    for root_key, hashes in root_types.items():
        for type_hash in hashes:
            type_roots.setdefault(type_hash, []).append(root_key)

    types = []
    for type_hash in sorted(type_roots, key=lambda value: (identities[value].name, value)):
        identity = identities[type_hash]
        used_by_roots = sorted(type_roots[type_hash])
        used_by_owners = sorted({owner for owner, _ in used_by_roots})
        types.append(
            {
                "typeHash": unsigned_hash(type_hash),
                "typeHashHex": hash_hex(type_hash),
                "kind": identity.kind,
                "name": identity.name,
                "definition": identity.definition,
                "ownership": "shared" if len(used_by_owners) > 1 else "private",
                "owners": used_by_owners,
                "roots": [
                    {"owner": owner, "label": label} for owner, label in used_by_roots
                ],
                "references": [hash_hex(value) for value in sorted(references[type_hash])],
            }
        )

    return {
        "format": "SparkBufferSchemaOwnership",
        "version": 1,
        "identity": "typeHash",
        "owners": owners,
        "roots": [
            {
                "owner": owner,
                "label": label,
                "rootName": root_names[(owner, label)],
                "sha256": root_digests[(owner, label)],
                "reachableTypeCount": len(root_types[(owner, label)]),
            }
            for owner, label in sorted(root_types)
        ],
        "summary": {
            "typeCount": len(types),
            "sharedTypeCount": sum(item["ownership"] == "shared" for item in types),
            "privateTypeCount": sum(item["ownership"] == "private" for item in types),
        },
        "types": types,
    }


def describe_schema(
    schema: Any,
) -> tuple[dict[int, TypeIdentity], dict[int, set[int]]]:
    identities: dict[int, TypeIdentity] = {}
    references: dict[int, set[int]] = {}
    for type_hash, bean in schema.registry.beans.items():
        identities[type_hash] = TypeIdentity(
            "bean", bean.name, bean_signature(bean), bean_definition(bean)
        )
        references[type_hash] = referenced_field_hashes(bean.fields)
    for type_hash, enum in schema.registry.enums.items():
        identities[type_hash] = TypeIdentity(
            "enum", enum.name, enum_signature(enum), enum_definition(enum)
        )
        references[type_hash] = set()
    return identities, references


def merge_type_graph(
    identities: dict[int, TypeIdentity],
    references: dict[int, set[int]],
    incoming_identities: dict[int, TypeIdentity],
    incoming_references: dict[int, set[int]],
    label: str,
) -> None:
    for type_hash, identity in incoming_identities.items():
        existing = identities.get(type_hash)
        if existing is not None and existing != identity:
            raise ValueError(
                f"{label}: type hash {hash_hex(type_hash)} conflicts: "
                f"{existing.kind} {existing.name!r} != {identity.kind} {identity.name!r}"
            )
        identities[type_hash] = identity
        references[type_hash] = set(incoming_references[type_hash])


def reachable_types(
    root: RootDef,
    identities: dict[int, TypeIdentity],
    references: dict[int, set[int]],
    label: str,
) -> set[int]:
    pending = list(referenced_root_hashes(root))
    seen: set[int] = set()
    while pending:
        type_hash = pending.pop()
        if type_hash in seen:
            continue
        if type_hash not in identities:
            raise ValueError(f"{label}: unresolved type hash {hash_hex(type_hash)}")
        seen.add(type_hash)
        pending.extend(references[type_hash] - seen)
    return seen


def referenced_root_hashes(root: RootDef) -> set[int]:
    result: set[int] = set()
    if root.field_type in {SparkType.BEAN, SparkType.ENUM} and root.type_hash is not None:
        result.add(root.type_hash)
    if root.field_type == SparkType.MAP:
        if root.type2 in {SparkType.BEAN, SparkType.ENUM} and root.type_hash is not None:
            result.add(root.type_hash)
        if root.type3 in {SparkType.BEAN, SparkType.ENUM} and root.type_hash2 is not None:
            result.add(root.type_hash2)
    return result


def referenced_field_hashes(fields: Iterable[BeanField]) -> set[int]:
    result: set[int] = set()
    for field in fields:
        if field.field_type in {SparkType.BEAN, SparkType.ENUM} and field.type_hash is not None:
            result.add(field.type_hash)
        elif field.field_type == SparkType.ARRAY:
            if field.type2 in {SparkType.BEAN, SparkType.ENUM} and field.type_hash is not None:
                result.add(field.type_hash)
        elif field.field_type == SparkType.MAP:
            if field.type2 in {SparkType.BEAN, SparkType.ENUM} and field.type_hash is not None:
                result.add(field.type_hash)
            if field.type3 in {SparkType.BEAN, SparkType.ENUM} and field.type_hash2 is not None:
                result.add(field.type_hash2)
    return result


def bean_signature(bean: BeanType) -> tuple[Any, ...]:
    return tuple(
        (
            field.name,
            int(field.field_type),
            None if field.type2 is None else int(field.type2),
            None if field.type3 is None else int(field.type3),
            field.type_hash,
            field.type_hash2,
        )
        for field in bean.fields
    )


def enum_signature(enum: EnumType) -> tuple[Any, ...]:
    return tuple(sorted(enum.values.items()))


def bean_definition(bean: BeanType) -> dict[str, Any]:
    return {
        "fields": [
            {
                "name": field.name,
                "fieldType": field.field_type.name,
                **({"itemOrKeyType": field.type2.name} if field.type2 is not None else {}),
                **({"valueType": field.type3.name} if field.type3 is not None else {}),
                **({"typeHashHex": hash_hex(field.type_hash)} if field.type_hash is not None else {}),
                **(
                    {"valueTypeHashHex": hash_hex(field.type_hash2)}
                    if field.type_hash2 is not None
                    else {}
                ),
            }
            for field in bean.fields
        ]
    }


def enum_definition(enum: EnumType) -> dict[str, Any]:
    return {
        "values": [
            {"value": value, "name": name} for value, name in sorted(enum.values.items())
        ]
    }


def parse_assignment(value: str) -> tuple[str, str]:
    owner, separator, target = value.partition("=")
    if not separator or not owner or not target:
        raise argparse.ArgumentTypeError("expected OWNER=PATH_OR_LOGICAL_ID")
    return owner, target


def read_file_root(assignment: tuple[str, str]) -> SchemaRootInput:
    owner, raw_path = assignment
    path = Path(raw_path)
    return SchemaRootInput(owner, str(path), path.read_bytes())


def read_vfs_roots(
    database: Path, assignments: Iterable[tuple[str, str]]
) -> list[SchemaRootInput]:
    result: list[SchemaRootInput] = []
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        for owner, logical_id in assignments:
            rows = connection.execute(
                """
                SELECT * FROM files
                WHERE logical_id = ?
                ORDER BY CASE source WHEN 'Persistent' THEN 0 ELSE 1 END, id
                """,
                (logical_id,),
            ).fetchall()
            row = next((item for item in rows if Path(item["chunk_path"]).exists()), None)
            if row is None:
                raise ValueError(f"{logical_id}: no available VFS chunk")
            payload = read_vfs_record(row)
            result.append(SchemaRootInput(owner, logical_id, payload))
    return result


def read_vfs_record(row: sqlite3.Row) -> bytes:
    path = Path(row["chunk_path"])
    with path.open("rb") as source:
        source.seek(int(row["offset"]))
        payload = source.read(int(row["length"]))
    expected = int(row["length"])
    if len(payload) != expected:
        raise ValueError(f"{row['logical_id']}: short read {len(payload)} != {expected}")
    if row["encrypted"]:
        return decrypt_vfs_file(payload, int(row["iv_seed"]))
    return payload


def unsigned_hash(value: int) -> int:
    return value & 0xFFFFFFFF


def hash_hex(value: int) -> str:
    return f"0x{unsigned_hash(value):08X}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        action="append",
        default=[],
        type=parse_assignment,
        metavar="OWNER=PATH",
        help="已解密 SparkBuffer 文件；可重复指定，同一 OWNER 可有多个根",
    )
    parser.add_argument(
        "--logical-root",
        action="append",
        default=[],
        type=parse_assignment,
        metavar="OWNER=LOGICAL_ID",
        help="VFS 索引中的 logical_id；必须同时提供 --vfs-db",
    )
    parser.add_argument("--vfs-db", type=Path, help="vfs-index-browser SQLite 索引")
    parser.add_argument("--revision", help="写入报告的版本/manifest 标签")
    parser.add_argument("--source-label", help="写入报告的证据来源说明")
    parser.add_argument("--output", type=Path, help="输出 JSON；省略时写到 stdout")
    args = parser.parse_args()

    inputs = [read_file_root(item) for item in args.root]
    if args.logical_root:
        if args.vfs_db is None:
            parser.error("--logical-root requires --vfs-db")
        inputs.extend(read_vfs_roots(args.vfs_db, args.logical_root))
    elif args.vfs_db is not None:
        parser.error("--vfs-db requires at least one --logical-root")

    try:
        report = analyze_schema_ownership(inputs)
    except (OSError, sqlite3.Error, ValueError) as error:
        raise SystemExit(str(error)) from error
    if args.revision is not None:
        report["revision"] = args.revision
    if args.source_label is not None:
        report["sourceLabel"] = args.source_label
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        print(rendered, end="")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"wrote {report['summary']['typeCount']} types to {args.output}")


if __name__ == "__main__":
    main()
