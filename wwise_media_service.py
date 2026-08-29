"""Resolve indexed Wwise media to a cached WEM or WAV artifact."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Callable, Mapping

from audio_package_service import AudioEntry, validate_indexed_audio_source


@dataclass(frozen=True)
class WwiseMediaArtifact:
    target: Path
    entry: AudioEntry
    mode: str
    download: bool


class WwiseMediaBuildError(RuntimeError):
    pass


class WwiseMediaService:
    def __init__(
        self,
        lookup_media: Callable[[int, int], dict | None],
        resolve_pck_source: Callable[[dict], tuple[dict, Path] | None],
        ensure_media: Callable[[dict, Path, AudioEntry, str, str], Path],
    ) -> None:
        self._lookup_media = lookup_media
        self._resolve_pck_source = resolve_pck_source
        self._ensure_media = ensure_media

    def resolve(self, query: Mapping[str, list[str]]) -> WwiseMediaArtifact:
        pck_file_id = int(query.get("pckFileId", [""])[0])
        ordinal = int(query.get("ordinal", [""])[0])
        mode = query.get("format", ["wav"])[0].lower()
        if mode not in {"wem", "wav"}:
            raise ValueError("Wwise media format must be wem or wav")

        media = self._lookup_media(pck_file_id, ordinal)
        if media is None:
            raise FileNotFoundError("Wwise media not found")
        entry = self._entry(media)
        physical = self._resolve_pck_source(media)
        if physical is None:
            raise FileNotFoundError("Wwise PCK source is unavailable")
        record, chunk_path = physical
        validate_indexed_audio_source(
            record,
            entry,
            index_name="Wwise index",
            expected_file_size=int(media["package_file_size"]),
        )
        try:
            target = self._ensure_media(record, chunk_path, entry, mode, "wwise")
        except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as error:
            raise WwiseMediaBuildError(str(error)) from error
        download = query.get("download", ["0"])[0] in {"1", "true", "yes"}
        return WwiseMediaArtifact(target, entry, mode, download)

    @staticmethod
    def _entry(media: dict) -> AudioEntry:
        return AudioEntry(
            wem_id=int(media["media_id"], 16),
            offset=int(media["offset"]),
            size=int(media["size"]),
            source=str(media["source"]),
            language=media["language"],
            bank_id=media["bank_id"],
            bank_offset=media["bank_offset"],
            bank_size=media["bank_size"],
            bank_wem_offset=media["bank_media_offset"],
            bank_encrypted=bool(media["bank_encrypted"]),
        )
