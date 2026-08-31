"""Shared query options for synchronous model HTTP routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence
from urllib.parse import urlencode


TRUE_VALUES = frozenset({"1", "true", "yes"})


@dataclass(frozen=True)
class ModelSyncRequest:
    lod: int
    download: bool
    prepare: bool

    @classmethod
    def parse(cls, query: Mapping[str, Sequence[str]]) -> "ModelSyncRequest":
        return cls(
            lod=int(cls._first(query, "lod", "0")),
            download=cls._first(query, "download", "0") in TRUE_VALUES,
            prepare=cls._first(query, "prepare", "0") in TRUE_VALUES,
        )

    @staticmethod
    def download_url(
        path: str,
        query: Mapping[str, Sequence[str]],
    ) -> str:
        download_query = {
            key: list(values)
            for key, values in query.items()
            if key != "prepare"
        }
        encoded = urlencode(download_query, doseq=True)
        return f"{path}?{encoded}" if encoded else path

    @staticmethod
    def _first(
        query: Mapping[str, Sequence[str]],
        key: str,
        default: str,
    ) -> str:
        values = query.get(key, ())
        return str(values[0]) if values else default
