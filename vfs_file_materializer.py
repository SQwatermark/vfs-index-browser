"""将一个 VFS 文件记录安全物化为本地文件。"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Callable


class VfsFileMaterializer:
    def __init__(
        self,
        decrypted_reader: Callable[[dict, Path], bytes],
        *,
        stream_chunk_size: int,
    ) -> None:
        self._read_decrypted = decrypted_reader
        self._chunk_size = stream_chunk_size

    def write(self, record: dict, chunk_path: Path, target: Path) -> None:
        expected_size = int(record["length"])
        if target.is_file() and target.stat().st_size == expected_size:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        candidate = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
        try:
            if record.get("encrypted"):
                candidate.write_bytes(self._read_decrypted(record, chunk_path))
            else:
                self._copy_plain_slice(record, chunk_path, candidate)
            actual_size = candidate.stat().st_size
            if actual_size != expected_size:
                raise OSError(
                    "materialized VFS file length is inconsistent: "
                    f"expected {expected_size}, got {actual_size}"
                )
            os.replace(candidate, target)
        finally:
            candidate.unlink(missing_ok=True)

    def _copy_plain_slice(
        self,
        record: dict,
        chunk_path: Path,
        candidate: Path,
    ) -> None:
        remaining = int(record["length"])
        with chunk_path.open("rb") as source, candidate.open("wb") as output:
            source.seek(int(record["offset"]))
            while remaining > 0:
                data = source.read(min(self._chunk_size, remaining))
                if not data:
                    break
                output.write(data)
                remaining -= len(data)
