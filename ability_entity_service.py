"""从精确 Unity 资源构建能力实体兼容文档。"""

from __future__ import annotations

import sqlite3
import subprocess
from typing import Callable

from ability_entity_data import (
    AbilityEntityDecodeError,
    AbilityEntityNotFoundError,
    AbilityEntityUnavailableError,
    parse_ability_entity_template,
    select_ability_entity_asset,
)
from unity_worker import UnityWorkerError


class AbilityEntityService:
    def __init__(
        self,
        resolve_logical_file: Callable,
        load_manifest_index: Callable,
        resolve_asset_bundle: Callable,
        export_raw: Callable,
        worker_is_unavailable: Callable[[UnityWorkerError], bool],
        *,
        manifest_logical_id: str,
        api_version: int = 1,
    ) -> None:
        self._resolve_logical_file = resolve_logical_file
        self._load_manifest_index = load_manifest_index
        self._resolve_asset_bundle = resolve_asset_bundle
        self._export_raw = export_raw
        self._worker_is_unavailable = worker_is_unavailable
        self._manifest_logical_id = manifest_logical_id
        self._api_version = api_version

    def build(self, entity_id: str) -> dict:
        resolved_manifest = self._resolve_logical_file(self._manifest_logical_id)
        if resolved_manifest is None:
            raise AbilityEntityUnavailableError(
                f"local VFS manifest is unavailable: {self._manifest_logical_id}"
            )
        manifest_record, manifest_chunk = resolved_manifest
        try:
            index = self._load_manifest_index(manifest_record, manifest_chunk)
            indexed_asset = select_ability_entity_asset(index, entity_id)
            asset, bundle_record, bundle_chunk = self._resolve_asset_bundle(
                index,
                int(indexed_asset["assetIndex"]),
            )
            raw_path, export_meta = self._export_raw(
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
            if self._worker_is_unavailable(error):
                raise AbilityEntityUnavailableError(str(error)) from error
            raise AbilityEntityDecodeError(
                f"Unity worker {error.code}: {error}"
            ) from error
        except (
            FileNotFoundError,
            OSError,
            sqlite3.Error,
            subprocess.SubprocessError,
        ) as error:
            raise AbilityEntityUnavailableError(str(error)) from error
        except (RuntimeError, ValueError) as error:
            raise AbilityEntityDecodeError(str(error)) from error
        return {
            "apiVersion": self._api_version,
            "abilityEntityId": entity_id,
            "source": {
                "assetPath": asset["path"],
                "assetIndex": int(asset["asset_index"]),
                "bundleName": asset["bundle_name"],
                "rawExport": export_meta.get("exportedFile"),
            },
            "abilityEntityTemplateData": template,
        }
