"""Assemble virtual directories inside supported secondary containers."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from file_preview_service import file_suffix


class InternalDirectoryError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def build_internal_tool_metadata(
    registry,
    *,
    vgmstream_default,
    usm_convert_default,
    ffmpeg_default,
) -> dict:
    """把可选工具探测结果压缩为稳定的内部目录 API 字段。"""

    vgmstream = registry.capability("vgmstream")
    usm_convert = registry.capability("usm-convert")
    ffmpeg = registry.capability("ffmpeg")
    return {
        "audio": {
            "wavPreviewAvailable": vgmstream.available,
            "vgmstreamCli": str(vgmstream.resolved_path or vgmstream_default),
        },
        "usm": {
            "usmConvertAvailable": usm_convert.available,
            "ffmpegAvailable": ffmpeg.available,
            "usmConvert": str(usm_convert.resolved_path or usm_convert_default),
            "ffmpeg": str(ffmpeg.resolved_path or ffmpeg_default),
        },
    }


class InternalDirectoryService:
    def __init__(
        self,
        ensure_assetbundle: Callable[[dict, Path], tuple[Path, dict] | None],
        list_assetbundle: Callable[[Path, str, dict], dict],
        ensure_audio: Callable[[dict, Path], dict],
        list_audio: Callable[[dict, str], dict],
        list_usm: Callable[[dict, str], dict],
        tool_meta: dict,
    ) -> None:
        self._ensure_assetbundle = ensure_assetbundle
        self._list_assetbundle = list_assetbundle
        self._ensure_audio = ensure_audio
        self._list_audio = list_audio
        self._list_usm = list_usm
        self._tool_meta = tool_meta

    def build(
        self,
        original: dict,
        record: dict,
        chunk_path: Path,
        path: str,
    ) -> dict | None:
        suffix = file_suffix(record["file_name"])
        try:
            if suffix == ".ab":
                ensured = self._ensure_assetbundle(record, chunk_path)
                if ensured is None:
                    return None
                export_root, meta = ensured
                listing = self._list_assetbundle(export_root, path, meta)
                return self._document(
                    "assetBundle",
                    original,
                    record,
                    listing,
                    {
                        "returncode": meta.get("returncode"),
                        "builtAtEpoch": meta.get("builtAtEpoch"),
                        "exportTypes": meta.get("exportTypes"),
                    },
                )
            if suffix == ".pck":
                meta = self._ensure_audio(record, chunk_path)
                listing = self._list_audio(meta, path)
                return self._document(
                    "audioPackage",
                    original,
                    record,
                    listing,
                    {
                        "builtAtEpoch": meta.get("builtAtEpoch"),
                        "entryCount": meta.get("entryCount"),
                        **self._tool_meta["audio"],
                    },
                )
            if suffix == ".usm":
                listing = self._list_usm(record, path)
                return self._document(
                    "criVideo",
                    original,
                    record,
                    listing,
                    self._tool_meta["usm"],
                )
        except (FileNotFoundError, ValueError) as error:
            message = "internal directory not found" if suffix == ".ab" else str(error)
            raise InternalDirectoryError(404, message) from error
        return {
            "kind": "plainFile",
            "status": "notContainer",
            "message": "该文件不是当前识别的二级容器。",
        }

    @staticmethod
    def _document(
        kind: str,
        original: dict,
        record: dict,
        listing: dict,
        meta: dict,
    ) -> dict:
        return {
            "kind": kind,
            "status": "ready",
            "file": original,
            "resolvedFile": record,
            "path": listing["path"],
            "dirs": listing["dirs"],
            "files": listing["files"],
            "meta": meta,
        }
