"""Assemble ordinary VFS file preview documents above physical file reads."""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Callable

from file_preview_service import (
    AUDIO_EXTENSIONS,
    CONTAINER_EXTENSIONS,
    IMAGE_EXTENSIONS,
    PREVIEW_BINARY_LIMIT,
    PREVIEW_TEXT_LIMIT,
    TEXT_EXTENSIONS,
    VIDEO_EXTENSIONS,
    FilePreviewService,
    decode_text,
    file_suffix,
    hex_preview,
    truncate_text,
)
from sparkbuffer import SparkBufferError


class VfsFilePreviewService:
    def __init__(
        self,
        read_slice: Callable[[dict, Path, int | None], bytes],
        parse_tablecfg: Callable[[dict, Path], tuple[dict, bytes]],
        decode_memorypack: Callable[[dict, Path], tuple[str, bool, dict] | None],
    ) -> None:
        self._read_slice = read_slice
        self._parse_tablecfg = parse_tablecfg
        self._decode_memorypack = decode_memorypack

    def build(
        self,
        file_id: int,
        original: dict,
        record: dict,
        chunk_path: Path,
    ) -> dict:
        suffix = file_suffix(record["file_name"])
        raw_url = f"/api/raw?id={file_id}"
        base = {
            "file": original,
            "resolvedFile": record,
            "usedFallback": original["id"] != record["id"],
            "rawUrl": raw_url,
            "downloadUrl": f"{raw_url}&download=1",
        }
        if suffix in CONTAINER_EXTENSIONS:
            return {**base, "kind": "container", "message": _container_message(suffix)}
        if suffix in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS:
            return FilePreviewService.build(
                base,
                record["file_name"],
                int(record["length"]),
                lambda limit: self._read_slice(record, chunk_path, limit),
            )

        table_name = tablecfg_name_for_file(record["file_name"])
        if table_name:
            return self._build_tablecfg(base, file_id, table_name, record, chunk_path)

        limit = PREVIEW_TEXT_LIMIT if suffix in TEXT_EXTENSIONS else PREVIEW_BINARY_LIMIT
        data = self._read_slice(record, chunk_path, limit)
        text, encoding = decode_text(data)
        truncated = int(record["length"]) > len(data)
        if suffix == ".json":
            if text is not None and text.lstrip().startswith(("{", "[")):
                try:
                    text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
                except json.JSONDecodeError:
                    pass
                return {
                    **base,
                    "kind": "text",
                    "encoding": encoding,
                    "text": text,
                    "truncated": truncated,
                }
            return self._build_binary_json(
                base,
                record,
                chunk_path,
                data,
                encoding,
                truncated,
            )
        document = FilePreviewService.build(
            base,
            record["file_name"],
            int(record["length"]),
            lambda requested: data[:requested],
        )
        if document["kind"] == "hex":
            document["message"] = "该文件不是可直接显示的文本，当前展示解密后的前段十六进制内容。"
        return document

    def _build_tablecfg(
        self,
        base: dict,
        file_id: int,
        table_name: str,
        record: dict,
        chunk_path: Path,
    ) -> dict:
        try:
            parsed, json_data = self._parse_tablecfg(record, chunk_path)
        except (SparkBufferError, struct.error, UnicodeDecodeError, ValueError) as error:
            data = self._read_slice(record, chunk_path, PREVIEW_BINARY_LIMIT)
            return {
                **base,
                "kind": "hex",
                "hex": hex_preview(data),
                "truncated": int(record["length"]) > len(data),
                "message": f"`{table_name}` 已完成 VFS 解密，但 SparkBuffer 解析失败：{error}",
            }
        text, truncated = truncate_text(json_data.decode("utf-8"))
        json_url = f"/api/tablecfg/json?id={file_id}"
        return {
            **base,
            "kind": "text",
            "encoding": "sparkbuffer-json",
            "text": text,
            "truncated": truncated,
            "convertedRawUrl": json_url,
            "convertedDownloadUrl": f"{json_url}&download=1",
            "message": f"`{table_name}` 已从本地 VFS 解密 bytes 解析为 SparkBuffer JSON。",
            "tableCfg": {"fileName": table_name, "rootName": parsed.get("name")},
        }

    def _build_binary_json(
        self,
        base: dict,
        record: dict,
        chunk_path: Path,
        data: bytes,
        encoding: str | None,
        truncated: bool,
    ) -> dict:
        error_message = None
        try:
            decoded = self._decode_memorypack(record, chunk_path)
        except RuntimeError as error:
            decoded = None
            error_message = str(error)
        if decoded is not None:
            text, decoded_truncated, meta = decoded
            return {
                **base,
                "kind": "text",
                "encoding": "memorypack-json",
                "text": text,
                "truncated": decoded_truncated,
                "message": (
                    "该 .json 文件已从本地 VFS 解密内容解析为 schema-based MemoryPack JSON。"
                    f" 已消费 {meta['consumed']} / {meta['bytes']} bytes。"
                ),
                "memoryPack": meta,
            }
        probe = binary_json_probe(data, int(record["length"]))
        return {
            **base,
            "kind": "binaryJson",
            "encoding": encoding,
            "probe": probe,
            "hex": hex_preview(data),
            "truncated": truncated,
            "message": (
                f"{probe['note']}\nMemoryPack 解码未完成：{error_message}"
                if error_message
                else probe["note"]
            ),
        }


def tablecfg_name_for_file(file_name: str) -> str | None:
    normalized = file_name.replace("\\", "/").strip("/")
    prefix = "Data/TableCfg/"
    suffix = ".bytes"
    if not normalized.startswith(prefix) or not normalized.endswith(suffix):
        return None
    name = normalized[len(prefix) : -len(suffix)]
    return name if name and "/" not in name else None


def binary_json_probe(data: bytes, full_length: int) -> dict:
    return {
        "formatHint": "schema-based binary JSON",
        "confidence": "medium",
        "firstByte": data[0] if data else None,
        "possibleMemberCount": data[0] if data else None,
        "fullLength": full_length,
        "sampleLength": len(data),
        "lengthPrefixedStrings": _length_prefixed_utf8_strings(data),
        "note": "VFS 解密已完成；该 .json 内容疑似按类型 schema 顺序写入的二进制配置，需要字段 schema 才能完整还原。",
    }


def _length_prefixed_utf8_strings(
    data: bytes,
    max_offset: int = 8192,
    max_count: int = 40,
) -> list[dict]:
    strings = []
    for offset in range(min(max(len(data) - 4, 0), max_offset)):
        length = int.from_bytes(data[offset : offset + 4], "little")
        if length < 4 or length > 160 or offset + 4 + length > len(data):
            continue
        raw = data[offset + 4 : offset + 4 + length]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if not text or any(ord(char) < 32 and char not in "\t\r\n" for char in text):
            continue
        strings.append({"offset": offset, "length": length, "text": text})
        if len(strings) >= max_count:
            break
    return strings


def _container_message(suffix: str) -> str:
    return {
        ".ab": "这是 Unity/AssetBundle 容器。可以下载原始 .ab，也可以点击“查看内部结构”按需导出并预览 Texture2D、Sprite、TextAsset 等资源。",
        ".pck": "这是音频 PCK 容器。VFS 层可以下载原始 PCK；单条语音需要继续解析 PCK/AKPK/WEM。",
        ".usm": "这是 CRI/USM 视频容器。可以下载原始 .usm，也可以点击“查看内部结构”按需转换为 MP4 预览。",
    }.get(suffix, "这是二级容器文件，可以下载；内部解析尚未接入。")
