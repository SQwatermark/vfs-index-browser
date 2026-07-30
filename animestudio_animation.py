"""Attach AnimeStudio's compact animation export to a ModelDocument."""

from __future__ import annotations

import hashlib
import json
import struct
import zlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from model_document import add_diagnostic


ANIMATION_FORMAT = "AnimeStudioAnimationClip"
ANIMATION_VERSION = "1.0.0"
GEOMETRY_BUFFER_ID = "buffer:geometry"
TRANSFORM_PROPERTIES = {
    "translation": ("vec3", 3),
    "rotation": ("vec4", 4),
    "scale": ("vec3", 3),
}


def load_unique_animation_clip(export_root: Path, expected_name: str) -> tuple[dict, Path]:
    """Load the single compact export whose clip name matches *expected_name*."""

    candidates = sorted(export_root.rglob("*.animation.json"))
    matching = []
    for candidate in candidates:
        clip = json.loads(candidate.read_text(encoding="utf-8"))
        if str(clip.get("name") or "").casefold() == expected_name.casefold():
            matching.append((clip, candidate))
    if len(matching) != 1:
        raise RuntimeError(
            f"AnimeStudio exported {len(candidates)} animation JSON files, "
            f"but {len(matching)} match {expected_name!r}; expected exactly one"
        )
    return matching[0]


def attach_animation_clip(
    document: dict[str, Any],
    geometry: bytes,
    clip: Mapping[str, Any],
    *,
    animation_id: str,
    source: Mapping[str, Any],
    buffer_uri: str | None = None,
) -> bytes:
    """Append transform curves and their binary accessors to *document*.

    Animation paths are Unity CRC32 hashes relative to an Animator root. A
    model snapshot does not yet identify that root explicitly, so every
    ancestor-relative node path is indexed and only unique matches are used.
    """

    if clip.get("format") != ANIMATION_FORMAT:
        raise ValueError(f"animation format must be {ANIMATION_FORMAT!r}")
    if clip.get("version") != ANIMATION_VERSION:
        raise ValueError(f"animation version must be {ANIMATION_VERSION!r}")

    timelines = clip.get("timelines")
    curves = clip.get("curves")
    if not isinstance(timelines, list) or not isinstance(curves, list):
        raise ValueError("animation timelines and curves must be arrays")

    node_hashes = _index_node_path_hashes(document)
    binary = bytearray(geometry)
    timeline_accessors: dict[int, str] = {}
    channels = []
    unresolved_hashes = set()
    ambiguous_hashes = set()
    unsupported_float_count = 0

    def add_accessor(
        suffix: str,
        values: list[float],
        value_type: str,
        count: int,
        *,
        bounds: bool = False,
    ) -> str:
        while len(binary) % 4:
            binary.append(0)
        offset = len(binary)
        binary.extend(struct.pack(f"<{len(values)}f", *values))
        view_id = f"{animation_id}:view:{suffix}"
        accessor_id = f"{animation_id}:accessor:{suffix}"
        document["bufferViews"].append(
            {
                "id": view_id,
                "bufferId": GEOMETRY_BUFFER_ID,
                "byteOffset": offset,
                "byteLength": len(binary) - offset,
            }
        )
        accessor: dict[str, Any] = {
            "id": accessor_id,
            "bufferViewId": view_id,
            "componentType": "f32",
            "type": value_type,
            "count": count,
        }
        if bounds and values:
            accessor["min"] = [min(values)]
            accessor["max"] = [max(values)]
        document["accessors"].append(accessor)
        return accessor_id

    for curve_index, curve in enumerate(curves):
        if not isinstance(curve, Mapping):
            raise ValueError(f"animation curve {curve_index} must be an object")
        property_name = curve.get("property")
        if property_name == "float":
            unsupported_float_count += 1
            continue
        layout = TRANSFORM_PROPERTIES.get(property_name)
        if layout is None:
            raise ValueError(f"unsupported animation property {property_name!r}")

        path_hash = curve.get("pathHash")
        if not isinstance(path_hash, int) or not 0 <= path_hash <= 0xFFFFFFFF:
            raise ValueError(f"animation curve {curve_index} has an invalid pathHash")
        targets = node_hashes.get(path_hash, set())
        if not targets:
            unresolved_hashes.add(path_hash)
            continue
        if len(targets) != 1:
            ambiguous_hashes.add(path_hash)
            continue
        target_id = next(iter(targets))

        timeline_index = curve.get("timeline")
        if (
            not isinstance(timeline_index, int)
            or not 0 <= timeline_index < len(timelines)
            or not isinstance(timelines[timeline_index], list)
        ):
            raise ValueError(f"animation curve {curve_index} has an invalid timeline")
        times = timelines[timeline_index]
        if not all(isinstance(value, (int, float)) for value in times):
            raise ValueError(f"animation timeline {timeline_index} contains a non-number")
        if any(right < left for left, right in zip(times, times[1:])):
            raise ValueError(f"animation timeline {timeline_index} is not sorted")
        if timeline_index not in timeline_accessors:
            timeline_accessors[timeline_index] = add_accessor(
                f"timeline:{timeline_index}",
                [float(value) for value in times],
                "scalar",
                len(times),
                bounds=True,
            )

        value_type, component_count = layout
        rows = curve.get("values")
        if not isinstance(rows, list) or len(rows) != len(times):
            raise ValueError(f"animation curve {curve_index} value count does not match its timeline")
        if not all(
            isinstance(row, list)
            and len(row) == component_count
            and all(isinstance(value, (int, float)) for value in row)
            for row in rows
        ):
            raise ValueError(f"animation curve {curve_index} has invalid {value_type} values")
        output_accessor = add_accessor(
            f"curve:{curve_index}",
            [float(value) for row in rows for value in row],
            value_type,
            len(rows),
        )
        channels.append(
            {
                "targetId": target_id,
                "property": property_name,
                "inputAccessorId": timeline_accessors[timeline_index],
                "outputAccessorId": output_accessor,
                "interpolation": "linear",
            }
        )

    if unresolved_hashes:
        add_diagnostic(
            document,
            "warning",
            "ANIMATION_PATHS_UNRESOLVED",
            f"{len(unresolved_hashes)} animation path hashes do not match model nodes.",
            object_id=animation_id,
            details={"pathHashes": sorted(unresolved_hashes)},
        )
    if ambiguous_hashes:
        add_diagnostic(
            document,
            "warning",
            "ANIMATION_PATHS_AMBIGUOUS",
            f"{len(ambiguous_hashes)} animation path hashes match multiple model nodes.",
            object_id=animation_id,
            details={"pathHashes": sorted(ambiguous_hashes)},
        )
    if unsupported_float_count:
        add_diagnostic(
            document,
            "info",
            "ANIMATION_FLOAT_CURVES_UNSUPPORTED",
            f"{unsupported_float_count} float curves remain available only in the source animation.",
            object_id=animation_id,
        )
    if channels:
        document["animations"].append(
            {
                "id": animation_id,
                "name": str(clip.get("name") or ""),
                "duration": float(clip.get("duration") or 0.0),
                "channels": channels,
                "source": dict(source),
            }
        )
        _update_geometry_buffer(document, binary, buffer_uri)
    return bytes(binary)


def _index_node_path_hashes(document: Mapping[str, Any]) -> dict[int, set[str]]:
    nodes = {
        node.get("id"): node
        for node in document.get("nodes", [])
        if isinstance(node, Mapping) and isinstance(node.get("id"), str)
    }
    result: dict[int, set[str]] = {}
    root_ids = set(document.get("asset", {}).get("rootNodeIds", []))
    for node_id, node in nodes.items():
        names = []
        current = node
        seen = set()
        while current is not None and current.get("id") not in seen:
            seen.add(current.get("id"))
            names.append(str(current.get("name") or ""))
            current = nodes.get(current.get("parentId"))
        names.reverse()
        for start in range(len(names)):
            path = "/".join(names[start:])
            # Match .NET Encoding.ASCII used by AnimeStudio: unsupported
            # characters become "?" before Unity's CRC32 is calculated.
            path_bytes = path.encode("ascii", errors="replace")
            path_hash = zlib.crc32(path_bytes) & 0xFFFFFFFF
            result.setdefault(path_hash, set()).add(node_id)
        if node_id in root_ids:
            result.setdefault(0, set()).add(node_id)
    return result


def _update_geometry_buffer(
    document: dict[str, Any],
    binary: bytearray,
    buffer_uri: str | None,
) -> None:
    buffer = next(
        (item for item in document["buffers"] if item.get("id") == GEOMETRY_BUFFER_ID),
        None,
    )
    if buffer is None:
        if buffer_uri is None:
            raise ValueError("buffer_uri is required when the model has no geometry buffer")
        buffer = {"id": GEOMETRY_BUFFER_ID, "uri": buffer_uri}
        document["buffers"].append(buffer)
    buffer["byteLength"] = len(binary)
    buffer["sha256"] = hashlib.sha256(binary).hexdigest()
