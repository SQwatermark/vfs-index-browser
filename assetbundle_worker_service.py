"""Unity worker application services for whole AssetBundle exports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from cache_versions import CACHE_VERSIONS
from worker_run_service import WorkerRunService


ASSETBUNDLE_EXPORT_TYPES = (
    "Texture2D",
    "Sprite",
    "TextAsset",
    "AudioClip",
    "VideoClip",
    "AnimationClip",
)


class AssetBundleWorkerService:
    def __init__(
        self,
        cache_root: Path,
        worker: object,
        write_file_slice: Callable[[dict, Path, Path], None],
        runs: WorkerRunService,
    ) -> None:
        self._cache_root = cache_root
        self._worker = worker
        self._write_file_slice = write_file_slice
        self._runs = runs

    def ensure_map(
        self,
        record: dict,
        chunk_path: Path,
        *,
        cancel_event: object | None = None,
    ) -> dict:
        root = self._cache_root / str(record["id"]) / "asset-map"
        runs_root, meta_path = root / "runs", root / "meta.json"
        source_label = str(record.get("logical_id") or f"record:{int(record['id'])}")
        source_identity = {
            "recordId": int(record["id"]),
            "length": int(record["length"]),
            "offset": int(record["offset"]),
            "chunkPath": str(record["chunk_path"]),
            "chunkMtimeNs": chunk_path.stat().st_mtime_ns,
            "exportTypes": list(ASSETBUNDLE_EXPORT_TYPES),
            "sourceLabel": source_label,
            "toolArtifacts": self._worker.artifact_identity(),
        }

        def run_export(
            run_root: Path,
            export_root: Path,
            request_id: str,
            cancel: object | None,
        ) -> dict:
            source_path = run_root / "source.ab"
            self._write_file_slice(record, chunk_path, source_path)
            return self._worker.build_asset_map(
                input_path=source_path,
                output_directory=export_root,
                source_label=source_label,
                included_types=ASSETBUNDLE_EXPORT_TYPES,
                request_id=request_id,
                cancel_event=cancel,
            )

        export_root, artifacts, meta = self._runs.ensure(
            runs_root=runs_root,
            meta_path=meta_path,
            request_prefix=f"asset-map-{int(record['id'])}",
            version=CACHE_VERSIONS.version("assetbundle-map"),
            source_identity=source_identity,
            invoke=run_export,
            cancel_event=cancel_event,
        )
        if len(artifacts) != 1:
            raise RuntimeError(f"expected one AssetMap artifact, found {len(artifacts)}")
        asset_map = json.loads(artifacts[0].read_text(encoding="utf-8-sig"))
        asset_entries = asset_map.get("AssetEntries")
        if not isinstance(asset_entries, list):
            raise RuntimeError("worker AssetMap is missing AssetEntries")
        return {
            **meta,
            "mapReturncode": 0,
            "exportTypes": ASSETBUNDLE_EXPORT_TYPES,
            "assetEntries": asset_entries,
            "assetMapFile": artifacts[0].relative_to(export_root).as_posix(),
        }
