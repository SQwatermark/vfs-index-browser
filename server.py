#!/usr/bin/env python3
"""Serve a small local browser for the Endfield VFS JSONL index."""

from __future__ import annotations

import argparse
import gzip
import io
import json
import mimetypes
import os
import re
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
from assetbundle_browser import (
    find_exported_file,
    list_export_directory,
    metadata_by_export_name,
    metadata_for_file,
)
from assetbundle_worker_service import (
    ASSETBUNDLE_EXPORT_TYPES,
    AssetBundleWorkerService,
    manifest_asset_entries,
)
from audio_package_service import (
    AudioEntry,
    AudioPackageIndexService,
    StaleAudioIndexError,
)
from cache_versions import CACHE_VERSIONS
from blender_export import BlenderExportService
from ability_entity_data import (
    AbilityEntityDecodeError,
    AbilityEntityNotFoundError,
    AbilityEntityUnavailableError,
    list_ability_entity_ids,
    normalize_ability_entity_id,
    parse_ability_entity_template,
    select_ability_entity_asset,
)
from audio_dialog_service import (
    AudioDialogConflictError,
    AudioDialogMediaBuildError,
    AudioDialogService,
)
from audio_dialog_store import create_audio_dialog_schema
from wwise_store import get_wwise_media
from wwise_catalog_service import WwiseCatalogService
from wwise_media_service import WwiseMediaBuildError, WwiseMediaService
from sparkbuffer import SparkBufferError, parse_sparkbuffer
from usm import UsmError
from usm_video_service import UsmVideoService
from manifest_index import ManifestIndex
from index_freshness import inspect_index_freshness
from secondary_audio_startup import ensure_secondary_audio_indexes
from vfs_directory_service import (
    MANIFEST_VIRTUAL_DIR,
    MANIFEST_VIRTUAL_NAME,
    VfsDirectoryService,
    join_manifest_virtual_path,
    split_manifest_virtual_path,
)
from index_rebuild import (
    IndexRebuildError,
    load_index_source_roots,
    rebuild_index_atomically,
)
from manifest_asset_service import (
    ManifestAssetResolutionError,
    ManifestAssetService,
    is_model_entry_path,
)
from manifest_asset_requests import (
    ManifestAssetRequestError,
    parse_animation_asset_indexes,
    parse_manifest_asset_reference,
    parse_manifest_id,
)
from manifest_worker_service import CUBEMAP_FACE_NAMES, ManifestWorkerService
from npc_avatar_resources import build_avatar_mesh_resource_plan
from avatar_mesh_snapshot import (
    selected_container_paths,
)
from avatar_model_document_service import AvatarModelDocumentService
from avatar_model_build_service import AvatarModelBuildService
from npc_avatar_model import build_static_avatar_mesh_document
from string_path_hash import StringPathHashIndex
from npc_avatar_config import (
    attach_resolved_paths,
    is_avatar_mesh_asset_path,
    parse_avatar_mesh,
    summarize_avatar_mesh,
)
from model_document import validate_model_document
from model_run_store import ModelRunStore, resolve_published_model_run
from model_worker_service import ModelWorkerService
from ordinary_model_document_service import OrdinaryModelDocumentService
from ordinary_model_build_service import OrdinaryModelBuildService
from model_glb_service import ModelGlbService
from model_animation_service import (
    AnimatedModelBundle,
    AnimationExportIssue,
    ModelAnimationService,
)
from model_blend_service import ModelBlendService
from model_preview_service import ModelPreviewService
from model_artifact_resolver import (
    ModelArtifactReference,
    ModelArtifactResolver,
    ModelArtifactRunNotFound,
)
from model_single_animation_service import ModelSingleAnimationService
from model_animation_catalog_service import ModelAnimationCatalogService
from gltf_export import build_glb
from material_semantic_plans import CHARACTER_NPR_PATH, build_blender_material_plans
from animestudio_animation import (
    MODEL_ANIMATION_CACHE_REVISION,
    attach_animation_clip,
    bind_animation_clip,
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
from task_service import TaskApplicationService
from task_requests import (
    ModelAnimationTaskRequest,
    ModelBlendTaskRequest,
    ModelTaskRequest,
    TaskInputError,
)
from task_operations import BackgroundTaskOperations
from runtime_config import RuntimeConfig, parse_port
from service_logging import LOGGER, configure_service_logging
from tool_registry import ToolRegistry
from worker_run_service import WorkerRunService

try:
    from tools.decode_memorypack_json import DecodeError, Decoder, MemoryPackReader, SchemaIndex, infer_class
except ImportError:
    DecodeError = Decoder = MemoryPackReader = SchemaIndex = None

    def infer_class(logical_id: str | None) -> str | None:
        return None


PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_CONFIG = RuntimeConfig.load(PROJECT_ROOT)
UNITY_WORKER = UnityWorkerClient.discover(PROJECT_ROOT)
INDEX_FRESHNESS_REPORT = {
    "status": "unverified",
    "reason": "startup_audit_not_run",
    "checkedChunkCount": 0,
    "missingChunkCount": 0,
    "examples": [],
}
INDEX_REBUILD_REPORT = {"status": "notRun"}
MANIFEST_INDEX_REPORT = {"status": "notRun"}
SECONDARY_AUDIO_INDEX_REPORT = {"status": "notRun"}
SECONDARY_AUDIO_REBUILD_REPORT = {"status": "notRun"}
WORKER_RUNS = WorkerRunService()


DEFAULT_INDEX = RUNTIME_CONFIG.default_index
DEFAULT_DB = RUNTIME_CONFIG.database
AUDIO_DIALOG_DB = RUNTIME_CONFIG.audio_dialog_database
WWISE_DB = RUNTIME_CONFIG.wwise_database
PUBLIC_DIR = RUNTIME_CONFIG.public_dir
INTERNAL_CACHE_DIR = RUNTIME_CONFIG.internal_cache
TASKS = BackgroundTaskRegistry(lambda: INTERNAL_CACHE_DIR / "tasks")
TASK_API = TaskApplicationService(TASKS)
SHADER_ARCHIVE_ROOT = RUNTIME_CONFIG.shader_archive_root
# 调试时可以分别覆盖特定导出链路，生产环境统一使用已验证的打包构建。
VGMSTREAM_CLI = RUNTIME_CONFIG.vgmstream_cli
USM_CONVERT = RUNTIME_CONFIG.usm_convert
FFMPEG = RUNTIME_CONFIG.ffmpeg


def optional_tool_registry() -> ToolRegistry:
    """按当前配置构造能力快照，便于测试和运行时 override 立即生效。"""

    return ToolRegistry(
        {
            "blender": BLENDER_EXE,
            "vgmstream": VGMSTREAM_CLI,
            "usm-convert": USM_CONVERT,
            "ffmpeg": FFMPEG,
        }
    )


def usm_video_service() -> UsmVideoService:
    tools = optional_tool_registry()
    return UsmVideoService(
        INTERNAL_CACHE_DIR,
        CACHE_VERSIONS.version("usm-video"),
        usm_convert=tools.capability("usm-convert").resolved_path,
        ffmpeg=tools.capability("ffmpeg").resolved_path or FFMPEG,
    )


def audio_package_index_service() -> AudioPackageIndexService:
    vgmstream = optional_tool_registry().capability("vgmstream").resolved_path
    return AudioPackageIndexService(
        INTERNAL_CACHE_DIR,
        CACHE_VERSIONS.version("audio-package"),
        vgmstream=vgmstream,
    )


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
        "exportCubemapFaces",
        "exportBundlePreviewMedia",
        "exportAnimationClipJson",
    ])
    index_status = INDEX_FRESHNESS_REPORT.get("status")
    secondary_audio_status = SECONDARY_AUDIO_INDEX_REPORT.get("status")
    return {
        "apiVersion": 1,
        "status": (
            "ready"
            if worker["status"] == "ready"
            and index_status not in {"stale", "unavailable"}
            and MANIFEST_INDEX_REPORT.get("status") != "unavailable"
            and secondary_audio_status not in {"stale", "unavailable"}
            else "degraded"
        ),
        "indexFreshness": INDEX_FRESHNESS_REPORT,
        "indexRebuild": INDEX_REBUILD_REPORT,
        "manifestIndex": MANIFEST_INDEX_REPORT,
        "secondaryAudioIndexes": SECONDARY_AUDIO_INDEX_REPORT,
        "secondaryAudioRebuild": SECONDARY_AUDIO_REBUILD_REPORT,
        "unityWorker": worker,
        "optionalTools": optional_tool_registry().diagnostics(),
        "cacheVersions": CACHE_VERSIONS.diagnostics(),
        "legacyTools": [],
    }


def unity_worker_is_unavailable(error: UnityWorkerError) -> bool:
    """区分运行环境不可用与输入或领域数据不可解码。"""

    return error.code in {
        "worker_not_found",
        "worker_timeout",
        "invalid_worker_response",
        "request_id_mismatch",
    }


BLENDER_EXE = RUNTIME_CONFIG.blender_executable
BLENDER_MODEL_IMPORTER = RUNTIME_CONFIG.blender_model_importer
BLENDER_ACTION_SWITCHER = RUNTIME_CONFIG.blender_action_switcher

CHACHA_KEY = bytes.fromhex(
    "e95b317ac4f828569d23a86bf271dcb53e846fa75c924d671dba8e38f4ca52e1"
)
VFS_PROTO_VERSION = 3
MODEL_SNAPSHOT_VERSION = CACHE_VERSIONS.version("model-snapshot")
AVATAR_MODEL_SNAPSHOT_VERSION = CACHE_VERSIONS.version("avatar-model-snapshot")
ANIMATION_CLIP_EXPORT_VERSION = CACHE_VERSIONS.version("animation-clip-export")
# Increment when the GLB representation changes without changing ModelDocument.
MODEL_GLB_VERSION = CACHE_VERSIONS.version("model-glb")
MODEL_BLEND_VERSION = CACHE_VERSIONS.version("model-blend")
MAX_BLEND_ANIMATION_COUNT = 100
STRING_PATH_HASH_LOGICAL_ID = "ExtendData/Data/ExtendData/Main/StringPathHash.bin"
MANIFEST_LOGICAL_ID = "BundleManifest/Data/Bundles/Windows/manifest.hgmmap"
PROJECTILE_API_VERSION = 1
PREVIEW_TEXT_LIMIT = 2 * 1024 * 1024
PREVIEW_BINARY_LIMIT = 256 * 1024
STREAM_CHUNK_SIZE = 1024 * 1024
MEMORYPACK_SCHEMA = RUNTIME_CONFIG.memorypack_schema
MEMORYPACK_UNION_MAP = RUNTIME_CONFIG.memorypack_union_map


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
    "LODGroup",
)
PAGE_SIZE_MAX = 500


@dataclass(frozen=True)
class FileView:
    file_id: int
    path: str
    name: str
    source: str
    chunk_exists: bool
    length: int
    encrypted: bool


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
            LOGGER.info("database_index_progress", extra={"fileCount": file_count})

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
    LOGGER.info(
        "database_built",
        extra={
            "database": str(db_path),
            "sourceFileCount": file_count,
            "effectiveFileCount": len(effective),
        },
    )


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


class BrowserHandler(BaseHTTPRequestHandler):
    db_path: Path
    manifest_indexes: dict[tuple[int, int, str], ManifestIndex] = {}
    manifest_index_lock = threading.Lock()
    shared_resource_lock = threading.Lock()
    memorypack_schema: SchemaIndex | None = None
    memorypack_union_map: dict[str, dict[int, str]] | None = None
    memorypack_load_error: str | None = None

    def log_message(self, fmt: str, *args) -> None:
        LOGGER.info(
            "http_request",
            extra={
                "clientAddress": self.client_address[0],
                "httpMessage": fmt % args,
            },
        )

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

    def require_current_index(self) -> bool:
        if INDEX_FRESHNESS_REPORT.get("status") == "current":
            return True
        self.send_json(
            {
                "error": "VFS index is stale or has not been verified against the current installation",
                "code": "index_stale",
                "indexFreshness": INDEX_FRESHNESS_REPORT,
                "indexRebuild": INDEX_REBUILD_REPORT,
            },
            status=503,
            cache_control="no-store",
        )
        return False

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
        conn = sqlite3.connect(AUDIO_DIALOG_DB)
        create_audio_dialog_schema(conn)
        conn.commit()
        return conn

    def connect_wwise(self) -> sqlite3.Connection:
        if not WWISE_DB.is_file():
            raise FileNotFoundError(f"Wwise index not built: {WWISE_DB}")
        return sqlite3.connect(WWISE_DB)

    def wwise_catalog_service(self) -> WwiseCatalogService:
        return WwiseCatalogService(self.connect_wwise, page_size_max=PAGE_SIZE_MAX)

    def audio_dialog_service(self) -> AudioDialogService:
        return AudioDialogService(
            self.connect_audio_dialog,
            self.resolve_audio_dialog_media_source,
            self.ensure_indexed_audio_media_file,
            page_size_max=PAGE_SIZE_MAX,
        )

    def resolve_audio_dialog_media_source(
        self, media: dict
    ) -> tuple[dict, Path] | None:
        logical_path = str(media.get("pck_logical_path") or "")
        if logical_path:
            return self.resolve_logical_file_source(logical_path)
        return self.resolve_vfs_file_source(int(media["pck_file_id"]))

    def lookup_wwise_media(self, pck_file_id: int, ordinal: int) -> dict | None:
        with closing(self.connect_wwise()) as conn:
            return get_wwise_media(conn, pck_file_id, ordinal)

    def resolve_vfs_file_source(self, file_id: int) -> tuple[dict, Path] | None:
        with closing(self.connect()) as conn:
            row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
            if row is None:
                return None
            return self.resolve_file_record_quiet(conn, row_to_dict(row))

    def wwise_media_service(self) -> WwiseMediaService:
        return WwiseMediaService(
            self.lookup_wwise_media,
            self.resolve_wwise_media_source,
            self.ensure_indexed_audio_media_file,
        )

    def resolve_wwise_media_source(self, media: dict) -> tuple[dict, Path] | None:
        logical_path = str(media.get("logical_path") or "")
        if logical_path:
            return self.resolve_logical_file_source(logical_path)
        return self.resolve_vfs_file_source(int(media["pck_file_id"]))

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
        if parsed.path == "/api/task-artifact":
            self.handle_task_artifact(parse_qs(parsed.query))
            return
        if parsed.path.startswith("/api/") and not self.require_current_index():
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
        if parsed.path.startswith("/api/") and not self.require_current_index():
            return
        if parsed.path == "/api/tasks/projectile":
            self.handle_start_projectile_task()
            return
        if parsed.path == "/api/tasks/model":
            self.handle_start_model_task()
            return
        if parsed.path == "/api/tasks/model-blend":
            self.handle_start_model_blend_task()
            return
        if parsed.path == "/api/tasks/model-animation":
            self.handle_start_model_animation_task()
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

    def background_task_operations(self) -> BackgroundTaskOperations:
        def create_service():
            service = object.__new__(BrowserHandler)
            service.db_path = self.db_path
            return service

        return BackgroundTaskOperations(TASK_API, create_service)

    def handle_start_projectile_task(self) -> None:
        try:
            body = self.read_json_body()
            projectile_id = normalize_projectile_id(str(body.get("projectileId") or ""))
        except ValueError as error:
            self.send_error_json(400, str(error))
            return

        created = self.background_task_operations().start_projectile(projectile_id)
        self.send_json(created, status=202, cache_control="no-store")

    def handle_start_model_task(self) -> None:
        try:
            request = ModelTaskRequest.parse(self.read_json_body())
        except (TaskInputError, ValueError):
            self.send_error_json(400, "manifestId, assetIndex or lod is invalid")
            return

        resolver = self.manifest_asset_service()
        try:
            resolved = resolver.resolve_model(request.manifest_id, request.asset_index)
        except ManifestAssetResolutionError as error:
            self.send_error_json(error.status, str(error))
            return
        animation_resolved = None
        if request.animation_asset_index is not None:
            try:
                animation_resolved = resolver.resolve(
                    request.manifest_id,
                    request.animation_asset_index,
                )
            except ManifestAssetResolutionError as error:
                self.send_error_json(error.status, str(error))
                return

        created = self.background_task_operations().start_model(
            request.manifest_id,
            resolved,
            animation_resolved,
            request.lod,
        )
        self.send_json(created, status=202, cache_control="no-store")

    def handle_start_model_blend_task(self) -> None:
        blender = optional_tool_registry().capability("blender")
        if not blender.available:
            self.send_error_json(503, f"Blender executable not found: {BLENDER_EXE}")
            return
        try:
            request = ModelBlendTaskRequest.parse(
                self.read_json_body(),
                max_animation_count=MAX_BLEND_ANIMATION_COUNT,
            )
        except (TaskInputError, ValueError):
            self.send_error_json(400, "model blend task input is invalid")
            return

        resolver = self.manifest_asset_service()
        try:
            resolved = resolver.resolve_model(request.manifest_id, request.asset_index)
            animation_sources = resolver.resolve_many(
                request.manifest_id,
                request.animation_asset_indexes,
            )
        except ManifestAssetResolutionError as error:
            self.send_error_json(error.status, str(error))
            return
        created = self.background_task_operations().start_model_blend(
            resolved,
            animation_sources,
            request.lod,
        )
        self.send_json(created, status=202, cache_control="no-store")

    def handle_start_model_animation_task(self) -> None:
        try:
            request = ModelAnimationTaskRequest.parse(self.read_json_body())
        except (TaskInputError, ValueError):
            self.send_error_json(400, "model animation task input is invalid")
            return

        resolver = self.manifest_asset_service()
        try:
            model_resolved = resolver.resolve_model(
                request.manifest_id,
                request.asset_index,
            )
            animation_resolved = resolver.resolve(
                request.manifest_id,
                request.animation_asset_index,
            )
        except ManifestAssetResolutionError as error:
            self.send_error_json(error.status, str(error))
            return
        created = self.background_task_operations().start_model_animation(
            model_resolved,
            animation_resolved,
            request.lod,
        )
        self.send_json(created, status=202, cache_control="no-store")

    def handle_task_status(self, query: dict[str, list[str]]) -> None:
        task_id = query.get("taskId", [""])[0]
        try:
            snapshot = TASK_API.snapshot(task_id)
        except TaskNotFoundError:
            self.send_error_json(404, "task not found")
            return
        self.send_json(snapshot, cache_control="no-store")

    def handle_cancel_task(self, query: dict[str, list[str]]) -> None:
        task_id = query.get("taskId", [""])[0]
        try:
            cancellation = TASK_API.cancel(task_id)
        except TaskNotFoundError:
            self.send_error_json(404, "task not found")
            return
        self.send_json(
            cancellation.snapshot,
            status=cancellation.http_status,
            cache_control="no-store",
        )

    def handle_task_artifact(self, query: dict[str, list[str]]) -> None:
        task_id = query.get("taskId", [""])[0]
        try:
            artifact = TASK_API.artifact(task_id)
        except TaskNotFoundError:
            self.send_error_json(404, "task artifact not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", artifact.content_type)
        self.send_header("Content-Length", str(artifact.path.stat().st_size))
        self.send_header(
            "Content-Disposition",
            f"attachment; filename*=UTF-8''{quote(artifact.name)}",
        )
        self.send_header("Cache-Control", "private, max-age=3600")
        self.end_headers()
        with artifact.path.open("rb") as source:
            while data := source.read(STREAM_CHUNK_SIZE):
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    # Downloads can be cancelled after headers have been accepted.  The
                    # artifact remains valid; ending this request quietly avoids a noisy
                    # socketserver traceback for an ordinary client-side cancellation.
                    return

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
        virtual_path = split_manifest_virtual_path(path)
        if virtual_path is not None:
            self.handle_manifest_virtual_list(scope, *virtual_path, page, page_size)
            return

        def manifest_asset_count(conn: sqlite3.Connection, file_id: int) -> int:
            manifest_record = self.original_file_record(conn, file_id)
            resolved_manifest = (
                self.resolve_file_record_quiet(conn, manifest_record)
                if manifest_record is not None
                else None
            )
            if resolved_manifest is None:
                return 0
            record, chunk = resolved_manifest
            try:
                return self.manifest_index(record, chunk).summary()["assetCount"]
            except (ValueError, OSError, sqlite3.Error):
                return 0

        with self.connect() as conn:
            try:
                document = VfsDirectoryService(manifest_asset_count).list_directory(
                    conn,
                    scope,
                    path,
                    page=page,
                    page_size=page_size,
                )
            except FileNotFoundError as error:
                self.send_error_json(404, str(error))
                return
        self.send_json(document)

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
        try:
            payload = self.audio_dialog_service().list(query)
        except ValueError as error:
            self.send_error_json(400, str(error))
            return
        except FileNotFoundError as error:
            self.send_error_json(404, str(error))
            return
        except sqlite3.DatabaseError as error:
            self.send_error_json(500, f"AudioDialog index error: {error}")
            return
        self.send_json(payload)

    def handle_audio_dialog_entry(self, query: dict[str, list[str]]) -> None:
        try:
            payload = self.audio_dialog_service().entry(query)
        except ValueError as error:
            self.send_error_json(400, str(error))
            return
        except FileNotFoundError as error:
            self.send_error_json(404, str(error))
            return
        except sqlite3.DatabaseError as error:
            self.send_error_json(500, f"AudioDialog index error: {error}")
            return
        self.send_json(payload)

    def handle_audio_dialog_preview(self, query: dict[str, list[str]]) -> None:
        try:
            payload = self.audio_dialog_service().preview(query)
        except ValueError as error:
            self.send_error_json(400, str(error))
            return
        except FileNotFoundError as error:
            self.send_error_json(404, str(error))
            return
        except AudioDialogConflictError as error:
            self.send_error_json(409, str(error))
            return
        except sqlite3.DatabaseError as error:
            self.send_error_json(500, f"AudioDialog index error: {error}")
            return
        self.send_json(payload)

    def handle_audio_dialog_raw(self, query: dict[str, list[str]]) -> None:
        try:
            artifact = self.audio_dialog_service().media(query)
        except ValueError as error:
            self.send_error_json(400, str(error))
            return
        except FileNotFoundError as error:
            self.send_error_json(404, str(error))
            return
        except AudioDialogConflictError as error:
            self.send_error_json(409, str(error))
            return
        except AudioDialogMediaBuildError as error:
            self.send_error_json(500, str(error))
            return
        except StaleAudioIndexError as error:
            self.send_error_json(503, str(error))
            return
        except sqlite3.DatabaseError as error:
            self.send_error_json(500, f"AudioDialog index error: {error}")
            return
        disposition = "attachment" if artifact.download else "inline"
        self.send_response(200)
        self.send_header("Content-Type", guess_content_type(artifact.target.name))
        self.send_header("Content-Length", str(artifact.target.stat().st_size))
        download_name = (
            Path(artifact.logical_path).with_suffix(f".{artifact.mode}").name
            or artifact.target.name
        )
        self.send_header(
            "Content-Disposition",
            f"{disposition}; filename*=UTF-8''{quote(download_name)}",
        )
        self.end_headers()
        with artifact.target.open("rb") as file:
            while data := file.read(STREAM_CHUNK_SIZE):
                self.wfile.write(data)

    def handle_wwise_list(self, query: dict[str, list[str]]) -> None:
        try:
            payload = self.wwise_catalog_service().list(query)
        except ValueError as error:
            self.send_error_json(400, str(error))
            return
        except FileNotFoundError as error:
            self.send_error_json(404, str(error))
            return
        except (sqlite3.DatabaseError, RuntimeError) as error:
            self.send_error_json(500, f"Wwise index error: {error}")
            return
        self.send_json(payload)

    def handle_wwise_preview(self, query: dict[str, list[str]]) -> None:
        try:
            payload = self.wwise_catalog_service().preview(query)
        except ValueError as error:
            self.send_error_json(400, str(error))
            return
        except FileNotFoundError as error:
            self.send_error_json(404, str(error))
            return
        except (sqlite3.DatabaseError, RuntimeError) as error:
            self.send_error_json(500, f"Wwise index error: {error}")
            return
        self.send_json(payload)

    def handle_wwise_raw(self, query: dict[str, list[str]]) -> None:
        try:
            artifact = self.wwise_media_service().resolve(query)
        except ValueError as error:
            self.send_error_json(400, str(error))
            return
        except FileNotFoundError as error:
            self.send_error_json(404, str(error))
            return
        except WwiseMediaBuildError as error:
            self.send_error_json(500, str(error))
            return
        except StaleAudioIndexError as error:
            self.send_error_json(503, str(error))
            return
        except (sqlite3.DatabaseError, RuntimeError) as error:
            self.send_error_json(500, f"Wwise index error: {error}")
            return
        disposition = "attachment" if artifact.download else "inline"
        self.send_response(200)
        self.send_header("Content-Type", guess_content_type(artifact.target.name))
        self.send_header("Content-Length", str(artifact.target.stat().st_size))
        self.send_header(
            "Content-Disposition",
            f"{disposition}; filename={artifact.entry.wem_id}.{artifact.mode}",
        )
        self.end_headers()
        with artifact.target.open("rb") as file:
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
            content_md5 = str(record.get("file_data_md5") or "").casefold()
            source_identity = (
                f"vfs-md5:{content_md5}:length:{int(record['length'])}"
                if content_md5
                else None
            )
            index = ManifestIndex.ensure_for_source(
                lambda: self.read_file_slice(record, chunk_path),
                INTERNAL_CACHE_DIR / "manifests",
                source_identity,
            )
            self.manifest_indexes[key] = index
            return index

    def manifest_asset_service(self) -> ManifestAssetService:
        return ManifestAssetService(self.db_path, self.manifest_index, source_rank)

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

        cache_version = CACHE_VERSIONS.version("string-path-hash")
        with self.shared_resource_lock:
            if target.is_file() and meta_path.is_file():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    if (
                        meta.get("version") == cache_version
                        and meta.get("source") == identity
                        and target.stat().st_size == int(record["length"])
                    ):
                        return target, meta
                except (OSError, json.JSONDecodeError):
                    pass
            root.mkdir(parents=True, exist_ok=True)
            target.unlink(missing_ok=True)
            self.write_file_slice(record, chunk_path, target)
            meta = {
                "version": cache_version,
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

    def model_snapshot_cache_paths(self, record: dict, asset_index: int) -> tuple[Path, Path, Path]:
        return self.model_run_store().cache_paths(int(record["id"]), asset_index)

    def model_run_store(self) -> ModelRunStore:
        return ModelRunStore(INTERNAL_CACHE_DIR, validate_model_document)

    def model_worker_service(self) -> ModelWorkerService:
        return ModelWorkerService(UNITY_WORKER, self.write_file_slice)

    def ordinary_model_document_service(self) -> OrdinaryModelDocumentService:
        return OrdinaryModelDocumentService()

    def ordinary_model_build_service(self) -> OrdinaryModelBuildService:
        return OrdinaryModelBuildService(
            self.model_run_store(),
            self.model_worker_service(),
            self.ordinary_model_document_service(),
            UNITY_WORKER.artifact_identity,
            version=MODEL_SNAPSHOT_VERSION,
            snapshot_types=MODEL_SNAPSHOT_TYPES,
        )

    def avatar_model_document_service(self) -> AvatarModelDocumentService:
        return AvatarModelDocumentService()

    def avatar_model_build_service(self) -> AvatarModelBuildService:
        return AvatarModelBuildService(
            self.model_run_store(),
            self.model_worker_service(),
            self.avatar_model_document_service(),
            self.load_avatar_mesh_plan,
            self.avatar_mesh_bundle_closure,
            self.resolve_bundle_sources,
            selected_container_paths,
            UNITY_WORKER.artifact_identity,
            version=AVATAR_MODEL_SNAPSHOT_VERSION,
            builder_paths=[
                Path(build_static_avatar_mesh_document.__code__.co_filename),
                Path(selected_container_paths.__code__.co_filename),
                Path(__file__).with_name("animestudio_model.py"),
            ],
        )

    def model_glb_service(self) -> ModelGlbService:
        return ModelGlbService(
            build_glb,
            build_blender_material_plans,
            material_plan_cache_identity,
            version=MODEL_GLB_VERSION,
            exporter_path=Path(build_glb.__code__.co_filename),
            shader_archive_root=SHADER_ARCHIVE_ROOT,
            character_shader_path=Path(CHARACTER_NPR_PATH),
        )

    def model_animation_service(self) -> ModelAnimationService:
        return ModelAnimationService(
            self.model_glb_service(),
            self.ensure_manifest_asset_model_glb,
            self.load_model_glb_inputs,
            self.ensure_animation_clip_export,
            attach_animation_clip,
            glb_version=MODEL_GLB_VERSION,
            clip_export_version=ANIMATION_CLIP_EXPORT_VERSION,
            binding_path=Path(attach_animation_clip.__code__.co_filename),
        )

    def blender_export_service(self) -> BlenderExportService:
        blender = optional_tool_registry().capability("blender")
        if blender.resolved_path is None:
            raise FileNotFoundError(f"Blender executable not found: {BLENDER_EXE}")
        return BlenderExportService(
            blender.resolved_path,
            PROJECT_ROOT,
            BLENDER_MODEL_IMPORTER,
            (
                BLENDER_ACTION_SWITCHER,
                PROJECT_ROOT / "blender_materials.py",
                Path(blender_material_plan.__file__),
                PROJECT_ROOT / "character_lighting.py",
            ),
        )

    def model_blend_service(self) -> ModelBlendService:
        return ModelBlendService(
            self.ensure_manifest_asset_model_glb,
            self.ensure_animated_model_glb,
            self.ensure_model_blend_file,
        )

    def model_preview_service(self) -> ModelPreviewService:
        return ModelPreviewService(
            self.ensure_avatar_mesh_model,
            self.ensure_model_hierarchy,
            self.resolve_bundle_sources,
            model_animation_query_hint,
            lambda: (
                optional_tool_registry().available("blender")
                and BLENDER_MODEL_IMPORTER.is_file()
            ),
            self.ensure_manifest_asset_model_glb,
            self.build_model_preview_result,
            glb_version=MODEL_GLB_VERSION,
            blend_version=MODEL_BLEND_VERSION,
            animation_version=MODEL_ANIMATION_CACHE_REVISION,
            max_blend_animation_count=MAX_BLEND_ANIMATION_COUNT,
        )

    def model_artifact_resolver(self) -> ModelArtifactResolver:
        return ModelArtifactResolver(self.model_run_store())

    def model_single_animation_service(self) -> ModelSingleAnimationService:
        return ModelSingleAnimationService(
            self.ensure_avatar_mesh_model,
            self.ensure_model_hierarchy,
            self.resolve_bundle_sources,
            is_dialog_morph_animation_path,
            self.build_skeletal_morph_animation,
            self.ensure_animation_clip_export,
            bind_animation_clip,
        )

    def model_animation_catalog_service(self) -> ModelAnimationCatalogService:
        return ModelAnimationCatalogService(
            default_model_animation_query,
            animation_version=MODEL_ANIMATION_CACHE_REVISION,
            blend_version=MODEL_BLEND_VERSION,
        )

    def ensure_animation_clip_export(
        self,
        record: dict,
        chunk_path: Path,
        asset: dict,
        *,
        cancel_event: object | None = None,
    ) -> tuple[dict, Path, dict]:
        map_meta = self.ensure_assetbundle_map(record, chunk_path, emit_errors=False)
        if map_meta is None:
            raise RuntimeError("AnimationClip export requires a valid AssetMap")
        return self.manifest_worker_service().ensure_animation_clip(
            record,
            chunk_path,
            asset,
            map_meta,
            cancel_event=cancel_event,
        )

    def ensure_model_hierarchy(
        self,
        record: dict,
        chunk_path: Path,
        asset: dict,
        dependency_bundles: list[dict],
        dependency_sources: list[tuple[dict, Path]],
        missing_dependency_bundles: list[dict],
        *,
        cancel_event: object | None = None,
        progress: Callable[[dict], None] | None = None,
    ) -> tuple[dict, dict]:
        return self.ordinary_model_build_service().ensure(
            record,
            chunk_path,
            asset,
            dependency_bundles,
            dependency_sources,
            missing_dependency_bundles,
            cancel_event=cancel_event,
            progress=progress,
        )

    def load_avatar_mesh_plan(
        self,
        index: ManifestIndex,
        asset: dict,
        bundle_record: dict,
        bundle_chunk: Path,
        lod: int,
        *,
        cancel_event: object | None = None,
    ) -> tuple[dict, dict, dict]:
        cancel_options = (
            {"cancel_event": cancel_event} if cancel_event is not None else {}
        )
        exported = self.ensure_manifest_monobehaviour_dump(
            bundle_record,
            bundle_chunk,
            asset,
            **cancel_options,
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
        *,
        cancel_event: object | None = None,
        progress: Callable[[dict], None] | None = None,
    ) -> tuple[dict, dict, Path]:
        return self.avatar_model_build_service().ensure(
            index,
            asset,
            bundle_record,
            bundle_chunk,
            lod,
            cancel_event=cancel_event,
            progress=progress,
        )

    def manifest_worker_service(self) -> ManifestWorkerService:
        return ManifestWorkerService(
            INTERNAL_CACHE_DIR,
            UNITY_WORKER,
            self.write_file_slice,
            WORKER_RUNS,
        )

    def ensure_manifest_projectile_component(
        self,
        record: dict,
        chunk_path: Path,
        asset: dict,
        projectile_id: str,
        *,
        cancel_event: object | None = None,
    ) -> tuple[Path, dict] | None:
        return self.manifest_worker_service().ensure_projectile_component(
            record,
            chunk_path,
            asset,
            projectile_id,
            cancel_event=cancel_event,
        )

    def ensure_manifest_cubemap_export(
        self,
        record: dict,
        chunk_path: Path,
        asset: dict,
    ) -> tuple[dict[str, Path], dict] | None:
        return self.manifest_worker_service().ensure_cubemap_export(
            record,
            chunk_path,
            asset,
        )

    def ensure_manifest_monobehaviour_dump(
        self,
        record: dict,
        chunk_path: Path,
        asset: dict,
        *,
        cancel_event: object | None = None,
    ) -> tuple[Path, dict] | None:
        return self.manifest_worker_service().ensure_monobehaviour_dump(
            record,
            chunk_path,
            asset,
            cancel_event=cancel_event,
        )

    def ensure_manifest_monobehaviour_raw(
        self,
        record: dict,
        chunk_path: Path,
        asset: dict,
        *,
        cancel_event: object | None = None,
    ) -> tuple[Path, dict]:
        return self.manifest_worker_service().ensure_monobehaviour_raw(
            record,
            chunk_path,
            asset,
            cancel_event=cancel_event,
        )

    def ensure_assetbundle_map(
        self,
        record: dict,
        chunk_path: Path,
        emit_errors: bool = True,
        *,
        cancel_event: object | None = None,
    ) -> dict | None:
        try:
            return AssetBundleWorkerService(
                INTERNAL_CACHE_DIR,
                UNITY_WORKER,
                self.write_file_slice,
                WORKER_RUNS,
            ).ensure_map(
                record,
                chunk_path,
                cancel_event=cancel_event,
            )
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

    def ensure_assetbundle_export(self, record: dict, chunk_path: Path, emit_errors: bool = True) -> tuple[Path, dict] | None:
        map_meta = self.ensure_assetbundle_map(record, chunk_path, emit_errors=emit_errors)
        if map_meta is None:
            return None
        try:
            return AssetBundleWorkerService(
                INTERNAL_CACHE_DIR,
                UNITY_WORKER,
                self.write_file_slice,
                WORKER_RUNS,
            ).ensure_preview_export(
                record,
                chunk_path,
                map_meta,
            )
        except (UnityWorkerError, OSError, RuntimeError) as error:
            if emit_errors:
                self.send_json(
                    {
                        "kind": "assetBundle",
                        "status": "exportFailed",
                        "message": str(error),
                    },
                    status=500,
                )
        return None

    def ensure_audio_package_index(self, record: dict, chunk_path: Path) -> dict:
        return audio_package_index_service().ensure_index(
            record,
            lambda offset, size: self.read_file_range(
                record,
                chunk_path,
                offset,
                size,
            ),
        )

    def list_audio_package(self, meta: dict, raw_path: str) -> dict:
        return audio_package_index_service().list_directory(meta, raw_path)

    def ensure_audio_entry_file(self, record: dict, chunk_path: Path, internal_path: str) -> tuple[Path, AudioEntry]:
        return audio_package_index_service().ensure_entry(
            record,
            internal_path,
            lambda offset, size: self.read_file_range(
                record,
                chunk_path,
                offset,
                size,
            ),
        )

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
        return audio_package_index_service().ensure_indexed_media(
            record,
            entry,
            mode,
            cache_namespace,
            lambda offset, size: self.read_file_range(
                record,
                chunk_path,
                offset,
                size,
            ),
        )


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
            reference = parse_manifest_asset_reference(query)
        except ManifestAssetRequestError as error:
            self.send_error_json(400, str(error))
            return None

        try:
            return self.manifest_asset_service().resolve(
                reference.manifest_id,
                reference.asset_index,
            )
        except ManifestAssetResolutionError as error:
            self.send_error_json(error.status, str(error))
            return None

    def resolve_optional_animation_source(
        self,
        query: dict[str, list[str]],
    ) -> tuple[ManifestIndex, dict, dict, Path] | None:
        values = query.get("animationAssetIndex")
        if not values:
            return None
        try:
            reference = parse_manifest_asset_reference(
                query,
                asset_parameter="animationAssetIndex",
            )
            return self.manifest_asset_service().resolve(
                reference.manifest_id,
                reference.asset_index,
            )
        except ManifestAssetRequestError as error:
            self.send_error_json(400, str(error))
        except ManifestAssetResolutionError as error:
            self.send_error_json(error.status, str(error))
        return None

    def resolve_manifest_model_source(
        self,
        query: dict[str, list[str]],
    ) -> tuple[ManifestIndex, dict, dict, Path] | None:
        try:
            reference = parse_manifest_asset_reference(query)
        except ManifestAssetRequestError as error:
            self.send_error_json(400, str(error))
            return None

        try:
            return self.manifest_asset_service().resolve_model(
                reference.manifest_id,
                reference.asset_index,
            )
        except ManifestAssetResolutionError as error:
            self.send_error_json(error.status, str(error))
            return None

    def resolve_animation_sources(
        self,
        query: dict[str, list[str]],
    ) -> list[tuple[ManifestIndex, dict, dict, Path]] | None:
        try:
            indexes = parse_animation_asset_indexes(
                query,
                maximum=MAX_BLEND_ANIMATION_COUNT,
            )
            if not indexes:
                return []
            manifest_id = parse_manifest_id(query)
        except ManifestAssetRequestError as error:
            self.send_error_json(400, str(error))
            return None
        try:
            return self.manifest_asset_service().resolve_many(
                manifest_id,
                indexes,
            )
        except ManifestAssetResolutionError as error:
            self.send_error_json(error.status, str(error))
            return None

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
            found = find_exported_file(
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
            asset_meta = metadata_for_file(
                target,
                export_root,
                metadata_by_export_name(meta),
            )
            return record, target, asset_meta

        asset_type = query.get("type", [""])[0]
        asset_name = query.get("name", [""])[0]
        path_id = query.get("pathId", [""])[0]
        if not asset_type or not asset_name:
            self.send_error_json(400, "AssetBundle asset preview expected path or type/name")
            return None
        found = find_exported_file(export_root, meta, asset_type, asset_name, path_id)
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
            target = usm_video_service().ensure_video(
                record,
                internal_path,
                lambda: self.read_file_slice(record, chunk_path),
            )
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

    def build_model_task_result(
        self,
        manifest_id: int,
        resolved: tuple[ManifestIndex, dict, dict, Path],
        animation_resolved: tuple[ManifestIndex, dict, dict, Path] | None,
        lod: int,
        *,
        cancel_event: threading.Event,
        progress: Callable[[dict], None],
    ) -> dict:
        return self.model_preview_service().build_task(
            manifest_id,
            resolved,
            animation_resolved,
            lod,
            cancel_event=cancel_event,
            progress=progress,
        )

    def build_model_blend_task_result(
        self,
        resolved: tuple[ManifestIndex, dict, dict, Path],
        animation_sources: list[tuple[ManifestIndex, dict, dict, Path]],
        lod: int,
        *,
        cancel_event: threading.Event,
        progress: Callable[[dict], None],
    ) -> dict:
        return self.model_blend_service().prepare(
            resolved,
            animation_sources,
            lod,
            cancel_event=cancel_event,
            progress=progress,
        )

    def build_model_animation_result(
        self,
        model_resolved: tuple[ManifestIndex, dict, dict, Path],
        animation_resolved: tuple[ManifestIndex, dict, dict, Path],
        lod: int,
        *,
        cancel_event: threading.Event | None = None,
        progress: Callable[[dict], None] | None = None,
    ) -> dict:
        return self.model_single_animation_service().build(
            model_resolved,
            animation_resolved,
            lod,
            cancel_event=cancel_event,
            progress=progress,
        )

    def build_model_preview_result(
        self,
        manifest_id: int,
        resolved: tuple[ManifestIndex, dict, dict, Path],
        animation_resolved: tuple[ManifestIndex, dict, dict, Path] | None,
        lod: int,
        *,
        cancel_event: object | None = None,
        progress: Callable[[dict], None] | None = None,
    ) -> dict:
        return self.model_preview_service().build(
            manifest_id,
            resolved,
            animation_resolved,
            lod,
            cancel_event=cancel_event,
            progress=progress,
        )

    def handle_manifest_asset_model(self, query: dict[str, list[str]]) -> None:
        resolved = self.resolve_manifest_model_source(query)
        if resolved is None:
            return
        animation_resolved = self.resolve_optional_animation_source(query)
        if query.get("animationAssetIndex") and animation_resolved is None:
            return
        try:
            lod = int(query.get("lod", ["0"])[0])
            manifest_id = int(query["manifestId"][0])
            payload = self.build_model_preview_result(
                manifest_id,
                resolved,
                animation_resolved,
                lod,
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
            self.send_error_json(504, "model export timed out")
            return
        except (KeyError, OSError, RuntimeError, ValueError, sqlite3.Error) as error:
            self.send_error_json(500, str(error))
            return
        self.send_json(payload)

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
        resolved: tuple[ManifestIndex, dict, dict, Path],
        *,
        lod: int = 0,
        cancel_event: threading.Event | None = None,
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
                cancel_event=cancel_event,
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
                cancel_event=cancel_event,
            )
            cache_root, _, _ = self.model_snapshot_cache_paths(
                bundle_record, int(asset["asset_index"])
            )
            published_root = resolve_published_model_run(
                cache_root,
                str(run_meta.get("selectedRun") or ""),
            )
            if published_root is None:
                raise RuntimeError("published model run is unavailable")
            model_path = published_root / "model.json"
        glb_path = self.model_glb_service().ensure(
            asset,
            bundle_record,
            model_path,
            lod=lod,
            cancel_event=cancel_event,
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
        return self.model_glb_service().load_inputs(
            asset, bundle_record, model_path, lod=lod
        )

    def ensure_animated_model_glb(
        self,
        model_resolved: tuple[ManifestIndex, dict, dict, Path],
        animation_sources: list[tuple[ManifestIndex, dict, dict, Path]],
        *,
        lod: int,
        skip_incompatible: bool = False,
        cancel_event: threading.Event | None = None,
        progress: Callable[[dict], None] | None = None,
    ) -> AnimatedModelBundle:
        return self.model_animation_service().ensure(
            model_resolved,
            animation_sources,
            lod=lod,
            skip_incompatible=skip_incompatible,
            cancel_event=cancel_event,
            progress=progress,
        )

    def handle_manifest_asset_model_glb(self, query: dict[str, list[str]]) -> None:
        resolved = self.resolve_manifest_model_source(query)
        if resolved is None:
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

    def ensure_model_blend_file(
        self,
        glb_path: Path,
        *,
        cancel_event: threading.Event | None = None,
    ) -> Path:
        return self.blender_export_service().ensure_model_blend(
            glb_path, cancel_event=cancel_event
        )

    def handle_manifest_asset_model_blend(self, query: dict[str, list[str]]) -> None:
        resolved = self.resolve_manifest_model_source(query)
        if resolved is None:
            return
        blender = optional_tool_registry().capability("blender")
        if not blender.available:
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
            blend_path = self.ensure_model_blend_file(glb_path)
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
        model_resolved = self.resolve_manifest_model_source(query)
        if model_resolved is None:
            return
        if not query.get("animationAssetIndex"):
            self.send_error_json(400, "animationAssetIndex is required")
            return
        animation_resolved = self.resolve_optional_animation_source(query)
        if animation_resolved is None:
            return

        try:
            lod = int(query.get("lod", ["0"])[0])
            animation = self.build_model_animation_result(
                model_resolved,
                animation_resolved,
                lod,
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
        resolved = self.resolve_manifest_model_source(query)
        if resolved is None:
            return
        try:
            payload = self.model_animation_catalog_service().search(query, resolved)
        except (ValueError, sqlite3.Error) as error:
            self.send_error_json(400, str(error))
            return
        self.send_json(payload, compress=True)

    def handle_manifest_asset_model_buffer(self, query: dict[str, list[str]]) -> None:
        try:
            reference = ModelArtifactReference.parse(query)
        except ValueError as error:
            self.send_error_json(400, str(error))
            return
        target = self.model_artifact_resolver().geometry(reference)
        if target is None:
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
            reference = ModelArtifactReference.parse(query)
        except ValueError as error:
            self.send_error_json(400, str(error))
            return
        try:
            target = self.model_artifact_resolver().texture(
                reference, query.get("path", [""])[0]
            )
        except ModelArtifactRunNotFound as error:
            self.send_error_json(404, str(error))
            return
        if target is None:
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
        optional_tools = optional_tool_registry()
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
                listing = list_export_directory(
                    export_root,
                    path,
                    meta,
                    internal_preview_kind,
                )
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
            vgmstream = optional_tools.capability("vgmstream")
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
                        "wavPreviewAvailable": vgmstream.available,
                        "vgmstreamCli": (
                            str(vgmstream.resolved_path)
                            if vgmstream.resolved_path is not None
                            else str(VGMSTREAM_CLI)
                        ),
                    },
                }
            )
            return
        if suffix == ".usm":
            try:
                listing = usm_video_service().list_directory(record, path)
            except FileNotFoundError as error:
                self.send_error_json(404, str(error))
                return
            usm_convert = optional_tools.capability("usm-convert")
            ffmpeg = optional_tools.capability("ffmpeg")
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
                        "usmConvertAvailable": usm_convert.available,
                        "ffmpegAvailable": ffmpeg.available,
                        "usmConvert": (
                            str(usm_convert.resolved_path)
                            if usm_convert.resolved_path is not None
                            else str(USM_CONVERT)
                        ),
                        "ffmpeg": (
                            str(ffmpeg.resolved_path)
                            if ffmpeg.resolved_path is not None
                            else str(FFMPEG)
                        ),
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
    parser.add_argument("--host", default=RUNTIME_CONFIG.host)
    parser.add_argument("--port", type=parse_port, default=RUNTIME_CONFIG.port)
    parser.add_argument(
        "--log-level",
        choices=("debug", "info", "warning", "error", "critical"),
        default=RUNTIME_CONFIG.log_level,
    )
    parser.add_argument(
        "--log-format",
        choices=("json", "text"),
        default=RUNTIME_CONFIG.log_format,
    )
    parser.add_argument("--rebuild", action="store_true", help="Rebuild SQLite database before serving")
    parser.add_argument("--build-only", action="store_true", help="Rebuild SQLite database and exit")
    parser.add_argument(
        "--no-auto-rebuild",
        action="store_true",
        help="Report stale VFS and secondary indexes without rebuilding them at startup",
    )
    return parser.parse_args(list(argv))


def main(argv: list[str] | None = None) -> int:
    global INDEX_FRESHNESS_REPORT, INDEX_REBUILD_REPORT, MANIFEST_INDEX_REPORT
    global SECONDARY_AUDIO_INDEX_REPORT, SECONDARY_AUDIO_REBUILD_REPORT

    args = parse_args(argv or sys.argv[1:])
    configure_service_logging(args.log_level, args.log_format)
    if args.rebuild or not args.db.exists():
        if not args.index.exists():
            raise SystemExit(f"index not found: {args.index}")
        build_database(args.index, args.db)
    if args.build_only:
        return 0

    BrowserHandler.db_path = args.db
    INDEX_FRESHNESS_REPORT = inspect_index_freshness(args.db)
    INDEX_REBUILD_REPORT = {"status": "notNeeded"}
    if (
        not args.no_auto_rebuild
        and INDEX_FRESHNESS_REPORT.get("status") in {"stale", "unverified"}
    ):
        LOGGER.warning(
            "index_rebuild_started",
            extra={"freshnessStatus": INDEX_FRESHNESS_REPORT.get("status")},
        )
        try:
            INDEX_REBUILD_REPORT = rebuild_index_atomically(
                args.db,
                load_index_source_roots(args.db),
                build_database,
            )
            INDEX_FRESHNESS_REPORT = inspect_index_freshness(args.db)
        except IndexRebuildError as error:
            INDEX_REBUILD_REPORT = {
                "status": "failed",
                "message": str(error),
            }
            LOGGER.error("index_rebuild_failed", extra={"error": str(error)})
    secondary_audio = ensure_secondary_audio_indexes(
        args.db,
        AUDIO_DIALOG_DB,
        WWISE_DB,
        PROJECT_ROOT,
        audio_package_index_service(),
        decrypt_vfs_file,
        auto_rebuild=not args.no_auto_rebuild,
        emit=lambda level, event, payload: getattr(LOGGER, level)(
            event,
            extra=payload,
        ),
    )
    SECONDARY_AUDIO_INDEX_REPORT = secondary_audio.index_report
    SECONDARY_AUDIO_REBUILD_REPORT = secondary_audio.rebuild_report
    if SECONDARY_AUDIO_INDEX_REPORT["status"] != "current":
        LOGGER.warning(
            "secondary_audio_index_audit_failed",
            extra={"report": SECONDARY_AUDIO_INDEX_REPORT},
        )
    manifest_service = object.__new__(BrowserHandler)
    manifest_service.db_path = args.db
    try:
        manifest_summary = manifest_service.resolve_installed_manifest_index().summary()
        MANIFEST_INDEX_REPORT = {
            "status": "ready",
            **manifest_summary,
        }
    except (OSError, sqlite3.Error, ValueError) as error:
        MANIFEST_INDEX_REPORT = {
            "status": "unavailable",
            "message": str(error),
        }
        LOGGER.error("manifest_prewarm_failed", extra={"error": str(error)})
    server = ThreadingHTTPServer((args.host, args.port), BrowserHandler)
    LOGGER.info(
        "server_started",
        extra={
            "host": args.host,
            "port": args.port,
            "database": str(args.db),
            "indexFreshness": INDEX_FRESHNESS_REPORT["status"],
            "missingChunkCount": INDEX_FRESHNESS_REPORT["missingChunkCount"],
            "checkedChunkCount": INDEX_FRESHNESS_REPORT["checkedChunkCount"],
        },
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("server_stopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
