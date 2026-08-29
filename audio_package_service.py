"""Cached AKPK media index and virtual directory service."""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import unquote

from audio_export import VgmstreamConversionService
from audio_package import AudioPackageMedia, decrypt_audio_bytes, parse_audio_package


ReadRange = Callable[[int, int], bytes]
AUDIO_ENTRY_RE = re.compile(
    r"^(wem|wav)/([0-9a-f]{1,2})/([0-9]+)\.(wem|wav)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AudioEntry:
    wem_id: int
    offset: int
    size: int
    source: str
    language: str | None = None
    bank_id: int | None = None
    bank_offset: int | None = None
    bank_size: int | None = None
    bank_wem_offset: int | None = None
    bank_encrypted: bool = False

    def to_json(self) -> dict:
        return {
            "id": self.wem_id,
            "offset": self.offset,
            "size": self.size,
            "source": self.source,
            "language": self.language,
            "bankId": self.bank_id,
            "bankOffset": self.bank_offset,
            "bankSize": self.bank_size,
            "bankWemOffset": self.bank_wem_offset,
            "bankEncrypted": self.bank_encrypted,
        }

    @staticmethod
    def from_json(payload: dict) -> "AudioEntry":
        return AudioEntry(
            wem_id=int(payload["id"]),
            offset=int(payload["offset"]),
            size=int(payload["size"]),
            source=str(payload.get("source") or "unknown"),
            language=payload.get("language"),
            bank_id=payload.get("bankId"),
            bank_offset=payload.get("bankOffset"),
            bank_size=payload.get("bankSize"),
            bank_wem_offset=payload.get("bankWemOffset"),
            bank_encrypted=bool(payload.get("bankEncrypted")),
        )


class StaleAudioIndexError(RuntimeError):
    pass


def validate_indexed_audio_source(
    record: dict,
    entry: AudioEntry,
    *,
    index_name: str,
    expected_file_size: int | None = None,
) -> None:
    file_name = str(record.get("file_name") or "")
    if not file_name.casefold().endswith(".pck"):
        raise StaleAudioIndexError(
            f"{index_name} references VFS file id {record.get('id')} that is no longer "
            "a PCK; rebuild the secondary audio index"
        )
    if (
        expected_file_size is not None
        and int(record["length"]) != expected_file_size
    ):
        raise StaleAudioIndexError(
            f"{index_name} PCK size changed from {expected_file_size} to "
            f"{record['length']}; rebuild the secondary audio index"
        )
    if entry.bank_encrypted:
        start = entry.bank_offset
        size = entry.bank_size
    else:
        start = entry.offset
        size = entry.size
    if start is None or size is None or start < 0 or size < 0:
        raise StaleAudioIndexError(
            f"{index_name} has incomplete PCK range metadata; rebuild the secondary "
            "audio index"
        )
    if start + size > int(record["length"]):
        raise StaleAudioIndexError(
            f"{index_name} media range exceeds current PCK file id {record.get('id')}; "
            "rebuild the secondary audio index"
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
    def __init__(
        self,
        cache_root: Path,
        cache_version: int,
        *,
        vgmstream: Path | None = None,
    ):
        self._cache_root = cache_root
        self._cache_version = cache_version
        self._vgmstream = vgmstream

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

    def ensure_entry(
        self,
        record: dict,
        internal_path: str,
        read_range: ReadRange,
    ) -> tuple[Path, AudioEntry]:
        parsed = parse_audio_internal_path(internal_path)
        if parsed is None:
            raise FileNotFoundError("audio entry not found")
        mode, media_id = parsed
        meta = self.ensure_index(record, read_range)
        entry = self.entries_by_id(meta).get(media_id)
        if entry is None:
            raise FileNotFoundError("audio entry not found")
        root = (
            self._cache_root
            / str(record["id"])
            / "audio"
            / self._package_cache_key(record)
        )
        return (
            self._ensure_output(
                entry,
                mode,
                root / "wem",
                root / "wav" / self._converter_identity(),
                read_range,
            ),
            entry,
        )

    def ensure_indexed_media(
        self,
        record: dict,
        entry: AudioEntry,
        mode: str,
        namespace: str,
        read_range: ReadRange,
    ) -> Path:
        package_identity = self._package_cache_key(record)
        media_identity = (
            f"{package_identity}-"
            f"{entry.offset:x}-{entry.size:x}-"
            f"{entry.bank_id if entry.bank_id is not None else 0:x}-"
            f"{entry.bank_wem_offset if entry.bank_wem_offset is not None else 0:x}"
        )
        root = self._cache_root / str(record["id"]) / namespace / media_identity
        return self._ensure_output(
            entry,
            mode,
            root / "wem",
            root / "wav" / self._converter_identity(),
            read_range,
        )

    @staticmethod
    def entries_by_id(meta: dict) -> dict[int, AudioEntry]:
        entries = [AudioEntry.from_json(item) for item in meta.get("entries") or []]
        return {entry.wem_id: entry for entry in entries}

    def _ensure_output(
        self,
        entry: AudioEntry,
        mode: str,
        wem_root: Path,
        wav_root: Path,
        read_range: ReadRange,
    ) -> Path:
        if mode not in {"wem", "wav"}:
            raise ValueError("audio output mode must be wem or wav")
        prefix = audio_entry_prefix(entry.wem_id)
        wem_path = wem_root / prefix / f"{entry.wem_id}.wem"
        if not wem_path.is_file() or wem_path.stat().st_size != entry.size:
            data = self._extract_wem(entry, read_range)
            if len(data) != entry.size:
                raise ValueError(
                    f"audio entry {entry.wem_id} expected {entry.size} bytes, got {len(data)}"
                )
            self._publish_bytes(wem_path, data)
        if mode == "wem":
            return wem_path
        if self._vgmstream is None:
            raise FileNotFoundError("vgmstream executable not found")
        wav_path = wav_root / prefix / f"{entry.wem_id}.wav"
        return VgmstreamConversionService(self._vgmstream).ensure_wav(wem_path, wav_path)

    @staticmethod
    def _extract_wem(entry: AudioEntry, read_range: ReadRange) -> bytes:
        if entry.bank_encrypted:
            if (
                entry.bank_id is None
                or entry.bank_offset is None
                or entry.bank_size is None
                or entry.bank_wem_offset is None
            ):
                raise ValueError("encrypted bank entry is missing bank metadata")
            bank = bytearray(read_range(entry.bank_offset, entry.bank_size))
            decrypt_audio_bytes(bank, entry.bank_id)
            data = bytes(bank[entry.bank_wem_offset : entry.bank_wem_offset + entry.size])
        else:
            data = read_range(entry.offset, entry.size)
        if len(data) >= 4 and data[:4] not in {b"RIFF", b"RIFX"}:
            decrypted = bytearray(data)
            decrypt_audio_bytes(decrypted, entry.wem_id)
            data = bytes(decrypted)
        return data

    def _converter_identity(self) -> str:
        if self._vgmstream is None or not self._vgmstream.is_file():
            return "unavailable"
        stat = self._vgmstream.stat()
        return f"{stat.st_size:x}-{stat.st_mtime_ns:x}"

    def _package_cache_key(self, record: dict) -> str:
        identity = str(
            record.get("file_data_md5")
            or record.get("file_chunk_md5")
            or f"{int(record.get('offset') or 0):x}-{int(record['length']):x}"
        ).casefold()
        return f"v{self._cache_version}-{identity}"

    @staticmethod
    def _publish_bytes(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_bytes(data)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _meta_path(self, record: dict) -> Path:
        return self._cache_root / str(record["id"]) / "audio_meta.json"

    def _identity(self, record: dict) -> dict:
        return {
            "version": self._cache_version,
            "recordId": int(record["id"]),
            "length": int(record["length"]),
            "offset": int(record.get("offset") or 0),
            "logicalId": str(record.get("logical_id") or ""),
            "fileName": str(record.get("file_name") or ""),
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
