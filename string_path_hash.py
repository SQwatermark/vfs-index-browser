"""读取终末地运行时的 ``StringPathHash.bin`` 路径哈希表。"""

from __future__ import annotations

import mmap
import struct
from collections.abc import Iterable
from pathlib import Path


HEADER_SIZE = 8
SLOT_SIZE = 8
MAPPING_SIZE = 16


class StringPathHashIndex:
    """按需解析路径哈希，避免把约 110 MiB 的字符串区整体载入 Python 对象。"""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def resolve_many(self, hashes: Iterable[int]) -> dict[int, tuple[str, ...]]:
        requested = set(hashes)
        resolved: dict[int, list[str]] = {value: [] for value in requested}
        if not requested:
            return {}

        with self.path.open("rb") as source:
            with mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as data:
                string_base, count, records_offset = self._validate_layout(data)
                for index in range(count):
                    offset = records_offset + index * MAPPING_SIZE
                    path_hash, string_offset, padding = struct.unpack_from("<qii", data, offset)
                    if padding != 0:
                        raise ValueError(
                            f"映射记录 {index} 的保留字段不是 0：{padding}"
                        )
                    if path_hash not in requested:
                        continue
                    path = self._read_string(data, string_base, string_offset)
                    if path not in resolved[path_hash]:
                        resolved[path_hash].append(path)

        return {key: tuple(values) for key, values in resolved.items()}

    def resolve_one(self, path_hash: int) -> tuple[str, ...]:
        """返回同一哈希对应的全部路径；空元组表示哈希不存在。"""
        return self.resolve_many((path_hash,))[path_hash]

    @staticmethod
    def _validate_layout(data: mmap.mmap) -> tuple[int, int, int]:
        if len(data) < HEADER_SIZE:
            raise ValueError("StringPathHash 文件短于固定头部")

        string_base, count = struct.unpack_from("<II", data, 0)
        records_offset = HEADER_SIZE + count * SLOT_SIZE
        records_end = records_offset + count * MAPPING_SIZE
        if records_offset > len(data):
            raise ValueError("哈希槽区超出文件边界")
        if records_end != string_base:
            raise ValueError(
                "映射记录区未与字符串区相接："
                f"records_end=0x{records_end:x}, string_base=0x{string_base:x}"
            )
        if string_base > len(data):
            raise ValueError("字符串区起点超出文件边界")
        return string_base, count, records_offset

    @staticmethod
    def _read_string(data: mmap.mmap, string_base: int, offset: int) -> str:
        if offset < 0:
            raise ValueError(f"字符串偏移为负数：{offset}")
        start = string_base + offset
        if start + 4 > len(data):
            raise ValueError(f"字符串头超出文件边界：0x{start:x}")

        byte_count = struct.unpack_from("<i", data, start)[0]
        end = start + 4 + byte_count
        if byte_count < 0 or byte_count % 2 != 0 or end > len(data):
            raise ValueError(
                f"非法 UTF-16LE 字符串长度：offset=0x{start:x}, bytes={byte_count}"
            )
        return data[start + 4 : end].decode("utf-16-le")
