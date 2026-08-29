"""Assemble the VFS directory document backed by one BundleManifest index."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from npc_avatar_config import is_avatar_mesh_asset_path
from vfs_directory_service import MANIFEST_VIRTUAL_NAME, join_manifest_virtual_path


class ManifestDirectoryIndex(Protocol):
    def list(self, path: str, page: int, page_size: int) -> dict: ...


class ManifestVirtualDirectoryService:
    def list_directory(
        self,
        index: ManifestDirectoryIndex,
        *,
        scope: str,
        base_path: str,
        inner_path: str,
        manifest_id: int,
        page: int,
        page_size: int,
    ) -> dict:
        listing = index.list(inner_path, page, page_size)
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
        files = [self._asset_document(asset, manifest_id) for asset in listing["files"]]
        directory = listing["directory"]
        return {
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
                "manifestId": manifest_id,
                "bundleCount": int(listing["meta"]["bundleCount"]),
                "assetCount": int(listing["meta"]["assetCount"]),
            },
        }

    @staticmethod
    def _asset_document(asset: dict, manifest_id: int) -> dict:
        params = f"manifestId={manifest_id}&assetIndex={asset['assetIndex']}"
        document = {
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
        if Path(asset["path"]).suffix.casefold() == ".prefab":
            document["modelUrl"] = f"/api/manifest-asset/model?{params}"
        if is_avatar_mesh_asset_path(asset["path"]):
            document["avatarPlanUrl"] = f"/api/manifest-asset/avatar-plan?{params}"
            document["modelUrl"] = f"/api/manifest-asset/model?{params}&lod=0"
        return document
