#!/usr/bin/env python3
"""Serve a small local browser for the Endfield VFS JSONL index."""

from __future__ import annotations

import argparse
import gzip
import json
import mimetypes
import os
import re
import sqlite3
import struct
import subprocess
import sys
import threading
import uuid
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import parse_qs, quote, unquote, urlparse

import blender_material_plan
from assetbundle_browser import (
    list_export_directory,
)
from assetbundle_worker_service import (
    ASSETBUNDLE_EXPORT_TYPES,
    AssetBundleWorkerService,
)
from assetbundle_export_service import (
    AssetBundleExportError,
    AssetBundleExportService,
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
)
from ability_entity_service import AbilityEntityService
from akedb_compatible_route import (
    AkedbCompatibleRouteError,
    resolve_akedb_compatible_route,
)
from akedb_compatible_data_service import (
    AkedbCompatibleDataError,
    AkedbCompatibleDataService,
)
from memorypack_value_decoder import MemoryPackValueDecodeError, MemoryPackValueDecoder
from memorypack_schema_service import MemoryPackSchemaService
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
from usm_video_service import UsmVideoService
from manifest_index import ManifestIndex
from manifest_index_service import ManifestIndexService
from index_freshness import inspect_index_freshness
from secondary_audio_startup import ensure_secondary_audio_indexes
from application_startup_service import ApplicationStartupService
from vfs_directory_service import (
    MANIFEST_VIRTUAL_DIR,
    VfsDirectoryService,
    split_manifest_virtual_path,
)
from manifest_virtual_directory_service import (
    ManifestVirtualDirectoryError,
    ManifestVirtualDirectoryService,
)
from manifest_asset_preview_service import ManifestAssetPreviewService
from manifest_asset_file_service import (
    ManifestAssetCubemap,
    ManifestAssetFile,
    ManifestAssetFileError,
    ManifestAssetFileService,
)
from vfs_search_service import VfsSearchService
from file_preview_service import (
    AUDIO_EXTENSIONS,
    IMAGE_EXTENSIONS,
    TEXT_EXTENSIONS,
    VIDEO_EXTENSIONS,
    FilePreviewService,
    file_suffix,
    guess_content_type,
    truncate_text,
)
from vfs_file_preview_service import VfsFilePreviewService, tablecfg_name_for_file
from vfs_file_materializer import VfsFileMaterializer
from vfs_file_reader import VfsFileReader
from vfs_crypto import (
    CHACHA_KEY,
    VFS_PROTO_VERSION,
    chacha20_apply,
    decrypt_vfs_file,
    quarter_round,
    rotl32,
)
from vfs_database_builder import build_database, source_rank
from tablecfg_service import TableCfgResolutionError, TableCfgService
from internal_directory_service import (
    InternalDirectoryError,
    InternalDirectoryService,
    build_internal_tool_metadata,
)
from internal_file_preview_service import InternalFilePreviewService
from internal_file_resolver_service import (
    InternalFileResolution,
    InternalFileResolutionError,
    InternalFileResolverService,
)
from logical_file_source_service import LogicalFileSourceError, LogicalFileSourceService
from bundle_source_service import BundleSourceService
from raw_file_service import RawFileResponse, RawFileService
from request_router import dispatch_get, dispatch_post
from avatar_resource_plan_service import (
    AvatarResourcePlanError,
    AvatarResourcePlanService,
)
from model_task_submission_service import ModelTaskSubmissionService
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
from manifest_request_resolver_service import (
    ManifestRequestResolutionError,
    ManifestRequestResolverService,
)
from manifest_worker_service import CUBEMAP_FACE_NAMES, ManifestWorkerService
from avatar_mesh_snapshot import (
    selected_container_paths,
)
from avatar_model_document_service import AvatarModelDocumentService
from avatar_model_build_service import AvatarModelBuildService
from npc_avatar_model import build_static_avatar_mesh_document
from string_path_hash_file_service import StringPathHashFileService
from npc_avatar_config import (
    is_avatar_mesh_asset_path,
)
from model_document import validate_model_document
from model_run_store import ModelRunStore, resolve_published_model_run
from model_worker_service import ModelWorkerService
from ordinary_model_document_service import OrdinaryModelDocumentService
from ordinary_model_build_service import OrdinaryModelBuildService
from model_glb_service import ModelGlbService
from manifest_model_glb_service import ManifestModelGlbService
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
from model_sync_request import ModelSyncRequest
from gltf_export import build_glb
from material_semantic_plans import CHARACTER_NPR_PATH, build_blender_material_plans
from animestudio_animation import (
    MODEL_ANIMATION_CACHE_REVISION,
    attach_animation_clip,
    bind_animation_clip,
)
from skeletal_morph import is_dialog_morph_animation_path
from skeletal_morph_service import SkeletalMorphService
from projectile_data import (
    ProjectileDecodeError,
    ProjectileNotFoundError,
    ProjectileUnavailableError,
    list_projectile_ids,
    normalize_projectile_id,
)
from projectile_service import ProjectileService
from unity_worker import UnityWorkerClient, UnityWorkerError
from task_registry import BackgroundTaskRegistry, TaskNotFoundError
from task_service import TaskApplicationService
from task_requests import TaskInputError
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
STRING_PATH_HASH_LOCK = threading.Lock()


DEFAULT_INDEX = RUNTIME_CONFIG.default_index
DEFAULT_DB = RUNTIME_CONFIG.database
AUDIO_DIALOG_DB = RUNTIME_CONFIG.audio_dialog_database
WWISE_DB = RUNTIME_CONFIG.wwise_database
PUBLIC_DIR = RUNTIME_CONFIG.public_dir
INTERNAL_CACHE_DIR = RUNTIME_CONFIG.internal_cache
MANIFEST_INDEXES = ManifestIndexService(INTERNAL_CACHE_DIR / "manifests")
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


def installed_manifest_summary(database_path: Path) -> dict:
    reader = VfsFileReader(decrypt_vfs_file)

    def provide_index(record: dict, chunk_path: Path) -> ManifestIndex:
        return MANIFEST_INDEXES.ensure(
            record,
            lambda: reader.read(record, chunk_path),
        )

    index, _, _ = ManifestAssetService(
        database_path,
        provide_index,
        source_rank,
    ).resolve_installed(MANIFEST_LOGICAL_ID)
    return index.summary()


def application_startup_service() -> ApplicationStartupService:
    def rebuild(database_path: Path) -> dict:
        return rebuild_index_atomically(
            database_path,
            load_index_source_roots(database_path),
            build_database,
        )

    def secondary_audio(database_path: Path, auto_rebuild: bool):
        return ensure_secondary_audio_indexes(
            database_path,
            AUDIO_DIALOG_DB,
            WWISE_DB,
            PROJECT_ROOT,
            audio_package_index_service(),
            decrypt_vfs_file,
            auto_rebuild=auto_rebuild,
            emit=lambda level, event, payload: getattr(LOGGER, level)(
                event,
                extra=payload,
            ),
        )

    return ApplicationStartupService(
        inspect_index_freshness,
        rebuild,
        secondary_audio,
        installed_manifest_summary,
        lambda level, event, payload: getattr(LOGGER, level)(event, extra=payload),
        rebuild_error_types=(IndexRebuildError,),
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


def row_to_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


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


def load_memorypack_union_map(path: Path) -> dict[str, dict[int, str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {base_type: {int(tag): derived_type for tag, derived_type in entries.items()} for base_type, entries in raw.items()}


MEMORYPACK_INPUTS = MemoryPackSchemaService(
    MEMORYPACK_SCHEMA,
    MEMORYPACK_UNION_MAP,
    SchemaIndex,
    load_memorypack_union_map,
)


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


class BrowserHandler(BaseHTTPRequestHandler):
    db_path: Path

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

    def send_raw_file(
        self,
        response: RawFileResponse,
        *,
        extra_headers: dict[str, str] | None = None,
        ignore_disconnect: bool = False,
    ) -> None:
        self.send_response(200)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(response.content_length))
        if response.content_disposition is not None:
            self.send_header("Content-Disposition", response.content_disposition)
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        for data in response.chunks():
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                if ignore_disconnect:
                    return
                raise

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
        return LogicalFileSourceService(self.db_path, source_rank).resolve_file_id(file_id)

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

    @staticmethod
    def load_memorypack_decoder_inputs() -> tuple[object, dict]:
        return MEMORYPACK_INPUTS.load()

    def decode_memorypack_json_preview(self, record: dict, chunk_path: Path) -> tuple[str, bool, dict] | None:
        try:
            decoded = self.memorypack_value_decoder().decode(
                record.get("logical_id"), record, chunk_path
            )
        except MemoryPackValueDecodeError as error:
            raise RuntimeError(str(error)) from error
        if decoded is None:
            return None
        meta = {
            "class": decoded.class_name,
            "bytes": decoded.byte_count,
            "consumed": decoded.consumed,
            "complete": decoded.complete,
            "discoveredUnions": {
                base_type: {str(tag): derived_type for tag, derived_type in sorted(entries.items())}
                for base_type, entries in sorted(decoded.discovered_unions.items())
            },
        }
        text, truncated = truncate_text(json.dumps({"__meta": meta, "value": decoded.value}, ensure_ascii=False, indent=2))
        return text, truncated, meta

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if dispatch_get(self, parsed.path, parse_qs(parsed.query)):
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if dispatch_post(self, parsed.path):
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

    def model_task_submission_service(self) -> ModelTaskSubmissionService:
        return ModelTaskSubmissionService(
            self.manifest_asset_service(),
            self.background_task_operations(),
            max_blend_animation_count=MAX_BLEND_ANIMATION_COUNT,
        )

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
            created = self.model_task_submission_service().start_model(
                self.read_json_body()
            )
        except (TaskInputError, ValueError):
            self.send_error_json(400, "manifestId, assetIndex or lod is invalid")
            return
        except ManifestAssetResolutionError as error:
            self.send_error_json(error.status, str(error))
            return
        self.send_json(created, status=202, cache_control="no-store")

    def handle_start_model_blend_task(self) -> None:
        blender = optional_tool_registry().capability("blender")
        if not blender.available:
            self.send_error_json(503, f"Blender executable not found: {BLENDER_EXE}")
            return
        try:
            created = self.model_task_submission_service().start_blend(
                self.read_json_body()
            )
        except (TaskInputError, ValueError):
            self.send_error_json(400, "model blend task input is invalid")
            return
        except ManifestAssetResolutionError as error:
            self.send_error_json(error.status, str(error))
            return
        self.send_json(created, status=202, cache_control="no-store")

    def handle_start_model_animation_task(self) -> None:
        try:
            created = self.model_task_submission_service().start_animation(
                self.read_json_body()
            )
        except (TaskInputError, ValueError):
            self.send_error_json(400, "model animation task input is invalid")
            return
        except ManifestAssetResolutionError as error:
            self.send_error_json(error.status, str(error))
            return
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
        self.send_raw_file(
            RawFileService(STREAM_CHUNK_SIZE).prepare_path(
                artifact.path,
                download=True,
                download_name=artifact.name,
                content_type=artifact.content_type,
            ),
            extra_headers={"Cache-Control": "private, max-age=3600"},
            ignore_disconnect=True,
        )

    def handle_akedb_compatible(self, request_path: str) -> None:
        """按 Endaxis 资源下载器约定输出与 AKEDB 同构的 JSON。"""
        try:
            route = resolve_akedb_compatible_route(request_path)
        except AkedbCompatibleRouteError as error:
            self.send_error_json(error.status, str(error))
            return
        getattr(self, route.handler_name)(*route.arguments)

    def handle_akedb_compatible_table(self, table_name: str) -> None:
        try:
            value = self.akedb_compatible_data_service().table(table_name)
        except AkedbCompatibleDataError as error:
            self.send_error_json(error.status, str(error))
            return
        self.send_json(
            value,
            extra_headers={"X-Endaxis-Source": "vfs-index-browser"},
        )

    def handle_akedb_compatible_collection_manifest(self, collection: str) -> None:
        try:
            value = self.akedb_compatible_data_service().collection_manifest(collection)
        except AkedbCompatibleDataError as error:
            self.send_error_json(error.status, str(error))
            return
        self.send_json(value, extra_headers={"X-Endaxis-Source": "vfs-index-browser"})

    def handle_akedb_compatible_collection_file(self, collection: str, file_name: str) -> None:
        try:
            value = self.akedb_compatible_data_service().collection_file(
                collection, file_name
            )
        except AkedbCompatibleDataError as error:
            self.send_error_json(error.status, str(error))
            return
        self.send_json(
            value,
            extra_headers={"X-Endaxis-Source": "vfs-index-browser"},
        )

    def resolve_installed_manifest_index(self) -> ManifestIndex:
        """打开当前安装版本的精确 Unity manifest 索引。"""
        index, _, _ = self.manifest_asset_service().resolve_installed(
            MANIFEST_LOGICAL_ID
        )
        return index

    def handle_manifest_assets_by_name(self, query: dict[str, list[str]]) -> None:
        """Return exact manifest asset candidates for one referenced resource filename.

        This endpoint intentionally does not pick a winner when Unity contains the same
        filename in several sprite collections.  Asset consumers must narrow candidates
        with their source-domain evidence instead of silently accepting an arbitrary icon.
        """

        try:
            document = self.manifest_asset_service().candidates_by_name(
                MANIFEST_LOGICAL_ID,
                query.get("name", [""])[0],
            )
        except ManifestAssetResolutionError as error:
            self.send_error_json(error.status, str(error))
            return
        self.send_json(
            document,
            extra_headers={"X-Endaxis-Source": "vfs-index-browser"},
        )

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
        return AbilityEntityService(
            self.resolve_logical_file_source,
            self.manifest_index,
            self.resolve_index_asset_bundle,
            self.ensure_manifest_monobehaviour_raw,
            unity_worker_is_unavailable,
            manifest_logical_id=MANIFEST_LOGICAL_ID,
        ).build(entity_id)

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
            document = VfsDirectoryService(lambda *_args: 0).overview(conn)
        self.send_json(document)

    def handle_list(self, query: dict[str, list[str]]) -> None:
        scope = query.get("scope", ["effective"])[0]
        path = unquote(query.get("path", [""])[0]).strip("/")
        page = max(int(query.get("page", ["1"])[0]), 1)
        page_size = min(max(int(query.get("pageSize", ["100"])[0]), 10), PAGE_SIZE_MAX)
        virtual_path = split_manifest_virtual_path(path)
        if virtual_path is not None:
            self.handle_manifest_virtual_list(scope, *virtual_path, page, page_size)
            return

        manifest_assets = self.manifest_asset_service()

        with self.connect() as conn:
            try:
                document = VfsDirectoryService(manifest_assets.asset_count).list_directory(
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
        try:
            with self.connect() as conn:
                document = ManifestVirtualDirectoryService(
                    LogicalFileSourceService(self.db_path, source_rank),
                    self.manifest_index,
                ).list_from_vfs(
                    conn,
                    scope=scope,
                    base_path=base_path,
                    inner_path=inner_path,
                    page=page,
                    page_size=page_size,
                )
        except ManifestVirtualDirectoryError as error:
            self.send_error_json(error.status, str(error))
            return
        except (ValueError, OSError, sqlite3.Error, FileNotFoundError) as error:
            self.send_error_json(400, str(error))
            return
        self.send_json(document)

    def handle_search(self, query: dict[str, list[str]]) -> None:
        scope = query.get("scope", ["effective"])[0]
        term = query.get("q", [""])[0].strip()
        limit = min(max(int(query.get("limit", ["100"])[0]), 1), 500)
        with self.connect() as conn:
            document = VfsSearchService().search(conn, scope, term, limit=limit)
        self.send_json(document)

    def build_projectile_document(
        self,
        projectile_id: str,
        *,
        cancel_event: object | None = None,
    ) -> dict:
        return ProjectileService(
            self.resolve_logical_file_source,
            self.manifest_index,
            self.resolve_index_asset_bundle,
            self.ensure_manifest_projectile_component,
            unity_worker_is_unavailable,
            manifest_logical_id=MANIFEST_LOGICAL_ID,
            api_version=PROJECTILE_API_VERSION,
        ).build(projectile_id, cancel_event=cancel_event)

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
        download_name = (
            Path(artifact.logical_path).with_suffix(f".{artifact.mode}").name
            or artifact.target.name
        )
        self.send_raw_file(
            RawFileService(STREAM_CHUNK_SIZE).prepare_path(
                artifact.target,
                download=artifact.download,
                download_name=download_name,
                content_type=guess_content_type(artifact.target.name),
            )
        )

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
        self.send_raw_file(
            RawFileService(STREAM_CHUNK_SIZE).prepare_path(
                artifact.target,
                download=artifact.download,
                download_name=f"{artifact.entry.wem_id}.{artifact.mode}",
                content_type=guess_content_type(artifact.target.name),
                encode_filename=False,
            )
        )

    def handle_file(self, query: dict[str, list[str]]) -> None:
        try:
            file_id = int(query.get("id", [""])[0])
        except ValueError:
            self.send_error_json(400, "invalid file id")
            return
        record = LogicalFileSourceService(self.db_path, source_rank).find_record(file_id)
        if record is None:
            self.send_error_json(404, "file not found")
            return
        self.send_json(record)

    def file_id_from_query(self, query: dict[str, list[str]]) -> int | None:
        try:
            return int(query.get("id", [""])[0])
        except ValueError:
            self.send_error_json(400, "invalid file id")
            return None

    def resolve_required_file_source(
        self,
        file_id: int,
    ) -> tuple[dict, dict, Path] | None:
        try:
            return LogicalFileSourceService(
                self.db_path,
                source_rank,
            ).resolve_file_id_required(file_id)
        except LogicalFileSourceError as error:
            self.send_error_json(error.status, str(error))
            return None

    def read_file_slice(self, record: dict, chunk_path: Path, limit: int | None = None) -> bytes:
        return VfsFileReader(decrypt_vfs_file).read(record, chunk_path, limit)

    def manifest_index(self, record: dict, chunk_path: Path) -> ManifestIndex:
        return MANIFEST_INDEXES.ensure(
            record,
            lambda: self.read_file_slice(record, chunk_path),
        )

    def manifest_asset_service(self) -> ManifestAssetService:
        return ManifestAssetService(self.db_path, self.manifest_index, source_rank)

    def memorypack_value_decoder(self) -> MemoryPackValueDecoder:
        decode_error_type = DecodeError if isinstance(DecodeError, type) else None
        return MemoryPackValueDecoder(
            infer_class,
            self.load_memorypack_decoder_inputs,
            self.read_file_slice,
            MemoryPackReader,
            Decoder,
            decode_error_type,
        )

    def akedb_compatible_data_service(self) -> AkedbCompatibleDataService:
        return AkedbCompatibleDataService(
            self.db_path,
            self.resolve_logical_file_source,
            self.parse_tablecfg_file,
            self.memorypack_value_decoder(),
        )

    def read_file_range(self, record: dict, chunk_path: Path, relative_offset: int, length: int) -> bytes:
        return VfsFileReader(decrypt_vfs_file).read_range(
            record,
            chunk_path,
            relative_offset,
            length,
        )

    def resolve_logical_file_source(self, logical_id: str) -> tuple[dict, Path] | None:
        """Resolve one local VFS logical file, preferring its effective entry."""
        return LogicalFileSourceService(self.db_path, source_rank).resolve(logical_id)

    def ensure_string_path_hash_file(self) -> tuple[Path, dict]:
        """Materialize the effective runtime path table into the shared cache."""
        return StringPathHashFileService(
            self.resolve_logical_file_source,
            self.write_file_slice,
            STRING_PATH_HASH_LOCK,
            INTERNAL_CACHE_DIR,
            logical_id=STRING_PATH_HASH_LOGICAL_ID,
            cache_version=CACHE_VERSIONS.version("string-path-hash"),
        ).ensure()

    def write_file_slice(self, record: dict, chunk_path: Path, target: Path) -> None:
        VfsFileMaterializer(
            self.read_file_slice,
            stream_chunk_size=STREAM_CHUNK_SIZE,
        ).write(record, chunk_path, target)

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
        avatar_plan_service = self.avatar_resource_plan_service()
        return AvatarModelBuildService(
            self.model_run_store(),
            self.model_worker_service(),
            self.avatar_model_document_service(),
            avatar_plan_service.load,
            avatar_plan_service.bundle_closure,
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

    def manifest_model_glb_service(self) -> ManifestModelGlbService:
        return ManifestModelGlbService(
            self.ensure_avatar_mesh_model,
            self.ensure_model_hierarchy,
            self.resolve_bundle_sources,
            self.model_run_store(),
            self.model_glb_service(),
        )

    def avatar_resource_plan_service(self) -> AvatarResourcePlanService:
        return AvatarResourcePlanService(
            self.ensure_manifest_monobehaviour_dump,
            self.ensure_string_path_hash_file,
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
            self.skeletal_morph_service().build,
            self.ensure_animation_clip_export,
            bind_animation_clip,
        )

    def skeletal_morph_service(self) -> SkeletalMorphService:
        return SkeletalMorphService(
            self.resolve_index_asset_bundle,
            self.ensure_manifest_monobehaviour_raw,
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

    def assetbundle_worker_service(self) -> AssetBundleWorkerService:
        return AssetBundleWorkerService(
            INTERNAL_CACHE_DIR,
            UNITY_WORKER,
            self.write_file_slice,
            WORKER_RUNS,
        )

    def assetbundle_export_service(self) -> AssetBundleExportService:
        return AssetBundleExportService(self.assetbundle_worker_service())

    def manifest_asset_file_service(self) -> ManifestAssetFileService:
        return ManifestAssetFileService(
            self.ensure_assetbundle_export,
            self.ensure_manifest_monobehaviour_dump,
            self.ensure_manifest_cubemap_export,
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
            return self.assetbundle_export_service().ensure_map(
                record,
                chunk_path,
                cancel_event=cancel_event,
            )
        except AssetBundleExportError as error:
            if emit_errors:
                self.send_json(error.document(), status=500)
        return None

    def ensure_assetbundle_export(
        self,
        record: dict,
        chunk_path: Path,
        emit_errors: bool = True,
    ) -> tuple[Path, dict] | None:
        map_meta = self.ensure_assetbundle_map(record, chunk_path, emit_errors=emit_errors)
        if map_meta is None:
            return None
        try:
            return self.assetbundle_export_service().ensure_preview(
                record,
                chunk_path,
                map_meta,
            )
        except AssetBundleExportError as error:
            if emit_errors:
                self.send_json(error.document(), status=500)
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

        return BundleSourceService(self.db_path, source_rank).resolve_many(bundles)

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

    def resolve_manifest_asset_source(
        self,
        query: dict[str, list[str]],
    ) -> tuple[ManifestIndex, dict, dict, Path] | None:
        try:
            return self.manifest_request_resolver_service().resolve_asset(query)
        except ManifestRequestResolutionError as error:
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
            return self.manifest_request_resolver_service().resolve_optional_animation(
                query
            )
        except ManifestRequestResolutionError as error:
            self.send_error_json(error.status, str(error))
        return None

    def resolve_manifest_model_source(
        self,
        query: dict[str, list[str]],
    ) -> tuple[ManifestIndex, dict, dict, Path] | None:
        try:
            return self.manifest_request_resolver_service().resolve_model(query)
        except ManifestRequestResolutionError as error:
            self.send_error_json(error.status, str(error))
            return None

    def resolve_animation_sources(
        self,
        query: dict[str, list[str]],
    ) -> list[tuple[ManifestIndex, dict, dict, Path]] | None:
        if not query.get("animationAssetIndex"):
            return []
        try:
            return self.manifest_request_resolver_service().resolve_animations(
                query,
                maximum=MAX_BLEND_ANIMATION_COUNT,
            )
        except ManifestRequestResolutionError as error:
            self.send_error_json(error.status, str(error))
            return None

    def manifest_request_resolver_service(self) -> ManifestRequestResolverService:
        return ManifestRequestResolverService(self.manifest_asset_service())

    def resolve_manifest_asset_file(
        self,
        query: dict[str, list[str]],
        resolved_source: tuple[ManifestIndex, dict, dict, Path] | None = None,
    ) -> ManifestAssetFile | None:
        resolved_source = resolved_source or self.resolve_manifest_asset_source(query)
        if resolved_source is None:
            return None
        try:
            return self.manifest_asset_file_service().resolve_file(resolved_source)
        except ManifestAssetFileError as error:
            self.send_error_json(error.status, str(error))
            return None

    def resolve_manifest_cubemap_files(
        self,
        query: dict[str, list[str]],
        resolved_source: tuple[ManifestIndex, dict, dict, Path] | None = None,
    ) -> ManifestAssetCubemap | None:
        resolved_source = resolved_source or self.resolve_manifest_asset_source(query)
        if resolved_source is None:
            return None
        return self.manifest_asset_file_service().resolve_cubemap(resolved_source)

    def resolve_internal_file(
        self,
        query: dict[str, list[str]],
    ) -> InternalFileResolution | None:
        file_id = self.file_id_from_query(query)
        if file_id is None:
            return None
        try:
            return self.internal_file_resolver_service().resolve_file_id(
                file_id,
                query,
            )
        except (LogicalFileSourceError, InternalFileResolutionError) as error:
            self.send_error_json(error.status, str(error))
            return None

    def internal_file_resolver_service(self) -> InternalFileResolverService:
        return InternalFileResolverService(
            self.ensure_assetbundle_export,
            self.ensure_audio_entry_file,
            lambda item, source, path: usm_video_service().ensure_video(
                item,
                path,
                lambda: self.read_file_slice(item, source),
            ),
            LogicalFileSourceService(
                self.db_path,
                source_rank,
            ).resolve_file_id_required,
        )

    def resolve_tablecfg_file(self, file_id: int) -> tuple[dict, dict, Path, str] | None:
        try:
            resolved = self.tablecfg_service().resolve_file_id(file_id)
        except TableCfgResolutionError as error:
            self.send_error_json(error.status, str(error))
            return None
        return (
            resolved.original,
            resolved.record,
            resolved.chunk_path,
            resolved.table_name,
        )

    def tablecfg_service(self) -> TableCfgService:
        return TableCfgService(
            LogicalFileSourceService(self.db_path, source_rank),
            self.read_file_slice,
            parse_sparkbuffer,
            tablecfg_name_for_file,
        )

    def parse_tablecfg_file(self, record: dict, chunk_path: Path) -> tuple[dict, bytes]:
        return self.tablecfg_service().parse(record, chunk_path)

    def handle_preview(self, query: dict[str, list[str]]) -> None:
        file_id = self.file_id_from_query(query)
        if file_id is None:
            return

        resolved = self.resolve_required_file_source(file_id)
        if resolved is None:
            return
        original, record, chunk_path = resolved

        document = VfsFilePreviewService(
            self.read_file_slice,
            self.parse_tablecfg_file,
            self.decode_memorypack_json_preview,
        ).build(file_id, original, record, chunk_path)
        self.send_json(document)

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
        root_name = str(parsed.get("name") or table_name)
        self.send_raw_file(
            RawFileService(STREAM_CHUNK_SIZE).prepare_bytes(
                data,
                content_type="application/json; charset=utf-8",
                download=download,
                download_name=f"{root_name}.json",
            )
        )

    def handle_raw(self, query: dict[str, list[str]]) -> None:
        file_id = self.file_id_from_query(query)
        if file_id is None:
            return
        download = query.get("download", ["0"])[0] in {"1", "true", "yes"}
        resolved = self.resolve_required_file_source(file_id)
        if resolved is None:
            return
        original, record, chunk_path = resolved

        response = RawFileService(STREAM_CHUNK_SIZE).prepare_vfs(
            original,
            record,
            chunk_path,
            download=download,
            read_decrypted=lambda: self.read_file_slice(record, chunk_path),
        )
        self.send_raw_file(response)

    def handle_manifest_asset_preview(self, query: dict[str, list[str]]) -> None:
        resolved_source = self.resolve_manifest_asset_source(query)
        if resolved_source is None:
            return
        cubemap = self.resolve_manifest_cubemap_files(query, resolved_source)
        if cubemap is not None:
            manifest_id = query.get("manifestId", [""])[0]
            asset_index = query.get("assetIndex", [""])[0]
            self.send_json(
                ManifestAssetPreviewService().build_cubemap(
                    cubemap.bundle_record,
                    cubemap.faces,
                    cubemap.asset,
                    cubemap.manifest_asset,
                    manifest_id=manifest_id,
                    asset_index=asset_index,
                )
            )
            return

        resolved = self.resolve_manifest_asset_file(query, resolved_source)
        if resolved is None:
            return
        manifest_id = query.get("manifestId", [""])[0]
        asset_index = query.get("assetIndex", [""])[0]
        self.send_json(
            ManifestAssetPreviewService().build_file(
                resolved.bundle_record,
                resolved.target,
                resolved.asset,
                resolved.manifest_asset,
                manifest_id=manifest_id,
                asset_index=asset_index,
            )
        )

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
            options = ModelSyncRequest.parse(query)
            manifest_id = int(query["manifestId"][0])
            payload = self.build_model_preview_result(
                manifest_id,
                resolved,
                animation_resolved,
                options.lod,
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
        try:
            options = ModelSyncRequest.parse(query)
            payload = self.avatar_resource_plan_service().build_from_resolved(
                resolved,
                options.lod,
            )
        except AvatarResourcePlanError as error:
            self.send_error_json(400, str(error))
            return
        except FileNotFoundError as error:
            self.send_error_json(503, str(error))
            return
        except subprocess.TimeoutExpired:
            self.send_error_json(504, "AnimeStudio timed out while exporting AvatarMesh")
            return
        except (KeyError, OSError, RuntimeError, ValueError, sqlite3.Error) as error:
            self.send_error_json(500, str(error))
            return
        self.send_json(payload, compress=True)

    def ensure_manifest_asset_model_glb(
        self,
        resolved: tuple[ManifestIndex, dict, dict, Path],
        *,
        lod: int = 0,
        cancel_event: threading.Event | None = None,
    ) -> tuple[dict, Path, Path]:
        return self.manifest_model_glb_service().ensure(
            resolved,
            lod=lod,
            cancel_event=cancel_event,
        )

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
            options = ModelSyncRequest.parse(query)
            asset, _, glb_path = self.ensure_manifest_asset_model_glb(
                resolved,
                lod=options.lod,
            )
        except subprocess.TimeoutExpired:
            self.send_error_json(504, "AnimeStudio timed out while exporting the model")
            return
        except (KeyError, OSError, RuntimeError, ValueError, sqlite3.Error) as error:
            self.send_error_json(500, str(error))
            return

        name = f"{Path(str(asset['path'])).stem}.glb"
        self.send_raw_file(
            RawFileService(STREAM_CHUNK_SIZE).prepare_path(
                glb_path,
                download=options.download,
                download_name=name,
                content_type="model/gltf-binary",
            ),
            extra_headers={"Cache-Control": "private, max-age=3600"},
        )

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
            options = ModelSyncRequest.parse(query)
            animation_sources = self.resolve_animation_sources(query)
            if animation_sources is None:
                return
            service = self.model_blend_service()
            bundle = service.prepare_bundle(
                resolved,
                animation_sources,
                options.lod,
            )

            if options.prepare:
                download_url = (
                    ModelSyncRequest.download_url(
                        "/api/manifest-asset/model-blend",
                        query,
                    )
                    if not bundle.all_animations_failed
                    else None
                )
                self.send_json(service.preparation_document(bundle, download_url))
                return

            if bundle.all_animations_failed:
                self.send_json(
                    service.failure_document(bundle),
                    status=422,
                )
                return
            artifact = service.build_artifact(bundle)
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

        self.send_raw_file(
            RawFileService(STREAM_CHUNK_SIZE).prepare_path(
                artifact.path,
                download=True,
                download_name=artifact.name,
                content_type="application/x-blender",
            ),
            extra_headers={
                "X-Endfield-Skipped-Animation-Count": str(
                    artifact.skipped_animation_count
                ),
                "Cache-Control": "private, max-age=3600",
            },
        )

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
            options = ModelSyncRequest.parse(query)
            animation = self.build_model_animation_result(
                model_resolved,
                animation_resolved,
                options.lod,
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
        self.send_raw_file(
            RawFileService(STREAM_CHUNK_SIZE).prepare_path(
                target,
                download=None,
                content_type="application/octet-stream",
            ),
            extra_headers={"Cache-Control": "private, max-age=3600"},
        )

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
        self.send_raw_file(
            RawFileService(STREAM_CHUNK_SIZE).prepare_path(
                target,
                download=None,
                content_type="image/png",
            ),
            extra_headers={"Cache-Control": "private, max-age=3600"},
        )

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
            target = cubemap.faces.get(face_name)
            if target is None:
                self.send_error_json(404, "Unknown Cubemap face")
                return
        else:
            resolved = self.resolve_manifest_asset_file(query, resolved_source)
            if resolved is None:
                return
            target = resolved.target
        download = query.get("download", ["0"])[0] in {"1", "true", "yes"}
        self.send_raw_file(
            RawFileService(STREAM_CHUNK_SIZE).prepare_path(target, download=download)
        )

    def handle_internal_list(self, query: dict[str, list[str]]) -> None:
        file_id = self.file_id_from_query(query)
        if file_id is None:
            return
        path = query.get("path", [""])[0]
        resolved = self.resolve_required_file_source(file_id)
        if resolved is None:
            return
        original, record, chunk_path = resolved

        service = InternalDirectoryService(
            self.ensure_assetbundle_export,
            lambda root, inner_path, meta: list_export_directory(
                root,
                inner_path,
                meta,
                internal_preview_kind,
            ),
            self.ensure_audio_package_index,
            self.list_audio_package,
            lambda source, inner_path: usm_video_service().list_directory(
                source,
                inner_path,
            ),
            build_internal_tool_metadata(
                optional_tool_registry(),
                vgmstream_default=VGMSTREAM_CLI,
                usm_convert_default=USM_CONVERT,
                ffmpeg_default=FFMPEG,
            ),
        )
        try:
            document = service.build(
                original,
                record,
                chunk_path,
                path,
            )
        except InternalDirectoryError as error:
            self.send_error_json(error.status, str(error))
            return
        if document is not None:
            self.send_json(document)

    def handle_internal_preview(self, query: dict[str, list[str]]) -> None:
        resolved = self.resolve_internal_file(query)
        if resolved is None:
            return
        rel_path = query.get("path", [""])[0]
        self.send_json(
            InternalFilePreviewService().build(
                resolved.record,
                resolved.target,
                rel_path,
                asset=resolved.asset,
                audio_entry=(
                    resolved.audio_entry.to_json()
                    if resolved.audio_entry is not None
                    else None
                ),
            )
        )

    def handle_internal_raw(self, query: dict[str, list[str]]) -> None:
        resolved = self.resolve_internal_file(query)
        if resolved is None:
            return
        download = query.get("download", ["0"])[0] in {"1", "true", "yes"}
        self.send_raw_file(
            RawFileService(STREAM_CHUNK_SIZE).prepare_path(
                resolved.target,
                download=download,
            )
        )

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
        self.send_raw_file(
            RawFileService(STREAM_CHUNK_SIZE).prepare_path(
                file_path,
                download=None,
                content_type=content_type,
            ),
            extra_headers={"Cache-Control": "no-cache"},
        )


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
    reports = application_startup_service().run(
        args.db,
        auto_rebuild=not args.no_auto_rebuild,
    )
    INDEX_FRESHNESS_REPORT = reports.index_freshness
    INDEX_REBUILD_REPORT = reports.index_rebuild
    SECONDARY_AUDIO_INDEX_REPORT = reports.secondary_audio_indexes
    SECONDARY_AUDIO_REBUILD_REPORT = reports.secondary_audio_rebuild
    MANIFEST_INDEX_REPORT = reports.manifest_index
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
