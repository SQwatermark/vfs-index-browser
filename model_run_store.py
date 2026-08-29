"""Published immutable model run lookup and cache validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable
from urllib.parse import unquote


def _safe_relative_path(root: Path, raw_path: str) -> Path | None:
    normalized = unquote(raw_path).replace("\\", "/").strip("/")
    if not normalized:
        return root
    candidate = (root / normalized).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def resolve_published_model_run(cache_root: Path, requested_run: str = "") -> Path | None:
    """Resolve one immutable completed run without scanning unpublished directories."""

    runs_root = cache_root / "runs"
    run_name = requested_run.strip()
    if not run_name:
        try:
            pointer = json.loads((cache_root / "run.json").read_text(encoding="utf-8"))
            run_name = str(pointer.get("selectedRun") or "").strip()
        except (OSError, json.JSONDecodeError, TypeError):
            return None
    selected = _safe_relative_path(runs_root, run_name)
    if (
        not run_name
        or selected is None
        or selected.parent != runs_root.resolve()
        or not selected.is_dir()
    ):
        return None
    try:
        completion = json.loads((selected / "run.json").read_text(encoding="utf-8"))
        if str(completion.get("selectedRun") or "") != run_name:
            return None
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return selected


class ModelRunStore:
    def __init__(
        self,
        cache_root: Path,
        validate_document: Callable[[dict], list],
    ) -> None:
        self._cache_root = cache_root
        self._validate_document = validate_document

    def cache_paths(
        self,
        record_id: int,
        asset_index: int,
        *,
        lod: int | None = None,
    ) -> tuple[Path, Path, Path]:
        root = self._cache_root / str(record_id) / "models" / str(asset_index)
        if lod is not None:
            root /= f"avatar-lod-{lod}"
        return root, root / "runs", root / "run.json"

    def load_cached(
        self,
        cache_root: Path,
        pointer_path: Path,
        *,
        version: int,
        source_identity: dict,
        geometry_required: bool,
    ) -> tuple[dict, dict, Path] | None:
        published_root = resolve_published_model_run(cache_root)
        if published_root is None or not pointer_path.is_file():
            return None
        try:
            meta = json.loads(pointer_path.read_text(encoding="utf-8"))
            model_path = published_root / "model.json"
            geometry_path = published_root / "geometry.bin"
            texture_root = published_root / "textures"
            document = json.loads(model_path.read_text(encoding="utf-8"))
            geometry_valid = (
                geometry_path.is_file()
                if geometry_required
                else not document.get("buffers") or geometry_path.is_file()
            )
            if (
                meta.get("version") != version
                or meta.get("source") != source_identity
                or not geometry_valid
                or (document.get("images") and not texture_root.is_dir())
                or self._validate_document(document)
            ):
                return None
            return document, meta, model_path
        except (OSError, json.JSONDecodeError, TypeError):
            return None
