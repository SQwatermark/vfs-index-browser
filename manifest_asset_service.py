"""Manifest 资源身份到本地可读 AssetBundle 的应用服务。"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Callable

from manifest_index import ManifestIndex


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
            original = self._file_by_id(conn, manifest_id)
            if original is None:
                raise ManifestAssetResolutionError(404, "file not found")
            manifest_source = self._readable_source(conn, original)
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

            bundle_file_name = f"Data/Bundles/Windows/{asset['bundle_name']}"
            candidates = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM files WHERE file_name = ?",
                    (bundle_file_name,),
                )
            ]
            candidates.sort(
                key=lambda row: self._source_ranker(
                    str(row["source"]),
                    bool(row["chunk_exists"]),
                )
            )
            bundle_source = next(
                (
                    (candidate, Path(candidate["chunk_path"]))
                    for candidate in candidates
                    if Path(candidate["chunk_path"]).exists()
                ),
                None,
            )
            if bundle_source is None:
                raise ManifestAssetResolutionError(
                    404,
                    f"找不到资源对应的 AssetBundle：{asset['bundle_name']}",
                )

        bundle_record, bundle_chunk = bundle_source
        return index, asset, bundle_record, bundle_chunk

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _file_by_id(conn: sqlite3.Connection, file_id: int) -> dict | None:
        row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        return dict(row) if row is not None else None

    def _readable_source(
        self,
        conn: sqlite3.Connection,
        original: dict,
    ) -> tuple[dict, Path] | None:
        original_path = Path(original["chunk_path"])
        if original_path.exists():
            return original, original_path
        candidates = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM files WHERE logical_id = ?",
                (original["logical_id"],),
            )
        ]
        candidates.sort(
            key=lambda row: self._source_ranker(
                str(row["source"]),
                bool(row["chunk_exists"]),
            )
        )
        return next(
            (
                (candidate, Path(candidate["chunk_path"]))
                for candidate in candidates
                if Path(candidate["chunk_path"]).exists()
            ),
            None,
        )
