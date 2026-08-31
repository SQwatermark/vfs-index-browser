"""解析 AB、PCK 和 USM 容器中的单个可预览文件。"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from assetbundle_browser import (
    find_exported_file,
    metadata_by_export_name,
    metadata_for_file,
    resolve_export_path,
)
from file_preview_service import file_suffix
from usm import UsmError


class InternalFileResolutionError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class InternalFileResolution:
    record: dict
    target: Path
    asset: dict | None = None
    audio_entry: Any | None = None


class InternalFileResolverService:
    def __init__(
        self,
        ensure_assetbundle: Callable[[dict, Path], tuple[Path, dict] | None],
        ensure_audio: Callable[[dict, Path, str], tuple[Path, Any]],
        ensure_usm: Callable[[dict, Path, str], Path],
    ) -> None:
        self._ensure_assetbundle = ensure_assetbundle
        self._ensure_audio = ensure_audio
        self._ensure_usm = ensure_usm

    def resolve(
        self,
        record: dict,
        chunk_path: Path,
        query: Mapping[str, Sequence[str]],
    ) -> InternalFileResolution | None:
        suffix = file_suffix(record["file_name"])
        internal_path = self._first(query, "path")
        if suffix == ".ab":
            return self._resolve_assetbundle(record, chunk_path, query, internal_path)
        if suffix == ".pck":
            return self._resolve_audio(record, chunk_path, internal_path)
        if suffix == ".usm":
            return self._resolve_usm(record, chunk_path, internal_path)
        raise InternalFileResolutionError(400, "unsupported internal preview container")

    def _resolve_assetbundle(
        self,
        record: dict,
        chunk_path: Path,
        query: Mapping[str, Sequence[str]],
        internal_path: str,
    ) -> InternalFileResolution | None:
        ensured = self._ensure_assetbundle(record, chunk_path)
        if ensured is None:
            # AssetBundle worker 已经通过原回调报告结构化错误。
            return None
        export_root, meta = ensured
        if internal_path:
            target = resolve_export_path(export_root, internal_path)
            if target is None or not target.is_file():
                raise InternalFileResolutionError(404, "internal file not found")
            asset = metadata_for_file(
                target,
                export_root,
                metadata_by_export_name(meta),
            )
            return InternalFileResolution(record, target, asset=asset)

        asset_type = self._first(query, "type")
        asset_name = self._first(query, "name")
        path_id = self._first(query, "pathId")
        if not asset_type or not asset_name:
            raise InternalFileResolutionError(
                400,
                "AssetBundle asset preview expected path or type/name",
            )
        found = find_exported_file(
            export_root,
            meta,
            asset_type,
            asset_name,
            path_id,
        )
        if found is None:
            raise InternalFileResolutionError(404, "exported asset not found")
        target, asset = found
        return InternalFileResolution(record, target, asset=asset)

    def _resolve_audio(
        self,
        record: dict,
        chunk_path: Path,
        internal_path: str,
    ) -> InternalFileResolution:
        try:
            target, entry = self._ensure_audio(record, chunk_path, internal_path)
        except FileNotFoundError as error:
            raise InternalFileResolutionError(404, str(error)) from error
        except (ValueError, RuntimeError) as error:
            raise InternalFileResolutionError(500, str(error)) from error
        return InternalFileResolution(record, target, audio_entry=entry)

    def _resolve_usm(
        self,
        record: dict,
        chunk_path: Path,
        internal_path: str,
    ) -> InternalFileResolution:
        try:
            target = self._ensure_usm(record, chunk_path, internal_path)
        except FileNotFoundError as error:
            raise InternalFileResolutionError(404, str(error)) from error
        except (UsmError, RuntimeError, OSError, subprocess.SubprocessError) as error:
            raise InternalFileResolutionError(500, str(error)) from error
        asset = {
            "Name": target.name,
            "Type": "MP4",
            "Container": record["file_name"],
            "Source": "USM",
        }
        return InternalFileResolution(record, target, asset=asset)

    @staticmethod
    def _first(query: Mapping[str, Sequence[str]], key: str) -> str:
        values = query.get(key, ())
        return str(values[0]) if values else ""
