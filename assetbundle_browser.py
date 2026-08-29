"""Browse published AssetBundle exports and bind files back to AssetMap rows."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable
from urllib.parse import unquote


def metadata_by_export_name(meta: dict) -> dict[tuple[str, str], list[dict]]:
    result: dict[tuple[str, str], list[dict]] = {}
    for entry in meta.get("assetEntries") or []:
        name = str(entry.get("Name") or "").lower()
        asset_type = str(entry.get("Type") or "").lower()
        if name and asset_type:
            result.setdefault((asset_type, name), []).append(entry)
    return result


def metadata_for_file(
    child: Path,
    export_root: Path,
    metadata: dict[tuple[str, str], list[dict]],
) -> dict | None:
    try:
        asset_type = child.relative_to(export_root).parts[0].lower()
    except (ValueError, IndexError):
        return None
    name = child.stem
    path_id = None
    suffixed = re.fullmatch(r"(.+)_p([0-9a-fA-F]{16})", name)
    if suffixed:
        name = suffixed.group(1)
        unsigned = int(suffixed.group(2), 16)
        path_id = unsigned - (1 << 64) if unsigned >= (1 << 63) else unsigned
    candidates = metadata.get((asset_type, name.lower()), [])
    if path_id is not None:
        candidates = [
            entry
            for entry in candidates
            if str(entry.get("PathID") or "") == str(path_id)
        ]
    return candidates[0] if len(candidates) == 1 else None


def metadata_matches(
    entry: dict | None,
    asset_type: str,
    asset_name: str,
    path_id: str,
) -> bool:
    if not entry:
        return False
    if str(entry.get("Type") or "").lower() != asset_type.lower():
        return False
    if str(entry.get("Name") or "").lower() != asset_name.lower():
        return False
    return not path_id or str(entry.get("PathID") or "") == path_id


def find_exported_file(
    export_root: Path,
    meta: dict,
    asset_type: str,
    asset_name: str,
    path_id: str = "",
) -> tuple[Path, dict] | None:
    metadata = metadata_by_export_name(meta)
    for child in sorted(export_root.rglob("*"), key=lambda item: item.as_posix().lower()):
        if not child.is_file():
            continue
        asset_meta = metadata_for_file(child, export_root, metadata)
        if metadata_matches(asset_meta, asset_type, asset_name, path_id):
            return child, asset_meta
    return None


def resolve_export_path(root: Path, raw_path: str) -> Path | None:
    normalized = unquote(raw_path).replace("\\", "/").strip("/")
    if not normalized:
        return root
    candidate = (root / normalized).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def list_export_directory(
    export_root: Path,
    raw_path: str,
    meta: dict,
    preview_kind: Callable[[Path], str],
) -> dict:
    current = resolve_export_path(export_root, raw_path)
    if current is None or not current.is_dir():
        raise FileNotFoundError("internal directory not found")

    dirs = []
    files = []
    metadata = metadata_by_export_name(meta)
    for child in sorted(current.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        relative = child.relative_to(export_root).as_posix()
        if child.is_dir():
            file_count, total_bytes = _folder_stats(child)
            dirs.append(
                {
                    "name": child.name,
                    "path": relative,
                    "fileCount": file_count,
                    "totalBytes": total_bytes,
                }
            )
        elif child.is_file():
            files.append(
                {
                    "name": child.name,
                    "path": relative,
                    "size": child.stat().st_size,
                    "kind": preview_kind(child),
                    "asset": metadata_for_file(child, export_root, metadata),
                }
            )
    path = "" if current == export_root else current.relative_to(export_root).as_posix()
    return {"path": path, "dirs": dirs, "files": files}


def _folder_stats(path: Path) -> tuple[int, int]:
    files = [child for child in path.rglob("*") if child.is_file()]
    return len(files), sum(child.stat().st_size for child in files)
