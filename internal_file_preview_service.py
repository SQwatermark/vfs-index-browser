"""Assemble previews for files safely resolved inside VFS containers."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from file_preview_service import FilePreviewService


class InternalFilePreviewService:
    def build(
        self,
        record: dict,
        target: Path,
        relative_path: str,
        *,
        asset: dict | None = None,
        audio_entry: dict | None = None,
    ) -> dict:
        raw_url = (
            f"/api/internal/raw?id={record['id']}"
            f"&path={quote(relative_path, safe='')}"
        )
        base = {
            "file": record,
            "path": relative_path,
            "name": target.name,
            "size": target.stat().st_size,
            "rawUrl": raw_url,
            "downloadUrl": f"{raw_url}&download=1",
            "asset": asset,
            "audioEntry": audio_entry,
        }
        return FilePreviewService.build_path(base, target)
