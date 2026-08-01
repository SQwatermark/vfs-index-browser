#!/usr/bin/env python3
"""Turn an existing Unity oracle input into controlled HumanPose probes."""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path


def _axis_angle(axis: tuple[float, float, float], degrees: float) -> list[float]:
    half_angle = math.radians(degrees) / 2
    scale = math.sin(half_angle)
    return [axis[0] * scale, axis[1] * scale, axis[2] * scale, math.cos(half_angle)]


def build_probe_input(source: dict, *, muscle_count: int = 95) -> dict:
    """Build independent Body and single-muscle perturbations around zero pose."""
    result = copy.deepcopy(source)
    result["name"] = f"{source.get('name', 'humanoid')}-pose-probes"
    result["times"] = []
    result["curves"] = []
    result["applyFootIK"] = False
    result["applyPlayableIK"] = False

    neutral_position = [0.0, 1.0, 0.0]
    identity_rotation = [0.0, 0.0, 0.0, 1.0]
    poses = [
        {
            "name": "neutral",
            "bodyPosition": neutral_position,
            "bodyRotation": identity_rotation,
            "hasMuscle": False,
        }
    ]
    for axis_index, axis_name in enumerate("xyz"):
        for sign in (-1, 1):
            position = neutral_position.copy()
            position[axis_index] += sign * 0.1
            poses.append(
                {
                    "name": f"body-position-{axis_name}-{sign:+d}",
                    "bodyPosition": position,
                    "bodyRotation": identity_rotation,
                    "hasMuscle": False,
                }
            )
    for axis, axis_name in zip(
        ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        "xyz",
    ):
        for degrees in (-15.0, 15.0):
            poses.append(
                {
                    "name": f"body-rotation-{axis_name}-{degrees:+g}",
                    "bodyPosition": neutral_position,
                    "bodyRotation": _axis_angle(axis, degrees),
                    "hasMuscle": False,
                }
            )
    for muscle_index in range(muscle_count):
        for value in (-0.25, 0.25):
            poses.append(
                {
                    "name": f"muscle-{muscle_index:02d}-{value:+g}",
                    "bodyPosition": neutral_position,
                    "bodyRotation": identity_rotation,
                    "hasMuscle": True,
                    "muscleIndex": muscle_index,
                    "muscleValue": value,
                }
            )
    result["syntheticPoses"] = poses
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="existing oracle input JSON")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--muscle-count",
        type=int,
        default=95,
        help="Unity HumanTrait muscle count; defaults to 95 for Unity 6",
    )
    args = parser.parse_args()

    source = json.loads(args.input.read_text(encoding="utf-8"))
    result = build_probe_input(source, muscle_count=args.muscle_count)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {args.output}: {len(result['syntheticPoses'])} synthetic poses")


if __name__ == "__main__":
    main()
