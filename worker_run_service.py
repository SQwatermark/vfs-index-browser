"""Validate, cache, and atomically publish filesystem worker runs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import unquote


class _PublicationSlot:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.users = 0


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


def validate_worker_artifacts(
    export_root: Path,
    result: object,
    *,
    allow_empty: bool = False,
) -> list[Path]:
    artifacts = result.get("artifacts") if isinstance(result, dict) else None
    if (
        not isinstance(result, dict)
        or not isinstance(artifacts, list)
        or (not artifacts and not allow_empty)
        or result.get("artifactCount") != len(artifacts)
        or any(not isinstance(artifact, dict) for artifact in artifacts)
    ):
        raise RuntimeError("Unity worker returned an invalid artifact collection")

    paths = []
    relative_paths = set()
    for artifact in artifacts:
        relative = str(artifact.get("relativePath") or "").replace("\\", "/")
        path = _safe_relative_path(export_root, relative)
        if not relative or relative in relative_paths or path is None or not path.is_file():
            raise RuntimeError("Unity worker returned an invalid or duplicate artifact path")
        relative_paths.add(relative)
        expected_size = artifact.get("byteCount")
        expected_sha256 = str(artifact.get("sha256") or "").casefold()
        actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        if (
            not isinstance(expected_size, int)
            or expected_size != path.stat().st_size
            or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
            or expected_sha256 != actual_sha256
        ):
            raise RuntimeError("Unity worker artifact identity is inconsistent")
        paths.append(path)
    return paths


def _describe_derived_artifacts(export_root: Path, files: object) -> dict[str, dict]:
    if not isinstance(files, dict):
        raise RuntimeError("derived worker artifact list is inconsistent")
    described = {}
    for name, relative_value in files.items():
        relative = str(relative_value).replace("\\", "/")
        path = _safe_relative_path(export_root, relative)
        if not name or path is None or not path.is_file():
            raise RuntimeError("derived worker artifact path is inconsistent")
        described[str(name)] = {
            "relativePath": relative,
            "byteCount": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    return described


def _validate_derived_artifacts(export_root: Path, described: object) -> dict[str, Path]:
    if not isinstance(described, dict):
        raise RuntimeError("cached derived artifact list is inconsistent")
    paths = {}
    for name, identity in described.items():
        if not isinstance(identity, dict):
            raise RuntimeError("cached derived artifact identity is inconsistent")
        path = _safe_relative_path(export_root, str(identity.get("relativePath") or ""))
        if (
            path is None
            or not path.is_file()
            or identity.get("byteCount") != path.stat().st_size
            or identity.get("sha256") != hashlib.sha256(path.read_bytes()).hexdigest()
        ):
            raise RuntimeError("cached derived artifact identity is inconsistent")
        paths[str(name)] = path
    return paths


class WorkerRunService:
    def __init__(self) -> None:
        self._slots_guard = threading.Lock()
        self._slots: dict[str, _PublicationSlot] = {}

    @contextmanager
    def _publication_lock(self, meta_path: Path) -> Iterator[None]:
        key = os.path.normcase(str(meta_path.resolve()))
        with self._slots_guard:
            slot = self._slots.setdefault(key, _PublicationSlot())
            slot.users += 1
        slot.lock.acquire()
        try:
            yield
        finally:
            slot.lock.release()
            with self._slots_guard:
                slot.users -= 1
                if slot.users == 0 and self._slots.get(key) is slot:
                    del self._slots[key]

    def ensure(
        self,
        *,
        runs_root: Path,
        meta_path: Path,
        request_prefix: str,
        version: int,
        source_identity: dict,
        invoke: Callable[[Path, Path, str, object | None], dict],
        derive: Callable[[Path, list[Path]], dict[str, str]] | None = None,
        validate: Callable[[Path, list[Path], dict], None] | None = None,
        cancel_event: object | None = None,
        allow_empty: bool = False,
    ) -> tuple[Path, list[Path], dict]:
        with self._publication_lock(meta_path):
            cached = self._load_cached(
                runs_root,
                meta_path,
                version,
                source_identity,
                validate,
                allow_empty,
            )
            if cached is not None:
                return cached
            return self._build(
                runs_root,
                meta_path,
                request_prefix,
                version,
                source_identity,
                invoke,
                derive,
                validate,
                cancel_event,
                allow_empty,
            )

    @staticmethod
    def _load_cached(
        runs_root: Path,
        meta_path: Path,
        version: int,
        source_identity: dict,
        validate: Callable[[Path, list[Path], dict], None] | None,
        allow_empty: bool,
    ) -> tuple[Path, list[Path], dict] | None:
        if not meta_path.is_file():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            selected = _safe_relative_path(runs_root, str(meta.get("selectedRun") or ""))
            export_root = selected / "exported" if selected is not None else None
            if (
                meta.get("version") != version
                or meta.get("source") != source_identity
                or export_root is None
                or not export_root.is_dir()
            ):
                return None
            artifacts = validate_worker_artifacts(
                export_root,
                meta.get("workerResult"),
                allow_empty=allow_empty,
            )
            if validate is not None:
                validate(export_root, artifacts, meta["workerResult"])
            expected = [path.relative_to(export_root).as_posix() for path in artifacts]
            if meta.get("exportedFiles") != expected:
                raise RuntimeError("cached worker artifact list is inconsistent")
            _validate_derived_artifacts(export_root, meta.get("derivedFiles", {}))
            return export_root, artifacts, meta
        except (OSError, json.JSONDecodeError, TypeError, RuntimeError):
            return None

    @staticmethod
    def _build(
        runs_root: Path,
        meta_path: Path,
        request_prefix: str,
        version: int,
        source_identity: dict,
        invoke: Callable[[Path, Path, str, object | None], dict],
        derive: Callable[[Path, list[Path]], dict[str, str]] | None,
        validate: Callable[[Path, list[Path], dict], None] | None,
        cancel_event: object | None,
        allow_empty: bool,
    ) -> tuple[Path, list[Path], dict]:
        request_id = f"{request_prefix}-{time.time_ns()}-{uuid.uuid4().hex}"
        run_root = runs_root / request_id
        export_root = run_root / "exported"
        temporary_meta = meta_path.with_name(f".{meta_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            result = invoke(run_root, export_root, request_id, cancel_event)
            artifacts = validate_worker_artifacts(export_root, result, allow_empty=allow_empty)
            if validate is not None:
                validate(export_root, artifacts, result)
            derived = _describe_derived_artifacts(
                export_root,
                derive(export_root, artifacts) if derive is not None else {},
            )
            meta = {
                "version": version,
                "source": source_identity,
                "selectedRun": request_id,
                "exportedFiles": [
                    path.relative_to(export_root).as_posix() for path in artifacts
                ],
                "derivedFiles": derived,
                "workerResult": result,
                "builtAtEpoch": int(time.time()),
            }
            meta_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_meta.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary_meta, meta_path)
            return export_root, artifacts, meta
        except Exception:
            temporary_meta.unlink(missing_ok=True)
            if run_root.parent.resolve() == runs_root.resolve() and run_root.is_dir():
                shutil.rmtree(run_root, ignore_errors=True)
            raise
