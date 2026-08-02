#!/usr/bin/env python3
"""Index types and RVA-bearing members from IL2CPP C# dump files."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


FORMAT = "Il2CppTypeIndex"
VERSION = 1

NAMESPACE_RE = re.compile(r"^namespace\s+(?P<name>[\w.]+)\s*$")
TYPE_META_RE = re.compile(
    r"^\s*// TypeToken:\s*(?P<token>0x[0-9A-Fa-f]+)"
    r"(?:\s+// size:\s*(?P<size>0x[0-9A-Fa-f]+))?"
)
TYPE_RE = re.compile(
    r"^\s*(?P<declaration>(?:public|private|protected|internal)[^;{}]*?"
    r"\b(?:class|struct|interface|enum)\s+(?P<name>[^\s:{]+)[^{}]*)\s*$"
)
RVA_RE = re.compile(
    r"^\s*// RVA:\s*(?P<rva>-1|0x[0-9A-Fa-f]+).*?"
    r"token:\s*(?P<token>0x[0-9A-Fa-f]+)"
)
MEMBER_RE = re.compile(r"^\s*(?P<signature>.+?)\s*\{.*\}\s*$")
PROPERTY_RE = re.compile(r"^\s*(?P<signature>.+?)\s*\{\s*get;")
FIELD_RE = re.compile(r"^\s*(?P<signature>.+?;)\s+//\s+(?P<offset>.+?)\s*$")
AI_CLASS_RE = re.compile(r"^CLASS:\s+(?P<name>.+?)\s*$")
AI_VALUE_RE = re.compile(r"^(?P<key>TYPE|TOKEN|SIZE|EXTENDS|IMPLEMENTS):\s*(?P<value>.*?)\s*$")
AI_FIELD_RE = re.compile(r"^\s{2}(?P<signature>.+?)\s+//\s+(?P<offset>.+?)\s*$")
AI_PROPERTY_RE = re.compile(r"^\s{2}(?P<signature>.+?)\s*$")
AI_METHOD_RE = re.compile(
    r"^\s{2}RVA=(?P<rva>-1|0x[0-9A-Fa-f]+)\s+"
    r"token=(?P<token>0x[0-9A-Fa-f]+)\s+(?P<signature>.+?)\s*$"
)


def _parse_int(value: str | None) -> int | None:
    if value is None or value == "-1":
        return None
    return int(value, 16)


def _new_type(qualified_name: str, line_number: int) -> dict:
    namespace, separator, name = qualified_name.rpartition(".")
    if not separator:
        namespace = ""
        name = qualified_name
    return {
        "namespace": namespace,
        "name": name,
        "qualifiedName": qualified_name,
        "declaration": "",
        "token": None,
        "size": None,
        "line": line_number,
        "fields": [],
        "properties": [],
        "methods": [],
    }


def _build_index(path: Path, dump_format: str, types: list[dict]) -> dict:
    return {
        "format": FORMAT,
        "version": VERSION,
        "dumpFormat": dump_format,
        "source": path.name,
        "typeCount": len(types),
        "types": types,
    }


def parse_normal_dump(path: Path) -> dict:
    namespace = ""
    pending_type_meta: dict | None = None
    pending_rva: dict | None = None
    current_type: dict | None = None
    types: list[dict] = []

    with path.open("r", encoding="utf-8-sig", errors="strict") as source:
        for line_number, raw_line in enumerate(source, 1):
            line = raw_line.rstrip("\r\n")

            if match := NAMESPACE_RE.match(line):
                namespace = match.group("name")
                current_type = None
                continue

            if match := TYPE_META_RE.match(line):
                pending_type_meta = {
                    "token": match.group("token"),
                    "size": _parse_int(match.group("size")),
                }
                current_type = None
                continue

            if pending_type_meta and (match := TYPE_RE.match(line)):
                current_type = {
                    "namespace": namespace,
                    "name": match.group("name"),
                    "qualifiedName": ".".join(
                        part for part in (namespace, match.group("name")) if part
                    ),
                    "declaration": match.group("declaration").strip(),
                    "token": pending_type_meta["token"],
                    "size": pending_type_meta["size"],
                    "line": line_number,
                    "fields": [],
                    "properties": [],
                    "methods": [],
                }
                types.append(current_type)
                pending_type_meta = None
                continue

            if current_type is None:
                continue

            if match := RVA_RE.match(line):
                pending_rva = {
                    "rva": _parse_int(match.group("rva")),
                    "token": match.group("token"),
                    "line": line_number,
                }
                continue

            if pending_rva and (match := MEMBER_RE.match(line)):
                current_type["methods"].append(
                    {
                        **pending_rva,
                        "signature": match.group("signature").strip(),
                    }
                )
                pending_rva = None
                continue

            if "/* RVA:" in line and (match := PROPERTY_RE.match(line)):
                current_type["properties"].append(
                    {"signature": match.group("signature").strip(), "line": line_number}
                )
                continue

            if match := FIELD_RE.match(line):
                current_type["fields"].append(
                    {
                        "signature": match.group("signature").strip(),
                        "offset": match.group("offset").strip(),
                        "line": line_number,
                    }
                )

    return _build_index(path, "normal-csharp", types)


def parse_ai_dump(path: Path) -> dict:
    types: list[dict] = []
    current_type: dict | None = None
    section: str | None = None

    with path.open("r", encoding="utf-8-sig", errors="strict") as source:
        for line_number, raw_line in enumerate(source, 1):
            line = raw_line.rstrip("\r\n")

            if match := AI_CLASS_RE.match(line):
                current_type = _new_type(match.group("name"), line_number)
                types.append(current_type)
                section = None
                continue

            if current_type is None:
                continue

            if line == "END_CLASS":
                current_type = None
                section = None
                continue

            if line in {"FIELDS:", "PROPERTIES:", "METHODS:"}:
                section = line[:-1].lower()
                continue

            if match := AI_VALUE_RE.match(line):
                key = match.group("key")
                value = match.group("value")
                if key == "TYPE":
                    current_type["declaration"] = value
                elif key == "TOKEN":
                    current_type["token"] = value
                elif key == "SIZE":
                    current_type["size"] = _parse_int(value)
                elif key == "EXTENDS":
                    current_type["extends"] = value
                elif key == "IMPLEMENTS":
                    current_type["implements"] = value.split()
                continue

            if section == "fields" and (match := AI_FIELD_RE.match(line)):
                current_type["fields"].append(
                    {
                        "signature": match.group("signature").strip(),
                        "offset": match.group("offset").strip(),
                        "line": line_number,
                    }
                )
                continue

            if section == "properties" and (match := AI_PROPERTY_RE.match(line)):
                current_type["properties"].append(
                    {"signature": match.group("signature").strip(), "line": line_number}
                )
                continue

            if section == "methods" and (match := AI_METHOD_RE.match(line)):
                current_type["methods"].append(
                    {
                        "rva": _parse_int(match.group("rva")),
                        "token": match.group("token"),
                        "signature": match.group("signature").strip(),
                        "line": line_number,
                    }
                )

    return _build_index(path, "ai-structured", types)


def parse_dump(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig", errors="strict") as source:
        for line in source:
            if line.startswith("CLASS:"):
                return parse_ai_dump(path)
    return parse_normal_dump(path)


def filter_types(index: dict, pattern: str | None) -> dict:
    if not pattern:
        return index
    matcher = re.compile(pattern, re.IGNORECASE)
    types = [
        type_info
        for type_info in index["types"]
        if matcher.search(
            "\n".join(
                [
                    type_info["namespace"],
                    type_info["qualifiedName"],
                    type_info["declaration"],
                    *(field["signature"] for field in type_info["fields"]),
                    *(prop["signature"] for prop in type_info["properties"]),
                    *(method["signature"] for method in type_info["methods"]),
                ]
            )
        )
    ]
    return {**index, "typeCount": len(types), "types": types}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--match",
        help="Optional case-insensitive regex matched against each complete type record",
    )
    args = parser.parse_args()

    index = filter_types(parse_dump(args.input), args.match)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {index['typeCount']} types to {args.output}")


if __name__ == "__main__":
    main()
