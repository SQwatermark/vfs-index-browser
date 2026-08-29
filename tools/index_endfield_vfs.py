#!/usr/bin/env python3
"""Build a logical file index from Endfield .blc/.chk VFS metadata.

Imported from the desktop research baseline on 2026-08-29. The normalized source
was preserved before productization; parser changes require fixture and real-data
validation in this repository.

The parser mirrors the Endfield VFS layout verified against
Variante/endfield_research_kit and Variante/AnimeStudio, but intentionally
stops at the VFS boundary. It does not decode Table SparkBuffer, Wwise PCK, CRI,
Unity AssetBundle, or other second-level formats.

Typical use:
  python index_endfield_vfs.py ^
    --game-data-root "D:/Hypergryph Launcher/games/Endfield Game/Endfield_Data" ^
    --output endfield-vfs-index.jsonl ^
    --summary endfield-vfs-summary.json
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


CHACHA_KEY = bytes.fromhex(
    "e95b317ac4f828569d23a86bf271dcb53e846fa75c924d671dba8e38f4ca52e1"
)
VFS_PROTO_VERSION = 3
BLOCK_HEAD_LEN = 12


BLOCK_TYPE_NAMES: dict[int, str] = {
    0: "None",
    1: "InitAudio",
    2: "InitBundle",
    3: "InitialExtendData",
    4: "BundleManifest",
    5: "IFixPatchOut",
    6: "AuditStreaming",
    7: "AuditDynamicStreaming",
    8: "AuditIV",
    9: "AuditAudio",
    10: "AuditVideo",
    11: "Bundle",
    12: "Audio",
    13: "Video",
    14: "IV",
    15: "Streaming",
    16: "DynamicStreaming",
    17: "Lua",
    18: "Table",
    19: "JsonData",
    20: "ExtendData",
    21: "HotfixAudio",
    101: "AudioChinese",
    102: "AudioEnglish",
    103: "AudioJapanese",
    104: "AudioKorean",
}

KNOWN_BLOCK_HASHES: dict[str, str] = {
    "07A1BB91": "InitAudio",
    "0CE8FA57": "InitBundle",
    "3C9D9D2D": "InitialExtendData",
    "1CDDBF1F": "BundleManifest",
    "DAFE52C9": "IFixPatchOut",
    "6432320A": "AuditStreaming",
    "B9358E30": "AuditDynamicStreaming",
    "06223FE2": "AuditIV",
    "1EBAF5C6": "AuditAudio",
    "2E6CE44D": "AuditVideo",
    "7064D8E2": "Bundle",
    "24ED34CF": "Audio",
    "55FC21C6": "Video",
    "A63D7E6A": "IV",
    "C3442D43": "Streaming",
    "23D53F5D": "DynamicStreaming",
    "19E3AE45": "Lua",
    "42A8FCA6": "Table",
    "775A31D1": "JsonData",
    "D6E622F7": "ExtendData",
    "F151B649": "HotfixAudio",
    "E1E7D7CE": "AudioChinese",
    "A31457D0": "AudioEnglish",
    "F668D4EE": "AudioJapanese",
    "E9D31017": "AudioKorean",
    "F84BF5E6": "Terrain",
}

FILE_TAG_NAMES = {
    0: "None",
    1: "Audit",
}


@dataclass
class VfsFileInfo:
    file_name: str
    file_name_hash: int
    file_chunk_md5: int
    file_data_md5: int
    offset: int
    length: int
    block_type: int
    use_encrypt: bool
    iv_seed: int
    file_tag: int


@dataclass
class VfsChunkInfo:
    md5_name: int
    content_md5: int
    length: int
    block_type: int
    main_tag: int
    files: list[VfsFileInfo] = field(default_factory=list)

    @property
    def chk_file_name(self) -> str:
        return self.md5_name.to_bytes(16, "little", signed=False).hex().upper() + ".chk"


@dataclass
class VfsBlockInfo:
    version: int
    group_cfg_name: str
    group_cfg_hash_name: int
    group_file_info_num: int
    group_chunks_length: int
    block_type: int
    code_version: int
    chunks: list[VfsChunkInfo] = field(default_factory=list)


class Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, size: int) -> bytes:
        end = self.pos + size
        if end > len(self.data):
            raise ValueError(f"short read at {self.pos}: want {size}, have {len(self.data) - self.pos}")
        out = self.data[self.pos:end]
        self.pos = end
        return out

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return struct.unpack("<H", self.take(2))[0]

    def i32(self) -> int:
        return struct.unpack("<i", self.take(4))[0]

    def i64(self) -> int:
        return struct.unpack("<q", self.take(8))[0]

    def u128(self) -> int:
        return int.from_bytes(self.take(16), "little", signed=False)

    def utf8(self, size: int) -> str:
        return self.take(size).decode("utf-8")


def rotl32(value: int, bits: int) -> int:
    return ((value << bits) & 0xFFFFFFFF) | (value >> (32 - bits))


def quarter_round(state: list[int], a: int, b: int, c: int, d: int) -> None:
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] = rotl32(state[d] ^ state[a], 16)
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] = rotl32(state[b] ^ state[c], 12)
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] = rotl32(state[d] ^ state[a], 8)
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] = rotl32(state[b] ^ state[c], 7)


def chacha20_apply(key: bytes, nonce12: bytes, counter: int, data: bytes) -> bytes:
    if len(key) != 32:
        raise ValueError("ChaCha20 key must be 32 bytes")
    if len(nonce12) != 12:
        raise ValueError("ChaCha20 nonce must be 12 bytes")

    constants = (0x61707865, 0x3320646E, 0x79622D32, 0x6B206574)
    key_words = list(struct.unpack("<8I", key))
    nonce_words = list(struct.unpack("<3I", nonce12))
    output = bytearray(data)
    offset = 0
    block_counter = counter & 0xFFFFFFFF
    nonce0 = nonce_words[0]

    while offset < len(output):
        state = [
            *constants,
            *key_words,
            block_counter,
            nonce0,
            nonce_words[1],
            nonce_words[2],
        ]
        working = state.copy()
        for _ in range(10):
            quarter_round(working, 0, 4, 8, 12)
            quarter_round(working, 1, 5, 9, 13)
            quarter_round(working, 2, 6, 10, 14)
            quarter_round(working, 3, 7, 11, 15)
            quarter_round(working, 0, 5, 10, 15)
            quarter_round(working, 1, 6, 11, 12)
            quarter_round(working, 2, 7, 8, 13)
            quarter_round(working, 3, 4, 9, 14)
        block = struct.pack("<16I", *((working[i] + state[i]) & 0xFFFFFFFF for i in range(16)))
        for i, value in enumerate(block[: len(output) - offset]):
            output[offset + i] ^= value
        offset += 64
        block_counter = (block_counter + 1) & 0xFFFFFFFF
        if block_counter == 0:
            nonce0 = (nonce0 + 1) & 0xFFFFFFFF

    return bytes(output)


def decrypt_blc(path: Path) -> bytes:
    raw = path.read_bytes()
    if len(raw) < BLOCK_HEAD_LEN:
        raise ValueError("blc file too short")
    return chacha20_apply(CHACHA_KEY, raw[:BLOCK_HEAD_LEN], 1, raw[BLOCK_HEAD_LEN:])


def parse_block_info(decrypted: bytes, verify_crc: bool = True) -> VfsBlockInfo:
    if len(decrypted) < 4:
        raise ValueError("decrypted .blc too short")
    if verify_crc:
        expected = struct.unpack("<I", decrypted[-4:])[0]
        actual = zlib.crc32(decrypted[:-4]) & 0xFFFFFFFF
        if expected != actual:
            raise ValueError(f"CRC mismatch: expected 0x{expected:08X}, actual 0x{actual:08X}")

    reader = Reader(decrypted)
    raw_version = reader.i32()
    if raw_version < 11:
        code_version = raw_version
        version = reader.i32()
    else:
        code_version = 3
        version = raw_version

    group_name = reader.utf8(reader.u16())
    block = VfsBlockInfo(
        version=version,
        group_cfg_name=group_name,
        group_cfg_hash_name=reader.i64(),
        group_file_info_num=reader.i32(),
        group_chunks_length=reader.i64(),
        block_type=reader.u8(),
        code_version=code_version,
    )

    chunk_count = reader.i32()
    if chunk_count < 0:
        raise ValueError(f"negative chunk count: {chunk_count}")
    for _ in range(chunk_count):
        chunk = VfsChunkInfo(
            md5_name=reader.u128(),
            content_md5=reader.u128(),
            length=reader.i64(),
            block_type=reader.u8(),
            main_tag=(reader.i32() & 0xFF) if code_version > 3 else 0,
        )
        file_count = reader.i32()
        if file_count < 0:
            raise ValueError(f"negative file count: {file_count}")
        for _file_index in range(file_count):
            file_name = reader.utf8(reader.u16())
            use_encrypt = False
            file = VfsFileInfo(
                file_name=file_name,
                file_name_hash=reader.i64(),
                file_chunk_md5=reader.u128(),
                file_data_md5=reader.u128(),
                offset=reader.i64(),
                length=reader.i64(),
                block_type=reader.u8(),
                use_encrypt=False,
                iv_seed=0,
                file_tag=0,
            )
            use_encrypt = reader.u8() != 0
            file.use_encrypt = use_encrypt
            if use_encrypt:
                file.iv_seed = reader.i64()
            if code_version > 3:
                file.file_tag = reader.i32() & 0xFF
            chunk.files.append(file)
        block.chunks.append(chunk)

    return block


def uint128_hex(value: int) -> str:
    return f"{value:032X}"


def source_roots(args: argparse.Namespace) -> list[tuple[str, Path]]:
    if args.game_data_root:
        game_root = args.game_data_root
        roots = [
            ("StreamingAssets", game_root / "StreamingAssets"),
            ("Persistent", game_root / "Persistent"),
        ]
    else:
        roots = []
        if args.streaming_assets:
            roots.append(("StreamingAssets", args.streaming_assets))
        if args.persistent_assets:
            roots.append(("Persistent", args.persistent_assets))
    return [(name, path) for name, path in roots if path.exists()]


def selected_blocks(vfs_root: Path, block_filter: set[str] | None) -> list[Path]:
    dirs = [path for path in vfs_root.iterdir() if path.is_dir()]
    if not block_filter:
        return sorted(dirs, key=lambda p: p.name.upper())

    normalized = {item.lower().replace("-", "").replace("_", "") for item in block_filter}
    out = []
    for path in dirs:
        known = KNOWN_BLOCK_HASHES.get(path.name.upper(), "")
        candidates = {
            path.name.lower(),
            known.lower().replace("-", "").replace("_", ""),
        }
        if candidates & normalized:
            out.append(path)
    return sorted(out, key=lambda p: p.name.upper())


def should_emit_file(file_name: str, regexes: list[re.Pattern[str]]) -> bool:
    if not file_name or file_name.endswith(("/", "\\")):
        return False
    return not regexes or any(pattern.search(file_name) for pattern in regexes)


def index_source(
    source_name: str,
    source_root: Path,
    writer: Any,
    args: argparse.Namespace,
    regexes: list[re.Pattern[str]],
) -> dict[str, Any]:
    vfs_root = source_root / "VFS"
    summary: dict[str, Any] = {
        "source": source_name,
        "sourceRoot": str(source_root),
        "vfsRoot": str(vfs_root),
        "blockCount": 0,
        "chunkCount": 0,
        "fileCount": 0,
        "selectedFileCount": 0,
        "encryptedFileCount": 0,
        "missingChunkCount": 0,
        "parseErrorCount": 0,
        "missingBlcCount": 0,
        "byteCount": 0,
        "blocks": {},
    }
    if not vfs_root.exists():
        summary["missingVfsRoot"] = True
        return summary

    for block_dir in selected_blocks(vfs_root, set(args.block_type or []) or None):
        block_hash = block_dir.name.upper()
        blc_path = block_dir / f"{block_dir.name}.blc"
        if not blc_path.exists():
            summary["missingBlcCount"] += 1
            writer.write(json.dumps({
                "recordType": "missingBlockMetadata",
                "source": source_name,
                "blockHash": block_hash,
                "knownBlockName": KNOWN_BLOCK_HASHES.get(block_hash),
                "blockDirectory": str(block_dir),
            }, ensure_ascii=False, separators=(",", ":")) + "\n")
            continue

        try:
            block = parse_block_info(decrypt_blc(blc_path), verify_crc=not args.no_crc)
        except Exception as exc:
            summary["parseErrorCount"] += 1
            writer.write(json.dumps({
                "recordType": "blockParseError",
                "source": source_name,
                "blockHash": block_hash,
                "knownBlockName": KNOWN_BLOCK_HASHES.get(block_hash),
                "blcPath": str(blc_path),
                "error": f"{type(exc).__name__}: {exc}",
            }, ensure_ascii=False, separators=(",", ":")) + "\n")
            continue

        block_name = BLOCK_TYPE_NAMES.get(block.block_type) or KNOWN_BLOCK_HASHES.get(block_hash) or block.group_cfg_name
        block_stats = {
            "blockName": block_name,
            "blockHash": block_hash,
            "chunkCount": len(block.chunks),
            "fileCount": sum(len(chunk.files) for chunk in block.chunks),
            "selectedFileCount": 0,
            "encryptedFileCount": 0,
            "missingChunkCount": 0,
            "byteCount": 0,
        }
        summary["blockCount"] += 1
        summary["chunkCount"] += len(block.chunks)
        summary["fileCount"] += block_stats["fileCount"]

        writer.write(json.dumps({
            "recordType": "block",
            "source": source_name,
            "sourceRoot": str(source_root),
            "blockHash": block_hash,
            "blockName": block_name,
            "groupConfigName": block.group_cfg_name,
            "groupConfigHashName": block.group_cfg_hash_name,
            "blockTypeId": block.block_type,
            "codeVersion": block.code_version,
            "version": block.version,
            "declaredFileCount": block.group_file_info_num,
            "declaredChunkBytes": block.group_chunks_length,
            "chunkCount": len(block.chunks),
            "fileCount": block_stats["fileCount"],
            "blcPath": str(blc_path),
        }, ensure_ascii=False, separators=(",", ":")) + "\n")

        for chunk_index, chunk in enumerate(block.chunks):
            chunk_path = block_dir / chunk.chk_file_name
            chunk_exists = chunk_path.exists()
            if not chunk_exists:
                summary["missingChunkCount"] += 1
                block_stats["missingChunkCount"] += 1
            writer.write(json.dumps({
                "recordType": "chunk",
                "source": source_name,
                "blockHash": block_hash,
                "blockName": block_name,
                "chunkIndex": chunk_index,
                "chunkId": f"{source_name}/{block_name}/{uint128_hex(chunk.md5_name)}",
                "chunkFile": chunk.chk_file_name,
                "chunkPath": str(chunk_path),
                "exists": chunk_exists,
                "chunkMd5Name": uint128_hex(chunk.md5_name),
                "contentMd5": uint128_hex(chunk.content_md5),
                "length": chunk.length,
                "blockTypeId": chunk.block_type,
                "blockType": BLOCK_TYPE_NAMES.get(chunk.block_type, "Raw"),
                "tag": FILE_TAG_NAMES.get(chunk.main_tag, str(chunk.main_tag)),
                "fileCount": len(chunk.files),
            }, ensure_ascii=False, separators=(",", ":")) + "\n")

            for file in chunk.files:
                if file.use_encrypt:
                    summary["encryptedFileCount"] += 1
                    block_stats["encryptedFileCount"] += 1
                if not should_emit_file(file.file_name, regexes):
                    continue
                summary["selectedFileCount"] += 1
                summary["byteCount"] += file.length
                block_stats["selectedFileCount"] += 1
                block_stats["byteCount"] += file.length
                logical_path = file.file_name.replace("\\", "/")
                writer.write(json.dumps({
                    "recordType": "file",
                    "source": source_name,
                    "sourceRoot": str(source_root),
                    "blockHash": block_hash,
                    "blockName": block_name,
                    "logicalId": f"{block_name}/{logical_path.lstrip('/')}",
                    "sourceLogicalId": f"{source_name}/{block_name}/{logical_path.lstrip('/')}",
                    "fileName": logical_path,
                    "fileNameHash": file.file_name_hash,
                    "fileBlockTypeId": file.block_type,
                    "fileBlockType": BLOCK_TYPE_NAMES.get(file.block_type, "Raw"),
                    "fileTag": FILE_TAG_NAMES.get(file.file_tag, str(file.file_tag)),
                    "chunkFile": chunk.chk_file_name,
                    "chunkPath": str(chunk_path),
                    "chunkExists": chunk_exists,
                    "chunkMd5Name": uint128_hex(chunk.md5_name),
                    "chunkContentMd5": uint128_hex(chunk.content_md5),
                    "fileChunkMd5": uint128_hex(file.file_chunk_md5),
                    "fileDataMd5": uint128_hex(file.file_data_md5),
                    "offset": file.offset,
                    "length": file.length,
                    "encrypted": file.use_encrypt,
                    "ivSeed": file.iv_seed,
                }, ensure_ascii=False, separators=(",", ":")) + "\n")

        summary["blocks"][block_name] = block_stats
        if args.progress_every and summary["blockCount"] % args.progress_every == 0:
            print(
                f"[{source_name}] indexed {summary['blockCount']} blocks, "
                f"{summary['selectedFileCount']} files",
                file=sys.stderr,
                flush=True,
            )

    return summary


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-data-root", type=Path, help="Path to Endfield_Data")
    parser.add_argument("--streaming-assets", type=Path, help="Path to StreamingAssets")
    parser.add_argument("--persistent-assets", type=Path, help="Path to Persistent")
    parser.add_argument("--output", required=True, type=Path, help="Output JSONL path")
    parser.add_argument("--summary", required=True, type=Path, help="Output summary JSON path")
    parser.add_argument("--block-type", action="append", help="Filter by block hash or block name; may repeat")
    parser.add_argument("--file-regex", action="append", default=[], help="Only emit matching file records; may repeat")
    parser.add_argument("--no-crc", action="store_true", help="Skip .blc CRC verification")
    parser.add_argument("--progress-every", type=int, default=4, help="Print progress every N parsed blocks")
    args = parser.parse_args(list(argv))
    if not args.game_data_root and not (args.streaming_assets or args.persistent_assets):
        parser.error("provide --game-data-root or at least one source root")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    roots = source_roots(args)
    if not roots:
        raise SystemExit("no existing source roots found")

    regexes = [re.compile(pattern, re.IGNORECASE) for pattern in args.file_regex]
    started = time.time()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    summaries = []

    with args.output.open("w", encoding="utf-8", newline="\n") as writer:
        writer.write(json.dumps({
            "recordType": "header",
            "format": "endfield-vfs-index",
            "schemaVersion": 1,
            "generatedAtEpoch": int(started),
            "sources": [{"name": name, "root": str(path)} for name, path in roots],
            "blockFilter": args.block_type or [],
            "fileRegex": args.file_regex,
            "scope": "vfs-boundary-only",
        }, ensure_ascii=False, separators=(",", ":")) + "\n")
        for source_name, source_root in roots:
            summaries.append(index_source(source_name, source_root, writer, args, regexes))
        writer.write(json.dumps({
            "recordType": "summary",
            "elapsedSeconds": round(time.time() - started, 3),
            "sources": summaries,
        }, ensure_ascii=False, separators=(",", ":")) + "\n")

    totals = {
        "blockCount": sum(item.get("blockCount", 0) for item in summaries),
        "chunkCount": sum(item.get("chunkCount", 0) for item in summaries),
        "fileCount": sum(item.get("fileCount", 0) for item in summaries),
        "selectedFileCount": sum(item.get("selectedFileCount", 0) for item in summaries),
        "encryptedFileCount": sum(item.get("encryptedFileCount", 0) for item in summaries),
        "missingChunkCount": sum(item.get("missingChunkCount", 0) for item in summaries),
        "parseErrorCount": sum(item.get("parseErrorCount", 0) for item in summaries),
        "missingBlcCount": sum(item.get("missingBlcCount", 0) for item in summaries),
        "byteCount": sum(item.get("byteCount", 0) for item in summaries),
    }
    payload = {
        "kind": "EndfieldVfsIndexSummary",
        "generatedAtEpoch": int(started),
        "elapsedSeconds": round(time.time() - started, 3),
        "output": str(args.output),
        "totals": totals,
        "sources": summaries,
        "notes": [
            "This index records VFS logical file boundaries only.",
            "Second-level formats such as SparkBuffer tables, AKPK/PCK audio, CRI/USM, and Unity bundles are intentionally not decoded here.",
        ],
    }
    args.summary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "summary": str(args.summary), "totals": totals}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
