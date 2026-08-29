"""Assemble virtual directories inside supported secondary containers."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from file_preview_service import file_suffix


class InternalDirectoryService:
    def __init__(
        self,
        ensure_assetbundle: Callable[[dict, Path], tuple[Path, dict] | None],
        list_assetbundle: Callable[[Path, str, dict], dict],
        ensure_audio: Callable[[dict, Path], dict],
        list_audio: Callable[[dict, str], dict],
        list_usm: Callable[[dict, str], dict],
    ) -> None:
        self._ensure_assetbundle = ensure_assetbundle
        self._list_assetbundle = list_assetbundle
        self._ensure_audio = ensure_audio
        self._list_audio = list_audio
        self._list_usm = list_usm

    def build(
        self,
        original: dict,
        record: dict,
        chunk_path: Path,
        path: str,
        tool_meta: dict,
    ) -> dict | None:
        suffix = file_suffix(record["file_name"])
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
                    **tool_meta["audio"],
                },
            )
        if suffix == ".usm":
            listing = self._list_usm(record, path)
            return self._document(
                "criVideo",
                original,
                record,
                listing,
                tool_meta["usm"],
            )
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
