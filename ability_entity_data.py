"""能力实体模板的稳定身份、精确资源定位与已证实前缀解析。"""

from __future__ import annotations

import re
import struct


ABILITY_ENTITY_ASSET_ROOT = "assets/beyond/dynamicassets/gamedata/abilityentity"
ABILITY_ENTITY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,159}$")
ROOT_TYPE = ("AbilityEntityTemplateData", "Beyond.Gameplay", "Gameplay.Beyond")


class AbilityEntityError(RuntimeError):
    """能力实体兼容资源端点的基础错误。"""


class AbilityEntityNotFoundError(AbilityEntityError):
    """已安装 manifest 中不存在请求的能力实体。"""


class AbilityEntityUnavailableError(AbilityEntityError):
    """本地 VFS 或导出工具暂时不可用。"""


class AbilityEntityDecodeError(AbilityEntityError):
    """原始 Unity 对象不符合已证实的序列化布局。"""


def normalize_ability_entity_id(value: str) -> str:
    entity_id = value.strip().casefold()
    if not ABILITY_ENTITY_ID_RE.fullmatch(entity_id):
        raise ValueError(
            "abilityEntityId must contain only lowercase ASCII letters, digits, and "
            "underscores (maximum 160 characters)"
        )
    return entity_id


def ability_entity_asset_path(entity_id: str) -> str:
    normalized = normalize_ability_entity_id(entity_id)
    return f"{ABILITY_ENTITY_ASSET_ROOT}/data_{normalized}.asset"


def select_ability_entity_asset(index, entity_id: str) -> dict:
    normalized = normalize_ability_entity_id(entity_id)
    path = ability_entity_asset_path(normalized)
    matches = index.assets_by_path(path)
    if not matches:
        raise AbilityEntityNotFoundError(
            f"ability entity {normalized!r} is not present in the installed manifest"
        )
    if len(matches) != 1:
        raise AbilityEntityDecodeError(
            f"ability entity path {path!r} is ambiguous: {len(matches)} matches"
        )
    return matches[0]


def list_ability_entity_ids(index) -> list[str]:
    identities: list[str] = []
    seen: set[str] = set()
    for asset in index.assets_in_directory(ABILITY_ENTITY_ASSET_ROOT):
        name = str(asset.get("name", "")).casefold()
        if not name.startswith("data_") or not name.endswith(".asset"):
            continue
        candidate = name[5:-6]
        try:
            entity_id = normalize_ability_entity_id(candidate)
        except ValueError:
            continue
        if entity_id in seen:
            raise AbilityEntityDecodeError(
                f"ability entity identity {entity_id!r} has duplicate manifest assets"
            )
        seen.add(entity_id)
        identities.append(entity_id)
    return sorted(identities)


def _align4(offset: int) -> int:
    return (offset + 3) & ~3


def _read_i32(data: bytes, offset: int) -> tuple[int, int]:
    if offset + 4 > len(data):
        raise AbilityEntityDecodeError(f"unexpected end of payload at 0x{offset:x}")
    return struct.unpack_from("<i", data, offset)[0], offset + 4


def _read_i64(data: bytes, offset: int) -> tuple[int, int]:
    if offset + 8 > len(data):
        raise AbilityEntityDecodeError(f"unexpected end of payload at 0x{offset:x}")
    return struct.unpack_from("<q", data, offset)[0], offset + 8


def _read_f32(data: bytes, offset: int) -> tuple[float, int]:
    if offset + 4 > len(data):
        raise AbilityEntityDecodeError(f"unexpected end of payload at 0x{offset:x}")
    return struct.unpack_from("<f", data, offset)[0], offset + 4


def _read_bool(data: bytes, offset: int) -> tuple[bool, int]:
    if offset >= len(data) or data[offset] not in (0, 1):
        raise AbilityEntityDecodeError(f"invalid serialized boolean at 0x{offset:x}")
    return data[offset] == 1, offset + 1


def _read_string(data: bytes, offset: int) -> tuple[str, int]:
    length, offset = _read_i32(data, offset)
    if length < 0 or offset + length > len(data):
        raise AbilityEntityDecodeError(f"invalid string length {length} at 0x{offset - 4:x}")
    try:
        value = data[offset : offset + length].decode("utf-8")
    except UnicodeDecodeError as error:
        raise AbilityEntityDecodeError(str(error)) from error
    return value, _align4(offset + length)


def _locate_root_data(data: bytes) -> tuple[int, int, int]:
    """按根 RID 与完整托管类型三元组定位根记录，避免依赖记录顺序。"""

    offset = 12
    _, offset = _read_bool(data, offset)
    offset = _align4(offset) + 12
    _, offset = _read_string(data, offset)
    root_rid, offset = _read_i64(data, offset)
    registry_version, offset = _read_i32(data, offset)
    reference_count, _ = _read_i32(data, offset)
    if registry_version != 2 or reference_count <= 0:
        raise AbilityEntityDecodeError(
            f"unexpected managed reference registry {registry_version}/{reference_count}"
        )

    class_bytes = ROOT_TYPE[0].encode("utf-8")
    search_at = 0
    matches: list[int] = []
    while True:
        occurrence = data.find(class_bytes, search_at)
        if occurrence < 0:
            break
        candidate = occurrence - 4
        try:
            rid = struct.unpack_from("<q", data, candidate - 8)[0]
            class_name, after_class = _read_string(data, candidate)
            namespace, after_namespace = _read_string(data, after_class)
            assembly, data_offset = _read_string(data, after_namespace)
        except (AbilityEntityDecodeError, struct.error):
            pass
        else:
            if rid == root_rid and (class_name, namespace, assembly) == ROOT_TYPE:
                matches.append(data_offset)
        search_at = occurrence + 1
    if len(matches) != 1:
        raise AbilityEntityDecodeError(
            f"expected one root AbilityEntityTemplateData record, got {matches}"
        )
    return matches[0], reference_count, root_rid


def _parse_blackboard_int(data: bytes, offset: int) -> tuple[dict, int]:
    use_key, offset = _read_bool(data, offset)
    value, offset = _read_i32(data, _align4(offset))
    key, offset = _read_string(data, offset)
    if use_key != bool(key):
        raise AbilityEntityDecodeError("BlackboardInt key flag does not match key")
    return {"useBlackboardKey": use_key, "value": value, "blackboardKey": key}, offset


def _parse_blackboard_double(data: bytes, offset: int) -> tuple[dict, int]:
    use_key, offset = _read_bool(data, offset)
    value, offset = _read_f32(data, _align4(offset))
    key, offset = _read_string(data, offset)
    if use_key != bool(key):
        raise AbilityEntityDecodeError("BlackboardDouble key flag does not match key")
    return {"useBlackboardKey": use_key, "value": value, "blackboardKey": key}, offset


def parse_ability_entity_template(data: bytes, expected_id: str) -> dict:
    """只解析反编译与样本共同证实的逻辑前缀，后续未知字段保持在边界外。"""

    expected_id = normalize_ability_entity_id(expected_id)
    offset, reference_count, root_rid = _locate_root_data(data)
    game_id, offset = _read_string(data, offset)
    name, offset = _read_string(data, offset)
    faction, offset = _read_i32(data, offset)
    tag_count, offset = _read_i32(data, offset)
    if not 0 <= tag_count <= 256:
        raise AbilityEntityDecodeError(f"invalid bornTag count {tag_count}")
    born_tags = []
    for _ in range(tag_count):
        tag, offset = _read_i32(data, offset)
        born_tags.append(tag)
    delay_to_recycle, offset = _read_f32(data, offset)
    delay_recycle_perform, offset = _read_f32(data, offset)
    send_die_event, offset = _read_bool(data, offset)
    enable_born_fade_in, offset = _read_bool(data, _align4(offset))
    fade_in_time, offset = _read_f32(data, _align4(offset))
    component_count, offset = _read_i32(data, offset)
    if component_count <= 0 or component_count >= reference_count:
        raise AbilityEntityDecodeError(
            f"invalid component count {component_count} for {reference_count} references"
        )
    component_rids = []
    for _ in range(component_count):
        rid, offset = _read_i64(data, offset)
        component_rids.append(rid)
    if 0 in component_rids or len(set(component_rids)) != component_count:
        raise AbilityEntityDecodeError("componentList contains null or duplicate references")
    max_stacking_count, offset = _read_i32(data, offset)
    max_stacking_count_bb, offset = _parse_blackboard_int(data, offset)
    life_type, offset = _read_i32(data, offset)
    duration, offset = _read_f32(data, offset)
    duration_bb, offset = _parse_blackboard_double(data, offset)
    max_duration_for_server, _ = _read_f32(data, offset)
    # GameDataWithId.id is the stable asset identity. BaseTemplateData.name is a
    # separate template label and may intentionally be shared by multiple assets
    # (Typhoea's floating-arrow variants are the first current-game example).
    if game_id != expected_id:
        raise AbilityEntityDecodeError(
            f"template identity mismatch: expected {expected_id!r}, got {game_id!r}/{name!r}"
        )
    return {
        "gameId": game_id,
        "name": name,
        "factionNativeValue": faction,
        "bornTagIds": born_tags,
        "lifeTypeNativeValue": life_type,
        "durationSeconds": duration,
        "durationBlackboard": duration_bb,
        "maxDurationForServerSeconds": max_duration_for_server,
        "maxStackingCount": max_stacking_count,
        "maxStackingCountBlackboard": max_stacking_count_bb,
        "delayToRecycleSeconds": delay_to_recycle,
        "delayRecyclePerformSeconds": delay_recycle_perform,
        "sendDieEvent": send_die_event,
        "enableBornFadeIn": enable_born_fade_in,
        "fadeInSeconds": fade_in_time,
        "componentCount": component_count,
        "managedReferenceCount": reference_count,
        "rootRid": root_rid,
    }
