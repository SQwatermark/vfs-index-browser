"""读取 Endfield v29 metadata 的枚举证据；不依赖本机研究目录。"""

from __future__ import annotations

import hashlib
import re
import struct
from pathlib import Path


INTEGER_FORMATS = {
    "System.SByte": "b", "System.Byte": "B",
    "System.Int16": "h", "System.UInt16": "H",
    "System.Int32": "i", "System.UInt32": "I",
    "System.Int64": "q", "System.UInt64": "Q",
}


def compressed_u32(data: bytes, offset: int) -> int:
    first = data[offset]
    if first < 0x80:
        return first
    if first < 0xC0:
        return ((first & 0x3F) << 8) | data[offset + 1]
    if first < 0xE0:
        return ((first & 0x1F) << 24) | int.from_bytes(data[offset + 1:offset + 4], "big")
    if first == 0xF0:
        return struct.unpack_from("<I", data, offset + 1)[0]
    if first in (0xFE, 0xFF):
        return 0xFFFFFFFE + (first - 0xFE)
    raise ValueError(f"invalid compressed integer at {offset:#x}")


def integer_constant(data: bytes, offset: int, underlying: str) -> int:
    # v29 只有 I4/U4 使用压缩表示；宽度来自 value__，不能按相邻记录间距猜。
    if underlying in ("System.Int32", "System.UInt32"):
        value = compressed_u32(data, offset)
        if underlying == "System.UInt32":
            return value
        return -((value >> 1) + 1) if value & 1 else value >> 1
    return struct.unpack_from("<" + INTEGER_FORMATS[underlying], data, offset)[0]


class EnumMetadata:
    """只读取本工具需要的表；明确限定已验证的 v29 / 92 字节类型定义布局。"""

    def __init__(self, data: bytes):
        self.data = data
        if struct.unpack_from("<II", data) != (0xFAB11BAF, 29):
            raise ValueError("expected Endfield metadata v29")
        self.strings = self.section(2)
        self.types = list(self.records(19, "<17i8HII"))
        self.fields = list(self.records(11, "<iiI"))
        self.defaults = {}
        for field, type_index, index in self.records(7, "<iii"):
            if field in self.defaults:
                raise ValueError(f"duplicate default: field {field}")
            self.defaults[field] = (type_index, index)
        self.constants = self.section(8)
        self.parents = {}
        nested = [item[0] for item in self.records(15, "<i")]
        self.images = {}
        for image in self.records(20, "<10i"):
            for index in range(image[2], image[2] + image[3]):
                self.images[index] = self.string(image[0])
        for index, row in enumerate(self.types):
            for child in nested[row[12]:row[12] + row[21]] if row[21] else []:
                if child in self.parents:
                    raise ValueError(f"duplicate nested type: {child}")
                self.parents[child] = index

    def section(self, index: int) -> bytes:
        offset, size = struct.unpack_from("<ii", self.data, 8 + index * 8)
        if offset < 0 or size < 0 or offset + size > len(self.data):
            raise ValueError(f"invalid metadata section {index}")
        return self.data[offset:offset + size]

    def records(self, index: int, fmt: str):
        return struct.iter_unpack(fmt, self.section(index))

    def string(self, index: int) -> str:
        if not 0 <= index < len(self.strings):
            raise ValueError(f"invalid string index {index}")
        return self.strings[index:self.strings.index(b"\0", index)].decode("utf-8")

    def full_name(self, index: int) -> str:
        row = self.types[index]
        name = self.string(row[0])
        if index in self.parents:
            return self.full_name(self.parents[index]) + "." + name
        namespace = self.string(row[1])
        return f"{namespace}.{name}" if namespace else name


def read_dump_enums(root: Path) -> tuple[dict, dict]:
    result, hashes = {}, {}
    for path in sorted(root.glob("*.cs")):
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig")
        assembly = re.search(r"^// Assembly: (.+)$", text, re.M)
        if not assembly:
            continue
        hashes[path.name] = hashlib.sha256(raw).hexdigest()
        for block in text.split("CLASS: ")[1:]:
            block = block.split("END_CLASS", 1)[0]
            underlying = re.search(r"(System\.\w+)\s+value__\s+//", block)
            if not underlying:
                continue
            if underlying[1] not in INTEGER_FORMATS:
                raise ValueError(f"unsupported enum underlying type: {underlying[1]}")
            token = re.search(r"^TOKEN:\s*(0x[\da-fA-F]+)", block, re.M)
            if not token:
                raise ValueError(f"enum has no token: {path}")
            key = (assembly[1].strip(), int(token[1], 16))
            if key in result:
                raise ValueError(f"duplicate dump enum: {key}")
            result[key] = (block.splitlines()[0].strip(), underlying[1], block)
    return result, hashes


def build_catalog(metadata: EnumMetadata, dump_enums: dict) -> list[dict]:
    result = []
    for index, row in enumerate(metadata.types):
        if not row[25] & 2:
            continue
        name = metadata.full_name(index)
        assembly = metadata.images[index]
        key = (assembly, row[26])
        if key not in dump_enums:
            raise ValueError(f"enum missing from matching dump: {assembly}:{name}")
        short_name, underlying, block = dump_enums[key]
        if short_name.replace("+", ".") not in (metadata.string(row[0]), name):
            raise ValueError(f"metadata/dump type mismatch: {assembly}:{name}")
        members = []
        for field_index in range(row[8], row[8] + row[19]):
            field = metadata.fields[field_index]
            member = metadata.string(field[0])
            if member == "value__":
                continue
            # dump 的类型与字段名可能连在一起，按完整类型+精确成员名验证，不按短名匹配。
            native = re.escape(name).replace(r"\.", r"[.+]")
            if not re.search(native + r"\s*" + re.escape(member) + r"\s+(?:=.+)?// const", block):
                raise ValueError(f"metadata/dump member mismatch: {name}.{member}")
            default = metadata.defaults.get(field_index)
            if default is None or not 0 <= default[1] < len(metadata.constants):
                raise ValueError(f"missing enum constant: {name}.{member}")
            members.append({"name": member, "value": integer_constant(metadata.constants, default[1], underlying)})
        if len(re.findall(r"// const", block)) != len(members):
            raise ValueError(f"metadata/dump member count mismatch: {name}")
        result.append({"type": name, "assembly": assembly, "token": row[26],
                       "underlyingType": underlying, "members": members})
    return result
