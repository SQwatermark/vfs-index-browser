"""Read metadata needed to audit an Endfield InjectFix patch asset."""

from __future__ import annotations

import struct
from dataclasses import dataclass


BRIDGE_PREFIX = "IFix.ILFixInterfaceBridge, "


@dataclass(frozen=True)
class PatchedMethod:
    declaring_type: str
    name: str
    parameters: tuple[str, ...]
    generic_arguments: tuple[str, ...]
    vm_method_id: int

    @property
    def qualified_name(self) -> str:
        return f"{self.declaring_type}.{self.name}"


@dataclass(frozen=True)
class InjectFixPatch:
    payload_offset: int
    magic: int
    interface_bridge: str
    assembly: str
    vm_method_count: int
    patched_methods: tuple[PatchedMethod, ...]


class InjectFixPatchError(ValueError):
    """The asset is not a supported InjectFix patch stream."""


class _Reader:
    def __init__(self, data: bytes, offset: int = 0):
        self.data = data
        self.offset = offset

    def _unpack(self, fmt: str):
        size = struct.calcsize(fmt)
        if self.offset + size > len(self.data):
            raise InjectFixPatchError(
                f"read at offset {self.offset} exceeds {len(self.data)} bytes"
            )
        value = struct.unpack_from(fmt, self.data, self.offset)[0]
        self.offset += size
        return value

    def boolean(self) -> bool:
        return self._unpack("<?")

    def int32(self) -> int:
        return self._unpack("<i")

    def uint64(self) -> int:
        return self._unpack("<Q")

    def string(self) -> str:
        length = 0
        shift = 0
        while True:
            byte = self._unpack("<B")
            length |= (byte & 0x7F) << shift
            if byte & 0x80 == 0:
                break
            shift += 7
            if shift >= 35:
                raise InjectFixPatchError("invalid .NET 7-bit string length")
        end = self.offset + length
        if end > len(self.data):
            raise InjectFixPatchError(
                f"string at offset {self.offset} exceeds {len(self.data)} bytes"
            )
        try:
            result = self.data[self.offset:end].decode("utf-8")
        except UnicodeDecodeError as error:
            raise InjectFixPatchError(
                f"invalid UTF-8 string at offset {self.offset}"
            ) from error
        self.offset = end
        return result


def _short_type(type_name: str) -> str:
    return type_name.split(",", 1)[0]


def _read_method(reader: _Reader, extern_types: list[str]):
    generic_instance = reader.boolean()
    declaring_type = _short_type(extern_types[reader.int32()])
    name = reader.string()
    generic_arguments: list[str] = []
    parameters: list[str] = []
    if generic_instance:
        generic_arguments = [
            _short_type(extern_types[reader.int32()])
            for _ in range(reader.int32())
        ]
        for _ in range(reader.int32()):
            parameters.append(
                reader.string()
                if reader.boolean()
                else _short_type(extern_types[reader.int32()])
            )
    else:
        parameters = [
            _short_type(extern_types[reader.int32()])
            for _ in range(reader.int32())
        ]
    return declaring_type, name, tuple(parameters), tuple(generic_arguments)


def _find_payload_offset(data: bytes) -> int:
    bridge_offset = data.find(BRIDGE_PREFIX.encode("utf-8"))
    if bridge_offset < 9:
        raise InjectFixPatchError("InjectFix interface bridge marker was not found")

    for prefix_size in range(1, 6):
        payload_offset = bridge_offset - prefix_size - 8
        if payload_offset < 0:
            continue
        candidate = _Reader(data, payload_offset)
        candidate.uint64()
        try:
            if candidate.string().startswith(BRIDGE_PREFIX):
                return payload_offset
        except InjectFixPatchError:
            pass
    raise InjectFixPatchError("InjectFix payload boundary was not found")


def parse_injectfix_patch(data: bytes) -> InjectFixPatch:
    """Parse method membership without loading game assemblies or executing code."""
    payload_offset = _find_payload_offset(data)
    reader = _Reader(data, payload_offset)
    magic = reader.uint64()
    interface_bridge = reader.string()
    extern_types = [reader.string() for _ in range(reader.int32())]

    vm_method_count = reader.int32()
    for _ in range(vm_method_count):
        for _ in range(reader.int32()):
            reader.int32()
            reader.int32()
        for _ in range(reader.int32()):
            for _ in range(6):
                reader.int32()

    for _ in range(reader.int32()):
        _read_method(reader, extern_types)
    for _ in range(reader.int32()):
        reader.string()

    for _ in range(reader.int32()):
        is_new_field = reader.boolean()
        reader.int32()
        reader.string()
        if is_new_field:
            reader.int32()
            reader.int32()

    for _ in range(reader.int32()):
        reader.int32()
        reader.int32()

    anonymous_storey_count = reader.int32()
    if anonymous_storey_count:
        raise InjectFixPatchError(
            "anonymous-storey slot metadata is not yet supported; "
            f"found {anonymous_storey_count} entries"
        )

    reader.string()
    assembly = reader.string()
    patched_methods = []
    for _ in range(reader.int32()):
        declaring_type, name, parameters, generic_arguments = _read_method(
            reader, extern_types
        )
        patched_methods.append(
            PatchedMethod(
                declaring_type=declaring_type,
                name=name,
                parameters=parameters,
                generic_arguments=generic_arguments,
                vm_method_id=reader.int32(),
            )
        )

    return InjectFixPatch(
        payload_offset=payload_offset,
        magic=magic,
        interface_bridge=interface_bridge,
        assembly=assembly,
        vm_method_count=vm_method_count,
        patched_methods=tuple(patched_methods),
    )
