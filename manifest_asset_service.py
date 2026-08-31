"""Manifest 资源身份到本地可读 AssetBundle 的应用服务。"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Callable, Iterable

from manifest_index import ManifestIndex
from logical_file_source_service import LogicalFileSourceService
from bundle_source_service import BundleSourceService
from npc_avatar_config import is_avatar_mesh_asset_path


def is_model_entry_path(path: str) -> bool:
    return Path(path).suffix.casefold() == ".prefab" or is_avatar_mesh_asset_path(path)


class ManifestAssetResolutionError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class ManifestAssetService:
    """解析稳定 manifest/asset ID，不写 HTTP 响应。"""

    def __init__(
        self,
        db_path: Path,
        index_provider: Callable[[dict, Path], ManifestIndex],
        source_ranker: Callable[[str, bool], tuple],
    ):
        self._db_path = db_path
        self._index_provider = index_provider
        self._source_ranker = source_ranker

    def resolve(
        self,
        manifest_id: int,
        asset_index: int,
    ) -> tuple[ManifestIndex, dict, dict, Path]:
        with closing(self._connect()) as conn:
            source_service = LogicalFileSourceService(
                self._db_path, self._source_ranker
            )
            original = source_service.find_record(manifest_id, connection=conn)
            if original is None:
                raise ManifestAssetResolutionError(404, "file not found")
            manifest_source = source_service.resolve_record(original, connection=conn)
            if manifest_source is None:
                raise ManifestAssetResolutionError(
                    404,
                    "chunk not found; this record likely requires a source fallback "
                    "that is unavailable on this host",
                )
            manifest_record, manifest_chunk = manifest_source
            try:
                index = self._index_provider(manifest_record, manifest_chunk)
                asset = index.asset(asset_index)
            except (ValueError, OSError, sqlite3.Error) as error:
                raise ManifestAssetResolutionError(400, str(error)) from error
            if asset is None:
                raise ManifestAssetResolutionError(404, "Manifest 中不存在该资源")

        bundle_sources, missing = BundleSourceService(
            self._db_path, self._source_ranker
        ).resolve_many([{"name": asset["bundle_name"]}])
        if missing or len(bundle_sources) != 1:
            raise ManifestAssetResolutionError(
                404,
                f"找不到资源对应的 AssetBundle：{asset['bundle_name']}",
            )
        bundle_record, bundle_chunk = bundle_sources[0]
        return index, asset, bundle_record, bundle_chunk

    def resolve_many(
        self,
        manifest_id: int,
        asset_indexes: Iterable[int | str],
    ) -> list[tuple[ManifestIndex, dict, dict, Path]]:
        return [
            self.resolve(manifest_id, asset_index)
            for asset_index in sorted({int(value) for value in asset_indexes})
        ]

    def resolve_model(
        self,
        manifest_id: int,
        asset_index: int,
    ) -> tuple[ManifestIndex, dict, dict, Path]:
        resolved = self.resolve(manifest_id, asset_index)
        if not is_model_entry_path(str(resolved[1]["path"])):
            raise ManifestAssetResolutionError(
                400,
                "resource is not a supported model entry",
            )
        return resolved

    def resolve_installed(
        self,
        logical_id: str,
    ) -> tuple[ManifestIndex, dict, Path]:
        """打开当前 VFS 安装中生效且本地可读的 Manifest。"""

        resolved = LogicalFileSourceService(
            self._db_path, self._source_ranker
        ).resolve(logical_id)
        if resolved is None:
            raise FileNotFoundError(f"local VFS manifest is unavailable: {logical_id}")
        record, chunk_path = resolved
        return self._index_provider(record, chunk_path), record, chunk_path

    def candidates_by_name(self, logical_id: str, name: str) -> dict:
        """返回同名资源的全部精确候选，不擅自选择其中一项。"""

        normalized_name = name.strip()
        if not normalized_name or "/" in normalized_name or "\\" in normalized_name:
            raise ManifestAssetResolutionError(
                400, "name must be one exact manifest asset filename"
            )
        try:
            index, record, _ = self.resolve_installed(logical_id)
            candidates = index.assets_by_name(normalized_name)
        except FileNotFoundError as error:
            raise ManifestAssetResolutionError(503, str(error)) from error
        except (OSError, sqlite3.Error, ValueError) as error:
            raise ManifestAssetResolutionError(503, str(error)) from error

        manifest_id = int(record["id"])
        return {
            "name": normalized_name,
            "manifestId": manifest_id,
            "candidates": [
                {
                    **candidate,
                    "previewUrl": (
                        "/api/manifest-asset/preview?"
                        f"manifestId={manifest_id}&assetIndex={candidate['assetIndex']}"
                    ),
                    "rawUrl": (
                        "/api/manifest-asset/raw?"
                        f"manifestId={manifest_id}&assetIndex={candidate['assetIndex']}"
                    ),
                }
                for candidate in candidates
            ],
        }

    def asset_count(
        self,
        connection: sqlite3.Connection,
        file_id: int,
    ) -> int:
        """为 VFS 目录摘要读取 Manifest 资产数；不可用资源按零项处理。"""

        sources = LogicalFileSourceService(self._db_path, self._source_ranker)
        original = sources.find_record(file_id, connection=connection)
        if original is None:
            return 0
        resolved = sources.resolve_record(original, connection=connection)
        if resolved is None:
            return 0
        record, chunk_path = resolved
        try:
            return int(self._index_provider(record, chunk_path).summary()["assetCount"])
        except (KeyError, TypeError, ValueError, OSError, sqlite3.Error):
            return 0

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection
