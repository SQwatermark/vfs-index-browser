"""Resolved model assembly provenance shared by model source adapters."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any


ASSEMBLY_KINDS = {"prefab", "avatarMesh"}
SOURCE_BASES = {"unity-y-up", "avatar-mesh-z-up"}
DOCUMENT_BASIS = "unity-y-up"


def create_prefab_assembly(entry_source: Mapping[str, Any]) -> dict[str, Any]:
    """Describe a Prefab whose renderer graph is authored by Unity references."""

    return {
        "kind": "prefab",
        "sourceBasis": "unity-y-up",
        "documentBasis": DOCUMENT_BASIS,
        "entrySource": deepcopy(dict(entry_source)),
        "parts": [],
    }


def create_avatar_mesh_assembly(
    *,
    entry_source: Mapping[str, Any],
    lod: int,
    parts: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Describe the concrete AvatarMesh choices used to build one model."""

    return {
        "kind": "avatarMesh",
        "sourceBasis": "avatar-mesh-z-up",
        "documentBasis": DOCUMENT_BASIS,
        "entrySource": deepcopy(dict(entry_source)),
        "lod": lod,
        "parts": [deepcopy(dict(part)) for part in parts],
    }


def validate_model_assembly(assembly: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Check assembly invariants that are clearer outside JSON Schema."""

    errors: list[dict[str, Any]] = []

    def error(code: str, message: str, part_id: str | None = None) -> None:
        value: dict[str, Any] = {"code": code, "message": message}
        if part_id is not None:
            value["objectId"] = part_id
        errors.append(value)

    kind = assembly.get("kind")
    if kind not in ASSEMBLY_KINDS:
        error("INVALID_ASSEMBLY_KIND", f"unsupported assembly kind {kind!r}")
    if assembly.get("sourceBasis") not in SOURCE_BASES:
        error("INVALID_SOURCE_BASIS", "assembly sourceBasis is not recognized")
    if assembly.get("documentBasis") != DOCUMENT_BASIS:
        error("INVALID_DOCUMENT_BASIS", f"documentBasis must be {DOCUMENT_BASIS!r}")

    parts = assembly.get("parts")
    if not isinstance(parts, list):
        error("INVALID_ASSEMBLY_PARTS", "assembly parts must be an array")
        return errors

    ids: set[str] = set()
    node_ids: set[str] = set()
    for index, part in enumerate(parts):
        if not isinstance(part, Mapping):
            error("INVALID_ASSEMBLY_PART", f"assembly part {index} must be an object")
            continue
        part_id = part.get("id")
        node_id = part.get("nodeId")
        if not isinstance(part_id, str) or not part_id:
            error("INVALID_ASSEMBLY_PART", f"assembly part {index} has no id")
            continue
        if part_id in ids:
            error("DUPLICATE_ASSEMBLY_PART", f"duplicate assembly part {part_id!r}", part_id)
        ids.add(part_id)
        if not isinstance(node_id, str) or not node_id:
            error("INVALID_ASSEMBLY_NODE", "assembly part has no nodeId", part_id)
        elif node_id in node_ids:
            error("DUPLICATE_ASSEMBLY_NODE", f"node {node_id!r} is selected twice", part_id)
        node_ids.add(node_id)

    if kind == "prefab" and parts:
        error(
            "PREFAB_ASSEMBLY_HAS_PARTS",
            "Prefab assembly choices come from the Unity reference graph, not explicit parts",
        )
    if kind == "avatarMesh":
        lod = assembly.get("lod")
        if not isinstance(lod, int) or lod not in range(4):
            error("INVALID_ASSEMBLY_LOD", f"AvatarMesh lod must be in 0..3, got {lod!r}")
        if not parts:
            error("EMPTY_AVATAR_ASSEMBLY", "AvatarMesh assembly must select at least one part")
        for part in parts:
            if isinstance(part, Mapping) and part.get("lod") != lod:
                error(
                    "INCONSISTENT_ASSEMBLY_LOD",
                    f"part lod {part.get('lod')!r} does not match assembly lod {lod!r}",
                    part.get("id") if isinstance(part.get("id"), str) else None,
                )

    return errors
