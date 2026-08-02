"""Minimal, strict DXBC container inspection for Shader identity recovery."""

from __future__ import annotations

import struct
from dataclasses import dataclass


STAGE_NAMES = {
    0: "pixel",
    1: "vertex",
    2: "geometry",
    3: "hull",
    4: "domain",
    5: "compute",
}


class DxbcError(ValueError):
    """Raised when a DXBC file violates the container or bytecode contract."""


@dataclass(frozen=True)
class DxbcChunk:
    fourcc: str
    offset: int
    payload: bytes


@dataclass(frozen=True)
class DxbcInspection:
    stage: str
    shader_model: str
    chunks: tuple[DxbcChunk, ...]


def inspect_dxbc(data: bytes) -> DxbcInspection:
    """Return stage and chunk evidence without decompiling shader instructions."""

    if len(data) < 32 or data[:4] != b"DXBC":
        raise DxbcError("data does not begin with a complete DXBC header")
    total_size, chunk_count = struct.unpack_from("<II", data, 24)
    if total_size != len(data):
        raise DxbcError(
            f"DXBC declares {total_size} bytes but contains {len(data)} bytes"
        )
    table_end = 32 + chunk_count * 4
    if table_end > len(data):
        raise DxbcError("DXBC chunk offset table exceeds the container")

    chunks = []
    occupied_ranges = []
    for index in range(chunk_count):
        offset = struct.unpack_from("<I", data, 32 + index * 4)[0]
        if offset < table_end or offset + 8 > len(data):
            raise DxbcError(f"DXBC chunk {index} has invalid offset {offset}")
        fourcc_bytes = data[offset : offset + 4]
        try:
            fourcc = fourcc_bytes.decode("ascii")
        except UnicodeDecodeError as error:
            raise DxbcError(f"DXBC chunk {index} has a non-ASCII FourCC") from error
        size = struct.unpack_from("<I", data, offset + 4)[0]
        end = offset + 8 + size
        if end > len(data):
            raise DxbcError(f"DXBC chunk {fourcc} exceeds the container")
        overlaps = any(
            offset < other_end and other_start < end
            for other_start, other_end in occupied_ranges
        )
        if overlaps:
            raise DxbcError(f"DXBC chunk {fourcc} overlaps another chunk")
        occupied_ranges.append((offset, end))
        chunks.append(
            DxbcChunk(
                fourcc=fourcc,
                offset=offset,
                payload=data[offset + 8 : end],
            )
        )

    bytecode_chunks = [chunk for chunk in chunks if chunk.fourcc in {"SHDR", "SHEX"}]
    if len(bytecode_chunks) != 1:
        raise DxbcError(
            f"expected exactly one SHDR/SHEX chunk, found {len(bytecode_chunks)}"
        )
    bytecode = bytecode_chunks[0].payload
    if len(bytecode) < 8 or len(bytecode) % 4:
        raise DxbcError("SHDR/SHEX bytecode is not a complete DWORD stream")
    version, declared_dwords = struct.unpack_from("<II", bytecode)
    if declared_dwords * 4 != len(bytecode):
        raise DxbcError(
            "SHDR/SHEX token count does not match the bytecode chunk length"
        )
    stage_code = (version >> 16) & 0xFFFF
    stage = STAGE_NAMES.get(stage_code)
    if stage is None:
        raise DxbcError(f"unsupported D3D11 shader stage {stage_code}")
    major = (version >> 4) & 0xF
    minor = version & 0xF
    return DxbcInspection(
        stage=stage,
        shader_model=f"{major}_{minor}",
        chunks=tuple(chunks),
    )
