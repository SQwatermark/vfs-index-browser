"""线程安全地加载并缓存 MemoryPack schema 与 union 映射。"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Callable


class MemoryPackSchemaService:
    def __init__(
        self,
        schema_path: Path,
        union_map_path: Path,
        schema_type: object,
        union_map_loader: Callable[[Path], dict],
    ) -> None:
        self._schema_path = schema_path
        self._union_map_path = union_map_path
        self._schema_type = schema_type
        self._load_union_map = union_map_loader
        self._schema: object | None = None
        self._union_map: dict | None = None
        self._load_error: str | None = None
        self._lock = threading.Lock()

    def load(self) -> tuple[object, dict]:
        with self._lock:
            if self._load_error is not None:
                raise RuntimeError(self._load_error)
            if self._schema_type is None:
                self._load_error = "MemoryPack decoder module is unavailable"
                raise RuntimeError(self._load_error)
            try:
                if self._schema is None:
                    self._schema = self._schema_type.load(self._schema_path)
                if self._union_map is None:
                    self._union_map = self._load_union_map(self._union_map_path)
            except (OSError, json.JSONDecodeError, ValueError) as error:
                self._load_error = str(error)
                raise RuntimeError(self._load_error) from error
            return self._schema, self._union_map
