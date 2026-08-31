"""Format decoded MemoryPack values for the ordinary file-preview contract."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from file_preview_service import truncate_text
from memorypack_value_decoder import (
    DecodedMemoryPackValue,
    MemoryPackValueDecodeError,
)


@dataclass(frozen=True)
class MemoryPackJsonExport:
    data: bytes
    meta: dict


class MemoryPackPreviewService:
    def __init__(
        self,
        decode: Callable[[str | None, dict, Path], DecodedMemoryPackValue | None],
    ) -> None:
        self._decode = decode

    def build(
        self,
        record: dict,
        chunk_path: Path,
    ) -> tuple[str, bool, dict] | None:
        exported = self.export(record, chunk_path)
        if exported is None:
            return None
        text, truncated = truncate_text(exported.data.decode("utf-8"))
        return text, truncated, exported.meta

    def export(
        self,
        record: dict,
        chunk_path: Path,
    ) -> MemoryPackJsonExport | None:
        try:
            decoded = self._decode(record.get("logical_id"), record, chunk_path)
        except MemoryPackValueDecodeError as error:
            raise RuntimeError(str(error)) from error
        if decoded is None:
            return None

        discovered_unions = {
            base_type: {
                str(tag): derived_type
                for tag, derived_type in sorted(entries.items())
            }
            for base_type, entries in sorted(decoded.discovered_unions.items())
        }
        meta = {
            "class": decoded.class_name,
            "bytes": decoded.byte_count,
            "consumed": decoded.consumed,
            "complete": decoded.complete,
            "discoveredUnions": discovered_unions,
        }
        data = json.dumps(
            {"__meta": meta, "value": decoded.value},
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        return MemoryPackJsonExport(data, meta)
