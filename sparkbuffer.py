from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from float32_value import canonical_float32


class SparkBufferError(Exception):
    pass


class SparkType(IntEnum):
    BOOL = 0
    BYTE = 1
    INT = 2
    LONG = 3
    FLOAT = 4
    DOUBLE = 5
    ENUM = 6
    STRING = 7
    BEAN = 8
    ARRAY = 9
    MAP = 10


@dataclass(frozen=True)
class EnumType:
    type_hash: int
    name: str
    values: dict[int, str]

    def name_for_value(self, value: int) -> str | int:
        return self.values.get(value, value)


@dataclass(frozen=True)
class BeanField:
    name: str
    field_type: SparkType
    type2: SparkType | None = None
    type3: SparkType | None = None
    type_hash: int | None = None
    type_hash2: int | None = None


@dataclass(frozen=True)
class BeanType:
    type_hash: int
    name: str
    fields: list[BeanField]


@dataclass(frozen=True)
class RootDef:
    field_type: SparkType
    name: str
    type_hash: int | None = None
    type2: SparkType | None = None
    type3: SparkType | None = None
    type_hash2: int | None = None


@dataclass(frozen=True)
class SparkBufferSchema:
    """SparkBuffer 文件自带的名义类型 schema 与数据区位置。"""

    root: RootDef
    registry: TypeRegistry
    data_offset: int


class TypeRegistry:
    def __init__(self) -> None:
        self.beans: dict[int, BeanType] = {}
        self.enums: dict[int, EnumType] = {}

    def add_bean(self, bean: BeanType) -> None:
        self.beans[bean.type_hash] = bean

    def add_enum(self, enum_type: EnumType) -> None:
        self.enums[enum_type.type_hash] = enum_type

    def bean(self, type_hash: int) -> BeanType:
        try:
            return self.beans[type_hash]
        except KeyError as error:
            raise SparkBufferError(f"unknown bean type hash: 0x{type_hash:08x}") from error

    def enum(self, type_hash: int) -> EnumType:
        try:
            return self.enums[type_hash]
        except KeyError as error:
            raise SparkBufferError(f"unknown enum type hash: 0x{type_hash:08x}") from error


class SparkReader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def _read(self, size: int) -> bytes:
        if size < 0 or self.pos + size > len(self.data):
            raise SparkBufferError("unexpected end of SparkBuffer data")
        result = self.data[self.pos : self.pos + size]
        self.pos += size
        return result

    def read_int32(self) -> int:
        return struct.unpack("<i", self._read(4))[0]

    def read_int64(self) -> int:
        return struct.unpack("<q", self._read(8))[0]

    def read_float(self) -> float:
        return canonical_float32(struct.unpack("<f", self._read(4))[0])

    def read_double(self) -> float:
        return struct.unpack("<d", self._read(8))[0]

    def read_bool(self) -> bool:
        return self._read(1)[0] != 0

    def read_type(self) -> SparkType:
        value = self._read(1)[0]
        try:
            return SparkType(value)
        except ValueError as error:
            raise SparkBufferError(f"invalid SparkBuffer type: {value}") from error

    def read_count(self, name: str) -> int:
        count = self.read_int32()
        if count < 0:
            raise SparkBufferError(f"invalid {name}: {count}")
        return count

    def read_null_terminated_string(self) -> str:
        end = self.data.find(b"\x00", self.pos)
        if end < 0:
            raise SparkBufferError("unterminated SparkBuffer string")
        raw = self.data[self.pos : end]
        self.pos = end + 1
        return raw.decode("utf-8", errors="replace")

    def read_string_at_offset(self) -> str:
        offset = self.read_int32()
        if offset == -1:
            return ""
        old_pos = self.pos
        self.seek(offset)
        value = self.read_null_terminated_string()
        self.seek(old_pos)
        return value

    def read_aligned_int64(self) -> int:
        self.align(8)
        return self.read_int64()

    def read_aligned_double(self) -> float:
        self.align(8)
        return self.read_double()

    def seek(self, offset: int) -> None:
        if offset < 0 or offset > len(self.data):
            raise SparkBufferError(f"invalid SparkBuffer offset: {offset}")
        self.pos = offset

    def skip(self, size: int) -> None:
        self.seek(self.pos + size)

    def align(self, alignment: int) -> None:
        pos_minus_one = self.pos - 1
        aligned = pos_minus_one + (alignment - (pos_minus_one % alignment))
        self.seek(aligned)


def parse_sparkbuffer(data: bytes) -> dict[str, Any]:
    schema = parse_sparkbuffer_schema(data)
    reader = SparkReader(data)
    reader.seek(schema.data_offset)
    root_def = schema.root
    registry = schema.registry
    if root_def.field_type == SparkType.BEAN:
        if root_def.type_hash is None:
            raise SparkBufferError("root bean missing type hash")
        value = _read_bean_value(reader, registry.bean(root_def.type_hash), registry, is_pointer=False)
    elif root_def.field_type == SparkType.MAP:
        value = _read_root_map_value(reader, root_def, registry)
    else:
        raise SparkBufferError(f"unsupported root type: {root_def.field_type.name}")

    return {"name": root_def.name, "data": value}


def parse_sparkbuffer_schema(data: bytes) -> SparkBufferSchema:
    """只读取类型注册表和根定义，不解析可能很大的数据区。"""

    reader = SparkReader(data)
    type_def_offset = reader.read_int32()
    root_def_offset = reader.read_int32()
    data_offset = reader.read_int32()

    registry = TypeRegistry()
    reader.seek(type_def_offset)
    _parse_type_definitions(reader, registry)
    reader.seek(root_def_offset)
    root_def = _parse_root_def(reader)
    return SparkBufferSchema(root_def, registry, data_offset)


def _is_enum_or_bean(value: SparkType) -> bool:
    return value in {SparkType.ENUM, SparkType.BEAN}


def _parse_type_definitions(reader: SparkReader, registry: TypeRegistry) -> None:
    count = reader.read_count("type definition count")
    for _ in range(count):
        spark_type = reader.read_type()
        reader.align(4)
        if spark_type == SparkType.ENUM:
            type_hash = reader.read_int32()
            name = reader.read_null_terminated_string()
            reader.align(4)
            enum_count = reader.read_count("enum item count")
            values: dict[int, str] = {}
            for _ in range(enum_count):
                item_name = reader.read_null_terminated_string()
                reader.align(4)
                values[reader.read_int32()] = item_name
            registry.add_enum(EnumType(type_hash, name, values))
            continue

        if spark_type == SparkType.BEAN:
            type_hash = reader.read_int32()
            name = reader.read_null_terminated_string()
            reader.align(4)
            field_count = reader.read_count("bean field count")
            fields = [_parse_bean_field(reader) for _ in range(field_count)]
            registry.add_bean(BeanType(type_hash, name, fields))
            continue

        raise SparkBufferError(f"invalid type definition type: {spark_type.name}")


def _parse_bean_field(reader: SparkReader) -> BeanField:
    name = reader.read_null_terminated_string()
    field_type = reader.read_type()
    type2 = None
    type3 = None
    type_hash = None
    type_hash2 = None

    if field_type in {
        SparkType.BOOL,
        SparkType.BYTE,
        SparkType.INT,
        SparkType.LONG,
        SparkType.FLOAT,
        SparkType.DOUBLE,
        SparkType.STRING,
    }:
        return BeanField(name, field_type)

    if field_type in {SparkType.ENUM, SparkType.BEAN}:
        reader.align(4)
        type_hash = reader.read_int32()
        return BeanField(name, field_type, type_hash=type_hash)

    if field_type == SparkType.ARRAY:
        type2 = reader.read_type()
        if _is_enum_or_bean(type2):
            reader.align(4)
            type_hash = reader.read_int32()
        return BeanField(name, field_type, type2=type2, type_hash=type_hash)

    if field_type == SparkType.MAP:
        type2 = reader.read_type()
        type3 = reader.read_type()
        if _is_enum_or_bean(type2):
            reader.align(4)
            type_hash = reader.read_int32()
        if _is_enum_or_bean(type3):
            reader.align(4)
            type_hash2 = reader.read_int32()
        return BeanField(name, field_type, type2=type2, type3=type3, type_hash=type_hash, type_hash2=type_hash2)

    raise SparkBufferError(f"unsupported field type in definition: {field_type.name}")


def _parse_root_def(reader: SparkReader) -> RootDef:
    field_type = reader.read_type()
    name = reader.read_null_terminated_string()
    type_hash = None
    type2 = None
    type3 = None
    type_hash2 = None

    if _is_enum_or_bean(field_type):
        reader.align(4)
        type_hash = reader.read_int32()

    if field_type == SparkType.MAP:
        type2 = reader.read_type()
        type3 = reader.read_type()
        if _is_enum_or_bean(type2):
            reader.align(4)
            type_hash = reader.read_int32()
        if _is_enum_or_bean(type3):
            reader.align(4)
            type_hash2 = reader.read_int32()

    return RootDef(field_type, name, type_hash, type2, type3, type_hash2)


def _sorted_object(values: dict[str, Any]) -> dict[str, Any]:
    return {key: values[key] for key in sorted(values)}


def _float_value(value: float) -> float | None:
    return value if math.isfinite(value) else None


def _read_bean_value(reader: SparkReader, bean: BeanType, registry: TypeRegistry, is_pointer: bool) -> Any:
    pointer_origin = None
    if is_pointer:
        bean_offset = reader.read_int32()
        if bean_offset == -1:
            return None
        pointer_origin = reader.pos
        reader.seek(bean_offset)

    values: dict[str, Any] = {}
    for index, field in enumerate(bean.fields):
        origin = None
        if field.field_type == SparkType.ARRAY:
            field_offset = reader.read_int32()
            if field_offset == -1:
                values[field.name] = None
                continue
            origin = reader.pos
            reader.seek(field_offset)

        values[field.name] = _read_field_value(reader, bean, index, field, registry)

        if origin is not None:
            reader.seek(origin)

    if pointer_origin is not None:
        reader.seek(pointer_origin)

    return _sorted_object(values)


def _read_field_value(
    reader: SparkReader,
    bean: BeanType,
    field_index: int,
    field: BeanField,
    registry: TypeRegistry,
) -> Any:
    field_type = field.field_type
    if field_type == SparkType.ARRAY:
        return _read_array_value(reader, field, registry)
    if field_type == SparkType.INT:
        return reader.read_int32()
    if field_type == SparkType.ENUM:
        return reader.read_int32()
    if field_type == SparkType.LONG:
        return reader.read_aligned_int64()
    if field_type == SparkType.FLOAT:
        return _float_value(reader.read_float())
    if field_type == SparkType.DOUBLE:
        return _float_value(reader.read_aligned_double())
    if field_type == SparkType.STRING:
        return reader.read_string_at_offset()
    if field_type == SparkType.BEAN:
        if field.type_hash is None:
            raise SparkBufferError(f"field {field.name} missing type hash")
        return _read_bean_value(reader, registry.bean(field.type_hash), registry, is_pointer=True)
    if field_type == SparkType.BOOL:
        return _read_bean_bool_value(reader, bean, field_index)
    if field_type == SparkType.MAP:
        return _read_nested_map_value(reader, field, registry)
    if field_type == SparkType.BYTE:
        raise SparkBufferError("unsupported field type: BYTE")
    raise SparkBufferError(f"unsupported field type: {field_type.name}")


def _read_array_value(reader: SparkReader, field: BeanField, registry: TypeRegistry) -> list[Any]:
    count = reader.read_count("array item count")
    item_type = field.type2
    if item_type is None:
        raise SparkBufferError("array field missing item type")

    return [_read_array_item(reader, item_type, field, registry) for _ in range(count)]


def _read_array_item(reader: SparkReader, item_type: SparkType, field: BeanField, registry: TypeRegistry) -> Any:
    if item_type == SparkType.STRING:
        return reader.read_string_at_offset()
    if item_type == SparkType.BEAN:
        if field.type_hash is None:
            raise SparkBufferError(f"field {field.name} missing type hash")
        return _read_bean_value(reader, registry.bean(field.type_hash), registry, is_pointer=True)
    if item_type == SparkType.FLOAT:
        return _float_value(reader.read_float())
    if item_type == SparkType.LONG:
        return reader.read_aligned_int64()
    if item_type == SparkType.INT:
        return reader.read_int32()
    if item_type == SparkType.ENUM:
        return reader.read_int32()
    if item_type == SparkType.BOOL:
        return reader.read_bool()
    if item_type == SparkType.DOUBLE:
        return _float_value(reader.read_aligned_double())
    raise SparkBufferError(f"unsupported array item type: {item_type.name}")


def _read_bean_bool_value(reader: SparkReader, bean: BeanType, field_index: int) -> bool:
    value = reader.read_bool()
    if field_index + 1 < len(bean.fields) and bean.fields[field_index + 1].field_type != SparkType.BOOL:
        reader.align(4)
    return value


def _read_nested_map_value(reader: SparkReader, field: BeanField, registry: TypeRegistry) -> dict[str, Any]:
    map_offset = reader.read_int32()
    origin = reader.pos
    reader.seek(map_offset)
    value = _read_map_value(reader, field, registry)
    reader.seek(origin)
    return value


def _read_map_value(reader: SparkReader, field: BeanField, registry: TypeRegistry) -> dict[str, Any]:
    count = reader.read_count("map item count")
    reader.skip(count * 8)
    key_type = field.type2
    value_type = field.type3
    if key_type is None or value_type is None:
        raise SparkBufferError("map field missing key or value type")

    values: dict[str, Any] = {}
    for _ in range(count):
        key = _read_map_key(reader, key_type)
        values[key] = _read_map_item(reader, value_type, field.type_hash2, registry)
    return _sorted_object(values)


def _read_root_map_value(reader: SparkReader, root: RootDef, registry: TypeRegistry) -> dict[str, Any]:
    count = reader.read_count("root map item count")
    reader.skip(count * 8)
    if root.type2 is None or root.type3 is None:
        raise SparkBufferError("root map missing key or value type")

    values: dict[str, Any] = {}
    for _ in range(count):
        key = _read_map_key(reader, root.type2)
        values[key] = _read_map_item(reader, root.type3, root.type_hash2, registry)
    return _sorted_object(values)


def _read_map_key(reader: SparkReader, key_type: SparkType) -> str:
    if key_type == SparkType.STRING:
        return reader.read_string_at_offset()
    if key_type == SparkType.INT:
        return str(reader.read_int32())
    if key_type == SparkType.LONG:
        return str(reader.read_aligned_int64())
    raise SparkBufferError(f"unsupported map key type: {key_type.name}")


def _read_map_item(
    reader: SparkReader,
    value_type: SparkType,
    type_hash: int | None,
    registry: TypeRegistry,
) -> Any:
    if value_type == SparkType.BEAN:
        if type_hash is None:
            raise SparkBufferError("map bean value missing type hash")
        return _read_bean_value(reader, registry.bean(type_hash), registry, is_pointer=True)
    if value_type == SparkType.STRING:
        return reader.read_string_at_offset()
    if value_type == SparkType.INT:
        return reader.read_int32()
    if value_type == SparkType.FLOAT:
        return _float_value(reader.read_float())
    if value_type == SparkType.ENUM:
        if type_hash is None:
            raise SparkBufferError("map enum value missing type hash")
        return registry.enum(type_hash).name_for_value(reader.read_int32())
    if value_type == SparkType.BOOL:
        value = reader.read_bool()
        reader.align(4)
        return value
    raise SparkBufferError(f"unsupported map value type: {value_type.name}")
