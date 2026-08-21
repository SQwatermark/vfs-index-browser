"""Stable projectile identities and ProjectileComponentData export parsing."""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Iterator


PROJECTILE_ASSET_ROOT = "assets/beyond/dynamicassets/gamedata/projectile"
PROJECTILE_COMPONENT_LAYOUT = "Beyond.Gameplay.Core.ProjectileComponentData"
PROJECTILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,159}$")


class ProjectileError(RuntimeError):
    """Base class for errors exposed by the projectile API."""


class ProjectileNotFoundError(ProjectileError):
    """The installed manifest has no projectile with the requested identity."""


class ProjectileUnavailableError(ProjectileError):
    """The identity is known but a required local resource or tool is unavailable."""


class ProjectileDecodeError(ProjectileError):
    """The Unity object was exported but did not yield one stable component value."""


def normalize_projectile_id(value: str) -> str:
    """Validate and normalize an AKEDB/game projectile identifier."""

    projectile_id = value.strip().casefold()
    if not PROJECTILE_ID_RE.fullmatch(projectile_id):
        raise ValueError(
            "projectileId must contain only lowercase ASCII letters, digits, and underscores "
            "(maximum 160 characters)"
        )
    return projectile_id


def projectile_asset_path(projectile_id: str) -> str:
    """Return the exact Unity manifest path for a projectile identifier."""

    normalized = normalize_projectile_id(projectile_id)
    return f"{PROJECTILE_ASSET_ROOT}/data_{normalized}.asset"


def select_projectile_asset(index, projectile_id: str) -> dict:
    """Resolve exactly one projectile asset without substring/fuzzy search."""

    normalized = normalize_projectile_id(projectile_id)
    path = projectile_asset_path(normalized)
    matches = index.assets_by_path(path)
    if not matches:
        raise ProjectileNotFoundError(
            f"projectile {normalized!r} is not present in the installed manifest"
        )
    if len(matches) != 1:
        raise ProjectileDecodeError(
            f"projectile path {path!r} is ambiguous in the installed manifest: "
            f"{len(matches)} matches"
        )
    return matches[0]


def _json_pointer_part(value: object) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def iter_projectile_components(
    value: object,
    pointer: str = "",
) -> Iterator[tuple[str, dict]]:
    """Yield JSON pointers and focused ProjectileComponentData dictionaries."""

    if isinstance(value, dict):
        if value.get("layout") == PROJECTILE_COMPONENT_LAYOUT:
            yield pointer or "/", value
        for key, child in value.items():
            yield from iter_projectile_components(
                child,
                f"{pointer}/{_json_pointer_part(key)}",
            )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_projectile_components(child, f"{pointer}/{index}")


def _safe_export_path(export_root: Path, relative: str) -> Path:
    root = export_root.resolve()
    target = root.joinpath(*PurePosixPath(relative.replace("\\", "/")).parts).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise ProjectileDecodeError(f"exported file escapes the cache root: {relative!r}") from error
    return target


def load_projectile_export(
    export_root: Path,
    exported_files: list[str],
    projectile_id: str,
) -> dict:
    """Load exported Unity JSON and select its ProjectileComponentData value."""

    normalized = normalize_projectile_id(projectile_id)
    candidates: list[dict] = []
    failures: list[str] = []
    for relative in exported_files:
        target = _safe_export_path(export_root, relative)
        if not target.is_file():
            failures.append(f"{relative}: missing")
            continue
        try:
            unity_object = json.loads(target.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            failures.append(f"{relative}: {error}")
            continue
        for pointer, component in iter_projectile_components(unity_object):
            candidates.append(
                {
                    "exportedFile": relative.replace("\\", "/"),
                    "componentPointer": pointer,
                    "component": component,
                    "unityObject": unity_object,
                    "idMatchesRequest": component.get("id") == normalized,
                }
            )

    exact = [candidate for candidate in candidates if candidate["idMatchesRequest"]]
    if len(exact) == 1:
        return exact[0]
    if not exact and len(candidates) == 1:
        return candidates[0]
    detail = f"; unreadable exports: {', '.join(failures)}" if failures else ""
    if not candidates:
        raise ProjectileDecodeError(
            "exported Unity object contains no decoded ProjectileComponentData" + detail
        )
    raise ProjectileDecodeError(
        f"exported Unity objects contain {len(candidates)} ProjectileComponentData values "
        f"and {len(exact)} exact id matches{detail}"
    )
