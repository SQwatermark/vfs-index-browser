"""CRI USM virtual directory and atomically published MP4 cache."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Callable
from urllib.parse import unquote

from usm import convert_usm_to_mp4


_CONVERSION_LOCK = threading.Lock()


class UsmVideoService:
    def __init__(
        self,
        cache_root: Path,
        cache_version: int,
        *,
        usm_convert: Path | None,
        ffmpeg: str | Path,
        converter: Callable[..., None] = convert_usm_to_mp4,
    ):
        self._cache_root = cache_root
        self._cache_version = cache_version
        self._usm_convert = usm_convert
        self._ffmpeg = ffmpeg
        self._converter = converter

    @staticmethod
    def virtual_path(record: dict) -> str:
        return f"mp4/{Path(str(record['file_name'])).stem}.mp4"

    def list_directory(self, record: dict, raw_path: str) -> dict:
        normalized = self._normalize_path(raw_path)
        virtual_path = self.virtual_path(record)
        if not normalized:
            return {
                "path": "",
                "dirs": [
                    {
                        "name": "mp4",
                        "path": "mp4",
                        "fileCount": 1,
                        "totalBytes": int(record["length"]),
                    }
                ],
                "files": [],
            }
        if normalized == "mp4":
            name = Path(virtual_path).name
            return {
                "path": "mp4",
                "dirs": [],
                "files": [
                    {
                        "name": name,
                        "path": virtual_path,
                        "size": int(record["length"]),
                        "kind": "video",
                        "asset": {
                            "Name": name,
                            "Type": "MP4",
                            "Container": record["file_name"],
                            "Source": "USM",
                        },
                    }
                ],
            }
        raise FileNotFoundError("USM virtual directory not found")

    def ensure_video(
        self,
        record: dict,
        internal_path: str,
        read_source: Callable[[], bytes],
    ) -> Path:
        if self._normalize_path(internal_path) != self.virtual_path(record):
            raise FileNotFoundError("USM video entry not found")

        target, meta_path = self._cache_paths(record)
        identity = self._cache_identity(record)
        with _CONVERSION_LOCK:
            if self._is_cache_hit(target, meta_path, identity):
                return target
            self._publish_conversion(target, meta_path, identity, read_source())
        return target

    def _cache_paths(self, record: dict) -> tuple[Path, Path]:
        root = self._cache_root / str(record["id"]) / "video"
        name = f"{Path(str(record['file_name'])).stem}.mp4"
        return root / name, root / "video_meta.json"

    def _cache_identity(self, record: dict) -> dict:
        return {
            "version": self._cache_version,
            "source": {
                "recordId": int(record["id"]),
                "length": int(record["length"]),
                "offset": int(record.get("offset") or 0),
                "fileDataMd5": str(record.get("file_data_md5") or ""),
                "fileChunkMd5": str(record.get("file_chunk_md5") or ""),
            },
            "tools": {
                "usmConvert": self._tool_identity(self._usm_convert),
                "ffmpeg": self._tool_identity(self._ffmpeg),
            },
        }

    @staticmethod
    def _tool_identity(command: str | Path | None) -> dict | None:
        if command is None:
            return None
        path = Path(str(command))
        if not path.is_file():
            return {"path": str(command), "available": False}
        stat = path.stat()
        return {
            "path": str(path.resolve()),
            "available": True,
            "size": stat.st_size,
            "mtimeNs": stat.st_mtime_ns,
        }

    @staticmethod
    def _is_cache_hit(target: Path, meta_path: Path, identity: dict) -> bool:
        if not target.is_file() or not meta_path.is_file() or target.stat().st_size <= 0:
            return False
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return meta.get("identity") == identity

    def _publish_conversion(
        self,
        target: Path,
        meta_path: Path,
        identity: dict,
        data: bytes,
    ) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        run_id = f"{os.getpid()}.{time.time_ns()}.{uuid.uuid4().hex}"
        temporary_video = target.with_name(
            f".{target.stem}.{run_id}.tmp{target.suffix}"
        )
        temporary_meta = meta_path.with_name(f".{meta_path.name}.{run_id}.tmp")
        try:
            self._converter(
                data,
                temporary_video,
                usm_convert=self._usm_convert,
                ffmpeg=self._ffmpeg,
            )
            if not temporary_video.is_file() or temporary_video.stat().st_size <= 0:
                raise RuntimeError("USM conversion completed without producing an MP4")
            temporary_meta.write_text(
                json.dumps(
                    {
                        "identity": identity,
                        "builtAtEpoch": int(time.time()),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            os.replace(temporary_video, target)
            os.replace(temporary_meta, meta_path)
        finally:
            temporary_video.unlink(missing_ok=True)
            temporary_meta.unlink(missing_ok=True)

    @staticmethod
    def _normalize_path(value: str) -> str:
        return unquote(value).replace("\\", "/").strip("/")
