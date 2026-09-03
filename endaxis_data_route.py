"""Parse the local VFS data-export routes consumed by Endaxis."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote

from ability_entity_data import normalize_ability_entity_id
from projectile_data import normalize_projectile_id


ENDAXIS_DATA_PREFIX = "/api/endaxis-data/"
ENDAXIS_JSON_COLLECTIONS = frozenset({"SkillData", "BuffData", "GameplayConfig"})


@dataclass(frozen=True)
class EndaxisDataRoute:
    handler_name: str
    arguments: tuple[str, ...] = ()


class EndaxisDataRouteError(ValueError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def is_safe_endaxis_json_file(value: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9_.-]+\.json", value) is not None


def resolve_endaxis_data_route(request_path: str) -> EndaxisDataRoute:
    if not request_path.startswith(ENDAXIS_DATA_PREFIX):
        raise EndaxisDataRouteError(404, "Endaxis data resource not found")
    logical_path = unquote(request_path[len(ENDAXIS_DATA_PREFIX) :]).strip("/")
    parts = logical_path.split("/") if logical_path else []
    if len(parts) != 2:
        raise EndaxisDataRouteError(404, "Endaxis data resource not found")

    collection, file_name = parts
    if collection == "TableCfg-current":
        table_name = file_name.removesuffix(".json")
        if not file_name.endswith(".json") or re.fullmatch(r"[A-Za-z0-9_]+", table_name) is None:
            raise EndaxisDataRouteError(400, "invalid TableCfg resource name")
        return EndaxisDataRoute("handle_endaxis_data_table", (table_name,))

    if collection in ENDAXIS_JSON_COLLECTIONS:
        if file_name == "manifest.json":
            return EndaxisDataRoute("handle_endaxis_data_collection_manifest", (collection,))
        if not is_safe_endaxis_json_file(file_name):
            raise EndaxisDataRouteError(400, "invalid collection resource name")
        return EndaxisDataRoute("handle_endaxis_data_collection_file", (collection, file_name))

    if collection == "ProjectileData":
        if file_name == "manifest.json":
            return EndaxisDataRoute("handle_endaxis_data_projectile_manifest")
        if file_name.endswith(".json"):
            try:
                projectile_id = normalize_projectile_id(file_name.removesuffix(".json"))
            except ValueError as error:
                raise EndaxisDataRouteError(400, str(error)) from error
            return EndaxisDataRoute("handle_endaxis_data_projectile_file", (projectile_id,))

    if collection == "AbilityEntityData":
        if file_name == "manifest.json":
            return EndaxisDataRoute("handle_endaxis_data_ability_entity_manifest")
        if file_name.endswith(".json"):
            try:
                entity_id = normalize_ability_entity_id(file_name.removesuffix(".json"))
            except ValueError as error:
                raise EndaxisDataRouteError(400, str(error)) from error
            return EndaxisDataRoute("handle_endaxis_data_ability_entity_file", (entity_id,))

    raise EndaxisDataRouteError(404, "Endaxis data resource not found")
