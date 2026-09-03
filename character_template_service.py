"""精确定位角色模板；二进制布局只交给既有 C# CharacterTemplateDecoder。"""

from __future__ import annotations

import re
import sqlite3
from typing import Callable

from unity_worker import UnityWorkerError


# 路径证据见 docs/research/memorypack-arcane-2026-08-26.md。
CHARACTER_ASSET_ROOT = "assets/beyond/dynamicassets/gamedata/characterdata"


class CharacterTemplateError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def normalize_character_id(value: str) -> str:
    identity = value.strip().casefold()
    if re.fullmatch(r"chr_[0-9]{4}_[a-z0-9]+", identity) is None:
        raise ValueError("character ID must match chr_<four digits>_<name>")
    return identity


class CharacterTemplateService:
    def __init__(
        self,
        resolve_index: Callable,
        resolve_asset_bundle: Callable,
        export_raw: Callable,
        decode: Callable,
        worker_is_unavailable: Callable[[UnityWorkerError], bool],
    ):
        self._resolve_index = resolve_index
        self._resolve_asset_bundle = resolve_asset_bundle
        self._export_raw = export_raw
        self._decode = decode
        self._worker_is_unavailable = worker_is_unavailable

    def manifest(self) -> list[dict]:
        try:
            index = self._resolve_index()
            identities: set[str] = set()
            for asset in index.assets_in_directory(CHARACTER_ASSET_ROOT):
                name = str(asset["name"]).casefold()
                if not name.startswith("data_") or not name.endswith(".asset"):
                    continue
                try:
                    identity = normalize_character_id(name[5:-6])
                except ValueError:
                    continue
                if identity in identities:
                    raise CharacterTemplateError(422, f"duplicate character asset: {identity}")
                identities.add(identity)
            return [
                {"contentFile": f"/api/endaxis-data/CharacterData/{identity}.runtime-template.json"}
                for identity in sorted(identities)
            ]
        except (OSError, sqlite3.Error) as error:
            raise CharacterTemplateError(503, str(error)) from error

    def build(self, character_id: str) -> dict:
        try:
            identity = normalize_character_id(character_id)
        except ValueError as error:
            raise CharacterTemplateError(400, str(error)) from error
        try:
            index = self._resolve_index()
            asset_path = f"{CHARACTER_ASSET_ROOT}/data_{identity}.asset"
            candidates = index.assets_by_path(asset_path)
            if not candidates:
                raise CharacterTemplateError(404, f"character asset not found: {identity}")
            if len(candidates) != 1:
                raise CharacterTemplateError(422, f"ambiguous character asset: {asset_path}")
            asset, record, chunk = self._resolve_asset_bundle(index, int(candidates[0]["assetIndex"]))
            raw_path, _ = self._export_raw(record, chunk, asset)
            # 返回唯一解码器的原始文档；不抹去 partial/raw，也不添加依赖本机路径的包装。
            return self._decode(
                input_path=raw_path,
                expected_id=identity,
                request_id=f"character-template:{identity}",
            )
        except CharacterTemplateError:
            raise
        except UnityWorkerError as error:
            unavailable = self._worker_is_unavailable(error) or error.code == "unknown_operation"
            raise CharacterTemplateError(503 if unavailable else 422, f"{error.code}: {error}") from error
        except (OSError, sqlite3.Error) as error:
            raise CharacterTemplateError(503, str(error)) from error
        except (RuntimeError, ValueError) as error:
            raise CharacterTemplateError(422, str(error)) from error
