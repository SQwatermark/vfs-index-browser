#!/usr/bin/env python3
"""Serve a small local browser for the Endfield VFS JSONL index."""

from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import io
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import tarfile
import threading
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Iterable, Iterator
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

import blender_material_plan
from ability_entity_data import (
    AbilityEntityDecodeError,
    AbilityEntityNotFoundError,
    AbilityEntityUnavailableError,
    list_ability_entity_ids,
    normalize_ability_entity_id,
    parse_ability_entity_template,
    select_ability_entity_asset,
)
from audio_dialog_store import get_audio_dialog_entry, list_audio_dialog_directory
from wwise_store import (
    get_wwise_bank,
    get_wwise_event,
    get_wwise_media,
    list_wwise_banks,
    list_wwise_events,
    list_wwise_media,
    list_wwise_media_prefixes,
    wwise_summary,
)
from sparkbuffer import SparkBufferError, parse_sparkbuffer
from usm import UsmError, convert_usm_to_mp4
from manifest_index import ManifestIndex
from npc_avatar_resources import build_avatar_mesh_resource_plan
from avatar_mesh_snapshot import (
    load_exported_objects,
    material_texture_selections,
    selected_container_paths,
)
from npc_avatar_model import build_static_avatar_mesh_document
from string_path_hash import StringPathHashIndex
from npc_avatar_config import (
    attach_resolved_paths,
    is_avatar_mesh_asset_path,
    parse_avatar_mesh,
    summarize_avatar_mesh,
)
from animestudio_model import (
    attach_mesh_geometry,
    attach_texture_images,
    build_hierarchy_document,
    collect_material_textures,
    find_container_root_game_object,
    load_animestudio_objects,
)
from model_document import validate_model_document
from gltf_export import build_glb
from material_semantic_plans import CHARACTER_NPR_PATH, build_blender_material_plans
from animestudio_animation import (
    MODEL_ANIMATION_CACHE_REVISION,
    attach_animation_clip,
    bind_animation_clip,
    load_unique_animation_clip,
)
from skeletal_morph import (
    bake_morph_animation,
    is_dialog_morph_animation_path,
    merge_morph_avatars,
    morph_avatar_asset_names,
    morph_clip_asset_path,
    parse_morph_avatar,
    parse_morph_clip,
)
from projectile_data import (
    ProjectileDecodeError,
    ProjectileNotFoundError,
    ProjectileUnavailableError,
    load_projectile_export,
    list_projectile_ids,
    normalize_projectile_id,
    select_projectile_asset,
)
from unity_worker import UnityWorkerClient, UnityWorkerError
from task_registry import BackgroundTaskRegistry, TaskNotFoundError

try:
    from tools.decode_memorypack_json import DecodeError, Decoder, MemoryPackReader, SchemaIndex, infer_class
except ImportError:
    DecodeError = Decoder = MemoryPackReader = SchemaIndex = None

    def infer_class(logical_id: str | None) -> str | None:
        return None


PROJECT_ROOT = Path(__file__).resolve().parent
UNITY_WORKER = UnityWorkerClient.discover(PROJECT_ROOT)


@dataclass(frozen=True)
class AnimationExportIssue:
    asset_index: int
    path: str
    stage: str
    message: str

    def as_json(self) -> dict:
        return {
            "assetIndex": self.asset_index,
            "path": self.path,
            "stage": self.stage,
            "message": self.message,
        }


@dataclass(frozen=True)
class AnimatedModelBundle:
    asset: dict
    animations: list[dict]
    model_path: Path
    glb_path: Path
    issues: list[AnimationExportIssue]


DEFAULT_INDEX = (
    PROJECT_ROOT.parent
    / "Endaxis"
    / "zmd-research"
    / "analysis"
    / "database"
    / "facts"
    / "endfield-vfs-index-20260727-234026.jsonl.tgz"
)
DEFAULT_DB = PROJECT_ROOT / "data" / "endfield-vfs-index.sqlite"
AUDIO_DIALOG_DB = Path(
    os.environ.get(
        "VFS_BROWSER_AUDIO_DIALOG_DB",
        PROJECT_ROOT / "data" / "audio-dialog-index.sqlite",
    )
)
WWISE_DB = Path(
    os.environ.get(
        "VFS_BROWSER_WWISE_DB",
        PROJECT_ROOT / "data" / "wwise-index.sqlite",
    )
)
PUBLIC_DIR = PROJECT_ROOT / "public"
INTERNAL_CACHE_DIR = Path(os.environ.get("VFS_BROWSER_INTERNAL_CACHE", PROJECT_ROOT / "data" / "internal-cache"))
TASKS = BackgroundTaskRegistry(lambda: INTERNAL_CACHE_DIR / "tasks")
SHADER_ARCHIVE_ROOT = Path(
    os.environ.get(
        "VFS_BROWSER_SHADER_ARCHIVE_ROOT",
        PROJECT_ROOT / "data" / "shader-archives" / "1.4.4",
    )
)
BUNDLED_ANIMESTUDIO_CLI = (
    PROJECT_ROOT / "tools" / "AnimeStudio.CLI-633f30c" / "AnimeStudio.CLI.exe"
)


def default_animestudio_cli() -> Path:
    """Prefer the packaged CLI, then a local research build with Endfield decoders."""

    if BUNDLED_ANIMESTUDIO_CLI.is_file():
        return BUNDLED_ANIMESTUDIO_CLI
    candidates = list(
        (
            PROJECT_ROOT
            / "data"
            / "research"
            / "AnimeStudio"
            / "AnimeStudio.CLI"
            / "bin"
            / "Release"
        ).glob("net*-windows/AnimeStudio.CLI.exe")
    )
    return max(candidates, key=lambda path: path.stat().st_mtime_ns) if candidates else BUNDLED_ANIMESTUDIO_CLI


ANIMESTUDIO_CLI = Path(
    os.environ.get("VFS_BROWSER_ANIMESTUDIO_CLI", default_animestudio_cli())
)
# 调试时可以分别覆盖特定导出链路，生产环境统一使用已验证的打包构建。
ANIMESTUDIO_CUBEMAP_CLI = Path(
    os.environ.get("VFS_BROWSER_ANIMESTUDIO_CUBEMAP_CLI", ANIMESTUDIO_CLI)
)
VGMSTREAM_CLI = Path(
    os.environ.get(
        "VGMSTREAM_CLI",
        PROJECT_ROOT / "tools" / "vgmstream" / "vgmstream-cli.exe",
    )
)
USM_CONVERT = Path(os.environ.get("USM_CONVERT", PROJECT_ROOT / "tools" / "usm-convert.exe"))
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")


def executable_diagnostic(name: str, configured: str | Path) -> dict:
    """解析可选命令，但不启动进程或隐式下载依赖。"""

    raw = str(configured)
    explicit = Path(raw)
    resolved = explicit.resolve() if explicit.is_file() else None
    if resolved is None and explicit.name == raw:
        discovered = shutil.which(raw)
        resolved = Path(discovered).resolve() if discovered else None
    return {
        "name": name,
        "configured": raw,
        "available": resolved is not None,
        "resolvedPath": str(resolved) if resolved is not None else None,
    }


def build_health_document() -> dict:
    """汇总运行时能力；可选工具缺失不影响核心服务存活状态。"""

    worker = UNITY_WORKER.diagnose([
        "decodeProjectileComponent",
        "exportMonoBehaviourRaw",
        "exportMonoBehaviourTypeTreeDump",
        "buildAssetMap",
        "buildCabMap",
        "exportObjectSnapshots",
        "exportIdentifiedTextures",
    ])
    return {
        "apiVersion": 1,
        "status": "ready" if worker["status"] == "ready" else "degraded",
        "unityWorker": worker,
        "optionalTools": [
            executable_diagnostic("blender", BLENDER_EXE),
            executable_diagnostic("vgmstream", VGMSTREAM_CLI),
            executable_diagnostic("usm-convert", USM_CONVERT),
            executable_diagnostic("ffmpeg", FFMPEG),
        ],
        "legacyTools": [
            {
                **executable_diagnostic("AnimeStudio.CLI", ANIMESTUDIO_CLI),
                "requiredByUnmigratedPaths": True,
            }
        ],
    }


def unity_worker_is_unavailable(error: UnityWorkerError) -> bool:
    """区分运行环境不可用与输入或领域数据不可解码。"""

    return error.code in {
        "worker_not_found",
        "worker_timeout",
        "invalid_worker_response",
        "request_id_mismatch",
    }


def find_blender_executable() -> Path:
    configured = os.environ.get("BLENDER_EXE") or shutil.which("blender")
    if configured:
        return Path(configured)
    install_root = Path(r"C:\Program Files\Blender Foundation")
    installed = sorted(
        install_root.glob("Blender */blender.exe"),
        key=lambda path: tuple(
            int(value) for value in re.findall(r"\d+", path.parent.name)
        ),
        reverse=True,
    )
    return installed[0] if installed else install_root / "Blender 4.3" / "blender.exe"


BLENDER_EXE = find_blender_executable()
BLENDER_MODEL_IMPORTER = PROJECT_ROOT / "tools" / "blender_import_model.py"
BLENDER_ACTION_SWITCHER = PROJECT_ROOT / "tools" / "blender_action_switcher.py"

CHACHA_KEY = bytes.fromhex(
    "e95b317ac4f828569d23a86bf271dcb53e846fa75c924d671dba8e38f4ca52e1"
)
VFS_PROTO_VERSION = 3
ASSETBUNDLE_META_VERSION = 3
ASSETBUNDLE_MAP_VERSION = 1
MONOBEHAVIOUR_DUMP_VERSION = 3
MONOBEHAVIOUR_RAW_VERSION = 2
PROJECTILE_COMPONENT_EXPORT_VERSION = 1
CUBEMAP_EXPORT_VERSION = 1
MODEL_SNAPSHOT_VERSION = 31
AVATAR_MODEL_SNAPSHOT_VERSION = 4
ANIMATION_CLIP_EXPORT_VERSION = 4
# Increment when the GLB representation changes without changing ModelDocument.
MODEL_GLB_VERSION = 4
MODEL_BLEND_VERSION = 12
MAX_BLEND_ANIMATION_COUNT = 100
AUDIO_PACKAGE_META_VERSION = 1
STRING_PATH_HASH_LOGICAL_ID = "ExtendData/Data/ExtendData/Main/StringPathHash.bin"
MANIFEST_LOGICAL_ID = "BundleManifest/Data/Bundles/Windows/manifest.hgmmap"
PROJECTILE_API_VERSION = 1
PREVIEW_TEXT_LIMIT = 2 * 1024 * 1024
PREVIEW_BINARY_LIMIT = 256 * 1024
STREAM_CHUNK_SIZE = 1024 * 1024
MODEL_BLEND_EXPORT_LOCK = threading.Lock()
MEMORYPACK_SCHEMA = Path(
    os.environ.get("VFS_BROWSER_MEMORYPACK_SCHEMA", PROJECT_ROOT / "schemas" / "memorypack-known-schema.json")
)
MEMORYPACK_UNION_MAP = Path(
    os.environ.get("VFS_BROWSER_MEMORYPACK_UNION_MAP", PROJECT_ROOT / "schemas" / "memorypack-known-unions.json")
)


def material_plan_cache_identity() -> dict:
    shader_path = SHADER_ARCHIVE_ROOT / CHARACTER_NPR_PATH
    identity = {
        "builderMtimeNs": Path(
            build_blender_material_plans.__code__.co_filename
        ).stat().st_mtime_ns,
        "shaderPath": str(shader_path),
    }
    if shader_path.is_file():
        stat = shader_path.stat()
        identity["shader"] = {"size": stat.st_size, "mtimeNs": stat.st_mtime_ns}
    else:
        identity["shader"] = None
    return identity


def load_cache_identity(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None

SOURCE_PRIORITY = {
    "Persistent": 0,
    "StreamingAssets": 1,
}

TEXT_EXTENSIONS = {".anim", ".json", ".lua", ".md", ".txt", ".csv", ".xml", ".yaml", ".yml"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".ogg", ".mov"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".m4a"}
CONTAINER_EXTENSIONS = {".ab", ".pck", ".usm"}
ASSETBUNDLE_EXPORT_TYPES = (
    "Texture2D",
    "Sprite",
    "TextAsset",
    "AudioClip",
    "VideoClip",
    "AnimationClip",
)
CUBEMAP_FACE_NAMES = (
    "PositiveX",
    "NegativeX",
    "PositiveY",
    "NegativeY",
    "PositiveZ",
    "NegativeZ",
)
MODEL_SNAPSHOT_TYPES = (
    "GameObject",
    "Transform",
    "MeshFilter",
    "MeshRenderer",
    "SkinnedMeshRenderer",
    "Mesh",
    "Material",
    "Animator",
    "Avatar",
)
AUDIO_ENTRY_RE = re.compile(r"^(wem|wav)/([0-9a-f]{1,2})/([0-9]+)\.(wem|wav)$", re.IGNORECASE)
PAGE_SIZE_MAX = 500
MANIFEST_VIRTUAL_DIR = "__manifest_assets__"
MANIFEST_VIRTUAL_NAME = "Manifest 资源"


@dataclass(frozen=True)
class FileView:
    file_id: int
    path: str
    name: str
    source: str
    chunk_exists: bool
    length: int
    encrypted: bool


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


def open_index(path: Path) -> Iterator[str]:
    name = path.name.lower()
    if name.endswith(".tgz") or name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            members = [member for member in archive.getmembers() if member.isfile()]
            if len(members) != 1:
                raise ValueError(f"expected one JSONL file in archive, found {len(members)}")
            raw = archive.extractfile(members[0])
            if raw is None:
                raise ValueError(f"cannot read {members[0].name} from {path}")
            with io.TextIOWrapper(raw, encoding="utf-8") as reader:
                yield from reader
        return

    if name.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as reader:
            yield from reader
        return

    with path.open("r", encoding="utf-8") as reader:
        yield from reader


def split_parent(path: str) -> tuple[str, str]:
    path = path.strip("/")
    if not path:
        return "", ""
    if "/" not in path:
        return "", path
    parent, name = path.rsplit("/", 1)
    return parent, name


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


def decrypt_vfs_file(data: bytes, iv_seed: int) -> bytes:
    nonce = struct.pack("<iq", VFS_PROTO_VERSION, int(iv_seed))
    return chacha20_apply(CHACHA_KEY, nonce, 1, data)


def derive_audio_key(seed: int) -> int:
    key = ((seed & 0xFF) ^ 0x9C5A0B29) * 81861667
    key &= 0xFFFFFFFF
    for shift in (8, 16, 24):
        key = (key ^ ((seed >> shift) & 0xFF)) * 81861667
        key &= 0xFFFFFFFF
    return key


def decrypt_audio_vfs_bytes(data: bytearray, start: int, length: int, seed: int, data_offset: int = 0) -> None:
    if start < 0 or length < 0 or start > len(data) or length > len(data) - start:
        raise ValueError("invalid audio decrypt range")

    key_index = (seed + (data_offset >> 2)) & 0xFFFFFFFF
    pos = start
    remaining = length
    alignment = data_offset & 3
    if alignment:
        key = derive_audio_key(key_index)
        to_align = min(4 - alignment, remaining)
        for i in range(to_align):
            data[pos] ^= (key >> ((alignment + i) * 8)) & 0xFF
            pos += 1
        remaining -= to_align
        key_index = (key_index + 1) & 0xFFFFFFFF

    for _ in range(remaining // 4):
        key = derive_audio_key(key_index)
        value = int.from_bytes(data[pos : pos + 4], "little") ^ key
        data[pos : pos + 4] = value.to_bytes(4, "little")
        pos += 4
        key_index = (key_index + 1) & 0xFFFFFFFF

    trailing = remaining & 3
    if trailing:
        key = derive_audio_key(key_index)
        for i in range(trailing):
            data[pos + i] ^= (key >> (i * 8)) & 0xFF


def decrypt_wem_bytes(data: bytes, wem_id: int) -> bytes:
    output = bytearray(data)
    decrypt_audio_vfs_bytes(output, 0, len(output), wem_id)
    return bytes(output)


def parse_akpk_header(header: bytes, label: str) -> tuple[bytes, int]:
    if len(header) < 28:
        raise ValueError(f"invalid AKPK header: {label}")
    data = bytearray(header)
    if data[:4] == b":)xD":
        header_size = int.from_bytes(data[4:8], "little")
        if header_size < 4 or header_size + 8 > len(data):
            raise ValueError(f"invalid encrypted AKPK header size: {label}")
        # 终末地音频包的包头会用同一套轻量异或流加密；解开后才是标准 AKPK 结构。
        decrypt_audio_vfs_bytes(data, 12, header_size - 4, header_size)
        data[:4] = b"AKPK"
        data[8:12] = (1).to_bytes(4, "little")
    if data[:4] != b"AKPK":
        raise ValueError(f"invalid AKPK magic: {label}")
    return bytes(data), int.from_bytes(data[4:8], "little")


def parse_akpk_languages(data: bytes, start: int, sector_size: int) -> dict[int, str]:
    languages = {}
    if sector_size < 4 or start + sector_size > len(data):
        return languages
    count = int.from_bytes(data[start : start + 4], "little")
    pos = start + 4
    for _ in range(count):
        if pos + 8 > start + sector_size:
            break
        name_offset = int.from_bytes(data[pos : pos + 4], "little")
        lang_id = int.from_bytes(data[pos + 4 : pos + 8], "little")
        name_start = start + name_offset
        name_end = min(start + sector_size, name_start + 32)
        raw = data[name_start:name_end]
        if len(raw) >= 2 and (raw[0] == 0 or raw[1] == 0):
            name = raw.decode("utf-16-le", errors="ignore").split("\x00", 1)[0]
        else:
            name = raw.decode("utf-8", errors="ignore").split("\x00", 1)[0]
        if name:
            languages[lang_id] = name
        pos += 8
    return languages


def parse_bnk_wem_ranges(payload: bytes) -> list[tuple[int, int, int]]:
    if len(payload) < 16 or payload[:4] != b"BKHD":
        return []
    bkhd_size = int.from_bytes(payload[4:8], "little")
    pos = 8 + bkhd_size
    end = len(payload)
    if pos + 8 > end or payload[pos : pos + 4] != b"DIDX":
        return []
    didx_size = int.from_bytes(payload[pos + 4 : pos + 8], "little")
    pos += 8
    rows = []
    for _ in range(didx_size // 12):
        if pos + 12 > end:
            return []
        wem_id = int.from_bytes(payload[pos : pos + 4], "little")
        wem_offset = int.from_bytes(payload[pos + 4 : pos + 8], "little")
        wem_size = int.from_bytes(payload[pos + 8 : pos + 12], "little")
        rows.append((wem_id, wem_offset, wem_size))
        pos += 12
    if pos + 8 > end or payload[pos : pos + 4] != b"DATA":
        return []
    data_offset = pos + 8
    return [(wem_id, data_offset + wem_offset, wem_size) for wem_id, wem_offset, wem_size in rows]


def audio_entry_prefix(wem_id: int) -> str:
    return f"{wem_id:x}"[:2].rjust(2, "0")


def parse_audio_internal_path(raw_path: str) -> tuple[str, int] | None:
    normalized = unquote(raw_path).replace("\\", "/").strip("/")
    match = AUDIO_ENTRY_RE.match(normalized)
    if not match or match.group(1).lower() != match.group(4).lower():
        return None
    return match.group(1).lower(), int(match.group(3))


def iter_ancestor_dirs(file_path: str) -> Iterator[str]:
    yield ""
    parts = [part for part in file_path.split("/") if part]
    current: list[str] = []
    for part in parts[:-1]:
        current.append(part)
        yield "/".join(current)


def add_dir_stats(
    dirs: dict[tuple[str, str], dict[str, int]],
    scope: str,
    file_path: str,
    length: int,
    encrypted: bool,
    chunk_exists: bool,
) -> None:
    for dir_path in iter_ancestor_dirs(file_path):
        stats = dirs.setdefault(
            (scope, dir_path),
            {"file_count": 0, "total_bytes": 0, "encrypted_count": 0, "missing_chunk_count": 0},
        )
        stats["file_count"] += 1
        stats["total_bytes"] += length
        if encrypted:
            stats["encrypted_count"] += 1
        if not chunk_exists:
            stats["missing_chunk_count"] += 1


def queue_file_entry(
    batch: list[tuple],
    scope: str,
    view: FileView,
) -> None:
    parent, name = split_parent(view.path)
    batch.append(
        (
            scope,
            parent,
            "file",
            name,
            view.path,
            view.file_id,
            view.length,
            int(view.encrypted),
            int(not view.chunk_exists),
        )
    )


def flush_file_entries(conn: sqlite3.Connection, batch: list[tuple]) -> None:
    if not batch:
        return
    conn.executemany(
        """
        INSERT INTO entries (
            scope, parent, type, name, path, file_id, total_bytes, encrypted_count, missing_chunk_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        batch,
    )
    batch.clear()


def source_rank(source: str, chunk_exists: bool) -> tuple[int, int]:
    return (0 if chunk_exists else 1, SOURCE_PRIORITY.get(source, 99))


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA journal_mode = OFF;
        PRAGMA synchronous = OFF;
        PRAGMA temp_store = MEMORY;

        DROP TABLE IF EXISTS meta;
        DROP TABLE IF EXISTS files;
        DROP TABLE IF EXISTS directories;
        DROP TABLE IF EXISTS entries;

        CREATE TABLE meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE files (
            id INTEGER PRIMARY KEY,
            source TEXT NOT NULL,
            source_root TEXT NOT NULL,
            block_hash TEXT NOT NULL,
            block_name TEXT NOT NULL,
            logical_id TEXT NOT NULL,
            source_logical_id TEXT NOT NULL,
            file_name TEXT NOT NULL,
            file_name_hash TEXT,
            chunk_file TEXT NOT NULL,
            chunk_path TEXT NOT NULL,
            chunk_exists INTEGER NOT NULL,
            chunk_md5_name TEXT,
            chunk_content_md5 TEXT,
            file_chunk_md5 TEXT,
            file_data_md5 TEXT,
            offset INTEGER NOT NULL,
            length INTEGER NOT NULL,
            encrypted INTEGER NOT NULL,
            iv_seed INTEGER NOT NULL
        );

        CREATE TABLE directories (
            scope TEXT NOT NULL,
            path TEXT NOT NULL,
            parent TEXT NOT NULL,
            name TEXT NOT NULL,
            file_count INTEGER NOT NULL,
            total_bytes INTEGER NOT NULL,
            encrypted_count INTEGER NOT NULL,
            missing_chunk_count INTEGER NOT NULL,
            child_dir_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (scope, path)
        );

        CREATE TABLE entries (
            scope TEXT NOT NULL,
            parent TEXT NOT NULL,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            path TEXT NOT NULL,
            file_id INTEGER,
            file_count INTEGER,
            total_bytes INTEGER,
            encrypted_count INTEGER,
            missing_chunk_count INTEGER
        );
        """
    )


def create_indexes(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE INDEX idx_entries_lookup ON entries(scope, parent, type, name);
        CREATE INDEX idx_entries_path ON entries(scope, path);
        CREATE INDEX idx_files_logical ON files(logical_id);
        CREATE INDEX idx_files_source_logical ON files(source_logical_id);
        CREATE INDEX idx_files_file_name ON files(file_name);
        """
    )


def insert_directories(conn: sqlite3.Connection, dirs: dict[tuple[str, str], dict[str, int]]) -> None:
    child_counts: dict[tuple[str, str], int] = {}
    for scope, path in dirs:
        if not path:
            continue
        parent, _ = split_parent(path)
        child_counts[(scope, parent)] = child_counts.get((scope, parent), 0) + 1

    directory_rows = []
    entry_rows = []
    for (scope, path), stats in dirs.items():
        parent, name = split_parent(path)
        child_dir_count = child_counts.get((scope, path), 0)
        directory_rows.append(
            (
                scope,
                path,
                parent,
                name,
                stats["file_count"],
                stats["total_bytes"],
                stats["encrypted_count"],
                stats["missing_chunk_count"],
                child_dir_count,
            )
        )
        if path:
            entry_rows.append(
                (
                    scope,
                    parent,
                    "dir",
                    name,
                    path,
                    None,
                    stats["file_count"],
                    stats["total_bytes"],
                    stats["encrypted_count"],
                    stats["missing_chunk_count"],
                )
            )

    conn.executemany(
        """
        INSERT INTO directories (
            scope, path, parent, name, file_count, total_bytes, encrypted_count,
            missing_chunk_count, child_dir_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        directory_rows,
    )
    conn.executemany(
        """
        INSERT INTO entries (
            scope, parent, type, name, path, file_id, file_count,
            total_bytes, encrypted_count, missing_chunk_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        entry_rows,
    )


def build_database(index_path: Path, db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    started = time.time()
    conn = sqlite3.connect(db_path)
    create_schema(conn)

    dirs: dict[tuple[str, str], dict[str, int]] = {}
    file_batch: list[tuple] = []
    entry_batch: list[tuple] = []
    effective: dict[str, tuple[tuple[int, int], FileView]] = {}
    header = None
    summary = None
    file_count = 0

    for line in open_index(index_path):
        record = json.loads(line)
        record_type = record.get("recordType")
        if record_type == "header":
            header = record
            continue
        if record_type == "summary":
            summary = record
            continue
        if record_type != "file":
            continue

        file_count += 1
        file_row = (
            record["source"],
            record.get("sourceRoot", ""),
            record["blockHash"],
            record["blockName"],
            record["logicalId"],
            record["sourceLogicalId"],
            record["fileName"],
            str(record.get("fileNameHash", "")),
            record["chunkFile"],
            record.get("chunkPath", ""),
            int(record.get("chunkExists", False)),
            record.get("chunkMd5Name"),
            record.get("chunkContentMd5"),
            record.get("fileChunkMd5"),
            record.get("fileDataMd5"),
            record["offset"],
            record["length"],
            int(record.get("encrypted", False)),
            record.get("ivSeed") or 0,
        )
        file_batch.append(file_row)
        if len(file_batch) >= 5000:
            conn.executemany(
                """
                INSERT INTO files (
                    source, source_root, block_hash, block_name, logical_id, source_logical_id,
                    file_name, file_name_hash, chunk_file, chunk_path, chunk_exists,
                    chunk_md5_name, chunk_content_md5, file_chunk_md5, file_data_md5,
                    offset, length, encrypted, iv_seed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                file_batch,
            )
            first_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0] - len(file_batch) + 1
            for i, row in enumerate(file_batch):
                file_id = first_id + i
                source = row[0]
                block_name = row[3]
                logical_id = row[4]
                length = int(row[16])
                encrypted = bool(row[17])
                chunk_exists = bool(row[10])
                source_path = logical_id
                all_path = f"{source}/{logical_id}"

                source_view = FileView(file_id, source_path, split_parent(source_path)[1], source, chunk_exists, length, encrypted)
                all_view = FileView(file_id, all_path, split_parent(all_path)[1], source, chunk_exists, length, encrypted)
                queue_file_entry(entry_batch, source, source_view)
                queue_file_entry(entry_batch, "all", all_view)
                add_dir_stats(dirs, source, source_path, length, encrypted, chunk_exists)
                add_dir_stats(dirs, "all", all_path, length, encrypted, chunk_exists)

                rank = source_rank(source, chunk_exists)
                current = effective.get(logical_id)
                if current is None or rank < current[0]:
                    effective[logical_id] = (
                        rank,
                        FileView(file_id, logical_id, split_parent(logical_id)[1], source, chunk_exists, length, encrypted),
                    )
            flush_file_entries(conn, entry_batch)
            file_batch.clear()

        if file_count % 100000 == 0:
            print(f"indexed {file_count:,} file records", flush=True)

    if file_batch:
        conn.executemany(
            """
            INSERT INTO files (
                source, source_root, block_hash, block_name, logical_id, source_logical_id,
                file_name, file_name_hash, chunk_file, chunk_path, chunk_exists,
                chunk_md5_name, chunk_content_md5, file_chunk_md5, file_data_md5,
                offset, length, encrypted, iv_seed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            file_batch,
        )
        first_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0] - len(file_batch) + 1
        for i, row in enumerate(file_batch):
            file_id = first_id + i
            source = row[0]
            logical_id = row[4]
            length = int(row[16])
            encrypted = bool(row[17])
            chunk_exists = bool(row[10])
            all_path = f"{source}/{logical_id}"
            queue_file_entry(entry_batch, source, FileView(file_id, logical_id, split_parent(logical_id)[1], source, chunk_exists, length, encrypted))
            queue_file_entry(entry_batch, "all", FileView(file_id, all_path, split_parent(all_path)[1], source, chunk_exists, length, encrypted))
            add_dir_stats(dirs, source, logical_id, length, encrypted, chunk_exists)
            add_dir_stats(dirs, "all", all_path, length, encrypted, chunk_exists)
            rank = source_rank(source, chunk_exists)
            current = effective.get(logical_id)
            if current is None or rank < current[0]:
                effective[logical_id] = (
                    rank,
                    FileView(file_id, logical_id, split_parent(logical_id)[1], source, chunk_exists, length, encrypted),
                )
        flush_file_entries(conn, entry_batch)

    for _, view in effective.values():
        queue_file_entry(entry_batch, "effective", view)
        add_dir_stats(dirs, "effective", view.path, view.length, view.encrypted, view.chunk_exists)
        if len(entry_batch) >= 5000:
            flush_file_entries(conn, entry_batch)
    flush_file_entries(conn, entry_batch)

    insert_directories(conn, dirs)
    create_indexes(conn)

    meta = {
        "indexPath": str(index_path),
        "builtAtEpoch": int(time.time()),
        "elapsedSeconds": round(time.time() - started, 3),
        "sourceFileCount": file_count,
        "effectiveFileCount": len(effective),
        "header": header,
        "summary": summary,
    }
    conn.executemany(
        "INSERT INTO meta(key, value) VALUES (?, ?)",
        [(key, json.dumps(value, ensure_ascii=False)) for key, value in meta.items()],
    )
    conn.commit()
    conn.close()
    print(f"database built: {db_path} ({file_count:,} source records, {len(effective):,} effective records)")


def row_to_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


def escape_sql_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def file_suffix(file_name: str) -> str:
    return Path(file_name).suffix.lower()


def is_safe_akedb_name(value: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9_]+", value) is not None


def is_safe_akedb_json_file(value: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9_.-]+\.json", value) is not None


def is_akedb_collection(value: str) -> bool:
    return value in {"SkillData", "BuffData"}


def is_model_entry_path(path: str) -> bool:
    return file_suffix(path) == ".prefab" or is_avatar_mesh_asset_path(path)


def default_model_animation_query(path: str) -> str:
    stem = Path(path.split("##", 1)[0]).stem.casefold()
    for pattern in (
        r"^data_npc_avatarmesh_(.+)$",
        r"^chr_\d+_(.+?)_postmodel$",
        r"^(?:p|sk)_actor_(.+?)(?:_\d+)?$",
    ):
        match = re.match(pattern, stem)
        if match:
            return match.group(1)
    return stem


def model_animation_query_hint(path: str, document: dict) -> str:
    """根据模型实际装配内容选择动画搜索词。"""

    fallback = default_model_animation_query(path)
    if not is_avatar_mesh_asset_path(path):
        return fallback
    parts = document.get("asset", {}).get("assembly", {}).get("parts", [])
    for part in parts:
        for mesh_path in part.get("meshPaths", []):
            normalized = str(mesh_path).replace("\\", "/").casefold()
            match = re.search(r"/entity/npc/major/([^/]+)/", normalized)
            if match:
                # Major NPC 按骨架家族共用身体动画；用 NPC 名搜索通常只会命中面部动画。
                return f"a_actor_{match.group(1)}"
    return fallback


def dotnet_tool_identity(executable: Path) -> list[dict]:
    candidates = (
        executable,
        executable.with_suffix(".dll"),
        executable.parent / "AnimeStudio.dll",
    )
    artifacts = []
    for path in candidates:
        if not path.is_file():
            continue
        stat = path.stat()
        artifacts.append(
            {
                "path": str(path.resolve()),
                "size": stat.st_size,
                "mtimeNs": stat.st_mtime_ns,
            }
        )
    return artifacts


def tablecfg_name_for_file(file_name: str) -> str | None:
    normalized = file_name.replace("\\", "/").strip("/")
    prefix = "Data/TableCfg/"
    suffix = ".bytes"
    if not normalized.startswith(prefix) or not normalized.endswith(suffix):
        return None
    name = normalized[len(prefix) : -len(suffix)]
    if "/" in name or not name:
        return None
    return name


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


def looks_like_text_bytes(data: bytes) -> bool:
    text, _ = decode_text(data)
    return text is not None and looks_like_text(text)


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


def length_prefixed_utf8_strings(data: bytes, max_offset: int = 8192, max_count: int = 40) -> list[dict]:
    strings = []
    scan_end = min(max(len(data) - 4, 0), max_offset)
    for offset in range(scan_end):
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


def binary_json_probe(data: bytes, full_length: int) -> dict:
    first_byte = data[0] if data else None
    return {
        "formatHint": "schema-based binary JSON",
        "confidence": "medium",
        "firstByte": first_byte,
        "possibleMemberCount": first_byte,
        "fullLength": full_length,
        "sampleLength": len(data),
        "lengthPrefixedStrings": length_prefixed_utf8_strings(data),
        "note": "VFS 解密已完成；该 .json 内容疑似按类型 schema 顺序写入的二进制配置，需要字段 schema 才能完整还原。",
    }


def load_memorypack_union_map(path: Path) -> dict[str, dict[int, str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {base_type: {int(tag): derived_type for tag, derived_type in entries.items()} for base_type, entries in raw.items()}


def internal_preview_kind(path: Path) -> str:
    suffix = file_suffix(path.name)
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    if suffix in TEXT_EXTENSIONS:
        return "text"
    return "binary"


def assetbundle_export_types_match(meta: dict) -> bool:
    return tuple(meta.get("exportTypes") or ()) == ASSETBUNDLE_EXPORT_TYPES


def manifest_asset_entries(meta: dict, logical_path: str) -> list[dict]:
    normalized = logical_path.replace("\\", "/").strip("/")
    container_key = normalized.casefold()
    entries = list(meta.get("assetEntries") or [])
    exact = [
        entry
        for entry in entries
        if str(entry.get("Container") or "").replace("\\", "/").strip("/").casefold()
        == container_key
    ]
    if exact or "##" not in normalized:
        return exact

    # Imported FBX sub-assets use ``path.fbx##clip_name`` in the manifest, while
    # AnimeStudio exposes the AnimationClip name without a container.
    sub_asset_name = normalized.rsplit("##", 1)[1].casefold()
    by_name = [
        entry
        for entry in entries
        if str(entry.get("Name") or "").casefold() == sub_asset_name
    ]
    return by_name if len(by_name) == 1 else []


def safe_relative_path(root: Path, raw_path: str) -> Path | None:
    normalized = unquote(raw_path).replace("\\", "/").strip("/")
    if not normalized:
        return root
    candidate = (root / normalized).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def validate_worker_artifacts(export_root: Path, result: object) -> list[Path]:
    """验证 worker 声明的全部产物，拒绝路径逃逸、重复路径和身份不一致。"""

    artifacts = result.get("artifacts") if isinstance(result, dict) else None
    if (
        not isinstance(result, dict)
        or not isinstance(artifacts, list)
        or not artifacts
        or result.get("artifactCount") != len(artifacts)
        or any(not isinstance(artifact, dict) for artifact in artifacts)
    ):
        raise RuntimeError("Unity worker returned an invalid artifact collection")

    paths = []
    relative_paths = set()
    for artifact in artifacts:
        relative = str(artifact.get("relativePath") or "").replace("\\", "/")
        path = safe_relative_path(export_root, relative)
        if not relative or relative in relative_paths or path is None or not path.is_file():
            raise RuntimeError("Unity worker returned an invalid or duplicate artifact path")
        relative_paths.add(relative)
        expected_size = artifact.get("byteCount")
        expected_sha256 = str(artifact.get("sha256") or "").casefold()
        actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        if (
            not isinstance(expected_size, int)
            or expected_size != path.stat().st_size
            or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
            or expected_sha256 != actual_sha256
        ):
            raise RuntimeError("Unity worker artifact identity is inconsistent")
        paths.append(path)
    return paths


def describe_derived_artifacts(export_root: Path, files: object) -> dict[str, dict]:
    """把发布前派生文件转换为可在缓存命中时复验的稳定身份。"""

    if not isinstance(files, dict):
        raise RuntimeError("derived worker artifact list is inconsistent")
    described = {}
    for name, relative_value in files.items():
        relative = str(relative_value).replace("\\", "/")
        path = safe_relative_path(export_root, relative)
        if not name or path is None or not path.is_file():
            raise RuntimeError("derived worker artifact path is inconsistent")
        described[str(name)] = {
            "relativePath": relative,
            "byteCount": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    return described


def validate_derived_artifacts(export_root: Path, described: object) -> dict[str, Path]:
    """复验已发布的派生文件，防止半成品或事后损坏继续命中缓存。"""

    if not isinstance(described, dict):
        raise RuntimeError("cached derived artifact list is inconsistent")
    paths = {}
    for name, identity in described.items():
        if not isinstance(identity, dict):
            raise RuntimeError("cached derived artifact identity is inconsistent")
        path = safe_relative_path(export_root, str(identity.get("relativePath") or ""))
        if (
            path is None
            or not path.is_file()
            or identity.get("byteCount") != path.stat().st_size
            or identity.get("sha256") != hashlib.sha256(path.read_bytes()).hexdigest()
        ):
            raise RuntimeError("cached derived artifact identity is inconsistent")
        paths[str(name)] = path
    return paths


def posix_relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def folder_stats(path: Path) -> tuple[int, int]:
    file_count = 0
    total_bytes = 0
    for child in path.rglob("*"):
        if child.is_file():
            file_count += 1
            total_bytes += child.stat().st_size
    return file_count, total_bytes


def split_manifest_virtual_path(path: str) -> tuple[str, str] | None:
    parts = [part for part in path.replace("\\", "/").strip("/").split("/") if part]
    if MANIFEST_VIRTUAL_DIR not in parts:
        return None
    marker = parts.index(MANIFEST_VIRTUAL_DIR)
    return "/".join(parts[:marker]), "/".join(parts[marker + 1 :])


def join_manifest_virtual_path(base_path: str, inner_path: str = "") -> str:
    return "/".join(
        part for part in (base_path.strip("/"), MANIFEST_VIRTUAL_DIR, inner_path.strip("/")) if part
    )


class BrowserHandler(BaseHTTPRequestHandler):
    db_path: Path
    manifest_indexes: dict[tuple[int, int, str], ManifestIndex] = {}
    manifest_index_lock = threading.Lock()
    shared_resource_lock = threading.Lock()
    memorypack_schema: SchemaIndex | None = None
    memorypack_union_map: dict[str, dict[int, str]] | None = None
    memorypack_load_error: str | None = None

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def send_json(
        self,
        payload: object,
        status: int = 200,
        *,
        cache_control: str | None = None,
        compress: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        accepted_encodings = self.headers.get("Accept-Encoding", "").casefold()
        is_compressed = compress and "gzip" in accepted_encodings
        if is_compressed:
            data = gzip.compress(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if is_compressed:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        if cache_control:
            self.send_header("Cache-Control", cache_control)
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def send_error_json(self, status: int, message: str) -> None:
        self.send_json({"error": message}, status=status)

    def read_json_body(self, *, maximum_bytes: int = 64 * 1024) -> dict:
        """读取有明确长度的小型 JSON 请求；长任务输入不得藏在无界请求体中。"""

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("invalid Content-Length") from error
        if length <= 0 or length > maximum_bytes:
            raise ValueError(f"JSON body length must be between 1 and {maximum_bytes} bytes")
        try:
            value = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as error:
            raise ValueError("invalid JSON body") from error
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def connect_audio_dialog(self) -> sqlite3.Connection:
        if not AUDIO_DIALOG_DB.is_file():
            raise FileNotFoundError(
                f"AudioDialog index not built: {AUDIO_DIALOG_DB}"
            )
        return sqlite3.connect(AUDIO_DIALOG_DB)

    def connect_wwise(self) -> sqlite3.Connection:
        if not WWISE_DB.is_file():
            raise FileNotFoundError(f"Wwise index not built: {WWISE_DB}")
        return sqlite3.connect(WWISE_DB)

    @classmethod
    def load_memorypack_decoder_inputs(cls) -> tuple[SchemaIndex, dict[str, dict[int, str]]]:
        if cls.memorypack_load_error:
            raise RuntimeError(cls.memorypack_load_error)
        if Decoder is None or MemoryPackReader is None or SchemaIndex is None:
            cls.memorypack_load_error = "MemoryPack decoder module is unavailable"
            raise RuntimeError(cls.memorypack_load_error)
        try:
            if cls.memorypack_schema is None:
                cls.memorypack_schema = SchemaIndex.load(MEMORYPACK_SCHEMA)
            if cls.memorypack_union_map is None:
                cls.memorypack_union_map = load_memorypack_union_map(MEMORYPACK_UNION_MAP)
        except (OSError, json.JSONDecodeError, ValueError) as error:
            cls.memorypack_load_error = str(error)
            raise RuntimeError(cls.memorypack_load_error) from error
        return cls.memorypack_schema, cls.memorypack_union_map

    def decode_memorypack_json_preview(self, record: dict, chunk_path: Path) -> tuple[str, bool, dict] | None:
        class_name = infer_class(record.get("logical_id"))
        if not class_name:
            return None
        schema, union_map = self.load_memorypack_decoder_inputs()
        data = self.read_file_slice(record, chunk_path)
        reader = MemoryPackReader(data)
        decoder = Decoder(schema, union_map=union_map)
        try:
            value = decoder.decode(reader, class_name)
        except DecodeError as error:
            raise RuntimeError(f"{error.message} at 0x{error.offset:x} ({error.path})") from error
        meta = {
            "class": class_name,
            "bytes": len(data),
            "consumed": reader.tell(),
            "complete": reader.tell() == len(data),
            "discoveredUnions": {
                base_type: {str(tag): derived_type for tag, derived_type in sorted(entries.items())}
                for base_type, entries in sorted(decoder.discovered_unions.items())
            },
        }
        text, truncated = truncate_text(json.dumps({"__meta": meta, "value": value}, ensure_ascii=False, indent=2))
        return text, truncated, meta

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self.handle_health()
            return
        if parsed.path == "/api/task":
            self.handle_task_status(parse_qs(parsed.query))
            return
        if parsed.path.startswith("/api/akedb-compatible/"):
            self.handle_akedb_compatible(parsed.path)
            return
        if parsed.path == "/api/manifest":
            self.handle_manifest()
            return
        if parsed.path == "/api/list":
            self.handle_list(parse_qs(parsed.query))
            return
        if parsed.path == "/api/search":
            self.handle_search(parse_qs(parsed.query))
            return
        if parsed.path == "/api/projectile":
            self.handle_projectile(parse_qs(parsed.query))
            return
        if parsed.path == "/api/audio-dialog/list":
            self.handle_audio_dialog_list(parse_qs(parsed.query))
            return
        if parsed.path == "/api/audio-dialog/entry":
            self.handle_audio_dialog_entry(parse_qs(parsed.query))
            return
        if parsed.path == "/api/audio-dialog/preview":
            self.handle_audio_dialog_preview(parse_qs(parsed.query))
            return
        if parsed.path == "/api/audio-dialog/raw":
            self.handle_audio_dialog_raw(parse_qs(parsed.query))
            return
        if parsed.path == "/api/wwise/list":
            self.handle_wwise_list(parse_qs(parsed.query))
            return
        if parsed.path == "/api/wwise/preview":
            self.handle_wwise_preview(parse_qs(parsed.query))
            return
        if parsed.path == "/api/wwise/raw":
            self.handle_wwise_raw(parse_qs(parsed.query))
            return
        if parsed.path == "/api/file":
            self.handle_file(parse_qs(parsed.query))
            return
        if parsed.path == "/api/preview":
            self.handle_preview(parse_qs(parsed.query))
            return
        if parsed.path == "/api/raw":
            self.handle_raw(parse_qs(parsed.query))
            return
        if parsed.path == "/api/manifest-asset/preview":
            self.handle_manifest_asset_preview(parse_qs(parsed.query))
            return
        if parsed.path == "/api/manifest-asset/raw":
            self.handle_manifest_asset_raw(parse_qs(parsed.query))
            return
        if parsed.path == "/api/manifest-asset/avatar-plan":
            self.handle_manifest_asset_avatar_plan(parse_qs(parsed.query))
            return
        if parsed.path == "/api/manifest-asset/model":
            self.handle_manifest_asset_model(parse_qs(parsed.query))
            return
        if parsed.path == "/api/manifest-asset/model-buffer":
            self.handle_manifest_asset_model_buffer(parse_qs(parsed.query))
            return
        if parsed.path == "/api/manifest-asset/model-texture":
            self.handle_manifest_asset_model_texture(parse_qs(parsed.query))
            return
        if parsed.path == "/api/manifest-asset/model-glb":
            self.handle_manifest_asset_model_glb(parse_qs(parsed.query))
            return
        if parsed.path == "/api/manifest-asset/model-blend":
            self.handle_manifest_asset_model_blend(parse_qs(parsed.query))
            return
        if parsed.path == "/api/manifest-asset/model-animation":
            self.handle_manifest_asset_model_animation(parse_qs(parsed.query))
            return
        if parsed.path == "/api/manifest-asset/model-animations":
            self.handle_manifest_asset_model_animations(parse_qs(parsed.query))
            return
        if parsed.path == "/api/tablecfg/json":
            self.handle_tablecfg_json(parse_qs(parsed.query))
            return
        if parsed.path == "/api/internal/list":
            self.handle_internal_list(parse_qs(parsed.query))
            return
        if parsed.path == "/api/internal/preview":
            self.handle_internal_preview(parse_qs(parsed.query))
            return
        if parsed.path == "/api/internal/raw":
            self.handle_internal_raw(parse_qs(parsed.query))
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/tasks/projectile":
            self.handle_start_projectile_task()
            return
        self.send_error_json(404, "API route not found")

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/task":
            self.handle_cancel_task(parse_qs(parsed.query))
            return
        self.send_error_json(404, "API route not found")

    def handle_health(self) -> None:
        self.send_json(build_health_document(), cache_control="no-store")

    def handle_start_projectile_task(self) -> None:
        try:
            body = self.read_json_body()
            projectile_id = normalize_projectile_id(str(body.get("projectileId") or ""))
        except ValueError as error:
            self.send_error_json(400, str(error))
            return

        # 后台任务不能捕获 HTTP handler；只复制应用服务所需的数据库配置。
        worker = object.__new__(BrowserHandler)
        worker.db_path = self.db_path
        created = TASKS.submit(
            "projectile",
            lambda cancel_event: worker.build_projectile_document(
                projectile_id,
                cancel_event=cancel_event,
            ),
        )
        self.send_json(created, status=202, cache_control="no-store")

    def handle_task_status(self, query: dict[str, list[str]]) -> None:
        task_id = query.get("taskId", [""])[0]
        try:
            snapshot = TASKS.snapshot(task_id)
        except (TaskNotFoundError, OSError, json.JSONDecodeError):
            self.send_error_json(404, "task not found")
            return
        self.send_json(snapshot, cache_control="no-store")

    def handle_cancel_task(self, query: dict[str, list[str]]) -> None:
        task_id = query.get("taskId", [""])[0]
        try:
            snapshot = TASKS.cancel(task_id)
        except (TaskNotFoundError, OSError, json.JSONDecodeError):
            self.send_error_json(404, "task not found")
            return
        status = 202 if snapshot["state"] == "cancelling" else 200
        self.send_json(snapshot, status=status, cache_control="no-store")

    def handle_akedb_compatible(self, request_path: str) -> None:
        """按 Endaxis 资源下载器约定输出与 AKEDB 同构的 JSON。"""

        prefix = "/api/akedb-compatible/"
        logical_path = unquote(request_path[len(prefix) :]).strip("/")
        parts = logical_path.split("/") if logical_path else []
        if len(parts) == 2 and re.fullmatch(r"TableCfg-[A-Za-z0-9@._-]+", parts[0]):
            table_name = parts[1].removesuffix(".json")
            if not parts[1].endswith(".json") or not is_safe_akedb_name(table_name):
                self.send_error_json(400, "invalid TableCfg resource name")
                return
            self.handle_akedb_compatible_table(table_name)
            return
        if len(parts) == 2 and parts[1] == "manifest.json" and is_akedb_collection(parts[0]):
            self.handle_akedb_compatible_collection_manifest(parts[0])
            return
        if len(parts) == 2 and parts[1].endswith(".json") and is_akedb_collection(parts[0]):
            file_name = parts[1]
            if not is_safe_akedb_json_file(file_name):
                self.send_error_json(400, "invalid collection resource name")
                return
            self.handle_akedb_compatible_collection_file(parts[0], file_name)
            return
        if len(parts) == 2 and parts[0] == "ProjectileData":
            if parts[1] == "manifest.json":
                self.handle_akedb_compatible_projectile_manifest()
                return
            if parts[1].endswith(".json"):
                projectile_id = parts[1].removesuffix(".json")
                try:
                    projectile_id = normalize_projectile_id(projectile_id)
                except ValueError as error:
                    self.send_error_json(400, str(error))
                    return
                self.handle_akedb_compatible_projectile_file(projectile_id)
                return
        if len(parts) == 2 and parts[0] == "AbilityEntityData":
            if parts[1] == "manifest.json":
                self.handle_akedb_compatible_ability_entity_manifest()
                return
            if parts[1].endswith(".json"):
                entity_id = parts[1].removesuffix(".json")
                try:
                    entity_id = normalize_ability_entity_id(entity_id)
                except ValueError as error:
                    self.send_error_json(400, str(error))
                    return
                self.handle_akedb_compatible_ability_entity_file(entity_id)
                return
        self.send_error_json(404, "AKEDB-compatible resource not found")

    def handle_akedb_compatible_table(self, table_name: str) -> None:
        logical_id = f"Table/Data/TableCfg/{table_name}.bytes"
        resolved = self.resolve_logical_file_source(logical_id)
        if resolved is None:
            self.send_error_json(404, f"local VFS resource is unavailable: {logical_id}")
            return
        record, chunk_path = resolved
        try:
            parsed, _ = self.parse_tablecfg_file(record, chunk_path)
        except (SparkBufferError, struct.error, UnicodeDecodeError, ValueError) as error:
            self.send_error_json(422, f"SparkBuffer parse failed: {error}")
            return
        self.send_json(
            parsed["data"],
            extra_headers={"X-Endaxis-Source": "vfs-index-browser"},
        )

    def handle_akedb_compatible_collection_manifest(self, collection: str) -> None:
        logical_parent = f"JsonData/Data/Json/{collection}"
        with closing(self.connect()) as conn:
            rows = conn.execute(
                """
                SELECT name FROM entries
                WHERE scope = 'effective' AND type = 'file' AND parent = ?
                ORDER BY name
                """,
                (logical_parent,),
            ).fetchall()
        files = sorted(
            {
                str(row["name"])
                for row in rows
                if is_safe_akedb_json_file(str(row["name"]))
            }
        )
        self.send_json(
            [
                {
                    "contentFile": f"/api/akedb-compatible/{collection}/{file_name}",
                }
                for file_name in files
            ],
            extra_headers={"X-Endaxis-Source": "vfs-index-browser"},
        )

    def handle_akedb_compatible_collection_file(self, collection: str, file_name: str) -> None:
        logical_id = f"JsonData/Data/Json/{collection}/{file_name}"
        resolved = self.resolve_logical_file_source(logical_id)
        if resolved is None:
            self.send_error_json(404, f"local VFS resource is unavailable: {logical_id}")
            return
        record, chunk_path = resolved
        class_name = infer_class(logical_id)
        if not class_name:
            self.send_error_json(422, f"MemoryPack class is unknown: {logical_id}")
            return
        try:
            schema, union_map = self.load_memorypack_decoder_inputs()
            data = self.read_file_slice(record, chunk_path)
            reader = MemoryPackReader(data)
            decoder = Decoder(schema, union_map=union_map)
            value = decoder.decode(reader, class_name)
        except (DecodeError, RuntimeError, ValueError) as error:
            self.send_error_json(422, f"MemoryPack decode failed: {error}")
            return
        if reader.tell() != len(data):
            self.send_error_json(
                422,
                f"MemoryPack decode was incomplete: consumed {reader.tell()} / {len(data)} bytes",
            )
            return
        self.send_json(
            value,
            extra_headers={"X-Endaxis-Source": "vfs-index-browser"},
        )

    def resolve_installed_manifest_index(self) -> ManifestIndex:
        """打开当前安装版本的精确 Unity manifest 索引。"""

        resolved = self.resolve_logical_file_source(MANIFEST_LOGICAL_ID)
        if resolved is None:
            raise FileNotFoundError(f"local VFS manifest is unavailable: {MANIFEST_LOGICAL_ID}")
        record, chunk_path = resolved
        return self.manifest_index(record, chunk_path)

    def handle_akedb_compatible_projectile_manifest(self) -> None:
        try:
            projectile_ids = list_projectile_ids(self.resolve_installed_manifest_index())
        except (ProjectileDecodeError, OSError, sqlite3.Error) as error:
            self.send_error_json(503, str(error))
            return
        self.send_json(
            [
                {
                    "contentFile": (
                        f"/api/akedb-compatible/ProjectileData/{projectile_id}.json"
                    ),
                }
                for projectile_id in projectile_ids
            ],
            extra_headers={"X-Endaxis-Source": "vfs-index-browser"},
        )

    def handle_akedb_compatible_projectile_file(self, projectile_id: str) -> None:
        try:
            document = self.build_projectile_document(projectile_id)
        except ProjectileNotFoundError as error:
            self.send_error_json(404, str(error))
            return
        except ProjectileDecodeError as error:
            self.send_error_json(422, str(error))
            return
        except (ProjectileUnavailableError, OSError, sqlite3.Error) as error:
            self.send_error_json(503, str(error))
            return
        self.send_json(
            document["projectileComponentData"],
            extra_headers={"X-Endaxis-Source": "vfs-index-browser"},
        )

    def build_ability_entity_document(self, entity_id: str) -> dict:
        """从精确 Unity asset 导出并解析能力实体模板的已证实前缀。"""

        resolved_manifest = self.resolve_logical_file_source(MANIFEST_LOGICAL_ID)
        if resolved_manifest is None:
            raise AbilityEntityUnavailableError(
                f"local VFS manifest is unavailable: {MANIFEST_LOGICAL_ID}"
            )
        manifest_record, manifest_chunk = resolved_manifest
        try:
            index = self.manifest_index(manifest_record, manifest_chunk)
            indexed_asset = select_ability_entity_asset(index, entity_id)
            asset, bundle_record, bundle_chunk = self.resolve_index_asset_bundle(
                index,
                int(indexed_asset["assetIndex"]),
            )
            raw_path, export_meta = self.ensure_manifest_monobehaviour_raw(
                bundle_record,
                bundle_chunk,
                asset,
            )
            template = parse_ability_entity_template(raw_path.read_bytes(), entity_id)
        except AbilityEntityNotFoundError:
            raise
        except AbilityEntityDecodeError:
            raise
        except UnityWorkerError as error:
            if unity_worker_is_unavailable(error):
                raise AbilityEntityUnavailableError(str(error)) from error
            raise AbilityEntityDecodeError(f"Unity worker {error.code}: {error}") from error
        except (FileNotFoundError, OSError, sqlite3.Error, subprocess.SubprocessError) as error:
            raise AbilityEntityUnavailableError(str(error)) from error
        except (RuntimeError, ValueError) as error:
            raise AbilityEntityDecodeError(str(error)) from error
        return {
            "apiVersion": 1,
            "abilityEntityId": entity_id,
            "source": {
                "assetPath": asset["path"],
                "assetIndex": int(asset["asset_index"]),
                "bundleName": asset["bundle_name"],
                "rawExport": export_meta.get("exportedFile"),
            },
            "abilityEntityTemplateData": template,
        }

    def handle_akedb_compatible_ability_entity_manifest(self) -> None:
        try:
            entity_ids = list_ability_entity_ids(self.resolve_installed_manifest_index())
        except (AbilityEntityDecodeError, OSError, sqlite3.Error) as error:
            self.send_error_json(503, str(error))
            return
        self.send_json(
            [
                {
                    "contentFile": (
                        f"/api/akedb-compatible/AbilityEntityData/{entity_id}.json"
                    ),
                }
                for entity_id in entity_ids
            ],
            extra_headers={"X-Endaxis-Source": "vfs-index-browser"},
        )

    def handle_akedb_compatible_ability_entity_file(self, entity_id: str) -> None:
        try:
            document = self.build_ability_entity_document(entity_id)
        except AbilityEntityNotFoundError as error:
            self.send_error_json(404, str(error))
            return
        except AbilityEntityDecodeError as error:
            self.send_error_json(422, str(error))
            return
        except AbilityEntityUnavailableError as error:
            self.send_error_json(503, str(error))
            return
        self.send_json(
            document["abilityEntityTemplateData"],
            extra_headers={"X-Endaxis-Source": "vfs-index-browser"},
        )

    def handle_manifest(self) -> None:
        with self.connect() as conn:
            meta = {row["key"]: json.loads(row["value"]) for row in conn.execute("SELECT key, value FROM meta")}
            scopes = []
            for scope in ["effective", "Persistent", "StreamingAssets", "all"]:
                row = conn.execute(
                    "SELECT * FROM directories WHERE scope = ? AND path = ''",
                    (scope,),
                ).fetchone()
                if row:
                    scopes.append(row_to_dict(row))
            self.send_json({"meta": meta, "scopes": scopes})

    def handle_list(self, query: dict[str, list[str]]) -> None:
        scope = query.get("scope", ["effective"])[0]
        path = unquote(query.get("path", [""])[0]).strip("/")
        page = max(int(query.get("page", ["1"])[0]), 1)
        page_size = min(max(int(query.get("pageSize", ["100"])[0]), 10), PAGE_SIZE_MAX)
        offset = (page - 1) * page_size
        virtual_path = split_manifest_virtual_path(path)
        if virtual_path is not None:
            self.handle_manifest_virtual_list(scope, *virtual_path, page, page_size)
            return

        with self.connect() as conn:
            current = conn.execute(
                "SELECT * FROM directories WHERE scope = ? AND path = ?",
                (scope, path),
            ).fetchone()
            if current is None:
                self.send_error_json(404, "directory not found")
                return
            dirs = [
                row_to_dict(row)
                for row in conn.execute(
                    """
                    SELECT path, name, file_count, total_bytes, encrypted_count,
                           missing_chunk_count
                    FROM entries
                    WHERE scope = ? AND parent = ? AND type = 'dir'
                    ORDER BY name COLLATE NOCASE
                    """,
                    (scope, path),
                )
            ]
            manifest_entry = conn.execute(
                """
                SELECT e.file_id, f.length, f.chunk_exists
                FROM entries e JOIN files f ON f.id = e.file_id
                WHERE e.scope = ? AND e.parent = ? AND e.type = 'file'
                  AND e.name = 'manifest.hgmmap'
                LIMIT 1
                """,
                (scope, path),
            ).fetchone()
            if manifest_entry is not None:
                manifest_count = 0
                if manifest_entry["chunk_exists"]:
                    manifest_record = self.original_file_record(conn, int(manifest_entry["file_id"]))
                    resolved_manifest = (
                        self.resolve_file_record_quiet(conn, manifest_record)
                        if manifest_record is not None
                        else None
                    )
                    if resolved_manifest is not None:
                        manifest_record, manifest_chunk = resolved_manifest
                        try:
                            manifest_count = self.manifest_index(
                                manifest_record,
                                manifest_chunk,
                            ).summary()["assetCount"]
                        except (ValueError, OSError, sqlite3.Error):
                            manifest_count = 0
                dirs.append(
                    {
                        "path": join_manifest_virtual_path(path),
                        "name": MANIFEST_VIRTUAL_NAME,
                        "file_count": manifest_count,
                        "total_bytes": int(manifest_entry["length"]),
                        "encrypted_count": 0,
                        "missing_chunk_count": 0 if manifest_entry["chunk_exists"] else 1,
                        "virtualKind": "bundleManifest",
                    }
                )
            total_files = conn.execute(
                "SELECT COUNT(*) AS count FROM entries WHERE scope = ? AND parent = ? AND type = 'file'",
                (scope, path),
            ).fetchone()["count"]
            entry_rows = [
                row_to_dict(row)
                for row in conn.execute(
                    """
                    SELECT path, name, file_id
                    FROM entries
                    WHERE scope = ? AND parent = ? AND type = 'file'
                    ORDER BY name COLLATE NOCASE
                    LIMIT ? OFFSET ?
                    """,
                    (scope, path, page_size, offset),
                )
            ]
            files_by_id = {}
            if entry_rows:
                placeholders = ",".join("?" for _ in entry_rows)
                files_by_id = {
                    row["id"]: row_to_dict(row)
                    for row in conn.execute(
                        f"""
                        SELECT id, source, block_name, block_hash, file_name, logical_id,
                               source_logical_id, chunk_file, chunk_exists, offset, length,
                               encrypted, iv_seed, file_data_md5
                        FROM files
                        WHERE id IN ({placeholders})
                        """,
                        [row["file_id"] for row in entry_rows],
                    )
                }
            files = []
            for entry in entry_rows:
                file = files_by_id.get(entry["file_id"])
                if not file:
                    continue
                files.append({**file, "path": entry["path"], "name": entry["name"]})
            self.send_json(
                {
                    "scope": scope,
                    "path": path,
                    "directory": row_to_dict(current),
                    "dirs": dirs,
                    "files": files,
                    "filePage": {
                        "page": page,
                        "pageSize": page_size,
                        "total": total_files,
                        "pages": max((total_files + page_size - 1) // page_size, 1),
                    },
                }
            )

    def handle_manifest_virtual_list(
        self,
        scope: str,
        base_path: str,
        inner_path: str,
        page: int,
        page_size: int,
    ) -> None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT e.file_id FROM entries e
                WHERE e.scope = ? AND e.parent = ? AND e.type = 'file'
                  AND e.name = 'manifest.hgmmap'
                LIMIT 1
                """,
                (scope, base_path),
            ).fetchone()
            if row is None:
                self.send_error_json(404, "manifest.hgmmap not found")
                return
            resolved = self.resolve_file_record(conn, int(row["file_id"]))
            if resolved is None:
                return
            original, record, chunk_path = resolved

        try:
            listing = self.manifest_index(record, chunk_path).list(inner_path, page, page_size)
        except (ValueError, OSError, sqlite3.Error, FileNotFoundError) as error:
            self.send_error_json(400, str(error))
            return

        virtual_path = join_manifest_virtual_path(base_path, inner_path)
        dirs = [
            {
                "path": join_manifest_virtual_path(base_path, item["path"]),
                "name": item["name"],
                "file_count": item["fileCount"],
                "total_bytes": item["totalBytes"],
                "encrypted_count": 0,
                "missing_chunk_count": 0,
                "virtualKind": "bundleManifest",
            }
            for item in listing["dirs"]
        ]
        files = []
        for asset in listing["files"]:
            params = f"manifestId={original['id']}&assetIndex={asset['assetIndex']}"
            files.append(
                {
                    "name": asset["name"],
                    "path": asset["path"],
                    "file_name": asset["path"],
                    "length": asset["size"],
                    "source": "BundleManifest",
                    "block_name": asset["bundleName"],
                    "chunk_file": "按需解析 AssetBundle",
                    "chunk_exists": True,
                    "offset": 0,
                    "encrypted": False,
                    "virtualKind": "manifestAsset",
                    "previewUrl": f"/api/manifest-asset/preview?{params}",
                }
            )
            if file_suffix(asset["path"]) == ".prefab":
                files[-1]["modelUrl"] = f"/api/manifest-asset/model?{params}"
            if is_avatar_mesh_asset_path(asset["path"]):
                files[-1]["avatarPlanUrl"] = (
                    f"/api/manifest-asset/avatar-plan?{params}"
                )
                files[-1]["modelUrl"] = (
                    f"/api/manifest-asset/model?{params}&lod=0"
                )
        directory = listing["directory"]
        self.send_json(
            {
                "scope": scope,
                "path": virtual_path,
                "directory": {
                    "scope": scope,
                    "path": virtual_path,
                    "name": MANIFEST_VIRTUAL_NAME if not inner_path else directory["name"],
                    "file_count": directory["file_count"],
                    "total_bytes": directory["total_bytes"],
                    "encrypted_count": 0,
                    "missing_chunk_count": 0,
                },
                "dirs": dirs,
                "files": files,
                "filePage": listing["filePage"],
                "virtual": {
                    "kind": "bundleManifest",
                    "manifestId": original["id"],
                    "bundleCount": int(listing["meta"]["bundleCount"]),
                    "assetCount": int(listing["meta"]["assetCount"]),
                },
            }
        )

    def handle_search(self, query: dict[str, list[str]]) -> None:
        scope = query.get("scope", ["effective"])[0]
        term = query.get("q", [""])[0].strip()
        limit = min(max(int(query.get("limit", ["100"])[0]), 1), 500)
        if not term:
            self.send_json({"items": []})
            return
        pattern = f"%{escape_sql_like(term)}%"
        with self.connect() as conn:
            rows = [
                row_to_dict(row)
                for row in conn.execute(
                    """
                    SELECT e.path, e.name, f.id, f.source, f.block_name, f.file_name,
                           f.chunk_file, f.chunk_exists, f.offset, f.length,
                           f.encrypted, f.iv_seed
                    FROM entries e
                    JOIN files f ON f.id = e.file_id
                    WHERE e.scope = ? AND e.type = 'file' AND e.path LIKE ? ESCAPE '\\'
                    ORDER BY e.path COLLATE NOCASE
                    LIMIT ?
                    """,
                    (scope, pattern, limit),
                )
            ]
        self.send_json({"items": rows, "limit": limit})

    def build_projectile_document(
        self,
        projectile_id: str,
        *,
        cancel_event: object | None = None,
    ) -> dict:
        """Resolve and decode one projectile through the local manifest/VFS chain."""

        resolved_manifest = self.resolve_logical_file_source(MANIFEST_LOGICAL_ID)
        if resolved_manifest is None:
            raise ProjectileUnavailableError(
                f"local VFS manifest is unavailable: {MANIFEST_LOGICAL_ID}"
            )
        manifest_record, manifest_chunk = resolved_manifest
        try:
            index = self.manifest_index(manifest_record, manifest_chunk)
            indexed_asset = select_projectile_asset(index, projectile_id)
            asset, bundle_record, bundle_chunk = self.resolve_index_asset_bundle(
                index,
                int(indexed_asset["assetIndex"]),
            )
        except ProjectileNotFoundError:
            raise
        except FileNotFoundError as error:
            raise ProjectileUnavailableError(str(error)) from error
        except (OSError, sqlite3.Error, ValueError) as error:
            raise ProjectileUnavailableError(f"cannot query the local manifest: {error}") from error

        try:
            ensured = self.ensure_manifest_projectile_component(
                bundle_record,
                bundle_chunk,
                asset,
                projectile_id,
                cancel_event=cancel_event,
            )
        except UnityWorkerError as error:
            if unity_worker_is_unavailable(error):
                raise ProjectileUnavailableError(str(error)) from error
            raise ProjectileDecodeError(f"Unity worker {error.code}: {error}") from error
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
            raise ProjectileUnavailableError(str(error)) from error
        if ensured is None:
            raise ProjectileDecodeError(
                "Unity worker did not export the projectile component"
            )

        export_root, export_meta = ensured
        exported_files = [str(value) for value in export_meta.get("exportedFiles", [])]
        parsed = load_projectile_export(
            export_root,
            exported_files,
            projectile_id,
        )
        component = parsed["component"]
        if component.get("$unparsed"):
            decode_status = "unparsed"
        elif component.get("$partial"):
            decode_status = "partial"
        else:
            decode_status = "decoded"

        return {
            "apiVersion": PROJECTILE_API_VERSION,
            "projectileId": projectile_id,
            "source": {
                "manifest": {
                    "recordId": int(manifest_record["id"]),
                    "source": manifest_record["source"],
                    "logicalId": manifest_record["logical_id"],
                },
                "asset": {
                    "assetIndex": int(asset["asset_index"]),
                    "path": asset["path"],
                    "pathHash": asset["path_hash"],
                    "size": int(asset["size"]),
                    "bundleIndex": int(asset["bundle_index"]),
                    "bundleName": asset["bundle_name"],
                },
                "bundle": {
                    "recordId": int(bundle_record["id"]),
                    "source": bundle_record["source"],
                    "logicalId": bundle_record["logical_id"],
                },
                "exportedFile": parsed["exportedFile"],
                "componentPointer": parsed["componentPointer"],
            },
            "decode": {
                "status": decode_status,
                "idMatchesRequest": parsed["idMatchesRequest"],
                "layout": component.get("layout"),
            },
            "projectileComponentData": component,
            "unityObject": parsed["unityObject"],
        }

    def handle_projectile(self, query: dict[str, list[str]]) -> None:
        raw_projectile_id = query.get("projectileId", [""])[0]
        try:
            projectile_id = normalize_projectile_id(raw_projectile_id)
        except ValueError as error:
            self.send_error_json(400, str(error))
            return
        try:
            payload = self.build_projectile_document(projectile_id)
        except ProjectileNotFoundError as error:
            self.send_error_json(404, str(error))
            return
        except ProjectileDecodeError as error:
            self.send_error_json(422, str(error))
            return
        except ProjectileUnavailableError as error:
            self.send_error_json(503, str(error))
            return
        except (OSError, sqlite3.Error, RuntimeError, subprocess.SubprocessError) as error:
            self.send_error_json(500, str(error))
            return
        self.send_json(payload, cache_control="private, max-age=3600", compress=True)

    def handle_audio_dialog_list(self, query: dict[str, list[str]]) -> None:
        language = query.get("language", ["chinese"])[0]
        path = unquote(query.get("path", [""])[0])
        try:
            page = max(int(query.get("page", ["1"])[0]), 1)
            page_size = min(
                max(int(query.get("pageSize", ["100"])[0]), 1),
                PAGE_SIZE_MAX,
            )
            with closing(self.connect_audio_dialog()) as conn:
                payload = list_audio_dialog_directory(
                    conn,
                    language,
                    path,
                    limit=page_size,
                    offset=(page - 1) * page_size,
                )
        except ValueError as error:
            self.send_error_json(400, str(error))
            return
        except FileNotFoundError as error:
            self.send_error_json(404, str(error))
            return
        except sqlite3.DatabaseError as error:
            self.send_error_json(500, f"AudioDialog index error: {error}")
            return
        payload["page"]["page"] = page
        payload["page"]["pageSize"] = page_size
        self.send_json(payload)

    def handle_audio_dialog_entry(self, query: dict[str, list[str]]) -> None:
        language = query.get("language", ["chinese"])[0]
        path = unquote(query.get("path", [""])[0])
        if not path:
            self.send_error_json(400, "AudioDialog path is required")
            return
        try:
            with closing(self.connect_audio_dialog()) as conn:
                entries = get_audio_dialog_entry(conn, language, path)
        except ValueError as error:
            self.send_error_json(400, str(error))
            return
        except FileNotFoundError as error:
            self.send_error_json(404, str(error))
            return
        except sqlite3.DatabaseError as error:
            self.send_error_json(500, f"AudioDialog index error: {error}")
            return
        if not entries:
            self.send_error_json(404, "AudioDialog entry not found")
            return
        self.send_json({"language": language, "path": path, "entries": entries})

    def resolve_audio_dialog_selection(
        self,
        query: dict[str, list[str]],
    ) -> tuple[str, str, dict] | None:
        language = query.get("language", ["chinese"])[0]
        path = unquote(query.get("path", [""])[0])
        if not path:
            self.send_error_json(400, "AudioDialog path is required")
            return None
        try:
            dialog_key_value = query.get("dialogKey", [None])[0]
            dialog_key = int(dialog_key_value) if dialog_key_value is not None else None
            with closing(self.connect_audio_dialog()) as conn:
                entries = get_audio_dialog_entry(conn, language, path)
        except ValueError as error:
            self.send_error_json(400, str(error))
            return None
        except FileNotFoundError as error:
            self.send_error_json(404, str(error))
            return None
        except sqlite3.DatabaseError as error:
            self.send_error_json(500, f"AudioDialog index error: {error}")
            return None
        if dialog_key is not None:
            entries = [entry for entry in entries if entry["dialog_key"] == dialog_key]
        if not entries:
            self.send_error_json(404, "AudioDialog entry not found")
            return None
        if len(entries) != 1:
            self.send_error_json(
                409,
                "AudioDialog path has multiple records; specify dialogKey",
            )
            return None
        return language, path, entries[0]

    def handle_audio_dialog_preview(self, query: dict[str, list[str]]) -> None:
        resolved = self.resolve_audio_dialog_selection(query)
        if resolved is None:
            return
        language, path, entry = resolved
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
        self.send_json({
            "kind": "audioDialog",
            "status": "ready" if playable else entry["match_status"],
            "language": language,
            "path": path,
            "entry": entry,
            **urls,
        })

    def handle_audio_dialog_raw(self, query: dict[str, list[str]]) -> None:
        resolved = self.resolve_audio_dialog_selection(query)
        if resolved is None:
            return
        _language, logical_path, dialog = resolved
        if dialog["match_status"] != "matched" or len(dialog["media"]) != 1:
            self.send_error_json(
                409,
                f"AudioDialog entry is not uniquely playable: {dialog['match_status']}",
            )
            return
        mode = query.get("format", ["wav"])[0].lower()
        if mode not in {"wem", "wav"}:
            self.send_error_json(400, "AudioDialog format must be wem or wav")
            return

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
        with closing(self.connect()) as conn:
            record = self.original_file_record(conn, int(media["pck_file_id"]))
            if record is None:
                return
            physical = self.resolve_file_record_quiet(conn, record)
        if physical is None:
            self.send_error_json(404, "AudioDialog PCK source is unavailable")
            return
        resolved_record, chunk_path = physical
        try:
            target = self.ensure_audio_dialog_media_file(
                resolved_record,
                chunk_path,
                entry,
                mode,
            )
        except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as error:
            self.send_error_json(500, str(error))
            return

        download = query.get("download", ["0"])[0] in {"1", "true", "yes"}
        disposition = "attachment" if download else "inline"
        self.send_response(200)
        self.send_header("Content-Type", guess_content_type(target.name))
        self.send_header("Content-Length", str(target.stat().st_size))
        download_name = Path(logical_path).with_suffix(f".{mode}").name or target.name
        self.send_header(
            "Content-Disposition",
            f"{disposition}; filename*=UTF-8''{quote(download_name)}",
        )
        self.end_headers()
        with target.open("rb") as file:
            while data := file.read(STREAM_CHUNK_SIZE):
                self.wfile.write(data)

    def handle_wwise_list(self, query: dict[str, list[str]]) -> None:
        path = unquote(query.get("path", [""])[0]).replace("\\", "/").strip("/")
        try:
            page = max(int(query.get("page", ["1"])[0]), 1)
            page_size = min(max(int(query.get("pageSize", ["100"])[0]), 1), PAGE_SIZE_MAX)
            offset = (page - 1) * page_size
            with closing(self.connect_wwise()) as conn:
                summary = wwise_summary(conn)
                dirs: list[dict] = []
                files: list[dict] = []
                total = 0
                if not path:
                    dirs = [
                        {"name": "Events", "path": "Events", "file_count": summary["eventCount"], "total_bytes": 0},
                        {"name": "Banks", "path": "Banks", "file_count": summary["bankCount"], "total_bytes": 0},
                        {"name": "Media", "path": "Media", "file_count": summary["mediaCount"], "total_bytes": summary["mediaBytes"]},
                    ]
                elif path == "Events":
                    total, rows = list_wwise_events(conn, limit=page_size, offset=offset)
                    files = [self.wwise_event_file(row) for row in rows]
                elif path == "Banks":
                    total, rows = list_wwise_banks(conn, limit=page_size, offset=offset)
                    files = [self.wwise_bank_file(row) for row in rows]
                elif path == "Media":
                    prefixes = list_wwise_media_prefixes(conn)
                    dirs = [
                        {
                            "name": row["prefix"],
                            "path": f"Media/{row['prefix']}",
                            "file_count": row["file_count"],
                            "total_bytes": row["total_bytes"],
                        }
                        for row in prefixes
                    ]
                elif path.startswith("Media/") and path.count("/") == 1:
                    prefix = path.split("/", 1)[1].casefold()
                    total, rows = list_wwise_media(
                        conn,
                        prefix,
                        limit=page_size,
                        offset=offset,
                    )
                    files = [self.wwise_media_file(row) for row in rows]
                else:
                    raise FileNotFoundError("Wwise virtual directory not found")
        except ValueError as error:
            self.send_error_json(400, str(error))
            return
        except FileNotFoundError as error:
            self.send_error_json(404, str(error))
            return
        except (sqlite3.DatabaseError, RuntimeError) as error:
            self.send_error_json(500, f"Wwise index error: {error}")
            return

        self.send_json({
            "path": path,
            "summary": summary,
            "directory": {
                "path": path,
                "file_count": total if path else sum(item["file_count"] for item in dirs),
                "total_bytes": sum(item["total_bytes"] for item in dirs),
                "encrypted_count": 0,
                "missing_chunk_count": 0,
            },
            "dirs": dirs,
            "files": files,
            "page": {
                "page": page,
                "pageSize": page_size,
                "total": total,
                "pages": max((total + page_size - 1) // page_size, 1),
            },
        })

    @staticmethod
    def wwise_event_file(row: dict) -> dict:
        params = (
            f"kind=event&pckFileId={row['pck_file_id']}&bankId={row['bank_id']}"
            f"&eventId={row['event_id']}"
        )
        return {
            "name": f"{row['event_id']}.event",
            "path": f"Events/{row['event_id']}.event",
            "file_name": row["logical_path"],
            "source": "Wwise Event",
            "block_name": str(row["bank_id"]),
            "chunk_file": f"{row['direct_relation_count']} direct relations",
            "offset": row["payload_offset"],
            "length": row["payload_size"],
            "encrypted": False,
            "chunk_exists": True,
            "virtualKind": "wwiseEvent",
            "previewUrl": f"/api/wwise/preview?{params}",
        }

    @staticmethod
    def wwise_bank_file(row: dict) -> dict:
        params = f"kind=bank&pckFileId={row['pck_file_id']}&bankId={row['bank_id']}"
        return {
            "name": f"{row['bank_id']}.bnk",
            "path": f"Banks/{row['bank_id']}.bnk",
            "file_name": row["logical_path"],
            "source": "Wwise Bank",
            "block_name": str(row["pck_file_id"]),
            "chunk_file": (
                f"{row['object_count']} objects / {row['relation_count']} relations"
                f" / {row['diagnostic_count']} diagnostics"
            ),
            "offset": row["offset"],
            "length": row["size"],
            "encrypted": bool(row["encrypted"]),
            "chunk_exists": True,
            "virtualKind": "wwiseBank",
            "previewUrl": f"/api/wwise/preview?{params}",
        }

    @staticmethod
    def wwise_media_file(row: dict) -> dict:
        params = f"kind=media&pckFileId={row['pck_file_id']}&ordinal={row['ordinal']}"
        return {
            "name": f"{row['media_id']}.wem",
            "path": f"Media/{row['media_id'][-2:]}/{row['media_id']}.wem",
            "file_name": row["logical_path"],
            "source": row["source"],
            "block_name": row["language"] or "sfx",
            "chunk_file": str(row["pck_file_id"]),
            "offset": row["offset"],
            "length": row["size"],
            "encrypted": bool(row["bank_encrypted"]),
            "chunk_exists": True,
            "virtualKind": "wwiseMedia",
            "previewUrl": f"/api/wwise/preview?{params}",
        }

    def handle_wwise_preview(self, query: dict[str, list[str]]) -> None:
        kind = query.get("kind", [""])[0]
        try:
            pck_file_id = int(query.get("pckFileId", [""])[0])
            with closing(self.connect_wwise()) as conn:
                if kind == "event":
                    bank_id = int(query.get("bankId", [""])[0])
                    event_id = int(query.get("eventId", [""])[0])
                    event = get_wwise_event(conn, pck_file_id, bank_id, event_id)
                    if event is None:
                        raise FileNotFoundError("Wwise event not found")
                    for media in event["media"]:
                        media["rawUrl"] = self.wwise_media_raw_url(media, "wav")
                        media["wemDownloadUrl"] = self.wwise_media_raw_url(media, "wem", download=True)
                    self.send_json({"kind": "wwiseEvent", "event": event})
                    return
                if kind == "bank":
                    bank_id = int(query.get("bankId", [""])[0])
                    bank = get_wwise_bank(conn, pck_file_id, bank_id)
                    if bank is None:
                        raise FileNotFoundError("Wwise bank not found")
                    self.send_json({"kind": "wwiseBank", "bank": bank})
                    return
                if kind == "media":
                    ordinal = int(query.get("ordinal", [""])[0])
                    media = get_wwise_media(conn, pck_file_id, ordinal)
                    if media is None:
                        raise FileNotFoundError("Wwise media not found")
                    self.send_json({
                        "kind": "wwiseMedia",
                        "media": media,
                        "rawUrl": self.wwise_media_raw_url(media, "wav"),
                        "wemDownloadUrl": self.wwise_media_raw_url(media, "wem", download=True),
                        "wavDownloadUrl": self.wwise_media_raw_url(media, "wav", download=True),
                    })
                    return
                raise ValueError("Wwise preview kind must be event, bank or media")
        except ValueError as error:
            self.send_error_json(400, str(error))
        except FileNotFoundError as error:
            self.send_error_json(404, str(error))
        except (sqlite3.DatabaseError, RuntimeError) as error:
            self.send_error_json(500, f"Wwise index error: {error}")

    @staticmethod
    def wwise_media_raw_url(media: dict, mode: str, *, download: bool = False) -> str:
        url = (
            f"/api/wwise/raw?pckFileId={media['pck_file_id']}"
            f"&ordinal={media['ordinal']}&format={mode}"
        )
        return f"{url}&download=1" if download else url

    def handle_wwise_raw(self, query: dict[str, list[str]]) -> None:
        try:
            pck_file_id = int(query.get("pckFileId", [""])[0])
            ordinal = int(query.get("ordinal", [""])[0])
            mode = query.get("format", ["wav"])[0].lower()
            if mode not in {"wem", "wav"}:
                raise ValueError("Wwise media format must be wem or wav")
            with closing(self.connect_wwise()) as conn:
                media = get_wwise_media(conn, pck_file_id, ordinal)
            if media is None:
                raise FileNotFoundError("Wwise media not found")
        except ValueError as error:
            self.send_error_json(400, str(error))
            return
        except FileNotFoundError as error:
            self.send_error_json(404, str(error))
            return
        except (sqlite3.DatabaseError, RuntimeError) as error:
            self.send_error_json(500, f"Wwise index error: {error}")
            return

        entry = AudioEntry(
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
        with closing(self.connect()) as conn:
            record = self.original_file_record(conn, pck_file_id)
            physical = self.resolve_file_record_quiet(conn, record) if record else None
        if physical is None:
            self.send_error_json(404, "Wwise PCK source is unavailable")
            return
        resolved_record, chunk_path = physical
        try:
            target = self.ensure_indexed_audio_media_file(
                resolved_record,
                chunk_path,
                entry,
                mode,
                "wwise",
            )
        except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as error:
            self.send_error_json(500, str(error))
            return
        download = query.get("download", ["0"])[0] in {"1", "true", "yes"}
        disposition = "attachment" if download else "inline"
        self.send_response(200)
        self.send_header("Content-Type", guess_content_type(target.name))
        self.send_header("Content-Length", str(target.stat().st_size))
        self.send_header(
            "Content-Disposition",
            f"{disposition}; filename={entry.wem_id}.{mode}",
        )
        self.end_headers()
        with target.open("rb") as file:
            while data := file.read(STREAM_CHUNK_SIZE):
                self.wfile.write(data)

    def handle_file(self, query: dict[str, list[str]]) -> None:
        try:
            file_id = int(query.get("id", [""])[0])
        except ValueError:
            self.send_error_json(400, "invalid file id")
            return
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
            if row is None:
                self.send_error_json(404, "file not found")
                return
            self.send_json(row_to_dict(row))

    def original_file_record(self, conn: sqlite3.Connection, file_id: int) -> dict | None:
        row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        if row is None:
            self.send_error_json(404, "file not found")
            return None
        return row_to_dict(row)

    def file_id_from_query(self, query: dict[str, list[str]]) -> int | None:
        try:
            return int(query.get("id", [""])[0])
        except ValueError:
            self.send_error_json(400, "invalid file id")
            return None

    def resolve_file_record(self, conn: sqlite3.Connection, file_id: int) -> tuple[dict, dict, Path] | None:
        original_dict = self.original_file_record(conn, file_id)
        if original_dict is None:
            return None
        resolved = self.resolve_file_record_quiet(conn, original_dict)
        if resolved is not None:
            record, chunk_path = resolved
            return original_dict, record, chunk_path

        self.send_error_json(
            404,
            "chunk not found; this record likely requires a source fallback that is unavailable on this host",
        )
        return None

    def resolve_file_record_quiet(self, conn: sqlite3.Connection, original_dict: dict) -> tuple[dict, Path] | None:
        original_path = Path(original_dict["chunk_path"])
        if original_path.exists():
            return original_dict, original_path
        candidates = [
            row_to_dict(row)
            for row in conn.execute(
                """
                SELECT * FROM files
                WHERE logical_id = ?
                """,
                (original_dict["logical_id"],),
            )
        ]
        candidates.sort(key=lambda row: source_rank(row["source"], bool(row["chunk_exists"])))
        for candidate in candidates:
            candidate_path = Path(candidate["chunk_path"])
            if candidate_path.exists():
                return candidate, candidate_path
        return None

    def read_file_slice(self, record: dict, chunk_path: Path, limit: int | None = None) -> bytes:
        length = int(record["length"])
        if limit is not None:
            length = min(length, limit)
        with chunk_path.open("rb") as file:
            file.seek(int(record["offset"]))
            data = file.read(length)
        if record.get("encrypted"):
            data = decrypt_vfs_file(data, int(record["iv_seed"]))
        return data

    def manifest_index(self, record: dict, chunk_path: Path) -> ManifestIndex:
        key = (
            int(record["id"]),
            int(record["length"]),
            str(record.get("file_data_md5") or ""),
        )
        with self.manifest_index_lock:
            cached = self.manifest_indexes.get(key)
            if cached is not None:
                return cached
            index = ManifestIndex.ensure(
                self.read_file_slice(record, chunk_path),
                INTERNAL_CACHE_DIR / "manifests",
            )
            self.manifest_indexes[key] = index
            return index

    def read_file_range(self, record: dict, chunk_path: Path, relative_offset: int, length: int) -> bytes:
        file_length = int(record["length"])
        if relative_offset < 0 or length < 0 or relative_offset + length > file_length:
            raise ValueError("file range is outside the VFS record")
        if record.get("encrypted"):
            return self.read_file_slice(record, chunk_path)[relative_offset : relative_offset + length]
        with chunk_path.open("rb") as file:
            file.seek(int(record["offset"]) + relative_offset)
            return file.read(length)

    def resolve_logical_file_source(self, logical_id: str) -> tuple[dict, Path] | None:
        """Resolve one local VFS logical file, preferring its effective entry."""

        with closing(self.connect()) as conn:
            effective_ids = {
                int(row[0])
                for row in conn.execute(
                    """
                    SELECT file_id FROM entries
                    WHERE scope = 'effective' AND type = 'file' AND path = ?
                      AND file_id IS NOT NULL
                    """,
                    (logical_id,),
                )
            }
            candidates = [
                row_to_dict(row)
                for row in conn.execute(
                    "SELECT * FROM files WHERE logical_id = ?",
                    (logical_id,),
                )
            ]
        candidates.sort(
            key=lambda row: (
                0 if int(row["id"]) in effective_ids else 1,
                *source_rank(row["source"], bool(row["chunk_exists"])),
            )
        )
        for candidate in candidates:
            chunk_path = Path(candidate["chunk_path"])
            if chunk_path.is_file():
                return candidate, chunk_path
        return None

    def ensure_string_path_hash_file(self) -> tuple[Path, dict]:
        """Materialize the effective runtime path table into the shared cache."""

        resolved = self.resolve_logical_file_source(STRING_PATH_HASH_LOGICAL_ID)
        if resolved is None:
            raise FileNotFoundError(
                f"local VFS file is unavailable: {STRING_PATH_HASH_LOGICAL_ID}"
            )
        record, chunk_path = resolved
        root = INTERNAL_CACHE_DIR / "shared" / "string-path-hash"
        target = root / "StringPathHash.bin"
        meta_path = root / "meta.json"
        identity = {
            "recordId": int(record["id"]),
            "length": int(record["length"]),
            "offset": int(record["offset"]),
            "chunkPath": str(record["chunk_path"]),
            "chunkMtimeNs": chunk_path.stat().st_mtime_ns,
            "fileDataMd5": str(record.get("file_data_md5") or ""),
        }

        with self.shared_resource_lock:
            if target.is_file() and meta_path.is_file():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    if (
                        meta.get("source") == identity
                        and target.stat().st_size == int(record["length"])
                    ):
                        return target, meta
                except (OSError, json.JSONDecodeError):
                    pass
            root.mkdir(parents=True, exist_ok=True)
            target.unlink(missing_ok=True)
            self.write_file_slice(record, chunk_path, target)
            meta = {
                "version": 1,
                "source": identity,
                "builtAtEpoch": int(time.time()),
            }
            meta_path.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        return target, meta

    def write_file_slice(self, record: dict, chunk_path: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        expected_size = int(record["length"])
        if target.exists() and target.stat().st_size == expected_size:
            return

        tmp = target.with_suffix(target.suffix + ".tmp")
        if record.get("encrypted"):
            tmp.write_bytes(self.read_file_slice(record, chunk_path))
        else:
            with chunk_path.open("rb") as source, tmp.open("wb") as output:
                source.seek(int(record["offset"]))
                remaining = expected_size
                while remaining > 0:
                    data = source.read(min(STREAM_CHUNK_SIZE, remaining))
                    if not data:
                        break
                    output.write(data)
                    remaining -= len(data)
        os.replace(tmp, target)

    def model_snapshot_paths(self, record: dict, asset_index: int) -> tuple[Path, Path, Path, Path]:
        root = INTERNAL_CACHE_DIR / str(record["id"]) / "models" / str(asset_index)
        return root / "source.ab", root / "objects", root / "model.json", root / "run.json"

    def avatar_model_snapshot_paths(
        self,
        record: dict,
        asset_index: int,
        lod: int,
    ) -> tuple[Path, Path, Path, Path]:
        root = (
            INTERNAL_CACHE_DIR
            / str(record["id"])
            / "models"
            / str(asset_index)
            / f"avatar-lod-{lod}"
        )
        return root / "inputs", root / "objects", root / "model.json", root / "run.json"

    def animation_clip_export_paths(
        self,
        record: dict,
        asset_index: int,
    ) -> tuple[Path, Path, Path]:
        root = (
            INTERNAL_CACHE_DIR
            / str(record["id"])
            / "manifest-assets"
            / str(asset_index)
            / "animation"
        )
        return root / "source.ab", root / "exported", root / "meta.json"

    def ensure_animation_clip_export(
        self,
        record: dict,
        chunk_path: Path,
        asset: dict,
    ) -> tuple[dict, Path, dict]:
        if not ANIMESTUDIO_CLI.exists():
            raise FileNotFoundError(f"AnimeStudio.CLI not found: {ANIMESTUDIO_CLI}")

        source_path, export_root, meta_path = self.animation_clip_export_paths(
            record,
            int(asset["asset_index"]),
        )
        source_identity = {
            "recordId": int(record["id"]),
            "length": int(record["length"]),
            "offset": int(record["offset"]),
            "chunkPath": str(record["chunk_path"]),
            "chunkMtimeNs": chunk_path.stat().st_mtime_ns,
            "assetIndex": int(asset["asset_index"]),
            "assetPath": str(asset["path"]),
            "toolArtifacts": dotnet_tool_identity(ANIMESTUDIO_CLI),
        }
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                target = export_root / str(meta.get("relativePath") or "")
                if (
                    meta.get("version") == ANIMATION_CLIP_EXPORT_VERSION
                    and meta.get("source") == source_identity
                    and target.is_file()
                ):
                    return json.loads(target.read_text(encoding="utf-8")), target, meta
            except (OSError, ValueError):
                pass

        source_path.parent.mkdir(parents=True, exist_ok=True)
        self.write_file_slice(record, chunk_path, source_path)
        shutil.rmtree(export_root, ignore_errors=True)
        export_root.mkdir(parents=True, exist_ok=True)
        animation_name = str(asset["path"]).rsplit("##", 1)[-1]
        # FBX 子资源直接使用 clip 名；独立 .anim 资源则需要从逻辑路径取文件名。
        animation_name = animation_name.replace("\\", "/").rsplit("/", 1)[-1]
        if animation_name.casefold().endswith(".anim"):
            animation_name = animation_name[:-5]
        command = [
            str(ANIMESTUDIO_CLI),
            str(source_path),
            str(export_root),
            "--game",
            "ArknightsEndfield",
            "--types",
            "AnimationClip",
            "--export_type",
            "AnimationJSON",
            "--group_assets",
            "ByType",
            "--logger_flags",
            "Error",
            "Warning",
            "Info",
        ]
        completed = subprocess.run(
            command,
            cwd=str(ANIMESTUDIO_CLI.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"AnimeStudio animation export failed: "
                f"{completed.stderr.strip() or completed.stdout.strip()}"
            )
        clip, target = load_unique_animation_clip(export_root, animation_name)
        meta = {
            "version": ANIMATION_CLIP_EXPORT_VERSION,
            "source": source_identity,
            "relativePath": target.relative_to(export_root).as_posix(),
            "command": command,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "builtAtEpoch": int(time.time()),
        }
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return clip, target, meta

    def ensure_model_hierarchy(
        self,
        record: dict,
        chunk_path: Path,
        asset: dict,
        dependency_bundles: list[dict],
        dependency_sources: list[tuple[dict, Path]],
        missing_dependency_bundles: list[dict],
    ) -> tuple[dict, dict]:
        source_path, object_root, model_path, run_path = self.model_snapshot_paths(
            record, int(asset["asset_index"])
        )
        geometry_path = model_path.with_name("geometry.bin")
        texture_root = model_path.parent / "textures"
        model_builder_path = Path(build_hierarchy_document.__code__.co_filename)
        source_identity = {
            "recordId": int(record["id"]),
            "length": int(record["length"]),
            "offset": int(record["offset"]),
            "chunkPath": str(record["chunk_path"]),
            "chunkMtimeNs": chunk_path.stat().st_mtime_ns,
            "assetIndex": int(asset["asset_index"]),
            "assetPath": str(asset["path"]),
            "bundleName": str(asset["bundle_name"]),
            # 解析逻辑变化后自动废弃旧 ModelDocument；跨文件协议变化仍由
            # MODEL_SNAPSHOT_VERSION 显式控制。
            "modelBuilderMtimeNs": model_builder_path.stat().st_mtime_ns,
            "dependencies": [
                {
                    "recordId": int(dependency["id"]),
                    "length": int(dependency["length"]),
                    "offset": int(dependency["offset"]),
                    "chunkPath": str(dependency["chunk_path"]),
                    "chunkMtimeNs": dependency_chunk.stat().st_mtime_ns,
                }
                for dependency, dependency_chunk in dependency_sources
            ],
            "missingDependencyBundles": missing_dependency_bundles,
            "toolArtifacts": UNITY_WORKER.artifact_identity(),
        }
        if model_path.exists() and run_path.exists():
            try:
                run_meta = json.loads(run_path.read_text(encoding="utf-8"))
                document = json.loads(model_path.read_text(encoding="utf-8"))
                if (
                    run_meta.get("version") == MODEL_SNAPSHOT_VERSION
                    and run_meta.get("source") == source_identity
                    and (not document.get("buffers") or geometry_path.exists())
                    and (not document.get("images") or texture_root.exists())
                    and not validate_model_document(document)
                ):
                    return document, run_meta
            except (OSError, json.JSONDecodeError):
                pass

        source_path.parent.mkdir(parents=True, exist_ok=True)
        input_root = source_path.parent / "inputs"
        shutil.rmtree(input_root, ignore_errors=True)
        input_root.mkdir(parents=True, exist_ok=True)
        source_path = input_root / "entry.ab"
        self.write_file_slice(record, chunk_path, source_path)
        for dependency, dependency_chunk in dependency_sources:
            dependency_path = input_root / f"dependency-{int(dependency['id'])}.ab"
            self.write_file_slice(dependency, dependency_chunk, dependency_path)
        shutil.rmtree(object_root, ignore_errors=True)
        object_root.mkdir(parents=True, exist_ok=True)
        staged_inputs = [
            {"inputId": "manifest:primary", "inputPath": str(source_path)},
            *[
                {
                    "inputId": f"record:{int(dependency['id'])}",
                    "inputPath": str(input_root / f"dependency-{int(dependency['id'])}.ab"),
                }
                for dependency, _dependency_chunk in dependency_sources
            ],
        ]
        cab_root = object_root.parent / "cab-map"
        shutil.rmtree(cab_root, ignore_errors=True)
        cab_result = UNITY_WORKER.build_cab_map(
            inputs=staged_inputs,
            output_directory=cab_root,
            request_id=(
                f"model-cab-{int(record['id'])}-{int(asset['asset_index'])}-"
                f"{time.time_ns()}"
            ),
        )
        validate_worker_artifacts(cab_root, cab_result)
        object_result = UNITY_WORKER.export_object_snapshots(
            inputs=staged_inputs,
            cab_map_path=cab_root / "cab-map.json",
            primary_input_id="manifest:primary",
            selection_input_ids=[value["inputId"] for value in staged_inputs],
            included_types=MODEL_SNAPSHOT_TYPES,
            containers=[],
            output_directory=object_root,
            request_id=(
                f"model-objects-{int(record['id'])}-{int(asset['asset_index'])}-"
                f"{time.time_ns()}"
            ),
        )
        validate_worker_artifacts(object_root, object_result)
        completed_steps = [
            {"name": "buildCABMap", "workerResult": cab_result},
            {"name": "exportObjectSnapshots", "workerResult": object_result},
        ]
        run_meta = {
            "version": MODEL_SNAPSHOT_VERSION,
            "source": source_identity,
            "steps": completed_steps,
            "builtAtEpoch": int(time.time()),
            "scope": "manifestDependencyClosure",
            "dependencyBundles": dependency_bundles,
            "missingDependencyBundles": missing_dependency_bundles,
        }
        objects = load_animestudio_objects(object_root)
        if not objects:
            bare_snapshots = sum(
                1
                for asset_type in ("GameObject", "Transform")
                for _ in (object_root / asset_type).glob("*.json")
            )
            if bare_snapshots:
                raise RuntimeError(
                    f"AnimeStudio exported {bare_snapshots} GameObject/Transform JSON files "
                    "without required $animestudio identity metadata"
                )
            raise RuntimeError("AnimeStudio produced no GameObject/Transform JSON snapshots")
        entry = find_container_root_game_object(objects, str(asset["path"]))
        document = build_hierarchy_document(
            objects,
            entry,
            logical_path=str(asset["path"]),
            bundle=str(asset["bundle_name"]),
        )
        geometry = attach_mesh_geometry(
            document,
            objects,
            buffer_uri=(
                f"/api/manifest-asset/model-buffer?recordId={int(record['id'])}"
                f"&assetIndex={int(asset['asset_index'])}"
            ),
        )
        textures = collect_material_textures(document, objects)
        image_uris = {}
        if textures:
            shutil.rmtree(texture_root, ignore_errors=True)
            texture_result = UNITY_WORKER.export_identified_textures(
                inputs=staged_inputs,
                cab_map_path=cab_root / "cab-map.json",
                primary_input_id="manifest:primary",
                selections=[
                    {"sourceFile": identity.source_file, "pathId": identity.path_id}
                    for identity in textures
                ],
                output_directory=texture_root,
                request_id=(
                    f"model-textures-{int(record['id'])}-{int(asset['asset_index'])}-"
                    f"{time.time_ns()}"
                ),
            )
            validate_worker_artifacts(texture_root, texture_result)
            completed_steps.append(
                {
                    "name": "exportIdentifiedTextures",
                    "workerResult": texture_result,
                }
            )
            texture_ids = {
                (identity.source_file.casefold(), identity.path_id): identity
                for identity in textures
            }
            for artifact in texture_result.get("artifacts", []):
                identity = texture_ids.get(
                    (str(artifact.get("sourceFile") or "").casefold(), int(artifact["pathId"]))
                )
                if identity is not None:
                    image_uris[identity] = (
                        f"/api/manifest-asset/model-texture?recordId={int(record['id'])}"
                        f"&assetIndex={int(asset['asset_index'])}"
                        f"&path={quote(str(artifact['relativePath']))}"
                    )
            attach_texture_images(document, textures, image_uris)
            missing_textures = [
                texture_id.document_id for texture_id in textures if texture_id not in image_uris
            ]
            if missing_textures:
                document["diagnostics"].append(
                    {
                        "severity": "warning",
                        "code": "MODEL_TEXTURES_MISSING",
                        "message": "部分模型纹理未能导出为预览图片。",
                        "details": {"textureIds": missing_textures},
                    }
                )
        if missing_dependency_bundles:
            document["diagnostics"].append(
                {
                    "severity": "warning",
                    "code": "DEPENDENCY_BUNDLES_MISSING",
                    "message": "部分跨 Bundle 依赖在当前 VFS 中不可用，模型层级可能不完整。",
                    "details": {"bundles": missing_dependency_bundles},
                }
            )
        validation_errors = validate_model_document(document)
        if validation_errors:
            run_meta["validationErrors"] = validation_errors
            raise RuntimeError("generated ModelDocument failed semantic validation")
        model_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if geometry:
            geometry_path.write_bytes(geometry)
        elif geometry_path.exists():
            geometry_path.unlink()
        temporary_run_path = run_path.with_name(f".{run_path.name}.{uuid.uuid4().hex}.tmp")
        temporary_run_path.write_text(
            json.dumps(run_meta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_run_path, run_path)
        return document, run_meta

    def load_avatar_mesh_plan(
        self,
        index: ManifestIndex,
        asset: dict,
        bundle_record: dict,
        bundle_chunk: Path,
        lod: int,
    ) -> tuple[dict, dict, dict]:
        exported = self.ensure_manifest_monobehaviour_dump(
            bundle_record,
            bundle_chunk,
            asset,
        )
        if exported is None:
            raise RuntimeError("AnimeStudio produced no AvatarMesh TypeTree dump")
        dump_path, dump_meta = exported
        avatar_mesh = parse_avatar_mesh(
            dump_path.read_text(encoding="utf-8", errors="replace")
        )
        path_hash_file, path_hash_meta = self.ensure_string_path_hash_file()
        attach_resolved_paths(avatar_mesh, StringPathHashIndex(path_hash_file))
        plan = build_avatar_mesh_resource_plan(index, avatar_mesh, lod=lod)
        return avatar_mesh, plan, {
            "dump": dump_meta,
            "stringPathHash": path_hash_meta,
        }

    def avatar_mesh_bundle_closure(
        self,
        index: ManifestIndex,
        plan: dict,
    ) -> list[dict]:
        bundles: dict[int, dict] = {}
        for direct in plan.get("bundles", []):
            bundle_index = int(direct["bundleIndex"])
            bundles[bundle_index] = {
                "bundleIndex": bundle_index,
                "name": str(direct["bundleName"]),
            }
            for dependency in index.bundle_dependencies(bundle_index):
                bundles[int(dependency["bundleIndex"])] = dependency
        return [bundles[key] for key in sorted(bundles)]

    def ensure_avatar_mesh_model(
        self,
        index: ManifestIndex,
        asset: dict,
        bundle_record: dict,
        bundle_chunk: Path,
        lod: int,
    ) -> tuple[dict, dict, Path]:
        if lod not in range(4):
            raise ValueError(f"LOD must be in 0..3, got {lod}")

        avatar_mesh, plan, plan_meta = self.load_avatar_mesh_plan(
            index,
            asset,
            bundle_record,
            bundle_chunk,
            lod,
        )
        bundles = self.avatar_mesh_bundle_closure(index, plan)
        bundle_sources, missing_bundles = self.resolve_bundle_sources(bundles)
        if missing_bundles:
            names = ", ".join(str(bundle["name"]) for bundle in missing_bundles)
            raise FileNotFoundError(f"AvatarMesh dependency bundles are missing: {names}")

        input_root, object_root, model_path, run_path = self.avatar_model_snapshot_paths(
            bundle_record,
            int(asset["asset_index"]),
            lod,
        )
        geometry_path = model_path.with_name("geometry.bin")
        texture_root = model_path.parent / "textures"
        builder_paths = [
            Path(build_static_avatar_mesh_document.__code__.co_filename),
            Path(selected_container_paths.__code__.co_filename),
            Path(__file__).with_name("animestudio_model.py"),
        ]
        source_identity = {
            "entry": {
                "recordId": int(bundle_record["id"]),
                "length": int(bundle_record["length"]),
                "offset": int(bundle_record["offset"]),
                "chunkPath": str(bundle_record["chunk_path"]),
                "chunkMtimeNs": bundle_chunk.stat().st_mtime_ns,
                "assetIndex": int(asset["asset_index"]),
                "assetPath": str(asset["path"]),
            },
            "lod": lod,
            "avatarMesh": avatar_mesh,
            "resourcePlan": plan,
            "bundles": [
                {
                    "recordId": int(record["id"]),
                    "length": int(record["length"]),
                    "offset": int(record["offset"]),
                    "chunkPath": str(record["chunk_path"]),
                    "chunkMtimeNs": chunk.stat().st_mtime_ns,
                }
                for record, chunk in bundle_sources
            ],
            "builders": {
                path.name: path.stat().st_mtime_ns for path in builder_paths
            },
            "toolArtifacts": UNITY_WORKER.artifact_identity(),
        }
        if model_path.is_file() and run_path.is_file():
            try:
                run_meta = json.loads(run_path.read_text(encoding="utf-8"))
                document = json.loads(model_path.read_text(encoding="utf-8"))
                if (
                    run_meta.get("version") == AVATAR_MODEL_SNAPSHOT_VERSION
                    and run_meta.get("source") == source_identity
                    and geometry_path.is_file()
                    and not validate_model_document(document)
                ):
                    return document, run_meta, model_path
            except (OSError, json.JSONDecodeError):
                pass

        shutil.rmtree(input_root, ignore_errors=True)
        shutil.rmtree(object_root, ignore_errors=True)
        shutil.rmtree(texture_root, ignore_errors=True)
        input_root.mkdir(parents=True, exist_ok=True)
        object_root.mkdir(parents=True, exist_ok=True)
        for record, chunk in bundle_sources:
            self.write_file_slice(
                record,
                chunk,
                input_root / f"bundle-{int(record['id'])}.ab",
            )

        staged_inputs = [
            {
                "inputId": f"record:{int(record['id'])}",
                "inputPath": str(input_root / f"bundle-{int(record['id'])}.ab"),
            }
            for record, _chunk in bundle_sources
        ]
        if not staged_inputs:
            raise RuntimeError("AvatarMesh resource plan produced no Bundle inputs")
        primary_input_id = staged_inputs[0]["inputId"]
        cab_root = object_root.parent / "cab-map"
        shutil.rmtree(cab_root, ignore_errors=True)
        cab_result = UNITY_WORKER.build_cab_map(
            inputs=staged_inputs,
            output_directory=cab_root,
            request_id=(
                f"avatar-cab-{int(bundle_record['id'])}-"
                f"{int(asset['asset_index'])}-lod{lod}-{time.time_ns()}"
            ),
        )
        validate_worker_artifacts(cab_root, cab_result)
        object_result = UNITY_WORKER.export_object_snapshots(
            inputs=staged_inputs,
            cab_map_path=cab_root / "cab-map.json",
            primary_input_id=primary_input_id,
            selection_input_ids=[value["inputId"] for value in staged_inputs],
            included_types=["Mesh", "Material", "Avatar"],
            containers=selected_container_paths(plan),
            output_directory=object_root,
            request_id=(
                f"avatar-objects-{int(bundle_record['id'])}-"
                f"{int(asset['asset_index'])}-lod{lod}-{time.time_ns()}"
            ),
        )
        validate_worker_artifacts(object_root, object_result)
        completed_steps = [
            {"name": "buildCABMap", "workerResult": cab_result},
            {"name": "exportObjectSnapshots", "workerResult": object_result},
        ]

        meshes, materials, avatar = load_exported_objects(object_root, plan)
        texture_selections = material_texture_selections(materials)
        texture_uris = {}
        if texture_selections:
            texture_result = UNITY_WORKER.export_identified_textures(
                inputs=staged_inputs,
                cab_map_path=cab_root / "cab-map.json",
                primary_input_id=primary_input_id,
                selections=texture_selections,
                output_directory=texture_root,
                request_id=(
                    f"avatar-textures-{int(bundle_record['id'])}-"
                    f"{int(asset['asset_index'])}-lod{lod}-{time.time_ns()}"
                ),
            )
            validate_worker_artifacts(texture_root, texture_result)
            completed_steps.append({
                "name": "exportIdentifiedTextures",
                "workerResult": texture_result,
            })
            selection_names = {
                (str(value["sourceFile"]).casefold(), int(value["pathId"])): str(value["name"])
                for value in texture_selections
            }
            for artifact in texture_result.get("artifacts", []):
                name = selection_names.get(
                    (str(artifact.get("sourceFile") or "").casefold(), int(artifact["pathId"]))
                )
                if name:
                    if name in texture_uris:
                        raise RuntimeError(f"AvatarMesh selects duplicate Texture2D name: {name}")
                    texture_uris[name] = (
                        f"/api/manifest-asset/model-texture?recordId={int(bundle_record['id'])}"
                        f"&assetIndex={int(asset['asset_index'])}&lod={lod}"
                        f"&path={quote(str(artifact['relativePath']))}"
                    )

        document, geometry = build_static_avatar_mesh_document(
            avatar_mesh,
            meshes,
            lod=lod,
            avatar=avatar,
            material_payloads=materials,
            texture_uris=texture_uris,
            buffer_uri=(
                f"/api/manifest-asset/model-buffer?recordId={int(bundle_record['id'])}"
                f"&assetIndex={int(asset['asset_index'])}&lod={lod}"
            ),
        )
        run_meta = {
            "version": AVATAR_MODEL_SNAPSHOT_VERSION,
            "source": source_identity,
            "scope": "avatarMeshBundleClosure",
            "resourcePlan": plan,
            "planRun": plan_meta,
            "steps": completed_steps,
            "builtAtEpoch": int(time.time()),
        }
        model_path.parent.mkdir(parents=True, exist_ok=True)
        model_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        geometry_path.write_bytes(geometry)
        temporary_run_path = run_path.with_name(f".{run_path.name}.{uuid.uuid4().hex}.tmp")
        temporary_run_path.write_text(
            json.dumps(run_meta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_run_path, run_path)
        return document, run_meta, model_path

    def assetbundle_cache_paths(self, record: dict) -> tuple[Path, Path, Path]:
        cache_root = INTERNAL_CACHE_DIR / str(record["id"])
        source_path = cache_root / "source.ab"
        export_root = cache_root / "exported"
        meta_path = cache_root / "meta.json"
        return source_path, export_root, meta_path

    def manifest_unity_worker_export_paths(
        self,
        record: dict,
        asset_index: int,
        export_name: str,
    ) -> tuple[Path, Path]:
        """返回某类 worker 产物的版本目录和当前版本原子指针。"""

        root = (
            INTERNAL_CACHE_DIR
            / str(record["id"])
            / "manifest-assets"
            / str(asset_index)
            / export_name
        )
        return root / "runs", root / "meta.json"

    def ensure_manifest_unity_worker_export(
        self,
        record: dict,
        chunk_path: Path,
        asset: dict,
        *,
        export_name: str,
        version: int,
        identity_extra: dict,
        invoke: Callable[[Path, Path, str, str, object | None], dict],
        derive: Callable[[Path, list[Path]], dict[str, str]] | None = None,
        cancel_event: object | None = None,
    ) -> tuple[Path, list[Path], dict]:
        """执行 worker 操作，完整校验全部产物后原子发布缓存指针。"""

        if file_suffix(str(asset["path"])) not in {".asset", ".prefab"}:
            raise ValueError("Unity worker MonoBehaviour export requires an .asset or .prefab")

        runs_root, meta_path = self.manifest_unity_worker_export_paths(
            record,
            int(asset["asset_index"]),
            export_name,
        )
        normalized_container = str(asset["path"]).replace("\\", "/").strip("/")
        source_identity = {
            "recordId": int(record["id"]),
            "length": int(record["length"]),
            "offset": int(record["offset"]),
            "chunkPath": str(record["chunk_path"]),
            "chunkMtimeNs": chunk_path.stat().st_mtime_ns,
            "assetIndex": int(asset["asset_index"]),
            "assetPath": normalized_container,
            "toolArtifacts": UNITY_WORKER.artifact_identity(),
            **identity_extra,
        }

        def run_export(
            run_root: Path,
            export_root: Path,
            request_id: str,
            cancel: object | None,
        ) -> dict:
            source_path = run_root / "source.ab"
            self.write_file_slice(record, chunk_path, source_path)
            return invoke(
                source_path,
                export_root,
                normalized_container,
                request_id,
                cancel,
            )

        return self.ensure_unity_worker_run(
            runs_root=runs_root,
            meta_path=meta_path,
            request_prefix=f"{export_name}-{int(asset['asset_index'])}",
            version=version,
            source_identity=source_identity,
            invoke=run_export,
            derive=derive,
            cancel_event=cancel_event,
        )

    def ensure_unity_worker_run(
        self,
        *,
        runs_root: Path,
        meta_path: Path,
        request_prefix: str,
        version: int,
        source_identity: dict,
        invoke: Callable[[Path, Path, str, object | None], dict],
        derive: Callable[[Path, list[Path]], dict[str, str]] | None = None,
        cancel_event: object | None = None,
    ) -> tuple[Path, list[Path], dict]:
        """校验并原子发布任意 worker run；输入布局由具体能力负责。"""

        # meta.json 是唯一已发布指针；缓存读取不能扫描尚未完成或已经过期的 run。
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                run_name = str(meta.get("selectedRun") or "")
                selected_run = safe_relative_path(runs_root, run_name)
                export_root = selected_run / "exported" if selected_run is not None else None
                if (
                    meta.get("version") == version
                    and meta.get("source") == source_identity
                    and export_root is not None
                    and export_root.is_dir()
                ):
                    cached_artifacts = validate_worker_artifacts(
                        export_root,
                        meta.get("workerResult"),
                    )
                    expected_files = [path.relative_to(export_root).as_posix() for path in cached_artifacts]
                    if meta.get("exportedFiles") != expected_files:
                        raise RuntimeError("cached worker artifact list is inconsistent")
                    validate_derived_artifacts(export_root, meta.get("derivedFiles", {}))
                    return export_root, cached_artifacts, meta
            except (OSError, json.JSONDecodeError, TypeError, RuntimeError):
                pass

        request_id = f"{request_prefix}-{time.time_ns()}-{uuid.uuid4().hex}"
        run_root = runs_root / request_id
        export_root = run_root / "exported"
        result = invoke(run_root, export_root, request_id, cancel_event)
        artifact_paths = validate_worker_artifacts(export_root, result)
        derived_files = describe_derived_artifacts(
            export_root,
            derive(export_root, artifact_paths) if derive is not None else {},
        )

        meta = {
            "version": version,
            "source": source_identity,
            "selectedRun": request_id,
            "exportedFiles": [path.relative_to(export_root).as_posix() for path in artifact_paths],
            "derivedFiles": derived_files,
            "workerResult": result,
            "builtAtEpoch": int(time.time()),
        }
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_meta = meta_path.with_name(f".{meta_path.name}.{uuid.uuid4().hex}.tmp")
        temporary_meta.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_meta, meta_path)
        return export_root, artifact_paths, meta

    def ensure_manifest_projectile_component(
        self,
        record: dict,
        chunk_path: Path,
        asset: dict,
        projectile_id: str,
        *,
        cancel_event: object | None = None,
    ) -> tuple[Path, dict] | None:
        """通过共用原子导出框架生成一个聚焦 Projectile 组件。"""

        if file_suffix(str(asset["path"])) not in {".asset", ".prefab"}:
            return None

        try:
            export_root, artifact_paths, meta = self.ensure_manifest_unity_worker_export(
                record,
                chunk_path,
                asset,
                export_name="projectile-component",
                version=PROJECTILE_COMPONENT_EXPORT_VERSION,
                identity_extra={"projectileId": projectile_id},
                invoke=lambda source, output, container, request_id, cancel: (
                    UNITY_WORKER.decode_projectile_component(
                        input_path=source,
                        output_directory=output,
                        container=container,
                        projectile_id=projectile_id,
                        request_id=request_id,
                        cancel_event=cancel,
                    )
                ),
                cancel_event=cancel_event,
            )
        except UnityWorkerError:
            raise
        except RuntimeError as error:
            raise ProjectileDecodeError(str(error)) from error
        if len(artifact_paths) != 1:
            raise ProjectileDecodeError(
                f"expected one projectile artifact, found {len(artifact_paths)}"
            )
        return export_root, meta

    def manifest_cubemap_export_paths(
        self,
        record: dict,
        asset_index: int,
    ) -> tuple[Path, Path]:
        root = (
            INTERNAL_CACHE_DIR
            / str(record["id"])
            / "manifest-assets"
            / str(asset_index)
            / "cubemap"
        )
        return root / "exported", root / "meta.json"

    def ensure_manifest_cubemap_export(
        self,
        record: dict,
        chunk_path: Path,
        asset: dict,
    ) -> tuple[dict[str, Path], dict] | None:
        if file_suffix(str(asset["path"])) not in {".exr", ".hdr", ".cubemap"}:
            return None
        if not ANIMESTUDIO_CUBEMAP_CLI.exists():
            raise FileNotFoundError(f"AnimeStudio Cubemap CLI not found: {ANIMESTUDIO_CUBEMAP_CLI}")

        export_root, meta_path = self.manifest_cubemap_export_paths(
            record,
            int(asset["asset_index"]),
        )
        source_path, _, _ = self.assetbundle_cache_paths(record)
        source_identity = {
            "recordId": int(record["id"]),
            "length": int(record["length"]),
            "offset": int(record["offset"]),
            "chunkPath": str(record["chunk_path"]),
            "chunkMtimeNs": chunk_path.stat().st_mtime_ns,
            "assetIndex": int(asset["asset_index"]),
            "assetPath": str(asset["path"]),
            "toolArtifacts": dotnet_tool_identity(ANIMESTUDIO_CUBEMAP_CLI),
        }

        def exported_faces() -> dict[str, Path]:
            faces = {}
            for path in export_root.rglob("*"):
                if not path.is_file() or file_suffix(path.name) not in IMAGE_EXTENSIONS:
                    continue
                stem = path.stem.casefold()
                for face_name in CUBEMAP_FACE_NAMES:
                    if stem.endswith(f"_{face_name}".casefold()):
                        faces[face_name] = path
                        break
            return faces

        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                faces = exported_faces()
                if (
                    meta.get("version") == CUBEMAP_EXPORT_VERSION
                    and meta.get("source") == source_identity
                    and meta.get("returncode") == 0
                ):
                    return (faces, meta) if set(faces) == set(CUBEMAP_FACE_NAMES) else None
            except (OSError, json.JSONDecodeError):
                pass

        self.write_file_slice(record, chunk_path, source_path)
        shutil.rmtree(export_root, ignore_errors=True)
        export_root.mkdir(parents=True, exist_ok=True)
        normalized_container = str(asset["path"]).replace("\\", "/").strip("/")
        command = [
            str(ANIMESTUDIO_CUBEMAP_CLI),
            str(source_path),
            str(export_root),
            "--game",
            "ArknightsEndfield",
            "--types",
            "Cubemap",
            "--containers",
            f"^{re.escape(normalized_container)}$",
            "--export_type",
            "Convert",
            "--group_assets",
            "ByType",
            "--logger_flags",
            "Error",
            "Warning",
            "Info",
        ]
        completed = subprocess.run(
            command,
            cwd=str(ANIMESTUDIO_CUBEMAP_CLI.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
        faces = exported_faces()
        meta = {
            "version": CUBEMAP_EXPORT_VERSION,
            "source": source_identity,
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "builtAtEpoch": int(time.time()),
            "faces": {
                name: str(path.relative_to(export_root)).replace("\\", "/")
                for name, path in faces.items()
            },
        }
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        if completed.returncode != 0 or set(faces) != set(CUBEMAP_FACE_NAMES):
            return None
        return faces, meta

    def ensure_manifest_monobehaviour_dump(
        self,
        record: dict,
        chunk_path: Path,
        asset: dict,
        *,
        cancel_event: object | None = None,
    ) -> tuple[Path, dict] | None:
        """导出精确 container 的全部 TypeTree 文本，并生成稳定的合并预览。"""

        if file_suffix(str(asset["path"])) not in {".asset", ".prefab"}:
            return None

        def build_combined_dump(export_root: Path, artifacts: list[Path]) -> dict[str, str]:
            sections = []
            for path in artifacts:
                relative = path.relative_to(export_root).as_posix()
                text = path.read_text(encoding="utf-8", errors="replace").rstrip()
                sections.append(f"===== {relative} =====\n{text}")
            combined = export_root / "combined-dump.txt"
            combined.write_text("\n\n".join(sections) + "\n", encoding="utf-8")
            return {"combinedDump": combined.relative_to(export_root).as_posix()}

        export_root, artifact_paths, meta = self.ensure_manifest_unity_worker_export(
            record,
            chunk_path,
            asset,
            export_name="monobehaviour-typetree",
            version=MONOBEHAVIOUR_DUMP_VERSION,
            identity_extra={},
            invoke=lambda source, output, container, request_id, cancel: (
                UNITY_WORKER.export_monobehaviour_typetree_dump(
                    input_path=source,
                    output_directory=output,
                    container=container,
                    request_id=request_id,
                    cancel_event=cancel,
                )
            ),
            derive=build_combined_dump,
            cancel_event=cancel_event,
        )
        if not artifact_paths:
            return None
        dump_path = safe_relative_path(
            export_root,
            str(meta["derivedFiles"]["combinedDump"]["relativePath"]),
        )
        if dump_path is None or not dump_path.is_file():
            raise RuntimeError("published TypeTree combined dump is unavailable")
        return dump_path, meta

    def ensure_manifest_monobehaviour_raw(
        self,
        record: dict,
        chunk_path: Path,
        asset: dict,
        *,
        cancel_event: object | None = None,
    ) -> tuple[Path, dict]:
        """通过 VFS worker 导出一个精确 container 的 MonoBehaviour 原始字节。"""

        if file_suffix(str(asset["path"])) not in {".asset", ".prefab"}:
            raise ValueError("raw MonoBehaviour export requires an .asset or .prefab")
        _export_root, artifact_paths, meta = self.ensure_manifest_unity_worker_export(
            record,
            chunk_path,
            asset,
            export_name="monobehaviour-raw",
            version=MONOBEHAVIOUR_RAW_VERSION,
            identity_extra={},
            invoke=lambda source, output, container, request_id, cancel: (
                UNITY_WORKER.export_monobehaviour_raw(
                    input_path=source,
                    output_directory=output,
                    container=container,
                    request_id=request_id,
                    cancel_event=cancel,
                )
            ),
            cancel_event=cancel_event,
        )
        if len(artifact_paths) != 1:
            raise RuntimeError(
                f"expected one raw MonoBehaviour artifact, found {len(artifact_paths)}"
            )
        # 保留既有调用方读取的字段名；它现在指向选定 run 的相对产物。
        meta["exportedFile"] = meta["exportedFiles"][0]
        return artifact_paths[0], meta

    def assetbundle_worker_map_paths(self, record: dict) -> tuple[Path, Path]:
        root = INTERNAL_CACHE_DIR / str(record["id"]) / "asset-map"
        return root / "runs", root / "meta.json"

    def ensure_assetbundle_map(
        self,
        record: dict,
        chunk_path: Path,
        emit_errors: bool = True,
        *,
        cancel_event: object | None = None,
    ) -> dict | None:
        """通过 worker 建立单 Bundle AssetMap；不在此处混入 CABMap。"""

        runs_root, meta_path = self.assetbundle_worker_map_paths(record)
        source_label = str(record.get("logical_id") or f"record:{int(record['id'])}")
        source_identity = {
            "recordId": int(record["id"]),
            "length": int(record["length"]),
            "offset": int(record["offset"]),
            "chunkPath": str(record["chunk_path"]),
            "chunkMtimeNs": chunk_path.stat().st_mtime_ns,
            "exportTypes": list(ASSETBUNDLE_EXPORT_TYPES),
            "sourceLabel": source_label,
            "toolArtifacts": UNITY_WORKER.artifact_identity(),
        }

        def run_export(
            run_root: Path,
            export_root: Path,
            request_id: str,
            cancel: object | None,
        ) -> dict:
            source_path = run_root / "source.ab"
            self.write_file_slice(record, chunk_path, source_path)
            return UNITY_WORKER.build_asset_map(
                input_path=source_path,
                output_directory=export_root,
                source_label=source_label,
                included_types=ASSETBUNDLE_EXPORT_TYPES,
                request_id=request_id,
                cancel_event=cancel,
            )

        try:
            export_root, artifact_paths, worker_meta = self.ensure_unity_worker_run(
                runs_root=runs_root,
                meta_path=meta_path,
                request_prefix=f"asset-map-{int(record['id'])}",
                version=ASSETBUNDLE_MAP_VERSION,
                source_identity=source_identity,
                invoke=run_export,
                cancel_event=cancel_event,
            )
            if len(artifact_paths) != 1:
                raise RuntimeError(
                    f"expected one AssetMap artifact, found {len(artifact_paths)}"
                )
            asset_map = json.loads(artifact_paths[0].read_text(encoding="utf-8-sig"))
            asset_entries = asset_map.get("AssetEntries")
            if not isinstance(asset_entries, list):
                raise RuntimeError("worker AssetMap is missing AssetEntries")
        except (UnityWorkerError, OSError, json.JSONDecodeError, RuntimeError) as error:
            if emit_errors:
                self.send_json(
                    {
                        "kind": "assetBundle",
                        "status": "mapFailed",
                        "message": str(error),
                    },
                    status=500,
                )
            return None

        return {
            **worker_meta,
            "mapReturncode": 0,
            "exportTypes": ASSETBUNDLE_EXPORT_TYPES,
            "assetEntries": asset_entries,
            "assetMapFile": artifact_paths[0].relative_to(export_root).as_posix(),
        }

    def ensure_assetbundle_export(self, record: dict, chunk_path: Path, emit_errors: bool = True) -> tuple[Path, dict] | None:
        source_path, export_root, meta_path = self.assetbundle_cache_paths(record)
        if export_root.exists() and meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if (
                    meta.get("version") == ASSETBUNDLE_META_VERSION
                    and meta.get("returncode") == 0
                    and assetbundle_export_types_match(meta)
                ):
                    return export_root, meta
            except (OSError, json.JSONDecodeError):
                pass

        if not ANIMESTUDIO_CLI.exists():
            if emit_errors:
                self.send_json(
                    {
                        "kind": "assetBundle",
                        "status": "toolMissing",
                        "message": f"AnimeStudio.CLI not found: {ANIMESTUDIO_CLI}",
                    }
                )
            return None

        self.write_file_slice(record, chunk_path, source_path)
        export_root.mkdir(parents=True, exist_ok=True)
        map_meta = self.ensure_assetbundle_map(record, chunk_path, emit_errors=emit_errors)
        if map_meta is None:
            return None

        export_command = [
            str(ANIMESTUDIO_CLI),
            str(source_path),
            str(export_root),
            "--game",
            "ArknightsEndfield",
            "--types",
            *ASSETBUNDLE_EXPORT_TYPES,
            "--export_type",
            "Convert",
            "--logger_flags",
            "Error",
            "Warning",
            "Info",
        ]
        export_completed = subprocess.run(
            export_command,
            cwd=str(ANIMESTUDIO_CLI.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
        meta = {
            "version": ASSETBUNDLE_META_VERSION,
            "command": export_command,
            "mapCommand": None,
            "returncode": export_completed.returncode,
            "mapReturncode": 0,
            "stdout": export_completed.stdout,
            "stderr": export_completed.stderr,
            "mapRun": map_meta,
            "builtAtEpoch": int(time.time()),
            "exportTypes": ASSETBUNDLE_EXPORT_TYPES,
            "assetEntries": map_meta["assetEntries"],
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        if export_completed.returncode != 0:
            if emit_errors:
                self.send_json(
                    {
                        "kind": "assetBundle",
                        "status": "exportFailed",
                        "message": "AnimeStudio failed to export this AssetBundle.",
                        "meta": meta,
                    },
                    status=500,
                )
            return None
        return export_root, meta

    def asset_metadata_by_export_name(self, meta: dict) -> dict[tuple[str, str], list[dict]]:
        out: dict[tuple[str, str], list[dict]] = {}
        for entry in meta.get("assetEntries") or []:
            name = str(entry.get("Name") or "").lower()
            asset_type = str(entry.get("Type") or "").lower()
            if name and asset_type:
                out.setdefault((asset_type, name), []).append(entry)
        return out

    def metadata_for_internal_file(
        self,
        child: Path,
        export_root: Path,
        metadata_by_name: dict[tuple[str, str], list[dict]],
    ) -> dict | None:
        try:
            asset_type = child.relative_to(export_root).parts[0].lower()
        except (ValueError, IndexError):
            return None
        name = child.stem
        path_id = None
        suffixed = re.fullmatch(r"(.+)_p([0-9a-fA-F]{16})", name)
        if suffixed:
            name = suffixed.group(1)
            unsigned_path_id = int(suffixed.group(2), 16)
            path_id = (
                unsigned_path_id - (1 << 64)
                if unsigned_path_id >= (1 << 63)
                else unsigned_path_id
            )
        candidates = metadata_by_name.get((asset_type, name.lower()), [])
        if path_id is not None:
            candidates = [
                entry
                for entry in candidates
                if str(entry.get("PathID") or "") == str(path_id)
            ]
        return candidates[0] if len(candidates) == 1 else None

    def asset_metadata_matches(self, entry: dict | None, asset_type: str, asset_name: str, path_id: str) -> bool:
        if not entry:
            return False
        if str(entry.get("Type") or "").lower() != asset_type.lower():
            return False
        if str(entry.get("Name") or "").lower() != asset_name.lower():
            return False
        if path_id and str(entry.get("PathID") or "") != path_id:
            return False
        return True

    def find_exported_asset_file(
        self,
        export_root: Path,
        meta: dict,
        asset_type: str,
        asset_name: str,
        path_id: str = "",
    ) -> tuple[Path, dict] | None:
        metadata_by_name = self.asset_metadata_by_export_name(meta)
        for child in sorted(export_root.rglob("*"), key=lambda item: item.as_posix().lower()):
            if not child.is_file():
                continue
            asset_meta = self.metadata_for_internal_file(child, export_root, metadata_by_name)
            if self.asset_metadata_matches(asset_meta, asset_type, asset_name, path_id):
                return child, asset_meta
        return None

    def list_internal_export(self, export_root: Path, raw_path: str, meta: dict) -> dict:
        current = safe_relative_path(export_root, raw_path)
        if current is None or not current.exists() or not current.is_dir():
            raise FileNotFoundError("internal directory not found")

        dirs = []
        files = []
        metadata_by_name = self.asset_metadata_by_export_name(meta)
        for child in sorted(current.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            rel_path = posix_relative(child, export_root)
            if child.is_dir():
                file_count, total_bytes = folder_stats(child)
                dirs.append(
                    {
                        "name": child.name,
                        "path": rel_path,
                        "fileCount": file_count,
                        "totalBytes": total_bytes,
                    }
                )
            elif child.is_file():
                asset_meta = self.metadata_for_internal_file(child, export_root, metadata_by_name)
                files.append(
                    {
                        "name": child.name,
                        "path": rel_path,
                        "size": child.stat().st_size,
                        "kind": internal_preview_kind(child),
                        "asset": asset_meta,
                    }
                )
        return {"path": posix_relative(current, export_root) if current != export_root else "", "dirs": dirs, "files": files}

    def audio_cache_paths(self, record: dict) -> tuple[Path, Path, Path]:
        cache_root = INTERNAL_CACHE_DIR / str(record["id"])
        meta_path = cache_root / "audio_meta.json"
        wem_root = cache_root / "audio" / "wem"
        wav_root = cache_root / "audio" / "wav"
        return meta_path, wem_root, wav_root

    def read_pck_header(self, record: dict, chunk_path: Path) -> bytes:
        probe = self.read_file_range(record, chunk_path, 0, min(28, int(record["length"])))
        if len(probe) < 8:
            raise ValueError("PCK is too small")
        magic = probe[:4]
        if magic not in {b":)xD", b"AKPK"}:
            raise ValueError("invalid AKPK magic")
        header_size = int.from_bytes(probe[4:8], "little")
        read_size = header_size + 8 if magic == b":)xD" else header_size
        if read_size < 28 or read_size > int(record["length"]):
            raise ValueError("invalid AKPK header size")
        return self.read_file_range(record, chunk_path, 0, read_size)

    def read_pck_payload(self, record: dict, chunk_path: Path, offset: int, size: int) -> bytes:
        return self.read_file_range(record, chunk_path, offset, size)

    def parse_pck_index(self, record: dict, chunk_path: Path) -> list[AudioEntry]:
        header, header_size = parse_akpk_header(self.read_pck_header(record, chunk_path), record["file_name"])
        language_size = int.from_bytes(header[12:16], "little")
        banks_size = int.from_bytes(header[16:20], "little")
        sounds_size = int.from_bytes(header[20:24], "little")
        has_externals = language_size + banks_size + sounds_size + 0x10 < header_size
        externals_size = int.from_bytes(header[24:28], "little") if has_externals else 0
        start = 28 if has_externals else 24
        languages = parse_akpk_languages(header, start, language_size)
        entries: list[AudioEntry] = []
        pos = start + language_size

        def parse_sector(sector_start: int, sector_size: int, is_sounds: bool, is_externals: bool) -> None:
            if sector_size == 0 or sector_start + 4 > len(header):
                return
            count = int.from_bytes(header[sector_start : sector_start + 4], "little")
            if count == 0:
                return
            entry_size = (sector_size - 4) // count
            alt_mode = entry_size == 0x18
            p = sector_start + 4
            for _ in range(count):
                if p + entry_size > len(header):
                    break
                file_id_low = int.from_bytes(header[p : p + 4], "little")
                q = p + 4
                file_id_high = None
                if alt_mode and is_externals:
                    file_id_high = int.from_bytes(header[q : q + 4], "little")
                    q += 4
                block_size = int.from_bytes(header[q : q + 4], "little")
                q += 4
                if alt_mode and is_externals:
                    size = int.from_bytes(header[q : q + 4], "little")
                    q += 4
                elif alt_mode:
                    size = int.from_bytes(header[q : q + 8], "little")
                    q += 8
                else:
                    size = int.from_bytes(header[q : q + 4], "little")
                    q += 4
                offset = int.from_bytes(header[q : q + 4], "little")
                lang_id = int.from_bytes(header[q + 4 : q + 8], "little") if q + 8 <= p + entry_size else 0
                if block_size:
                    offset *= block_size
                language = languages.get(lang_id)
                final_id = ((file_id_high << 32) | file_id_low) if file_id_high is not None else file_id_low
                if is_sounds:
                    entries.append(AudioEntry(final_id, offset, size, "external" if is_externals else "sound", language))
                else:
                    self.append_bank_wem_entries(record, chunk_path, entries, file_id_low, offset, size, language)
                p += entry_size

        parse_sector(pos, banks_size, False, False)
        pos += banks_size
        parse_sector(pos, sounds_size, True, False)
        pos += sounds_size
        if externals_size:
            parse_sector(pos, externals_size, True, True)
        return entries

    def append_bank_wem_entries(
        self,
        record: dict,
        chunk_path: Path,
        entries: list[AudioEntry],
        bank_id: int,
        bank_offset: int,
        bank_size: int,
        language: str | None,
    ) -> None:
        if bank_size <= 0:
            return
        payload = self.read_pck_payload(record, chunk_path, bank_offset, bank_size)
        bank_encrypted = False
        ranges = parse_bnk_wem_ranges(payload)
        if not ranges:
            decrypted = bytearray(payload)
            decrypt_audio_vfs_bytes(decrypted, 0, len(decrypted), bank_id)
            payload = bytes(decrypted)
            ranges = parse_bnk_wem_ranges(payload)
            bank_encrypted = bool(ranges)
        for wem_id, wem_offset, wem_size in ranges:
            if wem_offset + wem_size > len(payload):
                continue
            entries.append(
                AudioEntry(
                    wem_id=wem_id,
                    offset=bank_offset + wem_offset,
                    size=wem_size,
                    source="bank",
                    language=language,
                    bank_id=bank_id,
                    bank_offset=bank_offset,
                    bank_size=bank_size,
                    bank_wem_offset=wem_offset,
                    bank_encrypted=bank_encrypted,
                )
            )

    def ensure_audio_package_index(self, record: dict, chunk_path: Path) -> dict:
        meta_path, _, _ = self.audio_cache_paths(record)
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if (
                    meta.get("version") == AUDIO_PACKAGE_META_VERSION
                    and meta.get("fileLength") == int(record["length"])
                    and isinstance(meta.get("entries"), list)
                ):
                    return meta
            except (OSError, json.JSONDecodeError):
                pass

        entries = self.parse_pck_index(record, chunk_path)
        meta = {
            "version": AUDIO_PACKAGE_META_VERSION,
            "fileLength": int(record["length"]),
            "builtAtEpoch": int(time.time()),
            "entryCount": len(entries),
            "entries": [entry.to_json() for entry in entries],
        }
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return meta

    def list_audio_package(self, meta: dict, raw_path: str) -> dict:
        normalized = unquote(raw_path).replace("\\", "/").strip("/")
        entries = [AudioEntry.from_json(item) for item in meta.get("entries") or []]
        total_size = sum(entry.size for entry in entries)
        dirs = []
        files = []
        if not normalized:
            dirs = [
                {"name": "wem", "path": "wem", "fileCount": len(entries), "totalBytes": total_size},
                {"name": "wav", "path": "wav", "fileCount": len(entries), "totalBytes": total_size},
            ]
            return {"path": "", "dirs": dirs, "files": files}

        parts = normalized.split("/")
        if len(parts) == 1 and parts[0] in {"wem", "wav"}:
            groups: dict[str, tuple[int, int]] = {}
            for entry in entries:
                prefix = audio_entry_prefix(entry.wem_id)
                count, size = groups.get(prefix, (0, 0))
                groups[prefix] = (count + 1, size + entry.size)
            for prefix, (count, size) in sorted(groups.items()):
                dirs.append({"name": prefix, "path": f"{parts[0]}/{prefix}", "fileCount": count, "totalBytes": size})
            return {"path": normalized, "dirs": dirs, "files": files}

        if len(parts) == 2 and parts[0] in {"wem", "wav"}:
            mode, prefix = parts
            for entry in sorted(entries, key=lambda item: item.wem_id):
                if audio_entry_prefix(entry.wem_id) != prefix.lower():
                    continue
                name = f"{entry.wem_id}.{mode}"
                files.append(
                    {
                        "name": name,
                        "path": f"{mode}/{prefix}/{name}",
                        "size": entry.size,
                        "kind": "audio" if mode == "wav" else "wem",
                        "asset": {
                            "Name": str(entry.wem_id),
                            "Type": "WEM",
                            "Container": f"wwise/{entry.wem_id}.wem",
                            "Source": entry.source,
                            "PathID": entry.wem_id,
                            "Language": entry.language,
                            "BankID": entry.bank_id,
                        },
                    }
                )
            return {"path": normalized, "dirs": dirs, "files": files}

        raise FileNotFoundError("audio package directory not found")

    def audio_entries_by_id(self, meta: dict) -> dict[int, AudioEntry]:
        return {AudioEntry.from_json(item).wem_id: AudioEntry.from_json(item) for item in meta.get("entries") or []}

    def extract_wem_entry(self, record: dict, chunk_path: Path, entry: AudioEntry) -> bytes:
        if entry.bank_encrypted:
            if entry.bank_id is None or entry.bank_offset is None or entry.bank_size is None or entry.bank_wem_offset is None:
                raise ValueError("encrypted bank entry is missing bank metadata")
            bank_payload = bytearray(self.read_pck_payload(record, chunk_path, entry.bank_offset, entry.bank_size))
            decrypt_audio_vfs_bytes(bank_payload, 0, len(bank_payload), entry.bank_id)
            data = bytes(bank_payload[entry.bank_wem_offset : entry.bank_wem_offset + entry.size])
        else:
            data = self.read_pck_payload(record, chunk_path, entry.offset, entry.size)
        if len(data) >= 4 and data[:4] not in {b"RIFF", b"RIFX"}:
            data = decrypt_wem_bytes(data, entry.wem_id)
        return data

    def ensure_audio_entry_file(self, record: dict, chunk_path: Path, internal_path: str) -> tuple[Path, AudioEntry]:
        parsed = parse_audio_internal_path(internal_path)
        if parsed is None:
            raise FileNotFoundError("audio entry not found")
        mode, wem_id = parsed
        meta = self.ensure_audio_package_index(record, chunk_path)
        entry = self.audio_entries_by_id(meta).get(wem_id)
        if entry is None:
            raise FileNotFoundError("audio entry not found")

        _, wem_root, wav_root = self.audio_cache_paths(record)
        target = self.ensure_audio_entry_output(
            record,
            chunk_path,
            entry,
            mode,
            wem_root,
            wav_root,
        )
        return target, entry

    def ensure_audio_dialog_media_file(
        self,
        record: dict,
        chunk_path: Path,
        entry: AudioEntry,
        mode: str,
    ) -> Path:
        return self.ensure_indexed_audio_media_file(
            record,
            chunk_path,
            entry,
            mode,
            "audio-dialog",
        )

    def ensure_indexed_audio_media_file(
        self,
        record: dict,
        chunk_path: Path,
        entry: AudioEntry,
        mode: str,
        cache_namespace: str,
    ) -> Path:
        package_identity = str(
            record.get("file_data_md5")
            or record.get("file_chunk_md5")
            or record.get("length")
            or "unknown"
        ).casefold()
        converter_identity = "unavailable"
        if VGMSTREAM_CLI.is_file():
            stat = VGMSTREAM_CLI.stat()
            converter_identity = f"{stat.st_size:x}-{stat.st_mtime_ns:x}"
        media_identity = (
            f"v{AUDIO_PACKAGE_META_VERSION}-{package_identity}-"
            f"{entry.offset:x}-{entry.size:x}-"
            f"{entry.bank_id if entry.bank_id is not None else 0:x}-"
            f"{entry.bank_wem_offset if entry.bank_wem_offset is not None else 0:x}"
        )
        cache_root = (
            INTERNAL_CACHE_DIR
            / str(record["id"])
            / cache_namespace
            / media_identity
        )
        return self.ensure_audio_entry_output(
            record,
            chunk_path,
            entry,
            mode,
            cache_root / "wem",
            cache_root / "wav" / converter_identity,
        )

    def ensure_audio_entry_output(
        self,
        record: dict,
        chunk_path: Path,
        entry: AudioEntry,
        mode: str,
        wem_root: Path,
        wav_root: Path,
    ) -> Path:
        if mode not in {"wem", "wav"}:
            raise ValueError("audio output mode must be wem or wav")
        prefix = audio_entry_prefix(entry.wem_id)
        wem_path = wem_root / prefix / f"{entry.wem_id}.wem"
        if not wem_path.exists() or wem_path.stat().st_size != entry.size:
            wem_path.parent.mkdir(parents=True, exist_ok=True)
            wem_path.write_bytes(self.extract_wem_entry(record, chunk_path, entry))
        if mode == "wem":
            return wem_path

        wav_path = wav_root / prefix / f"{entry.wem_id}.wav"
        if wav_path.exists() and wav_path.stat().st_size > 0:
            return wav_path
        if not VGMSTREAM_CLI.exists():
            raise FileNotFoundError(f"vgmstream-cli.exe not found: {VGMSTREAM_CLI}")
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = wav_path.with_name(f".{wav_path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        command = [str(VGMSTREAM_CLI), "-o", str(tmp), str(wem_path)]
        try:
            completed = subprocess.run(
                command,
                cwd=str(VGMSTREAM_CLI.parent),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
            if completed.returncode != 0 or not tmp.exists():
                raise RuntimeError(f"vgmstream conversion failed: {completed.stderr or completed.stdout}")
            os.replace(tmp, wav_path)
        finally:
            if tmp.exists():
                tmp.unlink()
        return wav_path

    def usm_cache_paths(self, record: dict) -> tuple[Path, Path]:
        cache_root = INTERNAL_CACHE_DIR / str(record["id"]) / "video"
        file_name = f"{Path(record['file_name']).stem}.mp4"
        return cache_root / file_name, cache_root / "video_meta.json"

    def usm_virtual_path(self, record: dict) -> str:
        return f"mp4/{Path(record['file_name']).stem}.mp4"

    def list_usm_video(self, record: dict, raw_path: str) -> dict:
        normalized = unquote(raw_path).replace("\\", "/").strip("/")
        virtual_path = self.usm_virtual_path(record)
        if not normalized:
            return {
                "path": "",
                "dirs": [{"name": "mp4", "path": "mp4", "fileCount": 1, "totalBytes": int(record["length"])}],
                "files": [],
            }
        if normalized == "mp4":
            return {
                "path": "mp4",
                "dirs": [],
                "files": [
                    {
                        "name": Path(virtual_path).name,
                        "path": virtual_path,
                        "size": int(record["length"]),
                        "kind": "video",
                        "asset": {
                            "Name": Path(virtual_path).name,
                            "Type": "MP4",
                            "Container": record["file_name"],
                            "Source": "USM",
                        },
                    }
                ],
            }
        raise FileNotFoundError("USM virtual directory not found")

    def ensure_usm_video_file(self, record: dict, chunk_path: Path, internal_path: str) -> Path:
        normalized = unquote(internal_path).replace("\\", "/").strip("/")
        if normalized != self.usm_virtual_path(record):
            raise FileNotFoundError("USM video entry not found")

        target, meta_path = self.usm_cache_paths(record)
        if target.exists() and meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if meta.get("fileLength") == int(record["length"]) and target.stat().st_size > 0:
                    return target
            except (OSError, json.JSONDecodeError):
                pass

        target.parent.mkdir(parents=True, exist_ok=True)
        temp_path = target.with_name(f"{target.stem}.{os.getpid()}.{time.time_ns()}.mp4")
        try:
            convert_usm_to_mp4(
                self.read_file_slice(record, chunk_path),
                temp_path,
                usm_convert=USM_CONVERT,
                ffmpeg=FFMPEG,
            )
            os.replace(temp_path, target)
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass
        meta_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "fileLength": int(record["length"]),
                    "builtAtEpoch": int(time.time()),
                    "usmConvert": str(USM_CONVERT),
                    "ffmpeg": str(FFMPEG),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return target

    def resolve_bundle_sources(
        self,
        bundles: list[dict],
    ) -> tuple[list[tuple[dict, Path]], list[dict]]:
        """Resolve manifest bundle names to readable VFS records in manifest order."""

        resolved: list[tuple[dict, Path]] = []
        missing: list[dict] = []
        with self.connect() as conn:
            for bundle in bundles:
                file_name = f"Data/Bundles/Windows/{bundle['name']}"
                candidates = [
                    row_to_dict(row)
                    for row in conn.execute(
                        "SELECT * FROM files WHERE file_name = ?",
                        (file_name,),
                    )
                ]
                candidates.sort(
                    key=lambda row: source_rank(row["source"], bool(row["chunk_exists"]))
                )
                source = next(
                    (
                        (candidate, Path(candidate["chunk_path"]))
                        for candidate in candidates
                        if Path(candidate["chunk_path"]).exists()
                    ),
                    None,
                )
                if source is None:
                    missing.append(bundle)
                else:
                    resolved.append(source)
        return resolved, missing

    def resolve_index_asset_bundle(
        self,
        index: ManifestIndex,
        asset_index: int,
    ) -> tuple[dict, dict, Path]:
        asset = index.asset(asset_index)
        if asset is None:
            raise FileNotFoundError(f"manifest asset {asset_index} does not exist")
        sources, missing = self.resolve_bundle_sources([{"name": asset["bundle_name"]}])
        if missing or len(sources) != 1:
            raise FileNotFoundError(
                f"bundle for manifest asset {asset_index} is not available: {asset['bundle_name']}"
            )
        record, chunk_path = sources[0]
        return asset, record, chunk_path

    def build_skeletal_morph_animation(
        self,
        index: ManifestIndex,
        model_asset: dict,
        animation_asset: dict,
        document: dict,
    ) -> dict:
        sidecar_path = morph_clip_asset_path(str(animation_asset["path"]))
        sidecar_matches = index.assets_by_path(sidecar_path)
        if len(sidecar_matches) != 1:
            raise RuntimeError(
                f"expected one skeletal-morph sidecar {sidecar_path!r}, "
                f"found {len(sidecar_matches)}"
            )

        avatar_names = morph_avatar_asset_names(str(model_asset["path"]))
        avatar_matches = []
        for position, avatar_name in enumerate(avatar_names):
            matches = [
                asset
                for asset in index.assets_by_name(avatar_name)
                if "/skeletalmorph/skeletalmorphcfg/" in str(asset["path"]).casefold()
            ]
            if len(matches) > 1 or (position == 0 and len(matches) != 1):
                raise RuntimeError(
                    f"expected {'one' if position == 0 else 'at most one'} skeletal-morph "
                    f"avatar {avatar_name!r}, found {len(matches)}"
                )
            avatar_matches.extend(matches)
        if not avatar_matches:
            raise RuntimeError(
                f"expected a skeletal-morph avatar for {model_asset['path']!r}"
            )

        sidecar_asset, sidecar_record, sidecar_chunk = self.resolve_index_asset_bundle(
            index,
            int(sidecar_matches[0]["assetIndex"]),
        )
        sidecar_raw, _ = self.ensure_manifest_monobehaviour_raw(
            sidecar_record,
            sidecar_chunk,
            sidecar_asset,
        )
        clip = parse_morph_clip(sidecar_raw.read_bytes())
        avatar_assets = []
        avatars = []
        for avatar_match in avatar_matches:
            avatar_asset, avatar_record, avatar_chunk = self.resolve_index_asset_bundle(
                index,
                int(avatar_match["assetIndex"]),
            )
            avatar_raw, _ = self.ensure_manifest_monobehaviour_raw(
                avatar_record,
                avatar_chunk,
                avatar_asset,
            )
            avatar_assets.append(avatar_asset)
            avatars.append(parse_morph_avatar(avatar_raw.read_bytes()))
        avatar = merge_morph_avatars(tuple(avatars))
        animation_index = int(animation_asset["asset_index"])
        return bake_morph_animation(
            document,
            clip,
            avatar,
            animation_id=f"animation:{animation_index}",
            source={
                "logicalPath": str(animation_asset["path"]),
                "bundle": str(animation_asset["bundle_name"]),
                "morphClipPath": str(sidecar_asset["path"]),
                "morphAvatarPaths": [str(asset["path"]) for asset in avatar_assets],
            },
        )

    def resolve_manifest_asset_source(
        self,
        query: dict[str, list[str]],
    ) -> tuple[ManifestIndex, dict, dict, Path] | None:
        try:
            manifest_id = int(query.get("manifestId", [""])[0])
            asset_index = int(query.get("assetIndex", [""])[0])
        except ValueError:
            self.send_error_json(400, "Manifest 资源引用无效")
            return None

        with self.connect() as conn:
            resolved_manifest = self.resolve_file_record(conn, manifest_id)
            if resolved_manifest is None:
                return None
            _, manifest_record, manifest_chunk = resolved_manifest
            try:
                index = self.manifest_index(manifest_record, manifest_chunk)
                asset = index.asset(asset_index)
            except (ValueError, OSError, sqlite3.Error) as error:
                self.send_error_json(400, str(error))
                return None
            if asset is None:
                self.send_error_json(404, "Manifest 中不存在该资源")
                return None

            bundle_file_name = f"Data/Bundles/Windows/{asset['bundle_name']}"
            candidates = [
                row_to_dict(row)
                for row in conn.execute(
                    "SELECT * FROM files WHERE file_name = ?",
                    (bundle_file_name,),
                )
            ]
            candidates.sort(key=lambda row: source_rank(row["source"], bool(row["chunk_exists"])))
            resolved_bundle = None
            for candidate in candidates:
                candidate_path = Path(candidate["chunk_path"])
                if candidate_path.exists():
                    resolved_bundle = (candidate, candidate_path)
                    break
            if resolved_bundle is None:
                self.send_error_json(404, f"找不到资源对应的 AssetBundle：{asset['bundle_name']}")
                return None

        bundle_record, bundle_chunk = resolved_bundle
        return index, asset, bundle_record, bundle_chunk

    def resolve_optional_animation_source(
        self,
        query: dict[str, list[str]],
    ) -> tuple[ManifestIndex, dict, dict, Path] | None:
        values = query.get("animationAssetIndex")
        if not values:
            return None
        animation_query = dict(query)
        animation_query["assetIndex"] = values
        return self.resolve_manifest_asset_source(animation_query)

    def resolve_animation_sources(
        self,
        query: dict[str, list[str]],
    ) -> list[tuple[ManifestIndex, dict, dict, Path]] | None:
        raw_values = query.get("animationAssetIndex", [])
        if not raw_values:
            return []
        try:
            indexes = sorted(
                {
                    int(value)
                    for raw_value in raw_values
                    for value in raw_value.split(",")
                    if value
                }
            )
        except ValueError:
            self.send_error_json(400, "animationAssetIndex is invalid")
            return None
        if not indexes or len(indexes) > MAX_BLEND_ANIMATION_COUNT:
            self.send_error_json(
                400,
                f"animation selection must contain 1 to {MAX_BLEND_ANIMATION_COUNT} items",
            )
            return None

        resolved = []
        for index in indexes:
            animation_query = dict(query)
            animation_query["assetIndex"] = [str(index)]
            animation = self.resolve_manifest_asset_source(animation_query)
            if animation is None:
                return None
            resolved.append(animation)
        return resolved

    def resolve_manifest_asset_file(
        self,
        query: dict[str, list[str]],
        resolved_source: tuple[ManifestIndex, dict, dict, Path] | None = None,
    ) -> tuple[dict, Path, dict, dict] | None:
        resolved_source = resolved_source or self.resolve_manifest_asset_source(query)
        if resolved_source is None:
            return None
        _, asset, bundle_record, bundle_chunk = resolved_source
        ensured = self.ensure_assetbundle_export(bundle_record, bundle_chunk)
        if ensured is None:
            return None
        export_root, meta = ensured
        matches = manifest_asset_entries(meta, asset["path"])
        if not matches:
            try:
                fallback = self.ensure_manifest_monobehaviour_dump(
                    bundle_record,
                    bundle_chunk,
                    asset,
                )
            except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
                fallback = None
            if fallback is not None:
                target, dump_meta = fallback
                return (
                    bundle_record,
                    target,
                    {
                        "Type": "MonoBehaviourDump",
                        "Name": Path(str(asset["path"])).stem,
                        "Container": asset["path"],
                        "Components": dump_meta.get("exportedFiles", []),
                    },
                    asset,
                )
            self.send_error_json(
                404,
                "已解析对应 AssetBundle，但 AnimeStudio 暂不支持导出该资源类型。",
            )
            return None
        for entry in matches:
            found = self.find_exported_asset_file(
                export_root,
                meta,
                str(entry.get("Type") or ""),
                str(entry.get("Name") or ""),
                str(entry.get("PathID") or ""),
            )
            if found is not None:
                target, asset_meta = found
                return bundle_record, target, asset_meta, asset
        self.send_error_json(404, "已找到资源元数据，但对应的导出文件缺失。")
        return None

    def resolve_manifest_cubemap_files(
        self,
        query: dict[str, list[str]],
        resolved_source: tuple[ManifestIndex, dict, dict, Path] | None = None,
    ) -> tuple[dict, dict[str, Path], dict, dict] | None:
        resolved_source = resolved_source or self.resolve_manifest_asset_source(query)
        if resolved_source is None:
            return None
        _, asset, bundle_record, bundle_chunk = resolved_source
        try:
            ensured = self.ensure_manifest_cubemap_export(bundle_record, bundle_chunk, asset)
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            return None
        if ensured is None:
            return None
        faces, _ = ensured
        asset_meta = {
            "Type": "Cubemap",
            "Name": Path(str(asset["path"])).stem,
            "Container": asset["path"],
        }
        return bundle_record, faces, asset_meta, asset

    def resolve_assetbundle_internal_file(self, query: dict[str, list[str]]) -> tuple[dict, Path, dict | None] | None:
        file_id = self.file_id_from_query(query)
        if file_id is None:
            return None
        internal_path = query.get("path", [""])[0]
        with self.connect() as conn:
            resolved = self.resolve_file_record(conn, file_id)
            if resolved is None:
                return None
            _, record, chunk_path = resolved

        if file_suffix(record["file_name"]) != ".ab":
            self.send_error_json(400, "AssetBundle internal file preview expected an .ab record")
            return None

        ensured = self.ensure_assetbundle_export(record, chunk_path)
        if ensured is None:
            return None
        export_root, meta = ensured
        if internal_path:
            target = safe_relative_path(export_root, internal_path)
            if target is None or not target.is_file():
                self.send_error_json(404, "internal file not found")
                return None
            asset_meta = self.metadata_for_internal_file(target, export_root, self.asset_metadata_by_export_name(meta))
            return record, target, asset_meta

        asset_type = query.get("type", [""])[0]
        asset_name = query.get("name", [""])[0]
        path_id = query.get("pathId", [""])[0]
        if not asset_type or not asset_name:
            self.send_error_json(400, "AssetBundle asset preview expected path or type/name")
            return None
        found = self.find_exported_asset_file(export_root, meta, asset_type, asset_name, path_id)
        if found is None:
            self.send_error_json(404, "exported asset not found")
            return None
        target, asset_meta = found
        return record, target, asset_meta

    def resolve_audio_internal_file(self, query: dict[str, list[str]]) -> tuple[dict, Path, AudioEntry] | None:
        file_id = self.file_id_from_query(query)
        if file_id is None:
            return None
        internal_path = query.get("path", [""])[0]
        with self.connect() as conn:
            resolved = self.resolve_file_record(conn, file_id)
            if resolved is None:
                return None
            _, record, chunk_path = resolved

        if file_suffix(record["file_name"]) != ".pck":
            self.send_error_json(400, "audio internal file preview expected a .pck record")
            return None
        try:
            target, entry = self.ensure_audio_entry_file(record, chunk_path, internal_path)
        except FileNotFoundError as error:
            self.send_error_json(404, str(error))
            return None
        except (ValueError, RuntimeError) as error:
            self.send_error_json(500, str(error))
            return None
        return record, target, entry

    def resolve_usm_internal_file(self, query: dict[str, list[str]]) -> tuple[dict, Path, dict] | None:
        file_id = self.file_id_from_query(query)
        if file_id is None:
            return None
        internal_path = query.get("path", [""])[0]
        with self.connect() as conn:
            resolved = self.resolve_file_record(conn, file_id)
            if resolved is None:
                return None
            _, record, chunk_path = resolved

        if file_suffix(record["file_name"]) != ".usm":
            self.send_error_json(400, "USM internal file preview expected a .usm record")
            return None
        try:
            target = self.ensure_usm_video_file(record, chunk_path, internal_path)
        except FileNotFoundError as error:
            self.send_error_json(404, str(error))
            return None
        except (UsmError, RuntimeError, OSError, subprocess.SubprocessError) as error:
            self.send_error_json(500, str(error))
            return None
        asset_meta = {
            "Name": target.name,
            "Type": "MP4",
            "Container": record["file_name"],
            "Source": "USM",
        }
        return record, target, asset_meta

    def resolve_tablecfg_file(self, file_id: int) -> tuple[dict, dict, Path, str] | None:
        with self.connect() as conn:
            resolved = self.resolve_file_record(conn, file_id)
            if resolved is None:
                return None
            original, record, chunk_path = resolved
        table_name = tablecfg_name_for_file(record["file_name"])
        if table_name is None:
            self.send_error_json(400, "TableCfg JSON expected a Data/TableCfg/*.bytes record")
            return None
        return original, record, chunk_path, table_name

    def parse_tablecfg_file(self, record: dict, chunk_path: Path) -> tuple[dict, bytes]:
        parsed = parse_sparkbuffer(self.read_file_slice(record, chunk_path))
        data = json.dumps(parsed["data"], ensure_ascii=False, indent=2).encode("utf-8")
        return parsed, data

    def handle_preview(self, query: dict[str, list[str]]) -> None:
        file_id = self.file_id_from_query(query)
        if file_id is None:
            return

        with self.connect() as conn:
            resolved = self.resolve_file_record(conn, file_id)
            if resolved is None:
                return
            original, record, chunk_path = resolved

        suffix = file_suffix(record["file_name"])
        raw_url = f"/api/raw?id={file_id}"
        download_url = f"/api/raw?id={file_id}&download=1"
        base = {
            "file": original,
            "resolvedFile": record,
            "usedFallback": original["id"] != record["id"],
            "rawUrl": raw_url,
            "downloadUrl": download_url,
        }

        if suffix in CONTAINER_EXTENSIONS:
            kind = "container"
            if suffix == ".ab":
                message = "这是 Unity/AssetBundle 容器。可以下载原始 .ab，也可以点击“查看内部结构”按需导出并预览 Texture2D、Sprite、TextAsset 等资源。"
            elif suffix == ".pck":
                message = "这是音频 PCK 容器。VFS 层可以下载原始 PCK；单条语音需要继续解析 PCK/AKPK/WEM。"
            elif suffix == ".usm":
                message = "这是 CRI/USM 视频容器。可以下载原始 .usm，也可以点击“查看内部结构”按需转换为 MP4 预览。"
            else:
                message = "这是二级容器文件，可以下载；内部解析尚未接入。"
            self.send_json({**base, "kind": kind, "message": message})
            return

        if suffix in IMAGE_EXTENSIONS:
            self.send_json({**base, "kind": "image", "contentType": guess_content_type(record["file_name"])})
            return
        if suffix in VIDEO_EXTENSIONS:
            self.send_json({**base, "kind": "video", "contentType": guess_content_type(record["file_name"])})
            return
        if suffix in AUDIO_EXTENSIONS:
            self.send_json({**base, "kind": "audio", "contentType": guess_content_type(record["file_name"])})
            return

        tablecfg_name = tablecfg_name_for_file(record["file_name"])
        if tablecfg_name:
            try:
                parsed, json_data = self.parse_tablecfg_file(record, chunk_path)
            except (SparkBufferError, struct.error, UnicodeDecodeError, ValueError) as error:
                data = self.read_file_slice(record, chunk_path, limit=PREVIEW_BINARY_LIMIT)
                self.send_json(
                    {
                        **base,
                        "kind": "hex",
                        "hex": hex_preview(data),
                        "truncated": int(record["length"]) > len(data),
                        "message": f"`{tablecfg_name}` 已完成 VFS 解密，但 SparkBuffer 解析失败：{error}",
                    }
                )
                return

            text, truncated = truncate_text(json_data.decode("utf-8"))
            json_url = f"/api/tablecfg/json?id={file_id}"
            self.send_json(
                {
                    **base,
                    "kind": "text",
                    "encoding": "sparkbuffer-json",
                    "text": text,
                    "truncated": truncated,
                    "convertedRawUrl": json_url,
                    "convertedDownloadUrl": f"{json_url}&download=1",
                    "message": f"`{tablecfg_name}` 已从本地 VFS 解密 bytes 解析为 SparkBuffer JSON。",
                    "tableCfg": {
                        "fileName": tablecfg_name,
                        "rootName": parsed.get("name"),
                    },
                }
            )
            return

        limit = PREVIEW_TEXT_LIMIT if suffix in TEXT_EXTENSIONS else PREVIEW_BINARY_LIMIT
        data = self.read_file_slice(record, chunk_path, limit=limit)
        text, encoding = decode_text(data)
        truncated = int(record["length"]) > len(data)
        if suffix == ".json":
            if text is not None and text.lstrip().startswith(("{", "[")):
                try:
                    text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
                except json.JSONDecodeError:
                    pass
                self.send_json(
                    {
                        **base,
                        "kind": "text",
                        "encoding": encoding,
                        "text": text,
                        "truncated": truncated,
                    }
                )
                return

            memorypack_error = None
            try:
                decoded_preview = self.decode_memorypack_json_preview(record, chunk_path)
            except RuntimeError as error:
                decoded_preview = None
                memorypack_error = str(error)
            if decoded_preview is not None:
                decoded_text, decoded_truncated, decoded_meta = decoded_preview
                self.send_json(
                    {
                        **base,
                        "kind": "text",
                        "encoding": "memorypack-json",
                        "text": decoded_text,
                        "truncated": decoded_truncated,
                        "message": (
                            "该 .json 文件已从本地 VFS 解密内容解析为 schema-based MemoryPack JSON。"
                            f" 已消费 {decoded_meta['consumed']} / {decoded_meta['bytes']} bytes。"
                        ),
                        "memoryPack": decoded_meta,
                    }
                )
                return

            probe = binary_json_probe(data, int(record["length"]))
            self.send_json(
                {
                    **base,
                    "kind": "binaryJson",
                    "encoding": encoding,
                    "probe": probe,
                    "hex": hex_preview(data),
                    "truncated": truncated,
                    "message": f"{probe['note']}\nMemoryPack 解码未完成：{memorypack_error}" if memorypack_error else probe["note"],
                }
            )
            return
        if text is not None and (suffix in TEXT_EXTENSIONS or looks_like_text(text)):
            self.send_json(
                {
                    **base,
                    "kind": "text",
                    "encoding": encoding,
                    "text": text,
                    "truncated": truncated,
                }
            )
            return

        self.send_json(
            {
                **base,
                "kind": "hex",
                "hex": hex_preview(data),
                "truncated": truncated,
                "message": "该文件不是可直接显示的文本，当前展示解密后的前段十六进制内容。",
            }
        )

    def handle_tablecfg_json(self, query: dict[str, list[str]]) -> None:
        file_id = self.file_id_from_query(query)
        if file_id is None:
            return
        resolved = self.resolve_tablecfg_file(file_id)
        if resolved is None:
            return
        _, record, chunk_path, table_name = resolved
        try:
            parsed, data = self.parse_tablecfg_file(record, chunk_path)
        except (SparkBufferError, struct.error, UnicodeDecodeError, ValueError) as error:
            self.send_error_json(422, f"SparkBuffer parse failed: {error}")
            return

        download = query.get("download", ["0"])[0] in {"1", "true", "yes"}
        disposition = "attachment" if download else "inline"
        root_name = str(parsed.get("name") or table_name)
        encoded_name = quote(f"{root_name}.json")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f"{disposition}; filename*=UTF-8''{encoded_name}")
        self.end_headers()
        self.wfile.write(data)

    def handle_raw(self, query: dict[str, list[str]]) -> None:
        file_id = self.file_id_from_query(query)
        if file_id is None:
            return
        download = query.get("download", ["0"])[0] in {"1", "true", "yes"}
        with self.connect() as conn:
            resolved = self.resolve_file_record(conn, file_id)
            if resolved is None:
                return
            original, record, chunk_path = resolved

        file_name = Path(original["file_name"]).name or "vfs-file.bin"
        content_type = guess_content_type(original["file_name"])
        disposition = "attachment" if download else "inline"
        encoded_name = quote(file_name)

        if record.get("encrypted"):
            data = self.read_file_slice(record, chunk_path)
            content_type = guess_content_type(original["file_name"], data[:32])
            if content_type.startswith(("application/json", "text/plain")) and not looks_like_text_bytes(data[:8192]):
                content_type = "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header(
                "Content-Disposition",
                f"{disposition}; filename*=UTF-8''{encoded_name}",
            )
            self.end_headers()
            self.wfile.write(data)
            return

        length = int(record["length"])
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header(
            "Content-Disposition",
            f"{disposition}; filename*=UTF-8''{encoded_name}",
        )
        self.end_headers()
        with chunk_path.open("rb") as file:
            file.seek(int(record["offset"]))
            remaining = length
            while remaining > 0:
                data = file.read(min(STREAM_CHUNK_SIZE, remaining))
                if not data:
                    break
                self.wfile.write(data)
                remaining -= len(data)

    def handle_manifest_asset_preview(self, query: dict[str, list[str]]) -> None:
        resolved_source = self.resolve_manifest_asset_source(query)
        if resolved_source is None:
            return
        cubemap = self.resolve_manifest_cubemap_files(query, resolved_source)
        if cubemap is not None:
            bundle_record, faces, asset_meta, manifest_asset = cubemap
            manifest_id = query.get("manifestId", [""])[0]
            asset_index = query.get("assetIndex", [""])[0]
            face_payload = []
            for face_name in CUBEMAP_FACE_NAMES:
                target = faces[face_name]
                raw_url = (
                    f"/api/manifest-asset/raw?manifestId={manifest_id}"
                    f"&assetIndex={asset_index}&face={face_name}"
                )
                face_payload.append(
                    {
                        "name": face_name,
                        "size": target.stat().st_size,
                        "contentType": guess_content_type(target.name),
                        "rawUrl": raw_url,
                        "downloadUrl": f"{raw_url}&download=1",
                    }
                )
            default_face = next(item for item in face_payload if item["name"] == "PositiveZ")
            total_size = sum(item["size"] for item in face_payload)
            self.send_json(
                {
                    "file": {
                        **bundle_record,
                        "file_name": manifest_asset["path"],
                        "length": total_size,
                    },
                    "resolvedFile": bundle_record,
                    "usedFallback": False,
                    "name": Path(str(manifest_asset["path"])).name,
                    "size": total_size,
                    "rawUrl": default_face["rawUrl"],
                    "downloadUrl": default_face["downloadUrl"],
                    "asset": asset_meta,
                    "message": f"来自 {manifest_asset['bundle_name']}，按 Unity Cubemap 面序导出",
                    "kind": "cubemap",
                    "faces": face_payload,
                }
            )
            return

        resolved = self.resolve_manifest_asset_file(query, resolved_source)
        if resolved is None:
            return
        bundle_record, target, asset_meta, manifest_asset = resolved
        manifest_id = query.get("manifestId", [""])[0]
        asset_index = query.get("assetIndex", [""])[0]
        raw_url = f"/api/manifest-asset/raw?manifestId={manifest_id}&assetIndex={asset_index}"
        base = {
            "file": {
                **bundle_record,
                "file_name": manifest_asset["path"],
                "length": target.stat().st_size,
            },
            "resolvedFile": bundle_record,
            "usedFallback": False,
            "name": target.name,
            "size": target.stat().st_size,
            "rawUrl": raw_url,
            "downloadUrl": f"{raw_url}&download=1",
            "asset": asset_meta,
            "message": f"来自 {manifest_asset['bundle_name']}",
        }
        suffix = file_suffix(target.name)
        if suffix in IMAGE_EXTENSIONS:
            self.send_json({**base, "kind": "image", "contentType": guess_content_type(target.name)})
            return
        if suffix in VIDEO_EXTENSIONS:
            self.send_json({**base, "kind": "video", "contentType": guess_content_type(target.name)})
            return
        if suffix in AUDIO_EXTENSIONS:
            self.send_json({**base, "kind": "audio", "contentType": guess_content_type(target.name)})
            return
        limit = PREVIEW_TEXT_LIMIT if suffix in TEXT_EXTENSIONS else PREVIEW_BINARY_LIMIT
        data = target.read_bytes()[:limit]
        text, encoding = decode_text(data)
        truncated = target.stat().st_size > len(data)
        if text is not None and (suffix in TEXT_EXTENSIONS or looks_like_text(text)):
            self.send_json(
                {**base, "kind": "text", "encoding": encoding, "text": text, "truncated": truncated}
            )
            return
        self.send_json({**base, "kind": "hex", "hex": hex_preview(data), "truncated": truncated})

    def handle_manifest_asset_model(self, query: dict[str, list[str]]) -> None:
        resolved = self.resolve_manifest_asset_source(query)
        if resolved is None:
            return
        index, asset, bundle_record, bundle_chunk = resolved
        animation_resolved = self.resolve_optional_animation_source(query)
        if query.get("animationAssetIndex") and animation_resolved is None:
            return
        animation_asset = animation_resolved[1] if animation_resolved else None
        is_prefab = file_suffix(str(asset["path"])) == ".prefab"
        is_avatar_mesh = is_avatar_mesh_asset_path(str(asset["path"]))
        if not is_model_entry_path(str(asset["path"])):
            self.send_error_json(400, "resource is not a supported model entry")
            return
        try:
            lod = int(query.get("lod", ["0"])[0])
            if is_avatar_mesh:
                document, run_meta, _ = self.ensure_avatar_mesh_model(
                    index,
                    asset,
                    bundle_record,
                    bundle_chunk,
                    lod,
                )
            else:
                dependencies = index.bundle_dependencies(int(asset["bundle_index"]))
                dependency_sources, missing_dependencies = self.resolve_bundle_sources(dependencies)
                document, run_meta = self.ensure_model_hierarchy(
                    bundle_record,
                    bundle_chunk,
                    asset,
                    dependencies,
                    dependency_sources,
                    missing_dependencies,
                )
        except FileNotFoundError as error:
            self.send_json(
                {
                    "kind": "modelDocument",
                    "status": "toolMissing",
                    "message": str(error),
                },
                status=503,
            )
            return
        except subprocess.TimeoutExpired:
            self.send_error_json(504, "AnimeStudio timed out while exporting the model hierarchy")
            return
        except (KeyError, OSError, RuntimeError, ValueError, sqlite3.Error) as error:
            self.send_error_json(500, str(error))
            return

        manifest_id = int(query["manifestId"][0])
        asset_index = int(asset["asset_index"])
        lod_parameter = f"&lod={lod}" if is_avatar_mesh else ""
        animation_query_hint = model_animation_query_hint(str(asset["path"]), document)
        animation_query_parameter = f"&queryHint={quote(animation_query_hint)}"
        animation_url = (
            f"/api/manifest-asset/model-animation?manifestId={manifest_id}"
            f"&assetIndex={asset_index}"
            f"&animationAssetIndex={int(animation_asset['asset_index'])}"
            f"&v={MODEL_ANIMATION_CACHE_REVISION}"
            if animation_asset
            else None
        )
        animation_parameter = (
            f"&animationAssetIndex={int(animation_asset['asset_index'])}"
            if animation_asset
            else ""
        )
        self.send_json(
            {
                "kind": "modelDocument",
                "status": (
                    "texturedSkinnedModel"
                    if document.get("images") and document.get("skins")
                    else "staticGeometry"
                    if document.get("meshes")
                    else "hierarchyOnly"
                ),
                "asset": asset,
                "animationAsset": animation_asset,
                "glbUrl": (
                    f"/api/manifest-asset/model-glb?manifestId={manifest_id}"
                    f"&assetIndex={asset_index}{lod_parameter}&v={MODEL_GLB_VERSION}"
                ),
                "blendUrl": (
                    f"/api/manifest-asset/model-blend?manifestId={manifest_id}"
                    f"&assetIndex={asset_index}{lod_parameter}{animation_parameter}"
                    f"&v={MODEL_BLEND_VERSION}"
                    if (is_prefab or is_avatar_mesh)
                    and BLENDER_EXE.is_file()
                    and BLENDER_MODEL_IMPORTER.is_file()
                    else None
                ),
                "baseBlendUrl": (
                    f"/api/manifest-asset/model-blend?manifestId={manifest_id}"
                    f"&assetIndex={asset_index}{lod_parameter}&v={MODEL_BLEND_VERSION}"
                    if (is_prefab or is_avatar_mesh)
                    and BLENDER_EXE.is_file()
                    and BLENDER_MODEL_IMPORTER.is_file()
                    else None
                ),
                "animationCandidatesUrl": (
                    f"/api/manifest-asset/model-animations?manifestId={manifest_id}"
                    f"&assetIndex={asset_index}{lod_parameter}{animation_query_parameter}"
                ),
                "maxBlendAnimationCount": MAX_BLEND_ANIMATION_COUNT,
                "animationUrl": animation_url,
                "document": document,
                "run": {
                    "scope": run_meta.get("scope"),
                    "builtAtEpoch": run_meta.get("builtAtEpoch"),
                    "dependencyBundles": run_meta.get("dependencyBundles", []),
                    "missingDependencyBundles": run_meta.get("missingDependencyBundles", []),
                    "lod": lod if is_avatar_mesh else None,
                },
            }
        )

    def handle_manifest_asset_avatar_plan(
        self,
        query: dict[str, list[str]],
    ) -> None:
        resolved = self.resolve_manifest_asset_source(query)
        if resolved is None:
            return
        index, asset, bundle_record, bundle_chunk = resolved
        if not is_avatar_mesh_asset_path(str(asset["path"])):
            self.send_error_json(400, "resource is not an NPC AvatarMesh asset")
            return
        try:
            lod = int(query.get("lod", ["0"])[0])
            avatar_mesh, plan, plan_meta = self.load_avatar_mesh_plan(
                index,
                asset,
                bundle_record,
                bundle_chunk,
                lod,
            )
        except FileNotFoundError as error:
            self.send_error_json(503, str(error))
            return
        except subprocess.TimeoutExpired:
            self.send_error_json(504, "AnimeStudio timed out while exporting AvatarMesh")
            return
        except (KeyError, OSError, RuntimeError, ValueError, sqlite3.Error) as error:
            self.send_error_json(500, str(error))
            return
        self.send_json(
            {
                "kind": "avatarMeshResourcePlan",
                "asset": asset,
                "summary": summarize_avatar_mesh(avatar_mesh),
                "avatarMesh": avatar_mesh,
                "plan": plan,
                "run": {
                    "dump": {
                        "builtAtEpoch": plan_meta["dump"].get("builtAtEpoch"),
                        "toolArtifacts": plan_meta["dump"].get("source", {}).get(
                            "toolArtifacts", []
                        ),
                    },
                    "stringPathHash": plan_meta["stringPathHash"],
                },
            },
            compress=True,
        )

    def ensure_manifest_asset_model_glb(
        self,
        resolved: tuple[ManifestIndex, dict, dict, dict],
        *,
        lod: int = 0,
    ) -> tuple[dict, Path, Path]:
        index, asset, bundle_record, bundle_chunk = resolved
        is_avatar_mesh = is_avatar_mesh_asset_path(str(asset["path"]))
        if is_avatar_mesh:
            document, _, model_path = self.ensure_avatar_mesh_model(
                index,
                asset,
                bundle_record,
                bundle_chunk,
                lod,
            )
        else:
            dependencies = index.bundle_dependencies(int(asset["bundle_index"]))
            dependency_sources, missing_dependencies = self.resolve_bundle_sources(dependencies)
            document, _ = self.ensure_model_hierarchy(
                bundle_record,
                bundle_chunk,
                asset,
                dependencies,
                dependency_sources,
                missing_dependencies,
            )
            _, _, model_path, _ = self.model_snapshot_paths(
                bundle_record, int(asset["asset_index"])
            )
        document, geometry, image_paths = self.load_model_glb_inputs(
            asset,
            bundle_record,
            model_path,
            lod=lod,
        )
        geometry_path = model_path.with_name("geometry.bin")
        glb_path = model_path.with_name("model.glb")
        glb_meta_path = glb_path.with_suffix(".glb.meta.json")
        # Exporter changes can alter the GLB without rebuilding ModelDocument.
        exporter_path = Path(build_glb.__code__.co_filename)
        source_paths = [
            model_path,
            geometry_path,
            exporter_path,
            *image_paths.values(),
        ]
        material_plans = {}
        if SHADER_ARCHIVE_ROOT.is_dir():
            material_plans = build_blender_material_plans(document, SHADER_ARCHIVE_ROOT)
            source_paths.append(SHADER_ARCHIVE_ROOT / CHARACTER_NPR_PATH)
        newest_source_mtime = max(path.stat().st_mtime_ns for path in source_paths)
        cache_identity = {
            "version": MODEL_GLB_VERSION,
            "materialPlan": material_plan_cache_identity(),
        }
        if (
            not glb_path.is_file()
            or glb_path.stat().st_mtime_ns < newest_source_mtime
            or load_cache_identity(glb_meta_path) != cache_identity
        ):
            glb = build_glb(
                document,
                geometry,
                lambda image: image_paths[str(image["id"])].read_bytes(),
                material_plans,
            )
            temporary = glb_path.with_suffix(".glb.tmp")
            temporary.write_bytes(glb)
            os.replace(temporary, glb_path)
            glb_meta_path.write_text(
                json.dumps(cache_identity, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        return asset, model_path, glb_path

    def load_model_glb_inputs(
        self,
        asset: dict,
        bundle_record: dict,
        model_path: Path,
        *,
        lod: int,
    ) -> tuple[dict, bytes, dict[str, Path]]:
        document = json.loads(model_path.read_text(encoding="utf-8"))
        geometry_path = model_path.with_name("geometry.bin")
        if not geometry_path.is_file():
            raise FileNotFoundError("model geometry buffer not found")
        geometry = geometry_path.read_bytes()

        is_avatar_mesh = is_avatar_mesh_asset_path(str(asset["path"]))
        texture_root = (model_path.parent / "textures").resolve()
        image_paths: dict[str, Path] = {}
        for image in document.get("images", []):
            parsed = urlparse(str(image.get("uri") or ""))
            image_query = parse_qs(parsed.query)
            if parsed.path != "/api/manifest-asset/model-texture":
                raise ValueError(f"unsupported model image URI: {image.get('uri')}")
            if int(image_query.get("recordId", ["-1"])[0]) != int(bundle_record["id"]):
                raise ValueError("model image recordId does not match the current model")
            if int(image_query.get("assetIndex", ["-1"])[0]) != int(asset["asset_index"]):
                raise ValueError("model image assetIndex does not match the current model")
            image_lod = image_query.get("lod")
            if is_avatar_mesh and (
                not image_lod or int(image_lod[0]) != lod
            ):
                raise ValueError("model image LOD does not match the current AvatarMesh")
            relative = unquote(image_query.get("path", [""])[0]).replace("\\", "/").strip("/")
            target = (texture_root / relative).resolve()
            if not relative or texture_root not in target.parents or not target.is_file():
                raise FileNotFoundError(f"model texture not found: {relative}")
            image_paths[str(image["id"])] = target
        return document, geometry, image_paths

    def ensure_animated_model_glb(
        self,
        model_resolved: tuple[ManifestIndex, dict, dict, Path],
        animation_sources: list[tuple[ManifestIndex, dict, dict, Path]],
        *,
        lod: int,
        skip_incompatible: bool = False,
    ) -> AnimatedModelBundle:
        if not animation_sources:
            raise ValueError("at least one animation is required")
        animation_sources = sorted(
            animation_sources,
            key=lambda item: int(item[1]["asset_index"]),
        )
        model_asset, model_path, model_glb = self.ensure_manifest_asset_model_glb(
            model_resolved,
            lod=lod,
        )
        _, _, model_record, _ = model_resolved
        document, geometry, image_paths = self.load_model_glb_inputs(
            model_asset,
            model_record,
            model_path,
            lod=lod,
        )
        animated_document = copy.deepcopy(document)
        animated_geometry = geometry
        animation_assets = []
        issues = []
        clip_paths = []
        for _, animation_asset, animation_record, animation_chunk in animation_sources:
            animation_index = int(animation_asset["asset_index"])
            animation_path = str(animation_asset["path"])
            try:
                clip, clip_path, _ = self.ensure_animation_clip_export(
                    animation_record,
                    animation_chunk,
                    animation_asset,
                )
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
                if skip_incompatible:
                    issues.append(
                        AnimationExportIssue(
                            animation_index,
                            animation_path,
                            "clipExport",
                            str(error),
                        )
                    )
                    continue
                raise

            candidate_document = copy.deepcopy(animated_document)
            try:
                candidate_geometry = attach_animation_clip(
                    candidate_document,
                    animated_geometry,
                    clip,
                    animation_id=f"animation:{animation_index}",
                    source={
                        "logicalPath": animation_path,
                        "bundle": str(animation_asset["bundle_name"]),
                    },
                    bake_humanoid=True,
                )
                if len(candidate_document.get("animations", [])) == len(
                    animated_document.get("animations", [])
                ):
                    raise RuntimeError(
                        "animation has no transform tracks compatible with this model"
                    )
            except (KeyError, RuntimeError, ValueError) as error:
                if skip_incompatible:
                    issues.append(
                        AnimationExportIssue(
                            animation_index,
                            animation_path,
                            "modelBinding",
                            str(error),
                        )
                    )
                    continue
                raise

            animated_document = candidate_document
            animated_geometry = candidate_geometry
            animation_assets.append(animation_asset)
            clip_paths.append(clip_path)

        if not animation_assets:
            return AnimatedModelBundle(
                model_asset,
                [],
                model_path,
                model_glb,
                issues,
            )

        animation_indexes = [int(asset["asset_index"]) for asset in animation_assets]
        selection_key = hashlib.sha256(
            ",".join(map(str, animation_indexes)).encode("ascii")
        ).hexdigest()[:16]
        animation_root = model_path.parent / "animation-sets" / selection_key
        animated_glb = animation_root / "model.glb"
        animated_glb_meta = animated_glb.with_suffix(".glb.meta.json")
        source_paths = [
            model_path,
            model_path.with_name("geometry.bin"),
            *clip_paths,
            Path(attach_animation_clip.__code__.co_filename),
            Path(build_glb.__code__.co_filename),
            *image_paths.values(),
        ]
        material_plans = {}
        if SHADER_ARCHIVE_ROOT.is_dir():
            material_plans = build_blender_material_plans(
                animated_document,
                SHADER_ARCHIVE_ROOT,
            )
            source_paths.append(SHADER_ARCHIVE_ROOT / CHARACTER_NPR_PATH)
        newest_source_mtime = max(path.stat().st_mtime_ns for path in source_paths)
        cache_identity = {
            "version": MODEL_GLB_VERSION,
            "materialPlan": material_plan_cache_identity(),
            "animationAssetIndexes": animation_indexes,
        }
        if (
            not animated_glb.is_file()
            or animated_glb.stat().st_mtime_ns < newest_source_mtime
            or load_cache_identity(animated_glb_meta) != cache_identity
        ):
            payload = build_glb(
                animated_document,
                animated_geometry,
                lambda image: image_paths[str(image["id"])].read_bytes(),
                material_plans,
            )
            animation_root.mkdir(parents=True, exist_ok=True)
            temporary = animated_glb.with_suffix(".glb.tmp")
            temporary.write_bytes(payload)
            os.replace(temporary, animated_glb)
            animated_glb_meta.write_text(
                json.dumps(cache_identity, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        return AnimatedModelBundle(
            model_asset,
            animation_assets,
            model_path,
            animated_glb,
            issues,
        )

    def handle_manifest_asset_model_glb(self, query: dict[str, list[str]]) -> None:
        resolved = self.resolve_manifest_asset_source(query)
        if resolved is None:
            return
        path = str(resolved[1]["path"])
        if not is_model_entry_path(path):
            self.send_error_json(400, "resource is not a supported model entry")
            return

        try:
            lod = int(query.get("lod", ["0"])[0])
            asset, _, glb_path = self.ensure_manifest_asset_model_glb(
                resolved,
                lod=lod,
            )
        except subprocess.TimeoutExpired:
            self.send_error_json(504, "AnimeStudio timed out while exporting the model")
            return
        except (KeyError, OSError, RuntimeError, ValueError, sqlite3.Error) as error:
            self.send_error_json(500, str(error))
            return

        download = query.get("download", ["0"])[0] in {"1", "true", "yes"}
        disposition = "attachment" if download else "inline"
        name = f"{Path(str(asset['path'])).stem}.glb"
        self.send_response(200)
        self.send_header("Content-Type", "model/gltf-binary")
        self.send_header("Content-Length", str(glb_path.stat().st_size))
        self.send_header("Content-Disposition", f"{disposition}; filename*=UTF-8''{quote(name)}")
        self.send_header("Cache-Control", "private, max-age=3600")
        self.end_headers()
        with glb_path.open("rb") as source:
            while data := source.read(STREAM_CHUNK_SIZE):
                self.wfile.write(data)

    def handle_manifest_asset_model_blend(self, query: dict[str, list[str]]) -> None:
        resolved = self.resolve_manifest_asset_source(query)
        if resolved is None:
            return
        path = str(resolved[1]["path"])
        if not is_model_entry_path(path):
            self.send_error_json(400, "resource is not a supported model entry")
            return
        if not BLENDER_EXE.is_file():
            self.send_error_json(503, f"Blender executable not found: {BLENDER_EXE}")
            return

        try:
            lod = int(query.get("lod", ["0"])[0])
            animation_sources = self.resolve_animation_sources(query)
            if animation_sources is None:
                return
            animation_issues = []
            if animation_sources:
                bundle = self.ensure_animated_model_glb(
                    resolved,
                    animation_sources,
                    lod=lod,
                    skip_incompatible=len(animation_sources) > 1,
                )
                asset = bundle.asset
                animation_assets = bundle.animations
                model_path = bundle.model_path
                glb_path = bundle.glb_path
                animation_issues = bundle.issues
            else:
                asset, model_path, glb_path = self.ensure_manifest_asset_model_glb(
                    resolved,
                    lod=lod,
                )
                animation_assets = []

            if query.get("prepare", ["0"])[0] in {"1", "true", "yes"}:
                download_query = {
                    key: values
                    for key, values in query.items()
                    if key != "prepare"
                }
                download_url = (
                    f"/api/manifest-asset/model-blend?{urlencode(download_query, doseq=True)}"
                    if animation_assets or not animation_sources
                    else None
                )
                self.send_json(
                    {
                        "kind": "modelAnimationBundlePreparation",
                        "requestedCount": len(animation_sources),
                        "exportedCount": len(animation_assets),
                        "issues": [issue.as_json() for issue in animation_issues],
                        "downloadUrl": download_url,
                    }
                )
                return

            if animation_sources and not animation_assets:
                self.send_json(
                    {
                        "error": "none of the selected animations could be exported",
                        "issues": [issue.as_json() for issue in animation_issues],
                    },
                    status=422,
                )
                return
            blend_path = glb_path.with_suffix(".blend")
            material_backend = PROJECT_ROOT / "blender_materials.py"
            material_plan_backend = Path(blender_material_plan.__file__)
            source_paths = [
                glb_path,
                BLENDER_MODEL_IMPORTER,
                BLENDER_ACTION_SWITCHER,
                material_backend,
                material_plan_backend,
                PROJECT_ROOT / "character_lighting.py",
            ]
            newest_source_mtime = max(path.stat().st_mtime_ns for path in source_paths)
            with MODEL_BLEND_EXPORT_LOCK:
                if (
                    not blend_path.is_file()
                    or blend_path.stat().st_mtime_ns < newest_source_mtime
                ):
                    temporary = blend_path.with_name("model.tmp.blend")
                    temporary.unlink(missing_ok=True)
                    try:
                        result = subprocess.run(
                            [
                                str(BLENDER_EXE),
                                "--background",
                                "--factory-startup",
                                "--python",
                                str(BLENDER_MODEL_IMPORTER),
                                "--",
                                str(glb_path),
                                str(temporary),
                            ],
                            cwd=PROJECT_ROOT,
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            timeout=300,
                            check=True,
                        )
                        if not temporary.is_file():
                            raise RuntimeError(
                                "Blender export completed without producing a file: "
                                + result.stdout[-2000:]
                            )
                        os.replace(temporary, blend_path)
                    finally:
                        temporary.unlink(missing_ok=True)
        except subprocess.TimeoutExpired:
            self.send_error_json(504, "Blender timed out while exporting the model")
            return
        except subprocess.CalledProcessError as error:
            output = (error.stdout or error.stderr or "").strip()
            self.send_error_json(500, f"Blender export failed: {output[-4000:]}")
            return
        except (KeyError, OSError, RuntimeError, ValueError, sqlite3.Error) as error:
            self.send_error_json(500, str(error))
            return

        suffix = f"-animations-{len(animation_assets)}" if animation_assets else ""
        name = f"{Path(str(asset['path'])).stem}{suffix}.blend"
        self.send_response(200)
        self.send_header("Content-Type", "application/x-blender")
        self.send_header("Content-Length", str(blend_path.stat().st_size))
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(name)}")
        self.send_header(
            "X-Endfield-Skipped-Animation-Count",
            str(len(animation_issues)),
        )
        self.send_header("Cache-Control", "private, max-age=3600")
        self.end_headers()
        with blend_path.open("rb") as source:
            while data := source.read(STREAM_CHUNK_SIZE):
                self.wfile.write(data)

    def handle_manifest_asset_model_animation(
        self,
        query: dict[str, list[str]],
    ) -> None:
        model_resolved = self.resolve_manifest_asset_source(query)
        if model_resolved is None:
            return
        if not query.get("animationAssetIndex"):
            self.send_error_json(400, "animationAssetIndex is required")
            return
        animation_resolved = self.resolve_optional_animation_source(query)
        if animation_resolved is None:
            return

        index, model_asset, model_record, model_chunk = model_resolved
        _, animation_asset, animation_record, animation_chunk = animation_resolved
        if not is_model_entry_path(str(model_asset["path"])):
            self.send_error_json(400, "resource is not a supported model entry")
            return

        try:
            lod = int(query.get("lod", ["0"])[0])
            if is_avatar_mesh_asset_path(str(model_asset["path"])):
                document, _, _ = self.ensure_avatar_mesh_model(
                    index,
                    model_asset,
                    model_record,
                    model_chunk,
                    lod,
                )
            else:
                dependencies = index.bundle_dependencies(int(model_asset["bundle_index"]))
                dependency_sources, missing_dependencies = self.resolve_bundle_sources(dependencies)
                document, _ = self.ensure_model_hierarchy(
                    model_record,
                    model_chunk,
                    model_asset,
                    dependencies,
                    dependency_sources,
                    missing_dependencies,
                )
            animation_asset_index = int(animation_asset["asset_index"])
            if is_dialog_morph_animation_path(str(animation_asset["path"])):
                animation = self.build_skeletal_morph_animation(
                    index,
                    model_asset,
                    animation_asset,
                    document,
                )
            else:
                clip, _, _ = self.ensure_animation_clip_export(
                    animation_record,
                    animation_chunk,
                    animation_asset,
                )
                animation = bind_animation_clip(
                    document,
                    clip,
                    animation_id=f"animation:{animation_asset_index}",
                    source={
                        "logicalPath": str(animation_asset["path"]),
                        "bundle": str(animation_asset["bundle_name"]),
                    },
                    bake_humanoid=True,
                )
        except subprocess.TimeoutExpired:
            self.send_error_json(504, "AnimeStudio timed out while exporting the animation")
            return
        except (KeyError, OSError, RuntimeError, ValueError, sqlite3.Error) as error:
            self.send_error_json(500, str(error))
            return

        self.send_json(
            animation,
            cache_control="private, max-age=3600",
            compress=True,
        )

    def handle_manifest_asset_model_animations(
        self,
        query: dict[str, list[str]],
    ) -> None:
        resolved = self.resolve_manifest_asset_source(query)
        if resolved is None:
            return
        index, model_asset, _, _ = resolved
        if not is_model_entry_path(str(model_asset["path"])):
            self.send_error_json(400, "resource is not a supported model entry")
            return
        default_query = query.get(
            "queryHint",
            [default_model_animation_query(str(model_asset["path"]))],
        )[0].strip()
        search_query = query.get("q", [default_query])[0].strip()
        try:
            page = int(query.get("page", ["1"])[0])
            page_size = int(query.get("pageSize", ["50"])[0])
            lod = int(query.get("lod", ["0"])[0])
            if is_avatar_mesh_asset_path(str(model_asset["path"])) and lod not in range(4):
                raise ValueError("invalid AvatarMesh LOD")
            result = index.search_animation_assets(
                search_query,
                page=page,
                page_size=page_size,
            )
        except (ValueError, sqlite3.Error) as error:
            self.send_error_json(400, str(error))
            return
        manifest_id = int(query["manifestId"][0])
        model_asset_index = int(model_asset["asset_index"])
        lod_parameter = (
            f"&lod={lod}" if is_avatar_mesh_asset_path(str(model_asset["path"])) else ""
        )
        for animation_asset in result["files"]:
            animation_index = int(animation_asset["assetIndex"])
            parameters = (
                f"manifestId={manifest_id}&assetIndex={model_asset_index}{lod_parameter}"
                f"&animationAssetIndex={animation_index}"
            )
            animation_asset["previewUrl"] = (
                f"/api/manifest-asset/model-animation?{parameters}"
                f"&v={MODEL_ANIMATION_CACHE_REVISION}"
            )
            animation_asset["blendUrl"] = (
                f"/api/manifest-asset/model-blend?{parameters}&v={MODEL_BLEND_VERSION}"
            )
        self.send_json(
            {
                "kind": "modelAnimationCandidates",
                "modelAsset": model_asset,
                "defaultQuery": default_query,
                **result,
            },
            compress=True,
        )

    def handle_manifest_asset_model_buffer(self, query: dict[str, list[str]]) -> None:
        try:
            record_id = int(query.get("recordId", [""])[0])
            asset_index = int(query.get("assetIndex", [""])[0])
            lod_value = query.get("lod", [None])[0]
            lod = int(lod_value) if lod_value is not None else None
            if record_id < 0 or asset_index < 0:
                raise ValueError
            if lod is not None and lod not in range(4):
                raise ValueError
        except (ValueError, IndexError):
            self.send_error_json(400, "recordId, assetIndex or lod is invalid")
            return
        root = INTERNAL_CACHE_DIR / str(record_id) / "models" / str(asset_index)
        if lod is not None:
            root /= f"avatar-lod-{lod}"
        target = root / "geometry.bin"
        if not target.is_file():
            self.send_error_json(404, "model geometry buffer not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(target.stat().st_size))
        self.send_header("Cache-Control", "private, max-age=3600")
        self.end_headers()
        with target.open("rb") as source:
            while data := source.read(STREAM_CHUNK_SIZE):
                self.wfile.write(data)

    def handle_manifest_asset_model_texture(self, query: dict[str, list[str]]) -> None:
        try:
            record_id = int(query.get("recordId", [""])[0])
            asset_index = int(query.get("assetIndex", [""])[0])
            lod_value = query.get("lod", [None])[0]
            lod = int(lod_value) if lod_value is not None else None
            if record_id < 0 or asset_index < 0:
                raise ValueError
            if lod is not None and lod not in range(4):
                raise ValueError
        except (ValueError, IndexError):
            self.send_error_json(400, "recordId, assetIndex or lod is invalid")
            return
        root = INTERNAL_CACHE_DIR / str(record_id) / "models" / str(asset_index)
        if lod is not None:
            root /= f"avatar-lod-{lod}"
        root = (root / "textures").resolve()
        relative = unquote(query.get("path", [""])[0]).replace("\\", "/").strip("/")
        target = (root / relative).resolve()
        if not relative or root not in target.parents or not target.is_file():
            self.send_error_json(404, "model texture not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(target.stat().st_size))
        self.send_header("Cache-Control", "private, max-age=3600")
        self.end_headers()
        with target.open("rb") as source:
            while data := source.read(STREAM_CHUNK_SIZE):
                self.wfile.write(data)

    def handle_manifest_asset_raw(self, query: dict[str, list[str]]) -> None:
        resolved_source = self.resolve_manifest_asset_source(query)
        if resolved_source is None:
            return
        face_name = query.get("face", [""])[0]
        if face_name:
            cubemap = self.resolve_manifest_cubemap_files(query, resolved_source)
            if cubemap is None:
                self.send_error_json(404, "Cubemap face not found")
                return
            _, faces, _, _ = cubemap
            target = faces.get(face_name)
            if target is None:
                self.send_error_json(404, "Unknown Cubemap face")
                return
        else:
            resolved = self.resolve_manifest_asset_file(query, resolved_source)
            if resolved is None:
                return
            _, target, _, _ = resolved
        download = query.get("download", ["0"])[0] in {"1", "true", "yes"}
        disposition = "attachment" if download else "inline"
        with target.open("rb") as file:
            sniff = file.read(32)
        self.send_response(200)
        self.send_header("Content-Type", guess_content_type(target.name, sniff))
        self.send_header("Content-Length", str(target.stat().st_size))
        self.send_header(
            "Content-Disposition",
            f"{disposition}; filename*=UTF-8''{quote(target.name)}",
        )
        self.end_headers()
        with target.open("rb") as file:
            while data := file.read(STREAM_CHUNK_SIZE):
                self.wfile.write(data)

    def handle_internal_list(self, query: dict[str, list[str]]) -> None:
        file_id = self.file_id_from_query(query)
        if file_id is None:
            return
        path = query.get("path", [""])[0]
        with self.connect() as conn:
            resolved = self.resolve_file_record(conn, file_id)
            if resolved is None:
                return
            original, record, chunk_path = resolved

        suffix = file_suffix(record["file_name"])
        if suffix == ".ab":
            ensured = self.ensure_assetbundle_export(record, chunk_path)
            if ensured is None:
                return
            export_root, meta = ensured
            try:
                listing = self.list_internal_export(export_root, path, meta)
            except FileNotFoundError:
                self.send_error_json(404, "internal directory not found")
                return
            self.send_json(
                {
                    "kind": "assetBundle",
                    "status": "ready",
                    "file": original,
                    "resolvedFile": record,
                    "path": listing["path"],
                    "dirs": listing["dirs"],
                    "files": listing["files"],
                    "meta": {
                        "returncode": meta.get("returncode"),
                        "builtAtEpoch": meta.get("builtAtEpoch"),
                        "exportTypes": meta.get("exportTypes"),
                    },
                }
            )
            return
        if suffix == ".pck":
            try:
                meta = self.ensure_audio_package_index(record, chunk_path)
                listing = self.list_audio_package(meta, path)
            except (FileNotFoundError, ValueError) as error:
                self.send_error_json(404, str(error))
                return
            self.send_json(
                {
                    "kind": "audioPackage",
                    "status": "ready",
                    "file": original,
                    "resolvedFile": record,
                    "path": listing["path"],
                    "dirs": listing["dirs"],
                    "files": listing["files"],
                    "meta": {
                        "builtAtEpoch": meta.get("builtAtEpoch"),
                        "entryCount": meta.get("entryCount"),
                        "wavPreviewAvailable": VGMSTREAM_CLI.exists(),
                        "vgmstreamCli": str(VGMSTREAM_CLI),
                    },
                }
            )
            return
        if suffix == ".usm":
            try:
                listing = self.list_usm_video(record, path)
            except FileNotFoundError as error:
                self.send_error_json(404, str(error))
                return
            self.send_json(
                {
                    "kind": "criVideo",
                    "status": "ready",
                    "file": original,
                    "resolvedFile": record,
                    "path": listing["path"],
                    "dirs": listing["dirs"],
                    "files": listing["files"],
                    "meta": {
                        "usmConvertAvailable": USM_CONVERT.exists(),
                        "usmConvert": str(USM_CONVERT),
                        "ffmpeg": str(FFMPEG),
                    },
                }
            )
            return
        self.send_json({"kind": "plainFile", "status": "notContainer", "message": "该文件不是当前识别的二级容器。"})

    def handle_internal_preview(self, query: dict[str, list[str]]) -> None:
        file_id = self.file_id_from_query(query)
        if file_id is None:
            return
        with self.connect() as conn:
            resolved_record = self.resolve_file_record(conn, file_id)
            if resolved_record is None:
                return
            _, record, chunk_path = resolved_record

        suffix = file_suffix(record["file_name"])
        asset_meta = None
        audio_entry = None
        if suffix == ".ab":
            resolved = self.resolve_assetbundle_internal_file(query)
            if resolved is None:
                return
            record, target, asset_meta = resolved
        elif suffix == ".pck":
            resolved = self.resolve_audio_internal_file(query)
            if resolved is None:
                return
            record, target, audio_entry = resolved
        elif suffix == ".usm":
            resolved = self.resolve_usm_internal_file(query)
            if resolved is None:
                return
            record, target, asset_meta = resolved
        else:
            self.send_error_json(400, "unsupported internal preview container")
            return
        rel_path = query.get("path", [""])[0]
        raw_url = f"/api/internal/raw?id={record['id']}&path={quote(rel_path, safe='')}"
        download_url = f"{raw_url}&download=1"
        internal_suffix = file_suffix(target.name)
        base = {
            "file": record,
            "path": rel_path,
            "name": target.name,
            "size": target.stat().st_size,
            "rawUrl": raw_url,
            "downloadUrl": download_url,
            "asset": asset_meta,
            "audioEntry": audio_entry.to_json() if audio_entry else None,
        }
        if internal_suffix in IMAGE_EXTENSIONS:
            self.send_json({**base, "kind": "image", "contentType": guess_content_type(target.name)})
            return
        if internal_suffix in VIDEO_EXTENSIONS:
            self.send_json({**base, "kind": "video", "contentType": guess_content_type(target.name)})
            return
        if internal_suffix in AUDIO_EXTENSIONS:
            self.send_json({**base, "kind": "audio", "contentType": guess_content_type(target.name)})
            return

        data = target.read_bytes()[:PREVIEW_TEXT_LIMIT if internal_suffix in TEXT_EXTENSIONS else PREVIEW_BINARY_LIMIT]
        text, encoding = decode_text(data)
        truncated = target.stat().st_size > len(data)
        if text is not None and (internal_suffix in TEXT_EXTENSIONS or looks_like_text(text)):
            self.send_json({**base, "kind": "text", "encoding": encoding, "text": text, "truncated": truncated})
            return
        self.send_json({**base, "kind": "hex", "hex": hex_preview(data), "truncated": truncated})

    def handle_internal_raw(self, query: dict[str, list[str]]) -> None:
        file_id = self.file_id_from_query(query)
        if file_id is None:
            return
        with self.connect() as conn:
            resolved_record = self.resolve_file_record(conn, file_id)
            if resolved_record is None:
                return
            _, record, _ = resolved_record
        suffix = file_suffix(record["file_name"])
        if suffix == ".ab":
            resolved = self.resolve_assetbundle_internal_file(query)
            if resolved is None:
                return
            _, target, _ = resolved
        elif suffix == ".pck":
            resolved = self.resolve_audio_internal_file(query)
            if resolved is None:
                return
            _, target, _ = resolved
        elif suffix == ".usm":
            resolved = self.resolve_usm_internal_file(query)
            if resolved is None:
                return
            _, target, _ = resolved
        else:
            self.send_error_json(400, "unsupported internal raw container")
            return
        download = query.get("download", ["0"])[0] in {"1", "true", "yes"}
        disposition = "attachment" if download else "inline"
        encoded_name = quote(target.name)
        with target.open("rb") as file:
            sniff = file.read(32)
        self.send_response(200)
        self.send_header("Content-Type", guess_content_type(target.name, sniff))
        self.send_header("Content-Length", str(target.stat().st_size))
        self.send_header("Content-Disposition", f"{disposition}; filename*=UTF-8''{encoded_name}")
        self.end_headers()
        with target.open("rb") as file:
            while True:
                data = file.read(STREAM_CHUNK_SIZE)
                if not data:
                    break
                self.wfile.write(data)

    def serve_static(self, request_path: str) -> None:
        request_path = unquote(request_path)
        if request_path in ("", "/"):
            request_path = "/index.html"
        normalized = os.path.normpath(request_path.lstrip("/"))
        if normalized.startswith(".."):
            self.send_error(403)
            return
        file_path = PUBLIC_DIR / normalized
        if not file_path.exists() or not file_path.is_file():
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX, help="Path to JSONL/TGZ VFS index")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database path")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--rebuild", action="store_true", help="Rebuild SQLite database before serving")
    parser.add_argument("--build-only", action="store_true", help="Rebuild SQLite database and exit")
    return parser.parse_args(list(argv))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.rebuild or not args.db.exists():
        if not args.index.exists():
            raise SystemExit(f"index not found: {args.index}")
        build_database(args.index, args.db)
    if args.build_only:
        return 0

    BrowserHandler.db_path = args.db
    server = ThreadingHTTPServer((args.host, args.port), BrowserHandler)
    print(f"VFS index browser: http://{args.host}:{args.port}")
    print(f"database: {args.db}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
