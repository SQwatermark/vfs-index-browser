"""Resolve model task requests and submit them to background operations."""

from __future__ import annotations

from typing import Mapping, Protocol

from task_requests import (
    ModelAnimationTaskRequest,
    ModelBlendTaskRequest,
    ModelTaskRequest,
)


class ManifestModelResolver(Protocol):
    def resolve_model(self, manifest_id: int, asset_index: int): ...
    def resolve(self, manifest_id: int, asset_index: int): ...
    def resolve_many(self, manifest_id: int, asset_indexes: tuple[int, ...]): ...


class ModelTaskOperations(Protocol):
    def start_model(self, manifest_id: int, model, animation, lod: int) -> dict: ...
    def start_model_blend(self, model, animations, lod: int) -> dict: ...
    def start_model_animation(self, model, animation, lod: int) -> dict: ...


class ModelTaskSubmissionService:
    def __init__(
        self,
        resolver: ManifestModelResolver,
        operations: ModelTaskOperations,
        *,
        max_blend_animation_count: int,
    ) -> None:
        self._resolver = resolver
        self._operations = operations
        self._max_blend_animation_count = max_blend_animation_count

    def start_model(self, payload: Mapping[str, object]) -> dict:
        request = ModelTaskRequest.parse(payload)
        model = self._resolver.resolve_model(request.manifest_id, request.asset_index)
        animation = (
            self._resolver.resolve(request.manifest_id, request.animation_asset_index)
            if request.animation_asset_index is not None
            else None
        )
        return self._operations.start_model(
            request.manifest_id, model, animation, request.lod
        )

    def start_blend(self, payload: Mapping[str, object]) -> dict:
        request = ModelBlendTaskRequest.parse(
            payload,
            max_animation_count=self._max_blend_animation_count,
        )
        model = self._resolver.resolve_model(request.manifest_id, request.asset_index)
        animations = self._resolver.resolve_many(
            request.manifest_id,
            request.animation_asset_indexes,
        )
        return self._operations.start_model_blend(model, animations, request.lod)

    def start_animation(self, payload: Mapping[str, object]) -> dict:
        request = ModelAnimationTaskRequest.parse(payload)
        model = self._resolver.resolve_model(request.manifest_id, request.asset_index)
        animation = self._resolver.resolve(
            request.manifest_id,
            request.animation_asset_index,
        )
        return self._operations.start_model_animation(model, animation, request.lod)
