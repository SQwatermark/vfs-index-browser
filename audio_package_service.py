"""Cached AKPK media index and virtual directory service."""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Callable
from urllib.parse import unquote

from audio_package import AudioPackageMedia, parse_audio_package


ReadRange = Callable[[int, int], bytes]
AUDIO_ENTRY_RE = re.compile(
    r"^(wem|wav)/([0-9a-f]{1,2})/([0-9]+)\.(wem|wav)$",
    re.IGNORECASE,
)


def audio_entry_prefix(media_id: int) -> str:
    return f"{media_id:x}"[:2].rjust(2, "0")


def parse_audio_internal_path(raw_path: str) -> tuple[str, int] | None:
    normalized = unquote(raw_path).replace("\\", "/").strip("/")
    match = AUDIO_ENTRY_RE.match(normalized)
    if not match or match.group(1).lower() != match.group(4).lower():
        return None
    return match.group(1).lower(), int(match.group(3))


class AudioPackageIndexService:
    def __init__(self, cache_root: Path, cache_version: int):
        self._cache_root = cache_root
        self._cache_version = cache_version

    def ensure_index(self, record: dict, read_range: ReadRange) -> dict:
        meta_path = self._meta_path(record)
        identity = self._identity(record)
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if meta.get("identity") == identity and isinstance(meta.get("entries"), list):
                    return meta
            except (OSError, json.JSONDecodeError):
                pass

        package = parse_audio_package(
            read_range,
            int(record["length"]),
            str(record["file_name"]),
        )
        meta = {
            "identity": identity,
            "builtAtEpoch": int(time.time()),
            "entryCount": len(package.media),
            "entries": [self._media_json(entry) for entry in package.media],
        }
        self._publish_meta(meta_path, meta)
        return meta

    @staticmethod
    def list_directory(meta: dict, raw_path: str) -> dict:
        normalized = unquote(raw_path).replace("\\", "/").strip("/")
        entries = list(meta.get("entries") or [])
        total_size = sum(int(entry["size"]) for entry in entries)
        if not normalized:
            return {
                "path": "",
                "dirs": [
                    {
                        "name": mode,
                        "path": mode,
                        "fileCount": len(entries),
                        "totalBytes": total_size,
                    }
                    for mode in ("wem", "wav")
                ],
                "files": [],
            }

        parts = normalized.split("/")
        if len(parts) == 1 and parts[0] in {"wem", "wav"}:
            groups: dict[str, tuple[int, int]] = {}
            for entry in entries:
                prefix = audio_entry_prefix(int(entry["id"]))
                count, size = groups.get(prefix, (0, 0))
                groups[prefix] = (count + 1, size + int(entry["size"]))
            return {
                "path": normalized,
                "dirs": [
                    {
                        "name": prefix,
                        "path": f"{parts[0]}/{prefix}",
                        "fileCount": count,
                        "totalBytes": size,
                    }
                    for prefix, (count, size) in sorted(groups.items())
                ],
                "files": [],
            }

        if len(parts) == 2 and parts[0] in {"wem", "wav"}:
            mode, prefix = parts
            files = []
            for entry in sorted(entries, key=lambda item: int(item["id"])):
                media_id = int(entry["id"])
                if audio_entry_prefix(media_id) != prefix.lower():
                    continue
                name = f"{media_id}.{mode}"
                files.append(
                    {
                        "name": name,
                        "path": f"{mode}/{prefix}/{name}",
                        "size": int(entry["size"]),
                        "kind": "audio" if mode == "wav" else "wem",
                        "asset": {
                            "Name": str(media_id),
                            "Type": "WEM",
                            "Container": f"wwise/{media_id}.wem",
                            "Source": entry["source"],
                            "PathID": media_id,
                            "Language": entry.get("language"),
                            "BankID": entry.get("bankId"),
                        },
                    }
                )
            return {"path": normalized, "dirs": [], "files": files}

        raise FileNotFoundError("audio package directory not found")

    def _meta_path(self, record: dict) -> Path:
        return self._cache_root / str(record["id"]) / "audio_meta.json"

    def _identity(self, record: dict) -> dict:
        return {
            "version": self._cache_version,
            "recordId": int(record["id"]),
            "length": int(record["length"]),
            "offset": int(record.get("offset") or 0),
            "fileDataMd5": str(record.get("file_data_md5") or ""),
            "fileChunkMd5": str(record.get("file_chunk_md5") or ""),
        }

    @staticmethod
    def _media_json(entry: AudioPackageMedia) -> dict:
        return {
            "id": entry.media_id,
            "offset": entry.offset,
            "size": entry.size,
            "source": entry.source,
            "language": entry.language,
            "bankId": entry.bank_id,
            "bankOffset": entry.bank_offset,
            "bankSize": entry.bank_size,
            "bankWemOffset": entry.bank_media_offset,
            "bankEncrypted": entry.bank_encrypted,
        }

    @staticmethod
    def _publish_meta(path: Path, meta: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
