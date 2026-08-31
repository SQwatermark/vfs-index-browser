"""管理进程级 ManifestIndex 缓存与持久化索引入口。"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from manifest_index import ManifestIndex


class ManifestIndexService:
    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = cache_dir
        self._indexes: dict[tuple[int, int, str], ManifestIndex] = {}
        self._lock = threading.Lock()

    def ensure(
        self,
        record: dict,
        payload_reader: Callable[[], bytes],
    ) -> ManifestIndex:
        content_md5 = str(record.get("file_data_md5") or "").casefold()
        key = (int(record["id"]), int(record["length"]), content_md5)
        with self._lock:
            cached = self._indexes.get(key)
            if cached is not None:
                return cached
            source_identity = (
                f"vfs-md5:{content_md5}:length:{int(record['length'])}"
                if content_md5
                else None
            )
            index = ManifestIndex.ensure_for_source(
                payload_reader,
                self._cache_dir,
                source_identity,
            )
            self._indexes[key] = index
            return index
