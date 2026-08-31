"""缓存并发布运行时 StringPathHash 路径表。"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Callable


class StringPathHashFileService:
    def __init__(
        self,
        source_resolver: Callable[[str], tuple[dict, Path] | None],
        file_writer: Callable[[dict, Path, Path], None],
        lock: object,
        cache_root: Path,
        *,
        logical_id: str,
        cache_version: int,
    ) -> None:
        self._resolve_source = source_resolver
        self._write_file = file_writer
        self._lock = lock
        self._cache_root = cache_root
        self._logical_id = logical_id
        self._cache_version = cache_version

    def ensure(self) -> tuple[Path, dict]:
        resolved = self._resolve_source(self._logical_id)
        if resolved is None:
            raise FileNotFoundError(
                f"local VFS file is unavailable: {self._logical_id}"
            )
        record, chunk_path = resolved
        root = self._cache_root / "shared" / "string-path-hash"
        target = root / "StringPathHash.bin"
        meta_path = root / "meta.json"
        identity = self._source_identity(record, chunk_path)

        with self._lock:
            cached_meta = self._load_meta(meta_path)
            if (
                target.is_file()
                and cached_meta is not None
                and cached_meta.get("version") == self._cache_version
                and cached_meta.get("source") == identity
                and target.stat().st_size == int(record["length"])
            ):
                return target, cached_meta

            root.mkdir(parents=True, exist_ok=True)
            token = uuid.uuid4().hex
            candidate = root / f".{target.name}.{token}.tmp"
            candidate_meta = root / f".{meta_path.name}.{token}.tmp"
            meta = {
                "version": self._cache_version,
                "source": identity,
                "builtAtEpoch": int(time.time()),
            }
            try:
                self._write_file(record, chunk_path, candidate)
                if candidate.stat().st_size != int(record["length"]):
                    raise OSError("materialized StringPathHash length is inconsistent")
                candidate_meta.write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                os.replace(candidate, target)
                os.replace(candidate_meta, meta_path)
            finally:
                candidate.unlink(missing_ok=True)
                candidate_meta.unlink(missing_ok=True)
        return target, meta

    @staticmethod
    def _source_identity(record: dict, chunk_path: Path) -> dict:
        return {
            "recordId": int(record["id"]),
            "length": int(record["length"]),
            "offset": int(record["offset"]),
            "chunkPath": str(record["chunk_path"]),
            "chunkMtimeNs": chunk_path.stat().st_mtime_ns,
            "fileDataMd5": str(record.get("file_data_md5") or ""),
        }

    @staticmethod
    def _load_meta(path: Path) -> dict | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return None
