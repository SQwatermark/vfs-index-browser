#!/usr/bin/env python3
"""Extract one IL2CPP enum and its exact constants from global metadata."""

from __future__ import annotations

import argparse
import importlib.util
import json
import struct
import sys
from pathlib import Path
from typing import Any


DEFAULT_HELPER = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "endfield_research_kit"
    / "tools"
    / "endfield-il2cpp"
    / "catalog_option_flow_metadata.py"
)


def load_helper(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("endfield_metadata_helper", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load metadata helper: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def read_compressed_int32(data: bytes, offset: int) -> int:
    first = data[offset]
    if first == 0xFF:
        return -(1 << 31)
    if first & 0x80 == 0:
        raw = first
    elif first & 0xC0 == 0x80:
        raw = ((first & 0x3F) << 8) | data[offset + 1]
    elif first & 0xE0 == 0xC0:
        raw = (
            ((first & 0x1F) << 24)
            | (data[offset + 1] << 16)
            | (data[offset + 2] << 8)
            | data[offset + 3]
        )
    elif first == 0xF0:
        raw = struct.unpack_from(">I", data, offset + 1)[0]
    else:
        raise ValueError(f"unsupported compressed integer at 0x{offset:x}")
    return -((raw >> 1) + 1) if raw & 1 else raw >> 1


def infer_integer_encoding(data_indexes: list[int]) -> str:
    """Distinguish IL2CPP's compressed Int32 and fixed-width Int64 defaults."""
    unique_indexes = sorted(set(data_indexes))
    deltas = [
        right - left
        for left, right in zip(unique_indexes, unique_indexes[1:])
        if right > left
    ]
    if not deltas:
        raise RuntimeError("cannot infer enum encoding from fewer than two constants")
    if all(delta == 8 for delta in deltas):
        return "int64"
    return "compressed-int32"


def read_fixed_int64(data: bytes, offset: int) -> int:
    end = offset + 8
    if end > len(data):
        raise ValueError(f"integer at 0x{offset:x} exceeds input")
    return int.from_bytes(data[offset:end], byteorder="little", signed=True)


def read_enum_integer(data: bytes, offset: int, encoding: str) -> int:
    if encoding == "int64":
        return read_fixed_int64(data, offset)
    if encoding == "compressed-int32":
        return read_compressed_int32(data, offset)
    raise ValueError(f"unsupported enum encoding: {encoding}")


def normalize_type_name(type_name: str) -> str:
    """Treat IL2CPP's nested-type separator as equivalent to a namespace dot."""
    return type_name.replace("+", ".")


def extract_enum(metadata: Any, type_name: str) -> tuple[str, list[dict[str, Any]]]:
    defaults: dict[int, int] = {}
    section = metadata.sections["fieldDefaultValues"]
    if section.size % 12:
        raise RuntimeError("fieldDefaultValues is not aligned to 12-byte records")
    for offset in range(section.offset, section.offset + section.size, 12):
        field_index, _type_index, data_index = struct.unpack_from(
            "<iii", metadata.buf, offset
        )
        if field_index in defaults:
            raise RuntimeError(f"duplicate field default for field {field_index}")
        defaults[field_index] = data_index

    value_section = metadata.sections["fieldAndParameterDefaultValueData"]
    suggestions = []
    requested_name = normalize_type_name(type_name)
    for type_def in metadata.types:
        full_name = metadata.type_full_name(type_def)
        if type_name.rsplit(".", 1)[-1].lower() in full_name.lower():
            suggestions.append(full_name)
        if normalize_type_name(full_name) != requested_name:
            continue
        member_fields = [
            field
            for field in metadata.fields_for(type_def)
            if metadata.string(field.name_index) != "value__"
        ]
        member_data_indexes = []
        for field in member_fields:
            data_index = defaults.get(field.index)
            if data_index is None:
                name = metadata.string(field.name_index)
                raise RuntimeError(f"enum member has no constant value: {type_name}.{name}")
            member_data_indexes.append(data_index)
        encoding = infer_integer_encoding(member_data_indexes)

        rows = []
        for field, data_index in zip(member_fields, member_data_indexes):
            name = metadata.string(field.name_index)
            value = read_enum_integer(
                metadata.buf, value_section.offset + data_index, encoding
            )
            rows.append(
                {
                    "name": name,
                    "value": value,
                    "fieldIndex": field.index,
                    "token": f"0x{field.token:08x}",
                }
            )
        return full_name, sorted(rows, key=lambda row: (row["value"], row["name"]))
    nearby = ", ".join(suggestions[:12]) or "none"
    raise RuntimeError(f"metadata type not found: {type_name}; nearby: {nearby}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("metadata", type=Path)
    parser.add_argument("type_name")
    parser.add_argument("--helper", type=Path, default=DEFAULT_HELPER)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    helper = load_helper(args.helper)
    metadata = helper.Metadata(args.metadata)
    resolved_type_name, rows = extract_enum(metadata, args.type_name)
    report = {
        "format": "EndfieldMetadataEnum",
        "version": 1,
        "type": resolved_type_name,
        "members": rows,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"wrote {args.output} ({len(rows)} members)")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
