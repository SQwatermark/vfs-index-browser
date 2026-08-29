"""Shared media, text, and binary preview classification."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Callable


PREVIEW_TEXT_LIMIT = 2 * 1024 * 1024
PREVIEW_BINARY_LIMIT = 256 * 1024
TEXT_EXTENSIONS = {".anim", ".json", ".lua", ".md", ".txt", ".csv", ".xml", ".yaml", ".yml"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".ogg", ".mov"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".m4a"}
CONTAINER_EXTENSIONS = {".ab", ".pck", ".usm"}


class FilePreviewService:
    @classmethod
    def build_path(cls, base: dict, path: Path) -> dict:
        def read(limit: int) -> bytes:
            with path.open("rb") as source:
                return source.read(limit)

        return cls.build(base, path.name, path.stat().st_size, read)

    @staticmethod
    def build(
        base: dict,
        file_name: str,
        file_size: int,
        read: Callable[[int], bytes],
    ) -> dict:
        suffix = file_suffix(file_name)
        for kind, extensions in (
            ("image", IMAGE_EXTENSIONS),
            ("video", VIDEO_EXTENSIONS),
            ("audio", AUDIO_EXTENSIONS),
        ):
            if suffix in extensions:
                return {
                    **base,
                    "kind": kind,
                    "contentType": guess_content_type(file_name),
                }
        limit = PREVIEW_TEXT_LIMIT if suffix in TEXT_EXTENSIONS else PREVIEW_BINARY_LIMIT
        data = read(limit)
        text, encoding = decode_text(data)
        truncated = file_size > len(data)
        if text is not None and (suffix in TEXT_EXTENSIONS or looks_like_text(text)):
            return {
                **base,
                "kind": "text",
                "encoding": encoding,
                "text": text,
                "truncated": truncated,
            }
        return {
            **base,
            "kind": "hex",
            "hex": hex_preview(data),
            "truncated": truncated,
        }


def file_suffix(file_name: str) -> str:
    return Path(file_name).suffix.lower()


def guess_content_type(file_name: str, data: bytes | None = None) -> str:
    suffix = file_suffix(file_name)
    if suffix == ".wem":
        return "audio/x-wem"
    if data:
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            return "image/webp"
        if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
            return "image/gif"
        if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
            return "audio/wav"
    if suffix == ".json":
        return "application/json; charset=utf-8"
    if suffix in {".lua", ".md", ".txt", ".csv", ".xml", ".yaml", ".yml"}:
        return "text/plain; charset=utf-8"
    return mimetypes.guess_type(file_name)[0] or "application/octet-stream"


def decode_text(data: bytes) -> tuple[str | None, str | None]:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return None, None


def looks_like_text(value: str) -> bool:
    if not value:
        return True
    sample = value[:8192]
    controls = sum(1 for char in sample if ord(char) < 32 and char not in "\r\n\t")
    return controls <= max(2, len(sample) // 100)


def truncate_text(value: str, limit: int = PREVIEW_TEXT_LIMIT) -> tuple[str, bool]:
    data = value.encode("utf-8")
    if len(data) <= limit:
        return value, False
    return data[:limit].decode("utf-8", errors="replace"), True


def hex_preview(data: bytes, max_bytes: int = PREVIEW_BINARY_LIMIT) -> str:
    data = data[:max_bytes]
    lines = []
    for offset in range(0, len(data), 16):
        row = data[offset : offset + 16]
        hex_part = " ".join(f"{value:02x}" for value in row)
        ascii_part = "".join(chr(value) if 32 <= value < 127 else "." for value in row)
        lines.append(f"{offset:08x}  {hex_part:<47}  {ascii_part}")
    return "\n".join(lines)
