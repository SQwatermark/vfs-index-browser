"""Resolve Manifest HTTP query identities without writing an HTTP response."""

from __future__ import annotations

from typing import Mapping, Sequence

from manifest_asset_requests import (
    ManifestAssetRequestError,
    parse_animation_asset_indexes,
    parse_manifest_asset_reference,
    parse_manifest_id,
)
from manifest_asset_service import ManifestAssetResolutionError


class ManifestRequestResolutionError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class ManifestRequestResolverService:
    def __init__(self, asset_service: object) -> None:
        self._assets = asset_service

    def resolve_asset(self, query: Mapping[str, Sequence[str]]) -> tuple:
        return self._resolve_reference(query, model=False)

    def resolve_model(self, query: Mapping[str, Sequence[str]]) -> tuple:
        return self._resolve_reference(query, model=True)

    def resolve_optional_animation(
        self,
        query: Mapping[str, Sequence[str]],
    ) -> tuple | None:
        if not query.get("animationAssetIndex"):
            return None
        return self._resolve_reference(
            query,
            model=False,
            asset_parameter="animationAssetIndex",
        )

    def resolve_animations(
        self,
        query: Mapping[str, Sequence[str]],
        *,
        maximum: int,
    ) -> list[tuple]:
        try:
            indexes = parse_animation_asset_indexes(query, maximum=maximum)
            if not indexes:
                return []
            manifest_id = parse_manifest_id(query)
        except ManifestAssetRequestError as error:
            raise ManifestRequestResolutionError(400, str(error)) from error
        try:
            return self._assets.resolve_many(manifest_id, indexes)
        except ManifestAssetResolutionError as error:
            raise ManifestRequestResolutionError(error.status, str(error)) from error

    def _resolve_reference(
        self,
        query: Mapping[str, Sequence[str]],
        *,
        model: bool,
        asset_parameter: str = "assetIndex",
    ) -> tuple:
        try:
            reference = parse_manifest_asset_reference(
                query,
                asset_parameter=asset_parameter,
            )
        except ManifestAssetRequestError as error:
            raise ManifestRequestResolutionError(400, str(error)) from error
        try:
            resolver = self._assets.resolve_model if model else self._assets.resolve
            return resolver(reference.manifest_id, reference.asset_index)
        except ManifestAssetResolutionError as error:
            raise ManifestRequestResolutionError(error.status, str(error)) from error
