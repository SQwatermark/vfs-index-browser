"""Verify Unity Humanoid Body channels and quantify a direct-Hips approximation."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def vector(value: dict, fields: str) -> list[float]:
    return [float(value[field]) for field in fields]


def subtract(left: list[float], right: list[float]) -> list[float]:
    return [a - b for a, b in zip(left, right)]


def multiply(left: list[float], right: list[float]) -> list[float]:
    x1, y1, z1, w1 = left
    x2, y2, z2, w2 = right
    return [
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
    ]


def inverse(value: list[float]) -> list[float]:
    length_squared = sum(component * component for component in value)
    return [
        -value[0] / length_squared,
        -value[1] / length_squared,
        -value[2] / length_squared,
        value[3] / length_squared,
    ]


def rotate(rotation: list[float], value: list[float]) -> list[float]:
    return multiply(multiply(rotation, [*value, 0.0]), inverse(rotation))[:3]


def quaternion_error(left: list[float], right: list[float]) -> float:
    left_length = math.sqrt(sum(value * value for value in left))
    right_length = math.sqrt(sum(value * value for value in right))
    dot = sum(a * b for a, b in zip(left, right)) / (left_length * right_length)
    return 2.0 * math.degrees(math.acos(min(1.0, abs(dot))))


def distance(value: list[float]) -> float:
    return math.sqrt(sum(component * component for component in value))


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def curve_rows(animation: dict, prefix: str, fields: str) -> list[list[float]]:
    curves = {
        curve["propertyName"]: curve["values"]
        for curve in animation["curves"]
        if isinstance(curve.get("propertyName"), str)
    }
    return [
        [float(curves[f"{prefix}.{field}"][frame]) for field in fields]
        for frame in range(len(curves[f"{prefix}.{fields[0]}"]))
    ]


def print_summary(label: str, values: list[float], unit: str) -> None:
    print(
        f"{label}: median={statistics.median(values):.9g}{unit}, "
        f"p95={percentile(values, 0.95):.9g}{unit}, max={max(values):.9g}{unit}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("animation", type=Path, help="AnimeStudio compact animation JSON")
    parser.add_argument("oracle", type=Path, help="Unity Humanoid oracle output JSON")
    parser.add_argument("--avatar", type=Path, help="AnimeStudio Avatar JSON")
    parser.add_argument("--hips", default="Bip001", help="Hips track name in oracle output")
    args = parser.parse_args()

    animation = load(args.animation)
    oracle = load(args.oracle)
    motion_positions = curve_rows(animation, "MotionT", "xyz")
    motion_rotations = curve_rows(animation, "MotionQ", "xyzw")
    root_positions = curve_rows(animation, "RootT", "xyz")
    root_rotations = curve_rows(animation, "RootQ", "xyzw")

    reconstructed_positions = []
    reconstructed_rotations = []
    for motion_position, motion_rotation, root_position, root_rotation in zip(
        motion_positions,
        motion_rotations,
        root_positions,
        root_rotations,
    ):
        inverse_motion = inverse(motion_rotation)
        reconstructed_positions.append(
            rotate(inverse_motion, subtract(root_position, motion_position))
        )
        reconstructed_rotations.append(multiply(inverse_motion, root_rotation))

    body_positions = [vector(value, "xyz") for value in oracle["bodyPositions"]]
    body_rotations = [vector(value, "xyzw") for value in oracle["bodyRotations"]]
    position_errors = [
        distance(subtract(actual, expected))
        for actual, expected in zip(reconstructed_positions, body_positions)
    ]
    rotation_errors = [
        quaternion_error(actual, expected)
        for actual, expected in zip(reconstructed_rotations, body_rotations)
    ]
    print_summary("Body position reconstruction", position_errors, "")
    print_summary("Body rotation reconstruction", rotation_errors, " deg")

    hips = next(track for track in oracle["tracks"] if track["name"] == args.hips)
    hips_positions = [vector(value, "xyz") for value in hips["translations"]]
    hips_rotations = [vector(value, "xyzw") for value in hips["rotations"]]
    human_scale = 1.0
    if args.avatar:
        human_scale = float(load(args.avatar)["m_Avatar"]["m_Human"]["m_Scale"])

    body_origin_position = body_positions[0]
    body_origin_rotation = body_rotations[0]
    hips_origin_position = hips_positions[0]
    hips_origin_rotation = hips_rotations[0]
    approximation_position_errors = []
    approximation_rotation_errors = []
    for body_position, body_rotation, hips_position, hips_rotation in zip(
        body_positions,
        body_rotations,
        hips_positions,
        hips_rotations,
    ):
        body_delta_position = [
            value * human_scale
            for value in subtract(body_position, body_origin_position)
        ]
        hips_delta_position = subtract(hips_position, hips_origin_position)
        approximation_position_errors.append(
            distance(subtract(body_delta_position, hips_delta_position))
        )
        body_delta_rotation = multiply(body_rotation, inverse(body_origin_rotation))
        hips_delta_rotation = multiply(hips_rotation, inverse(hips_origin_rotation))
        approximation_rotation_errors.append(
            quaternion_error(body_delta_rotation, hips_delta_rotation)
        )

    print_summary(
        "Direct Body-delta as Hips translation",
        approximation_position_errors,
        "",
    )
    print_summary(
        "Direct Body-delta as Hips rotation",
        approximation_rotation_errors,
        " deg",
    )


if __name__ == "__main__":
    main()
