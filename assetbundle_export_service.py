"""Coordinate AssetBundle map and preview exports without HTTP side effects."""

from __future__ import annotations

import json
from pathlib import Path

from unity_worker import UnityWorkerError


class AssetBundleExportError(RuntimeError):
    """Stable application error for one AssetBundle export stage."""

    def __init__(self, stage: str, message: str) -> None:
        if stage not in {"map", "preview"}:
            raise ValueError(f"unsupported AssetBundle export stage: {stage}")
        super().__init__(message)
        self.stage = stage

    @property
    def status(self) -> str:
        return "mapFailed" if self.stage == "map" else "exportFailed"

    def document(self) -> dict:
        return {
            "kind": "assetBundle",
            "status": self.status,
            "message": str(self),
        }


class AssetBundleExportService:
    """Expose worker exports through a stable application-error boundary."""

    def __init__(self, worker_service: object) -> None:
        self._worker = worker_service

    def ensure_map(
        self,
        record: dict,
        chunk_path: Path,
        *,
        cancel_event: object | None = None,
    ) -> dict:
        try:
            return self._worker.ensure_map(
                record,
                chunk_path,
                cancel_event=cancel_event,
            )
        except (UnityWorkerError, OSError, json.JSONDecodeError, RuntimeError) as error:
            raise AssetBundleExportError("map", str(error)) from error

    def ensure_preview(
        self,
        record: dict,
        chunk_path: Path,
        map_meta: dict,
        *,
        cancel_event: object | None = None,
    ) -> tuple[Path, dict]:
        try:
            return self._worker.ensure_preview_export(
                record,
                chunk_path,
                map_meta,
                cancel_event=cancel_event,
            )
        except (UnityWorkerError, OSError, RuntimeError) as error:
            raise AssetBundleExportError("preview", str(error)) from error
