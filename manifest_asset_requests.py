"""Parse manifest asset identities from HTTP query parameters."""

from __future__ import annotations

from dataclasses import dataclass


class ManifestAssetRequestError(ValueError):
    pass


@dataclass(frozen=True)
class ManifestAssetReference:
    manifest_id: int
    asset_index: int


def parse_manifest_id(query: dict[str, list[str]]) -> int:
    try:
        return int(query.get("manifestId", [""])[0])
    except (IndexError, ValueError):
        raise ManifestAssetRequestError("Manifest 资源引用无效") from None


def parse_manifest_asset_reference(
    query: dict[str, list[str]],
    *,
    asset_parameter: str = "assetIndex",
) -> ManifestAssetReference:
    try:
        return ManifestAssetReference(
            manifest_id=parse_manifest_id(query),
            asset_index=int(query.get(asset_parameter, [""])[0]),
        )
    except (IndexError, ValueError):
        raise ManifestAssetRequestError("Manifest 资源引用无效") from None


def parse_animation_asset_indexes(
    query: dict[str, list[str]],
    *,
    maximum: int,
) -> tuple[int, ...]:
    raw_values = query.get("animationAssetIndex", [])
    if not raw_values:
        return ()
    try:
        indexes = tuple(
            sorted(
                {
                    int(value)
                    for raw_value in raw_values
                    for value in raw_value.split(",")
                    if value
                }
            )
        )
    except ValueError:
        raise ManifestAssetRequestError("animationAssetIndex is invalid") from None
    if not indexes or len(indexes) > maximum:
        raise ManifestAssetRequestError(
            f"animation selection must contain 1 to {maximum} items"
        )
    return indexes
