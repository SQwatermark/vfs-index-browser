"""Validate the packaged AnimeStudio build used by VFS integrations."""

from __future__ import annotations

import json
from pathlib import Path

from animestudio_model import OBJECT_SNAPSHOT_CONTRACT, OBJECT_SNAPSHOT_VERSION


REQUIRED_CAPABILITIES = frozenset(
    {"BuildCABMap", "UseCABMap", "ObjectJSON", "IdentifiedTexture"}
)


def load_animestudio_tool_manifest(executable: Path) -> dict:
    """Load and validate the capability manifest beside *executable*."""

    path = executable.parent / "vfs-tool-manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RuntimeError(f"AnimeStudio tool manifest is missing: {path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"cannot read AnimeStudio tool manifest {path}: {error}") from error
    if not isinstance(manifest, dict):
        raise RuntimeError(f"AnimeStudio tool manifest root is not an object: {path}")
    snapshot = manifest.get("objectSnapshot")
    if not isinstance(snapshot, dict) or (
        snapshot.get("contract") != OBJECT_SNAPSHOT_CONTRACT
        or snapshot.get("version") != OBJECT_SNAPSHOT_VERSION
    ):
        raise RuntimeError(
            "AnimeStudio build does not declare the required object snapshot contract "
            f"{OBJECT_SNAPSHOT_CONTRACT}/{OBJECT_SNAPSHOT_VERSION}"
        )
    capabilities = manifest.get("capabilities")
    available = set(capabilities) if isinstance(capabilities, list) else set()
    if not REQUIRED_CAPABILITIES.issubset(available):
        missing = sorted(REQUIRED_CAPABILITIES - available)
        raise RuntimeError(f"AnimeStudio build is missing capabilities: {', '.join(missing)}")
    return manifest
