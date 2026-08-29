"""Resolve immutable model geometry and texture artifacts without HTTP coupling."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import unquote

from model_run_store import resolve_published_model_run


class ModelArtifactRunNotFound(FileNotFoundError):
    pass


@dataclass(frozen=True)
class ModelArtifactReference:
    record_id: int
    asset_index: int
    lod: int | None
    run: str

    @classmethod
    def parse(cls, query: Mapping[str, list[str]]) -> "ModelArtifactReference":
        try:
            record_id = int(query.get("recordId", [""])[0])
            asset_index = int(query.get("assetIndex", [""])[0])
            raw_lod = query.get("lod", [None])[0]
            lod = int(raw_lod) if raw_lod is not None else None
            if record_id < 0 or asset_index < 0:
                raise ValueError
            if lod is not None and lod not in range(4):
                raise ValueError
        except (IndexError, TypeError, ValueError) as error:
            raise ValueError("recordId, assetIndex or lod is invalid") from error
        return cls(record_id, asset_index, lod, query.get("run", [""])[0])


class ModelArtifactResolver:
    def __init__(self, run_store: object) -> None:
        self._runs = run_store

    def geometry(self, reference: ModelArtifactReference) -> Path | None:
        root = self._published_root(reference)
        target = root / "geometry.bin" if root is not None else None
        return target if target is not None and target.is_file() else None

    def texture(
        self, reference: ModelArtifactReference, raw_path: str
    ) -> Path | None:
        published = self._published_root(reference)
        if published is None:
            raise ModelArtifactRunNotFound("model texture run not found")
        root = (published / "textures").resolve()
        relative = unquote(raw_path).replace("\\", "/").strip("/")
        target = (root / relative).resolve()
        if not relative or root not in target.parents or not target.is_file():
            return None
        return target

    def _published_root(self, reference: ModelArtifactReference) -> Path | None:
        cache_root, _, _ = self._runs.cache_paths(
            reference.record_id,
            reference.asset_index,
            lod=reference.lod,
        )
        return resolve_published_model_run(cache_root, reference.run)
