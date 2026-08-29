"""Prepare bounded raw-file responses while keeping HTTP transport outside the service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import quote

from file_preview_service import decode_text, guess_content_type, looks_like_text


DEFAULT_STREAM_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class RawFileResponse:
    content_type: str
    content_length: int
    content_disposition: str | None
    _path: Path | None = None
    _offset: int = 0
    _data: bytes | None = None
    _chunk_size: int = DEFAULT_STREAM_CHUNK_SIZE

    def chunks(self) -> Iterator[bytes]:
        if self._data is not None:
            for offset in range(0, len(self._data), self._chunk_size):
                yield self._data[offset : offset + self._chunk_size]
            return
        if self._path is None:
            return
        with self._path.open("rb") as source:
            source.seek(self._offset)
            remaining = self.content_length
            while remaining > 0:
                data = source.read(min(self._chunk_size, remaining))
                if not data:
                    break
                yield data
                remaining -= len(data)


class RawFileService:
    def __init__(self, chunk_size: int = DEFAULT_STREAM_CHUNK_SIZE) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        self._chunk_size = chunk_size

    def prepare_vfs(
        self,
        original: dict,
        record: dict,
        chunk_path: Path,
        *,
        download: bool,
        read_decrypted: Callable[[], bytes],
    ) -> RawFileResponse:
        file_name = Path(original["file_name"]).name or "vfs-file.bin"
        disposition = self._disposition(file_name, download)
        content_type = guess_content_type(original["file_name"])
        if record.get("encrypted"):
            data = read_decrypted()
            content_type = guess_content_type(original["file_name"], data[:32])
            if content_type.startswith(("application/json", "text/plain")):
                text, _ = decode_text(data[:8192])
                if text is None or not looks_like_text(text):
                    content_type = "application/octet-stream"
            return RawFileResponse(
                content_type,
                len(data),
                disposition,
                _data=data,
                _chunk_size=self._chunk_size,
            )
        return RawFileResponse(
            content_type,
            int(record["length"]),
            disposition,
            _path=chunk_path,
            _offset=int(record["offset"]),
            _chunk_size=self._chunk_size,
        )

    def prepare_path(
        self,
        target: Path,
        *,
        download: bool | None,
        download_name: str | None = None,
        content_type: str | None = None,
        encode_filename: bool = True,
    ) -> RawFileResponse:
        sniff = None
        if content_type is None:
            with target.open("rb") as source:
                sniff = source.read(32)
        return RawFileResponse(
            content_type or guess_content_type(target.name, sniff),
            target.stat().st_size,
            (
                self._disposition(
                    download_name or target.name,
                    download,
                    encode_filename=encode_filename,
                )
                if download is not None
                else None
            ),
            _path=target,
            _chunk_size=self._chunk_size,
        )

    def prepare_bytes(
        self,
        data: bytes,
        *,
        content_type: str,
        download: bool | None,
        download_name: str | None = None,
        encode_filename: bool = True,
    ) -> RawFileResponse:
        if download is not None and not download_name:
            raise ValueError("download_name is required when disposition is enabled")
        return RawFileResponse(
            content_type,
            len(data),
            (
                self._disposition(
                    str(download_name),
                    download,
                    encode_filename=encode_filename,
                )
                if download is not None
                else None
            ),
            _data=data,
            _chunk_size=self._chunk_size,
        )

    @staticmethod
    def _disposition(
        file_name: str,
        download: bool,
        *,
        encode_filename: bool = True,
    ) -> str:
        mode = "attachment" if download else "inline"
        if encode_filename:
            return f"{mode}; filename*=UTF-8''{quote(file_name)}"
        return f"{mode}; filename={file_name}"
