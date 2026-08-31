"""Published immutable model run lookup and cache validation."""

from __future__ import annotations

import json
import os
import uuid
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

    def resolve_model_path(
        self,
        record_id: int,
        asset_index: int,
        requested_run: str = "",
        *,
        lod: int | None = None,
    ) -> Path | None:
        """解析一次已经完成并发布的模型运行产物。"""

        cache_root, _, _ = self.cache_paths(record_id, asset_index, lod=lod)
        published_root = resolve_published_model_run(cache_root, requested_run)
        if published_root is None:
            return None
        model_path = published_root / "model.json"
        return model_path if model_path.is_file() else None

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

    def publish(
        self,
        cache_root: Path,
        pointer_path: Path,
        run_root: Path,
        *,
        document: dict,
        meta: dict,
        geometry: bytes,
        geometry_required: bool,
        before_pointer: Callable[[], None] | None = None,
    ) -> Path:
        selected_run = str(meta.get("selectedRun") or "")
        if (
            pointer_path.resolve() != (cache_root / "run.json").resolve()
            or run_root.parent.resolve() != (cache_root / "runs").resolve()
            or not selected_run
            or selected_run != run_root.name
        ):
            raise ValueError("model run publication target is inconsistent")
        model_path = run_root / "model.json"
        geometry_path = run_root / "geometry.bin"
        model_path.parent.mkdir(parents=True, exist_ok=True)
        model_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if geometry or geometry_required:
            geometry_path.write_bytes(geometry)
        else:
            geometry_path.unlink(missing_ok=True)
        (run_root / "run.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        cache_root.mkdir(parents=True, exist_ok=True)
        temporary = pointer_path.with_name(f".{pointer_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            if before_pointer is not None:
                before_pointer()
            os.replace(temporary, pointer_path)
        finally:
            temporary.unlink(missing_ok=True)
        return model_path
