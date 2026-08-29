"""Stable cache identities for ordinary and Avatar model snapshots."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable


def bundle_identity(record: dict, chunk_path: Path) -> dict:
    return {
        "recordId": int(record["id"]),
        "length": int(record["length"]),
        "offset": int(record["offset"]),
        "chunkPath": str(record["chunk_path"]),
        "chunkMtimeNs": chunk_path.stat().st_mtime_ns,
    }


def ordinary_model_source_identity(
    record: dict,
    chunk_path: Path,
    asset: dict,
    dependency_sources: Iterable[tuple[dict, Path]],
    missing_dependency_bundles: list[dict],
    *,
    builder_mtime_ns: int,
    tool_artifacts: object,
) -> dict:
    return {
        **bundle_identity(record, chunk_path),
        "assetIndex": int(asset["asset_index"]),
        "assetPath": str(asset["path"]),
        "bundleName": str(asset["bundle_name"]),
        "modelBuilderMtimeNs": builder_mtime_ns,
        "dependencies": [
            bundle_identity(dependency, dependency_chunk)
            for dependency, dependency_chunk in dependency_sources
        ],
        "missingDependencyBundles": missing_dependency_bundles,
        "toolArtifacts": tool_artifacts,
    }


def avatar_model_source_identity(
    entry_record: dict,
    entry_chunk: Path,
    asset: dict,
    *,
    lod: int,
    avatar_mesh: dict,
    resource_plan: dict,
    bundle_sources: Iterable[tuple[dict, Path]],
    builder_paths: Iterable[Path],
    tool_artifacts: object,
) -> dict:
    return {
        "entry": {
            **bundle_identity(entry_record, entry_chunk),
            "assetIndex": int(asset["asset_index"]),
            "assetPath": str(asset["path"]),
        },
        "lod": lod,
        "avatarMesh": avatar_mesh,
        "resourcePlan": resource_plan,
        "bundles": [
            bundle_identity(record, chunk)
            for record, chunk in bundle_sources
        ],
        "builders": {
            path.name: path.stat().st_mtime_ns for path in builder_paths
        },
        "toolArtifacts": tool_artifacts,
    }
