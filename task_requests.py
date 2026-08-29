"""后台任务创建请求的严格应用层 DTO。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


class TaskInputError(ValueError):
    pass


@dataclass(frozen=True)
class ModelTaskRequest:
    manifest_id: int
    asset_index: int
    lod: int
    animation_asset_index: int | None

    @classmethod
    def parse(cls, payload: Mapping[str, object]) -> "ModelTaskRequest":
        try:
            manifest_id = int(payload.get("manifestId"))
            asset_index = int(payload.get("assetIndex"))
            lod = int(payload.get("lod", 0))
            animation_value = payload.get("animationAssetIndex")
            animation_asset_index = (
                int(animation_value) if animation_value not in (None, "") else None
            )
            if manifest_id < 0 or asset_index < 0 or lod not in range(4):
                raise ValueError
        except (TypeError, ValueError) as error:
            raise TaskInputError("manifestId, assetIndex or lod is invalid") from error
        return cls(manifest_id, asset_index, lod, animation_asset_index)


@dataclass(frozen=True)
class ModelBlendTaskRequest:
    manifest_id: int
    asset_index: int
    lod: int
    animation_asset_indexes: tuple[int, ...]

    @classmethod
    def parse(
        cls,
        payload: Mapping[str, object],
        *,
        max_animation_count: int,
    ) -> "ModelBlendTaskRequest":
        try:
            manifest_id = int(payload.get("manifestId"))
            asset_index = int(payload.get("assetIndex"))
            lod = int(payload.get("lod", 0))
            raw_indexes = payload.get("animationAssetIndexes", [])
            if not isinstance(raw_indexes, list):
                raise ValueError
            animation_indexes = tuple(int(value) for value in raw_indexes)
            if (
                manifest_id < 0
                or asset_index < 0
                or lod not in range(4)
                or len(animation_indexes) > max_animation_count
            ):
                raise ValueError
        except (TypeError, ValueError) as error:
            raise TaskInputError("model blend task input is invalid") from error
        return cls(manifest_id, asset_index, lod, animation_indexes)


@dataclass(frozen=True)
class ModelAnimationTaskRequest:
    manifest_id: int
    asset_index: int
    animation_asset_index: int
    lod: int

    @classmethod
    def parse(cls, payload: Mapping[str, object]) -> "ModelAnimationTaskRequest":
        try:
            manifest_id = int(payload.get("manifestId"))
            asset_index = int(payload.get("assetIndex"))
            animation_asset_index = int(payload.get("animationAssetIndex"))
            lod = int(payload.get("lod", 0))
            if (
                manifest_id < 0
                or asset_index < 0
                or animation_asset_index < 0
                or lod not in range(4)
            ):
                raise ValueError
        except (TypeError, ValueError) as error:
            raise TaskInputError("model animation task input is invalid") from error
        return cls(manifest_id, asset_index, animation_asset_index, lod)
