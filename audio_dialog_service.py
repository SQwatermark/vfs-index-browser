"""AudioDialog virtual catalog, preview, selection, and media application service."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Callable, Mapping
from urllib.parse import quote, unquote

from audio_dialog_store import get_audio_dialog_entry, list_audio_dialog_directory
from audio_package_service import AudioEntry, validate_indexed_audio_source


class AudioDialogConflictError(RuntimeError):
    pass


class AudioDialogMediaBuildError(RuntimeError):
    pass


@dataclass(frozen=True)
class AudioDialogMediaArtifact:
    target: Path
    logical_path: str
    mode: str
    download: bool


class AudioDialogService:
    def __init__(
        self,
        connect: Callable,
        resolve_pck_source: Callable[[dict], tuple[dict, Path] | None],
        ensure_media: Callable[[dict, Path, AudioEntry, str, str], Path],
        *,
        page_size_max: int,
    ) -> None:
        self._connect = connect
        self._resolve_pck_source = resolve_pck_source
        self._ensure_media = ensure_media
        self._page_size_max = page_size_max

    def list(self, query: Mapping[str, list[str]]) -> dict:
        language = query.get("language", ["chinese"])[0]
        path = unquote(query.get("path", [""])[0])
        page = max(int(query.get("page", ["1"])[0]), 1)
        page_size = min(
            max(int(query.get("pageSize", ["100"])[0]), 1),
            self._page_size_max,
        )
        with closing(self._connect()) as conn:
            payload = list_audio_dialog_directory(
                conn,
                language,
                path,
                limit=page_size,
                offset=(page - 1) * page_size,
            )
        payload["page"]["page"] = page
        payload["page"]["pageSize"] = page_size
        return payload

    def entry(self, query: Mapping[str, list[str]]) -> dict:
        language, path, entries = self._entries(query)
        if not entries:
            raise FileNotFoundError("AudioDialog entry not found")
        return {"language": language, "path": path, "entries": entries}

    def preview(self, query: Mapping[str, list[str]]) -> dict:
        language, path, entry = self._selection(query)
        playable = entry["match_status"] == "matched" and len(entry["media"]) == 1
        urls = {}
        if playable:
            base = (
                "/api/audio-dialog/raw"
                f"?language={quote(language, safe='')}"
                f"&path={quote(path, safe='')}"
                f"&dialogKey={entry['dialog_key']}"
            )
            urls = {
                "rawUrl": f"{base}&format=wav",
                "wemDownloadUrl": f"{base}&format=wem&download=1",
                "wavDownloadUrl": f"{base}&format=wav&download=1",
            }
        return {
            "kind": "audioDialog",
            "status": "ready" if playable else entry["match_status"],
            "language": language,
            "path": path,
            "entry": entry,
            **urls,
        }

    def media(self, query: Mapping[str, list[str]]) -> AudioDialogMediaArtifact:
        _language, logical_path, dialog = self._selection(query)
        if dialog["match_status"] != "matched" or len(dialog["media"]) != 1:
            raise AudioDialogConflictError(
                "AudioDialog entry is not uniquely playable: "
                f"{dialog['match_status']}"
            )
        mode = query.get("format", ["wav"])[0].lower()
        if mode not in {"wem", "wav"}:
            raise ValueError("AudioDialog format must be wem or wav")

        media = dialog["media"][0]
        entry = AudioEntry(
            wem_id=int(media["media_id"], 16),
            offset=int(media["offset"]),
            size=int(media["size"]),
            source=str(media["source"]),
            language=media["language"],
            bank_id=media["bank_id"],
            bank_offset=media["bank_offset"],
            bank_size=media["bank_size"],
            bank_wem_offset=media["bank_wem_offset"],
            bank_encrypted=bool(media["bank_encrypted"]),
        )
        physical = self._resolve_pck_source(media)
        if physical is None:
            raise FileNotFoundError("AudioDialog PCK source is unavailable")
        record, chunk_path = physical
        expected_file_size = media.get("pck_file_size")
        validate_indexed_audio_source(
            record,
            entry,
            index_name="AudioDialog index",
            expected_file_size=(
                int(expected_file_size) if expected_file_size is not None else None
            ),
        )
        try:
            target = self._ensure_media(
                record, chunk_path, entry, mode, "audio-dialog"
            )
        except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as error:
            raise AudioDialogMediaBuildError(str(error)) from error
        return AudioDialogMediaArtifact(
            target,
            logical_path,
            mode,
            query.get("download", ["0"])[0] in {"1", "true", "yes"},
        )

    def _entries(
        self, query: Mapping[str, list[str]]
    ) -> tuple[str, str, list[dict]]:
        language = query.get("language", ["chinese"])[0]
        path = unquote(query.get("path", [""])[0])
        if not path:
            raise ValueError("AudioDialog path is required")
        with closing(self._connect()) as conn:
            entries = get_audio_dialog_entry(conn, language, path)
        return language, path, entries

    def _selection(
        self, query: Mapping[str, list[str]]
    ) -> tuple[str, str, dict]:
        language, path, entries = self._entries(query)
        dialog_key_value = query.get("dialogKey", [None])[0]
        dialog_key = int(dialog_key_value) if dialog_key_value is not None else None
        if dialog_key is not None:
            entries = [entry for entry in entries if entry["dialog_key"] == dialog_key]
        if not entries:
            raise FileNotFoundError("AudioDialog entry not found")
        if len(entries) != 1:
            raise AudioDialogConflictError(
                "AudioDialog path has multiple records; specify dialogKey"
            )
        return language, path, entries[0]
