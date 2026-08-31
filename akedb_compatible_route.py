"""解析 Endaxis 使用的 AKEDB-compatible 资源路径。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote

from ability_entity_data import normalize_ability_entity_id
from projectile_data import normalize_projectile_id


AKEDB_COMPATIBLE_PREFIX = "/api/akedb-compatible/"
AKEDB_COLLECTIONS = frozenset({"SkillData", "BuffData"})


@dataclass(frozen=True)
class AkedbCompatibleRoute:
    handler_name: str
    arguments: tuple[str, ...] = ()


class AkedbCompatibleRouteError(ValueError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def is_safe_akedb_name(value: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9_]+", value) is not None


def is_safe_akedb_json_file(value: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9_.-]+\.json", value) is not None


def is_akedb_collection(value: str) -> bool:
    return value in AKEDB_COLLECTIONS


def resolve_akedb_compatible_route(request_path: str) -> AkedbCompatibleRoute:
    if not request_path.startswith(AKEDB_COMPATIBLE_PREFIX):
        raise AkedbCompatibleRouteError(404, "AKEDB-compatible resource not found")
    logical_path = unquote(request_path[len(AKEDB_COMPATIBLE_PREFIX) :]).strip("/")
    parts = logical_path.split("/") if logical_path else []
    if len(parts) != 2:
        raise AkedbCompatibleRouteError(404, "AKEDB-compatible resource not found")

    collection, file_name = parts
    if re.fullmatch(r"TableCfg-[A-Za-z0-9@._-]+", collection):
        table_name = file_name.removesuffix(".json")
        if not file_name.endswith(".json") or not is_safe_akedb_name(table_name):
            raise AkedbCompatibleRouteError(400, "invalid TableCfg resource name")
        return AkedbCompatibleRoute("handle_akedb_compatible_table", (table_name,))

    if is_akedb_collection(collection):
        if file_name == "manifest.json":
            return AkedbCompatibleRoute(
                "handle_akedb_compatible_collection_manifest", (collection,)
            )
        if file_name.endswith(".json"):
            if not is_safe_akedb_json_file(file_name):
                raise AkedbCompatibleRouteError(
                    400, "invalid collection resource name"
                )
            return AkedbCompatibleRoute(
                "handle_akedb_compatible_collection_file",
                (collection, file_name),
            )

    if collection == "ProjectileData":
        if file_name == "manifest.json":
            return AkedbCompatibleRoute(
                "handle_akedb_compatible_projectile_manifest"
            )
        if file_name.endswith(".json"):
            try:
                projectile_id = normalize_projectile_id(
                    file_name.removesuffix(".json")
                )
            except ValueError as error:
                raise AkedbCompatibleRouteError(400, str(error)) from error
            return AkedbCompatibleRoute(
                "handle_akedb_compatible_projectile_file", (projectile_id,)
            )

    if collection == "AbilityEntityData":
        if file_name == "manifest.json":
            return AkedbCompatibleRoute(
                "handle_akedb_compatible_ability_entity_manifest"
            )
        if file_name.endswith(".json"):
            try:
                entity_id = normalize_ability_entity_id(
                    file_name.removesuffix(".json")
                )
            except ValueError as error:
                raise AkedbCompatibleRouteError(400, str(error)) from error
            return AkedbCompatibleRoute(
                "handle_akedb_compatible_ability_entity_file", (entity_id,)
            )

    raise AkedbCompatibleRouteError(404, "AKEDB-compatible resource not found")
