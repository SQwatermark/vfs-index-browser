"""从本地 Manifest/VFS 证据构建稳定的 Projectile API 文档。"""

from __future__ import annotations

import sqlite3
import subprocess
from typing import Callable

from projectile_data import (
    ProjectileDecodeError,
    ProjectileNotFoundError,
    ProjectileUnavailableError,
    load_projectile_export,
    select_projectile_asset,
)
from unity_worker import UnityWorkerError


class ProjectileService:
    def __init__(
        self,
        resolve_logical_file: Callable,
        load_manifest_index: Callable,
        resolve_asset_bundle: Callable,
        ensure_component: Callable,
        worker_is_unavailable: Callable[[UnityWorkerError], bool],
        *,
        manifest_logical_id: str,
        api_version: int,
    ) -> None:
        self._resolve_logical_file = resolve_logical_file
        self._load_manifest_index = load_manifest_index
        self._resolve_asset_bundle = resolve_asset_bundle
        self._ensure_component = ensure_component
        self._worker_is_unavailable = worker_is_unavailable
        self._manifest_logical_id = manifest_logical_id
        self._api_version = api_version

    def build(
        self,
        projectile_id: str,
        *,
        cancel_event: object | None = None,
    ) -> dict:
        resolved_manifest = self._resolve_logical_file(self._manifest_logical_id)
        if resolved_manifest is None:
            raise ProjectileUnavailableError(
                f"local VFS manifest is unavailable: {self._manifest_logical_id}"
            )
        manifest_record, manifest_chunk = resolved_manifest
        try:
            index = self._load_manifest_index(manifest_record, manifest_chunk)
            indexed_asset = select_projectile_asset(index, projectile_id)
            asset, bundle_record, bundle_chunk = self._resolve_asset_bundle(
                index,
                int(indexed_asset["assetIndex"]),
            )
        except ProjectileNotFoundError:
            raise
        except FileNotFoundError as error:
            raise ProjectileUnavailableError(str(error)) from error
        except (OSError, sqlite3.Error, ValueError) as error:
            raise ProjectileUnavailableError(
                f"cannot query the local manifest: {error}"
            ) from error

        try:
            ensured = self._ensure_component(
                bundle_record,
                bundle_chunk,
                asset,
                projectile_id,
                cancel_event=cancel_event,
            )
        except UnityWorkerError as error:
            if self._worker_is_unavailable(error):
                raise ProjectileUnavailableError(str(error)) from error
            raise ProjectileDecodeError(
                f"Unity worker {error.code}: {error}"
            ) from error
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
            raise ProjectileUnavailableError(str(error)) from error
        if ensured is None:
            raise ProjectileDecodeError(
                "Unity worker did not export the projectile component"
            )

        export_root, export_meta = ensured
        parsed = load_projectile_export(
            export_root,
            [str(value) for value in export_meta.get("exportedFiles", [])],
            projectile_id,
        )
        component = parsed["component"]
        decode_status = self._decode_status(component)
        return {
            "apiVersion": self._api_version,
            "projectileId": projectile_id,
            "source": {
                "manifest": self._record_identity(manifest_record),
                "asset": {
                    "assetIndex": int(asset["asset_index"]),
                    "path": asset["path"],
                    "pathHash": asset["path_hash"],
                    "size": int(asset["size"]),
                    "bundleIndex": int(asset["bundle_index"]),
                    "bundleName": asset["bundle_name"],
                },
                "bundle": self._record_identity(bundle_record),
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

    @staticmethod
    def _decode_status(component: dict) -> str:
        if component.get("$unparsed"):
            return "unparsed"
        if component.get("$partial"):
            return "partial"
        return "decoded"

    @staticmethod
    def _record_identity(record: dict) -> dict:
        return {
            "recordId": int(record["id"]),
            "source": record["source"],
            "logicalId": record["logical_id"],
        }
