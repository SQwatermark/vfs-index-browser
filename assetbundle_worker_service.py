"""Unity worker application services for whole AssetBundle exports."""

from __future__ import annotations

import json
from collections import Counter
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
ASSETBUNDLE_WORKER_MEDIA_TYPES = (
    "Texture2D",
    "Sprite",
    "TextAsset",
    "VideoClip",
    "AnimationClip",
)


def _media_identity(item: object) -> tuple[str, int, str, str]:
    if not isinstance(item, dict):
        raise RuntimeError("worker preview media contains an invalid identity")
    try:
        path_id = int(item.get("pathId") if "pathId" in item else item.get("PathID"))
    except (TypeError, ValueError) as error:
        raise RuntimeError("worker preview media contains an invalid PathID") from error
    return (
        str(item.get("type") if "type" in item else item.get("Type") or ""),
        path_id,
        str(item.get("name") if "name" in item else item.get("Name") or ""),
        str(item.get("container") if "container" in item else item.get("Container") or "")
        .replace("\\", "/"),
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

    def ensure_preview_export(
        self,
        record: dict,
        chunk_path: Path,
        map_meta: dict,
        *,
        cancel_event: object | None = None,
    ) -> tuple[Path, dict]:
        asset_entries = list(map_meta["assetEntries"])
        present_types = {
            str(entry.get("Type") or "")
            for entry in asset_entries
            if isinstance(entry, dict)
        }
        worker_types = tuple(
            asset_type
            for asset_type in ASSETBUNDLE_WORKER_MEDIA_TYPES
            if asset_type in present_types
        )
        unsupported = sorted(present_types.difference(ASSETBUNDLE_WORKER_MEDIA_TYPES))
        root = self._cache_root / str(record["id"]) / "asset-export"
        runs_root, meta_path = root / "runs", root / "meta.json"
        source_identity = {
            "recordId": int(record["id"]),
            "length": int(record["length"]),
            "offset": int(record["offset"]),
            "chunkPath": str(record["chunk_path"]),
            "chunkMtimeNs": chunk_path.stat().st_mtime_ns,
            "workerTypes": list(worker_types),
            "unsupportedPreviewTypes": unsupported,
            "mapRun": map_meta.get("selectedRun"),
            "toolArtifacts": self._worker.artifact_identity(),
        }

        if worker_types:
            expected = Counter(
                _media_identity(entry)
                for entry in asset_entries
                if isinstance(entry, dict) and str(entry.get("Type") or "") in worker_types
            )

            def invoke(
                run_root: Path,
                export_root: Path,
                request_id: str,
                cancel: object | None,
            ) -> dict:
                source_path = run_root / "source.ab"
                self._write_file_slice(record, chunk_path, source_path)
                return self._worker.export_bundle_preview_media(
                    input_path=source_path,
                    output_directory=export_root,
                    included_types=worker_types,
                    request_id=request_id,
                    cancel_event=cancel,
                )

            def validate(_root: Path, _paths: list[Path], result: dict) -> None:
                described = list(result.get("artifacts") or []) + list(result.get("skipped") or [])
                if Counter(_media_identity(item) for item in described) != expected:
                    raise RuntimeError("worker preview media identities do not match AssetMap")
                if set(result.get("includedTypes") or []) != set(worker_types):
                    raise RuntimeError("worker preview media types do not match the request")

        else:
            def invoke(
                _run_root: Path,
                export_root: Path,
                _request_id: str,
                _cancel: object | None,
            ) -> dict:
                export_root.mkdir(parents=True, exist_ok=False)
                return {
                    "artifactCount": 0,
                    "artifacts": [],
                    "skippedCount": 0,
                    "skipped": [],
                    "includedTypes": [],
                }

            validate = None

        export_root, _artifacts, run_meta = self._runs.ensure(
            runs_root=runs_root,
            meta_path=meta_path,
            request_prefix=f"asset-export-{int(record['id'])}",
            version=CACHE_VERSIONS.version("assetbundle-preview"),
            source_identity=source_identity,
            invoke=invoke,
            validate=validate,
            cancel_event=cancel_event,
            allow_empty=True,
        )
        return export_root, {
            **run_meta,
            "returncode": 0,
            "mapReturncode": 0,
            "mapRun": map_meta,
            "exportTypes": ASSETBUNDLE_EXPORT_TYPES,
            "assetEntries": asset_entries,
            "unsupportedPreviewTypes": unsupported,
        }
