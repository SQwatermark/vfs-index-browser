"""Format decoded MemoryPack values for the ordinary file-preview contract."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from file_preview_service import truncate_text
from memorypack_value_decoder import (
    DecodedMemoryPackValue,
    MemoryPackValueDecodeError,
)


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
        text, truncated = truncate_text(
            json.dumps(
                {"__meta": meta, "value": decoded.value},
                ensure_ascii=False,
                indent=2,
            )
        )
        return text, truncated, meta
