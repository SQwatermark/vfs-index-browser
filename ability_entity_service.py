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
        decode_template: Callable | None = None,
        api_version: int = 1,
    ) -> None:
        self._resolve_logical_file = resolve_logical_file
        self._load_manifest_index = load_manifest_index
        self._resolve_asset_bundle = resolve_asset_bundle
        self._export_raw = export_raw
        self._worker_is_unavailable = worker_is_unavailable
        self._manifest_logical_id = manifest_logical_id
        self._api_version = api_version
        self._decode_template = decode_template

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
            if self._decode_template is not None:
                try:
                    decoded = self._decode_template(
                        input_path=raw_path,
                        expected_id=entity_id,
                        request_id=f"ability-entity-{entity_id}",
                    )
                    if decoded.get("id") != entity_id:
                        raise AbilityEntityDecodeError("完整模板 ID 与请求不一致")
                    template["nativeData"] = {"decodeStatus": "complete", **decoded}
                except UnityWorkerError as error:
                    if error.code != "ability_entity_template_decode_failed":
                        raise
                    # 保留已知前缀，但明确指出完整解码尚不支持，不能用于证明引用闭合。
                    template["nativeData"] = {
                        "decodeStatus": "partial",
                        "reason": str(error),
                    }
                    # 技能包在 AbilitySystem 组件的前段，可独立读取；挂点等其他部分失败
                    # 不能让已经有明确边界的技能注册信息丢失，也不能改称完整模板。
                    try:
                        registration = self._decode_template(
                            input_path=raw_path,
                            expected_id=entity_id,
                            request_id=f"ability-entity-skills-{entity_id}",
                            skill_registration_only=True,
                        )
                        if registration.get("id") != entity_id:
                            raise AbilityEntityDecodeError("技能注册表 ID 与请求不一致")
                        template["skillRegistration"] = registration["skillDataBundle"]
                    except UnityWorkerError as registration_error:
                        if registration_error.code != "ability_entity_template_decode_failed":
                            raise
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
