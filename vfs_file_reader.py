"""按 VFS 记录读取、解密和校验文件范围。"""

from __future__ import annotations

from pathlib import Path
from typing import Callable


class VfsFileReader:
    def __init__(self, decryptor: Callable[[bytes, int], bytes]) -> None:
        self._decrypt = decryptor

    def read(
        self,
        record: dict,
        chunk_path: Path,
        limit: int | None = None,
    ) -> bytes:
        length = int(record["length"])
        if limit is not None:
            length = min(length, limit)
        with chunk_path.open("rb") as source:
            source.seek(int(record["offset"]))
            data = source.read(length)
        if record.get("encrypted"):
            data = self._decrypt(data, int(record["iv_seed"]))
        return data

    def read_range(
        self,
        record: dict,
        chunk_path: Path,
        relative_offset: int,
        length: int,
    ) -> bytes:
        file_length = int(record["length"])
        if (
            relative_offset < 0
            or length < 0
            or relative_offset + length > file_length
        ):
            raise ValueError("file range is outside the VFS record")
        if record.get("encrypted"):
            return self.read(record, chunk_path)[
                relative_offset : relative_offset + length
            ]
        with chunk_path.open("rb") as source:
            source.seek(int(record["offset"]) + relative_offset)
            return source.read(length)
