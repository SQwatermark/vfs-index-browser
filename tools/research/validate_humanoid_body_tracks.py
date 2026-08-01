#!/usr/bin/env python3
"""Compare recovered Humanoid Hips tracks with a Unity oracle export."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from animestudio_humanoid import (  # noqa: E402
    annotate_humanoid_bones,
    bake_humanoid_body_tracks,
    bake_humanoid_rotation_tracks,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path)
    parser.add_argument("avatar", type=Path)
    parser.add_argument("animation", type=Path)
    parser.add_argument("oracle", type=Path)
    args = parser.parse_args()

    model_wrapper = _load_json(args.model)
    document = model_wrapper.get("document", model_wrapper)
    avatar_wrapper = _load_json(args.avatar)
    avatar = avatar_wrapper.get("payload", avatar_wrapper)
    clip = _load_json(args.animation)
    oracle = _load_json(args.oracle)

    attached = annotate_humanoid_bones(document, [avatar])
    if not attached:
        raise RuntimeError("Avatar did not match the model skeleton")
    timelines = [[float(value) for value in row] for row in clip["timelines"]]
    float_curves = [curve for curve in clip["curves"] if curve.get("property") == "float"]
    rotations, _ = bake_humanoid_rotation_tracks(document, float_curves, timelines)
    body_tracks, _ = bake_humanoid_body_tracks(
        document,
        float_curves,
        timelines,
        rotations,
    )
    by_property = {track["property"]: track for track in body_tracks}
    if set(by_property) != {"translation", "rotation"}:
        raise RuntimeError("Body curves did not produce both Hips tracks")

    hips = next(track for track in oracle["tracks"] if track.get("name") == "Bip001")
    position_errors = [
        math.dist(actual, expected)
        for actual, expected in zip(
            by_property["translation"]["values"],
            (_components(value, "xyz") for value in hips["translations"]),
        )
    ]
    rotation_errors = [
        _quaternion_error_degrees(actual, expected)
        for actual, expected in zip(
            by_property["rotation"]["values"],
            (_components(value, "xyzw") for value in hips["rotations"]),
        )
    ]
    print(f"matched bones: {attached}")
    _print_stats("position error", position_errors, "m")
    _print_stats("rotation error", rotation_errors, "deg")
    return 0


def _load_json(path: Path):
    with path.open("r", encoding="utf-8") as source:
        return json.load(source)


def _quaternion_error_degrees(left, right):
    dot = abs(sum(a * b for a, b in zip(left, right)))
    return math.degrees(2.0 * math.acos(max(-1.0, min(1.0, dot))))


def _components(value, fields):
    if isinstance(value, dict):
        return [float(value[field]) for field in fields]
    return [float(component) for component in value]


def _print_stats(label, values, unit):
    ordered = sorted(values)
    median = ordered[len(ordered) // 2]
    p95 = ordered[min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)]
    print(f"{label}: median={median:.9g}{unit}, p95={p95:.9g}{unit}, max={max(values):.9g}{unit}")


if __name__ == "__main__":
    raise SystemExit(main())
