#!/usr/bin/env python3
"""Experimentally decode Endfield schema-based binary JSON payloads.

This is a research decoder, not a complete MemoryPack implementation. It uses
the schema emitted by `extract_memorypack_schema.py`, reads a VFS-decrypted
payload, and decodes the supported subset: object headers, strings, primitives,
arrays/lists/dictionaries, enum-like int32 values, and generated wrapper
classes. Unsupported polymorphic unions are reported with the exact offset so
they can be researched next.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_DB = PROJECT_ROOT / "data" / "endfield-vfs-index.sqlite"
DEFAULT_SCHEMA = PROJECT_ROOT / "data" / "reports" / "memorypack-core-schema-x64-release.json"

NULL_OBJECT = 0xFF
HEADER_U16 = 0xFA
HEADER_U32 = 0xFB
MAX_COLLECTION_LENGTH = 200_000

INT32_ENUM_PREFIXES = (
    "Beyond.",
    "Cinemachine.",
    "Proto.",
)

SCALAR_READERS = {
    "System.Boolean": "read_bool",
    "System.Byte": "read_u8",
    "System.SByte": "read_i8",
    "System.Int16": "read_i16",
    "System.UInt16": "read_u16",
    "System.Int32": "read_i32",
    "System.UInt32": "read_u32",
    "System.Int64": "read_i64",
    "System.UInt64": "read_u64",
    "System.Single": "read_f32",
    "System.Double": "read_f64",
    "System.String": "read_string",
}

MEMBER_TYPE_OVERRIDES = {
    ("Beyond.Blackboard.BlackboardDouble", "value"): "System.Single",
    ("Beyond.Blackboard.BlackboardInt", "value"): "System.Int32",
    ("Beyond.Blackboard.BlackboardString", "value"): "System.String",
    ("Beyond.Gameplay.Core.BlackboardImpactValue", "value"): "System.Int32",
    # 反编译泛型实参和三份本地技能文件的完整消费均确认该值为 Int32。
    ("Beyond.Gameplay.Core.BlackboardSuperArmorValue", "value"): "System.Int32",
    ("Beyond.Gameplay.Core.Conditions.BlackboardBuffId", "value"): "System.String",
    (
        "Beyond.Gameplay.Core.Conditions.CheckBuffIdInContext.Data",
        "buffIdList",
    ): "System.Collections.Generic.List<Beyond.Gameplay.Core.Conditions.BlackboardBuffId>",
    (
        "Beyond.Gameplay.Core.Conditions.CheckBuffIdInContextAdvanced.Data",
        "buffIdList",
    ): "System.Collections.Generic.List<Beyond.Gameplay.Core.Conditions.BlackboardBuffId>",
}

TYPE_OVERRIDES = {
    "Beyond.Gameplay.Core.Buff.LifeType": "System.Byte",
    "Beyond.Gameplay.Core.BuffStackingSettings.IdentifierType": "System.Byte",
    "Beyond.Gameplay.Core.BuffStackingSettings.StackingType": "System.Byte",
    "Beyond.Gameplay.Core.EnemyHurtShakeIntensity": "System.Byte",
    "UnityEngine.AnimatorControllerParameterType": "System.Int32",
}

TYPE_ALIASES = {}

RAW_GAMEPLAY_TAG_FIELDS = {
    # 1.4.4 ObtainCost wrapper Deserialize (RVA 0x03E61583):
    # one bool + one inline int32, no GameplayTag object header.
    ("Beyond.Gameplay.Core.ObtainCostAction.Data", "uspRecoverTag"),
    ("Beyond.Gameplay.Core.HitStopAction.Data", "timeDilationPriority"),
    ("Beyond.Gameplay.Core.TimeDilationAction.Data", "slot"),
    ("Beyond.Gameplay.Core.TimeDilationAction.Data", "timeDilationPriority"),
    ("Beyond.Gameplay.Core.UltimateTimeAction.Data", "timeDilationPriority"),
    ("Beyond.Gameplay.AI.EnemyCheckAIMarker.EnemyCheckAIMarkerInfo", "marker"),
}

RAW_GAMEPLAY_TAG_COLLECTION_FIELDS = {
    # BuffData.Deserialize 0x0387B803 -> raw count * 4 reader 0x03A0EC30;
    # result is stored in BuffData.applyTags (+0x68) at 0x0387BBB5.
    ("Beyond.Gameplay.Core.BuffData", "applyTags"),
    ("Beyond.Gameplay.Core.GameplayTagQuery", "tags"),
}

UNMANAGED_STRUCT_LAYOUTS = {
    "Beyond.Gameplay.AI.EnemyCheckAIMarker.EnemyCheckAIMarkerInfo": {
        "size": 8,
        "fields": [
            {"name": "invert", "type": "System.Boolean", "offset": 0},
            {
                "name": "marker",
                "type": "Beyond.Gameplay.Core.GameplayTag",
                "offset": 4,
            },
        ],
    },
    "Beyond.Gameplay.Core.DispelConfig": {
        "size": 8,
        "fields": [
            {"name": "canBeDispelled", "type": "System.Boolean", "offset": 0},
            {"name": "dispelledLevel", "type": "Beyond.Gameplay.Core.DispelLevel", "offset": 4},
        ],
    },
    "Beyond.Gameplay.Core.BuffIconConfig.OrderPriorityConfig": {
        "size": 12,
        "fields": [
            {"name": "useDirectoryValue", "type": "System.Boolean", "offset": 0},
            {"name": "priorityValue", "type": "System.Int32", "offset": 4},
            {"name": "priorityEnum", "type": "Beyond.Gameplay.Core.BuffIconConfig.OrderPriority", "offset": 8},
        ],
    },
    "Beyond.Gameplay.View.CameraControlStateInitialParam": {
        "size": 24,
        "fields": [
            {"name": "applyHorizontalAngle", "type": "System.Boolean", "offset": 0},
            {"name": "horizontalAngleRelativeToCharacter", "type": "System.Boolean", "offset": 1},
            {"name": "horizontalAngle", "type": "System.Single", "offset": 4},
            {"name": "applyVerticalValue", "type": "System.Boolean", "offset": 8},
            {"name": "verticalValue", "type": "System.Single", "offset": 12},
            {"name": "applyZoomScale", "type": "System.Boolean", "offset": 16},
            {"name": "zoomScale", "type": "System.Single", "offset": 20},
        ],
    },
}

KNOWN_UNION_BASE_TYPES = {
    "Beyond.Gameplay.Core.AbilityAction.AbilityActionData",
    "Beyond.Gameplay.Core.CalculationBase",
    "Beyond.Gameplay.Core.DamageProcessorBase",
    "Beyond.Gameplay.Core.HealProcessorBase",
    "Beyond.Gameplay.Core.PoiseProcessorBase",
    "Beyond.Gameplay.Core.Selector.Finder.Data",
    "Beyond.Gameplay.Core.Selector.PostProcessor.Data",
    "Beyond.Gameplay.Core.Selector.Validator.Data",
}


class DecodeError(Exception):
    def __init__(self, message: str, offset: int, path: str, partial: Any | None = None):
        super().__init__(f"{message} at 0x{offset:x} ({path})")
        self.message = message
        self.offset = offset
        self.path = path
        self.partial = partial


@dataclass(frozen=True)
class VfsPayload:
    data: bytes
    label: str
    logical_id: str | None


class MemoryPackReader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def tell(self) -> int:
        return self.pos

    def read(self, size: int) -> bytes:
        if size < 0:
            raise DecodeError(f"invalid read size {size}", self.pos, "$")
        if self.pos + size > len(self.data):
            raise DecodeError(f"unexpected EOF while reading {size} bytes", self.pos, "$")
        chunk = self.data[self.pos : self.pos + size]
        self.pos += size
        return chunk

    def read_u8(self) -> int:
        return self.read(1)[0]

    def read_i8(self) -> int:
        return struct.unpack("<b", self.read(1))[0]

    def read_bool(self) -> bool:
        value = self.read_u8()
        if value not in (0, 1):
            raise DecodeError(f"invalid boolean byte {value}", self.pos - 1, "$")
        return bool(value)

    def read_i16(self) -> int:
        return struct.unpack("<h", self.read(2))[0]

    def read_u16(self) -> int:
        return struct.unpack("<H", self.read(2))[0]

    def read_i32(self) -> int:
        return struct.unpack("<i", self.read(4))[0]

    def read_u32(self) -> int:
        return struct.unpack("<I", self.read(4))[0]

    def read_i64(self) -> int:
        return struct.unpack("<q", self.read(8))[0]

    def read_u64(self) -> int:
        return struct.unpack("<Q", self.read(8))[0]

    def read_f32(self) -> float:
        return struct.unpack("<f", self.read(4))[0]

    def read_f64(self) -> float:
        return struct.unpack("<d", self.read(8))[0]

    def read_string(self) -> str | None:
        length = self.read_i32()
        if length == -1:
            return None
        if length < 0:
            raise DecodeError(f"invalid string length {length}", self.pos - 4, "$")
        raw = self.read(length)
        return raw.decode("utf-8")

    def read_collection_header(self) -> int | None:
        length = self.read_i32()
        if length == -1:
            return None
        if length < 0 or length > MAX_COLLECTION_LENGTH:
            raise DecodeError(f"invalid collection length {length}", self.pos - 4, "$")
        return length

    def read_compact_header(self) -> int | None:
        marker = self.read_u8()
        if marker == NULL_OBJECT:
            return None
        if marker < HEADER_U16:
            return marker
        if marker == HEADER_U16:
            return self.read_u16()
        if marker == HEADER_U32:
            return self.read_u32()
        raise DecodeError(f"unsupported compact header marker 0x{marker:02x}", self.pos - 1, "$")


class SchemaIndex:
    def __init__(self, payload: dict):
        self.payload = payload
        self.classes = {item["class"]: item for item in payload["classes"] if item.get("class")}

    @classmethod
    def load(cls, path: Path) -> "SchemaIndex":
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def get(self, class_name: str) -> dict | None:
        return self.classes.get(class_name)


class Decoder:
    def __init__(
        self,
        schema: SchemaIndex,
        union_map: dict[str, dict[int, str]] | None = None,
        reference_types: dict[str, str] | None = None,
        trace_enabled: bool = False,
    ):
        self.schema = schema
        self.union_map = union_map or {}
        self.reference_types = reference_types or {}
        self.discovered_unions: dict[str, dict[int, str]] = {}
        self.trace_enabled = trace_enabled
        self.trace: list[dict] = []

    def decode(self, reader: MemoryPackReader, class_name: str) -> Any:
        return self.read_object(reader, class_name, "$")

    def read_value(self, reader: MemoryPackReader, type_name: str, path: str, class_name: str | None = None, member: str | None = None) -> Any:
        try:
            return self._read_value(reader, type_name, path, class_name, member)
        except DecodeError as error:
            if error.path == "$":
                raise DecodeError(error.message, error.offset, path, error.partial) from error
            raise

    def _read_value(
        self,
        reader: MemoryPackReader,
        type_name: str,
        path: str,
        class_name: str | None = None,
        member: str | None = None,
    ) -> Any:
        type_name = MEMBER_TYPE_OVERRIDES.get((class_name, member), type_name)
        type_name = type_name.strip()
        type_name = TYPE_ALIASES.get(type_name, type_name)
        type_name = TYPE_OVERRIDES.get(type_name, type_name)

        if (class_name, member) == ("Beyond.Gameplay.Core.BuffData", "tagsAfterTriggerExtendBuffAction"):
            return self.read_buff_tags_after_trigger(reader, path, type_name)
        if type_name == "Beyond.Gameplay.Core.GameplayTag" and (class_name, member) in RAW_GAMEPLAY_TAG_FIELDS:
            return {"tagId": reader.read_i32()}
        if type_name == "Beyond.Gameplay.Core.GameplayTag[]" and (class_name, member) in RAW_GAMEPLAY_TAG_COLLECTION_FIELDS:
            return self.read_raw_gameplay_tag_collection(reader)

        if type_name == "TSerializeValue":
            raise DecodeError("generic runtime field type needs a concrete override", reader.tell(), path)
        if type_name.endswith("[]"):
            return self.read_collection(reader, type_name[:-2].strip(), path)
        if type_name.startswith("System.Collections.Generic.List<"):
            return self.read_collection(reader, generic_arg(type_name), path)
        if type_name.startswith("System.Collections.Generic.Dictionary<"):
            key_type, value_type = generic_args(type_name)
            return self.read_dictionary(reader, key_type, value_type, path)
        if type_name == "UnityEngine.Vector3":
            return {
                "x": reader.read_f32(),
                "y": reader.read_f32(),
                "z": reader.read_f32(),
            }
        if type_name == "UnityEngine.Vector2":
            return {
                "x": reader.read_f32(),
                "y": reader.read_f32(),
            }
        if type_name in {"UnityEngine.Vector4", "UnityEngine.Color", "UnityEngine.Quaternion"}:
            return {
                "x": reader.read_f32(),
                "y": reader.read_f32(),
                "z": reader.read_f32(),
                "w": reader.read_f32(),
            }
        if type_name == "UnityEngine.AnimationCurve":
            return self.read_animation_curve(reader, path)

        reader_name = SCALAR_READERS.get(type_name)
        if reader_name:
            return getattr(reader, reader_name)()

        if type_name == "UnityEngine.LayerMask":
            return {"m_Mask": reader.read_i32()}


        if type_name in self.union_map or type_name in KNOWN_UNION_BASE_TYPES:
            return self.read_union(reader, path, type_name)

        if type_name in UNMANAGED_STRUCT_LAYOUTS:
            return self.read_unmanaged_struct(reader, path, type_name)

        if self.schema.get(type_name):
            return self.read_object(reader, type_name, path)

        if type_name.startswith(INT32_ENUM_PREFIXES):
            return reader.read_i32()

        raise DecodeError(f"unsupported type {type_name}", reader.tell(), path)

    def read_object(self, reader: MemoryPackReader, class_name: str, path: str) -> Any:
        class_schema = self.schema.get(class_name)
        if not class_schema:
            raise DecodeError(f"missing schema for {class_name}", reader.tell(), path)

        start = reader.tell()
        member_count = reader.read_compact_header()
        if member_count is None:
            return None
        members = class_schema.get("memberDetails") or [{"name": name} for name in class_schema.get("members", [])]
        if member_count > len(members):
            raise DecodeError(
                f"{class_name} declares {len(members)} members but payload has {member_count}",
                start,
                path,
            )

        result: dict[str, Any] = {}
        try:
            for index in range(member_count):
                member = members[index]
                name = member["name"]
                member_type = member.get("type")
                if not member_type:
                    raise DecodeError(f"missing runtime type for member {name}", reader.tell(), f"{path}.{name}")
                field_path = f"{path}.{name}"
                field_start = reader.tell()
                result[name] = self.read_value(reader, member_type, field_path, class_name, name)
                if self.trace_enabled:
                    self.trace.append(
                        {
                            "path": field_path,
                            "type": member_type,
                            "start": field_start,
                            "startHex": f"0x{field_start:x}",
                            "end": reader.tell(),
                            "endHex": f"0x{reader.tell():x}",
                        }
                    )
        except DecodeError as error:
            result["__decodeError"] = {
                "message": error.message,
                "offset": error.offset,
                "offsetHex": f"0x{error.offset:x}",
                "path": error.path,
            }
            raise DecodeError(error.message, error.offset, error.path, result) from error

        for member in members[member_count:]:
            result[member["name"]] = None
        return result

    def read_buff_tags_after_trigger(self, reader: MemoryPackReader, path: str, type_name: str) -> list[Any]:
        start = reader.tell()
        if reader.data[start : start + 5] == b"\x00\x00\x00\x00\x00":
            reader.read(5)
            return []
        return self.read_collection(reader, "Beyond.Gameplay.Core.GameplayTag", path)

    def read_raw_gameplay_tag_collection(self, reader: MemoryPackReader) -> list[dict[str, int]] | None:
        length = reader.read_collection_header()
        if length is None:
            return None
        return [{"tagId": reader.read_i32()} for _ in range(length)]

    def read_unmanaged_struct(self, reader: MemoryPackReader, path: str, type_name: str) -> dict[str, Any]:
        layout = UNMANAGED_STRUCT_LAYOUTS[type_name]
        start = reader.tell()
        raw = reader.read(layout["size"])
        result = {"$encoding": "unmanagedStruct"}
        for field in layout["fields"]:
            field_reader = MemoryPackReader(raw)
            field_reader.pos = field["offset"]
            result[field["name"]] = self.read_value(
                field_reader,
                field["type"],
                f"{path}.{field['name']}",
                type_name,
                field["name"],
            )
        if self.trace_enabled:
            self.trace.append(
                {
                    "path": path,
                    "type": type_name,
                    "encoding": "unmanagedStruct",
                    "start": start,
                    "startHex": f"0x{start:x}",
                    "end": reader.tell(),
                    "endHex": f"0x{reader.tell():x}",
                }
            )
        return result

    def read_collection(self, reader: MemoryPackReader, element_type: str, path: str) -> list[Any] | None:
        length = reader.read_collection_header()
        if length is None:
            return None
        return [
            self.read_value(reader, element_type, f"{path}[{index}]")
            for index in range(length)
        ]

    def read_dictionary(self, reader: MemoryPackReader, key_type: str, value_type: str, path: str) -> dict[str, Any] | None:
        length = reader.read_collection_header()
        if length is None:
            return None
        result: dict[str, Any] = {}
        for index in range(length):
            key = self.read_value(reader, key_type, f"{path}{{key:{index}}}")
            value = self.read_value(reader, value_type, f"{path}[{key!r}]")
            result[str(key)] = value
        return result

    def read_animation_curve(self, reader: MemoryPackReader, path: str) -> dict[str, Any] | None:
        start = reader.tell()
        member_count = reader.read_compact_header()
        if member_count is None:
            return None
        if member_count != 3:
            raise DecodeError(f"UnityEngine.AnimationCurve expected 3 members, got {member_count}", start, path)
        pre_wrap_mode = reader.read_i32()
        post_wrap_mode = reader.read_i32()
        key_count = reader.read_collection_header()
        keys = None
        if key_count is not None:
            keys = [
                {
                    "time": reader.read_f32(),
                    "value": reader.read_f32(),
                    "inTangent": reader.read_f32(),
                    "outTangent": reader.read_f32(),
                    "tangentMode": reader.read_i32(),
                    "weightedMode": reader.read_i32(),
                    "inWeight": reader.read_f32(),
                    "outWeight": 0.0,
                }
                for _ in range(key_count)
            ]
        return {
            "keys": keys,
            "preWrapMode": pre_wrap_mode,
            "postWrapMode": post_wrap_mode,
        }

    def read_unsupported_union(self, reader: MemoryPackReader, path: str, type_name: str) -> Any:
        start = reader.tell()
        tag = reader.read_compact_header()
        if tag is None:
            return None
        raise DecodeError(f"unsupported MemoryPack union {type_name} tag {tag}", start, path)

    def read_union(self, reader: MemoryPackReader, path: str, type_name: str) -> Any:
        start = reader.tell()
        tag = reader.read_compact_header()
        if tag is None:
            return None
        derived_type = self.union_map.get(type_name, {}).get(tag)
        if not derived_type:
            derived_type = self.reference_types.get(path)
        if not derived_type:
            raise DecodeError(f"unsupported MemoryPack union {type_name} tag {tag}", start, path)
        if not self.schema.get(derived_type):
            raise DecodeError(
                f"reference suggests union {type_name} tag {tag} is {derived_type}, but schema is missing",
                start,
                path,
            )
        self.discovered_unions.setdefault(type_name, {})[tag] = derived_type
        value = self.read_object(reader, derived_type, path)
        if isinstance(value, dict):
            value["$type"] = derived_type
            value["$tag"] = tag
        return value


def generic_arg(type_name: str) -> str:
    args = generic_args(type_name)
    if len(args) != 1:
        raise ValueError(f"expected one generic arg: {type_name}")
    return args[0]


def generic_args(type_name: str) -> list[str]:
    start = type_name.index("<") + 1
    end = type_name.rindex(">")
    content = type_name[start:end]
    result: list[str] = []
    depth = 0
    item_start = 0
    for index, char in enumerate(content):
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
        elif char == "," and depth == 0:
            result.append(content[item_start:index].strip())
            item_start = index + 1
    tail = content[item_start:].strip()
    if tail:
        result.append(tail)
    return result


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--id", type=int, help="file id in the VFS index database")
    source.add_argument("--binary", type=Path, help="already VFS-decrypted binary payload")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database generated by server.py")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA, help="schema generated by extract_memorypack_schema.py")
    parser.add_argument("--class", dest="class_name", help="root runtime class name")
    parser.add_argument("--reference-json", type=Path, help="decoded JSON reference file")
    parser.add_argument("--reference-url", help="decoded JSON reference URL")
    parser.add_argument("--union-map", type=Path, help="JSON map: {baseType: {tag: derivedType}}")
    parser.add_argument(
        "--union",
        action="append",
        default=[],
        help="Inline union mapping, e.g. Base.Type:132=Derived.Type. Can be repeated.",
    )
    parser.add_argument("--output", type=Path, help="write decoded JSON to this file")
    parser.add_argument("--trace", action="store_true", help="include decoded field offset trace in output metadata")
    return parser.parse_args(list(argv))


def read_vfs_payload(db_path: Path, file_id: int) -> VfsPayload:
    from server import decrypt_vfs_file

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        if row is None:
            raise SystemExit(f"file id not found: {file_id}")
        chunk_path = Path(row["chunk_path"])
        with chunk_path.open("rb") as file:
            file.seek(int(row["offset"]))
            data = file.read(int(row["length"]))
        if row["encrypted"]:
            data = decrypt_vfs_file(data, int(row["iv_seed"]))
        return VfsPayload(
            data=data,
            label=f"id {file_id}: {row['source_logical_id']}",
            logical_id=row["logical_id"],
        )
    finally:
        conn.close()


def infer_class(logical_id: str | None) -> str | None:
    if not logical_id:
        return None
    normalized = logical_id.replace("\\", "/")
    if "/SkillData/" in normalized:
        return "Beyond.Gameplay.Core.SkillData"
    if "/BuffData/" in normalized:
        return "Beyond.Gameplay.Core.BuffData"
    return None


def read_reference(args: argparse.Namespace) -> Any | None:
    if args.reference_json:
        return json.loads(args.reference_json.read_text(encoding="utf-8-sig"))
    if args.reference_url:
        request = Request(args.reference_url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8-sig"))
    return None


def build_reference_type_index(value: Any, path: str = "$") -> dict[str, str]:
    result: dict[str, str] = {}
    if isinstance(value, dict):
        raw_type = value.get("$type")
        if isinstance(raw_type, str):
            result[path] = normalize_reference_type(raw_type)
        for key, item in value.items():
            if key == "$type":
                continue
            result.update(build_reference_type_index(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            result.update(build_reference_type_index(item, f"{path}[{index}]"))
    return result


def normalize_reference_type(raw_type: str) -> str:
    type_name = raw_type.split(",", 1)[0].strip()
    return type_name.replace("+", ".")


def read_union_map(args: argparse.Namespace) -> dict[str, dict[int, str]]:
    result: dict[str, dict[int, str]] = {}
    if args.union_map:
        raw = json.loads(args.union_map.read_text(encoding="utf-8"))
        for base_type, entries in raw.items():
            result[base_type] = {int(tag): derived_type for tag, derived_type in entries.items()}
    for item in args.union:
        try:
            base_type, rest = item.split(":", 1)
            tag_text, derived_type = rest.split("=", 1)
        except ValueError as exc:
            raise SystemExit(f"invalid --union mapping: {item}") from exc
        result.setdefault(base_type, {})[int(tag_text, 0)] = derived_type
    return result


def summarize_reference_compare(decoded: Any, reference: Any | None) -> dict | None:
    if not isinstance(decoded, dict) or not isinstance(reference, dict):
        return None
    compared = []
    for key, value in decoded.items():
        if key.startswith("__") or key not in reference:
            continue
        ref_value = reference[key]
        compared.append(
            {
                "field": key,
                "decodedType": type(value).__name__,
                "referenceType": type(ref_value).__name__,
                "decodedPreview": preview_value(value),
                "referencePreview": preview_value(ref_value),
                "exact": value == ref_value,
            }
        )
    return {
        "fieldCount": len(compared),
        "exactCount": sum(1 for item in compared if item["exact"]),
        "fields": compared,
    }


def preview_value(value: Any) -> Any:
    if isinstance(value, list):
        return {"length": len(value), "first": preview_value(value[0]) if value else None}
    if isinstance(value, dict):
        keys = list(value)[:8]
        return {"keys": keys, "size": len(value)}
    return value


def main(argv: Iterable[str]) -> int:
    args = parse_args(argv)
    if args.binary:
        payload = VfsPayload(args.binary.read_bytes(), str(args.binary), None)
    else:
        payload = read_vfs_payload(args.db, args.id)

    class_name = args.class_name or infer_class(payload.logical_id)
    if not class_name:
        raise SystemExit("--class is required when the root class cannot be inferred from the VFS path")

    reader = MemoryPackReader(payload.data)
    reference = read_reference(args)
    decoder = Decoder(
        SchemaIndex.load(args.schema),
        read_union_map(args),
        build_reference_type_index(reference),
        trace_enabled=args.trace,
    )
    complete = True
    try:
        value = decoder.decode(reader, class_name)
        error = None
    except DecodeError as exc:
        complete = False
        value = exc.partial
        error = {
            "message": exc.message,
            "offset": exc.offset,
            "offsetHex": f"0x{exc.offset:x}",
            "path": exc.path,
        }

    output = {
        "__meta": {
            "label": payload.label,
            "class": class_name,
            "bytes": len(payload.data),
            "consumed": reader.tell(),
            "consumedHex": f"0x{reader.tell():x}",
            "complete": complete,
            "error": error,
            "discoveredUnions": {
                base_type: {str(tag): derived_type for tag, derived_type in sorted(entries.items())}
                for base_type, entries in sorted(decoder.discovered_unions.items())
            },
            "trace": decoder.trace[-500:] if args.trace else None,
            "referenceCompare": summarize_reference_compare(value, reference),
        },
        "value": value,
    }
    text = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
