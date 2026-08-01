"""Compare Endfield's dedicated root-motion ACL buffer with Humanoid curves."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


ROOT_TRACK_NAMES = (
    "MotionT.x", "MotionT.y", "MotionT.z",
    "MotionQ.x", "MotionQ.y", "MotionQ.z", "MotionQ.w",
    "RootT.x", "RootT.y", "RootT.z",
    "RootQ.x", "RootQ.y", "RootQ.z", "RootQ.w",
    "LeftFootT.x", "LeftFootT.y", "LeftFootT.z",
    "LeftFootQ.x", "LeftFootQ.y", "LeftFootQ.z", "LeftFootQ.w",
    "RightFootT.x", "RightFootT.y", "RightFootT.z",
    "RightFootQ.x", "RightFootQ.y", "RightFootQ.z", "RightFootQ.w",
)


def rms(values: list[float]) -> float:
    return math.sqrt(sum(value * value for value in values) / len(values))


def analyze(path: Path) -> None:
    clip = json.loads(path.read_text(encoding="utf-8"))
    root = clip.get("rootMotion")
    if not isinstance(root, dict):
        raise ValueError(f"{path}: rootMotion is missing")
    track_count = int(root["trackCount"])
    times = [float(value) for value in root["times"]]
    flat_values = [float(value) for value in root["values"]]
    if track_count != len(ROOT_TRACK_NAMES):
        raise ValueError(f"{path}: expected 28 root tracks, got {track_count}")
    if len(flat_values) != len(times) * track_count:
        raise ValueError(f"{path}: root-motion value count does not match its shape")

    timelines = clip["timelines"]
    float_curves = {
        curve.get("propertyName"): curve
        for curve in clip["curves"]
        if curve.get("property") == "float" and curve.get("classId") == 95
    }

    print(f"{path.name}: {clip['name']}")
    print(
        f"  root={track_count} tracks x {len(times)} frames, "
        f"range={times[0]:.6g}..{times[-1]:.6g}s"
    )
    for track_index, name in enumerate(ROOT_TRACK_NAMES):
        root_values = flat_values[track_index::track_count]
        curve = float_curves.get(name)
        if curve is None:
            print(f"  {track_index:02d} {name:<14} root only")
            continue
        curve_times = timelines[curve["timeline"]]
        curve_by_time = {
            round(float(time), 6): float(value)
            for time, value in zip(curve_times, curve["values"])
        }
        pairs = [
            (root_value, curve_by_time[round(time, 6)])
            for time, root_value in zip(times, root_values)
            if round(time, 6) in curve_by_time
        ]
        if not pairs:
            print(f"  {track_index:02d} {name:<14} no shared samples")
            continue
        differences = [left - right for left, right in pairs]
        print(
            f"  {track_index:02d} {name:<14} shared={len(pairs):4d} "
            f"root=[{min(root_values): .5g},{max(root_values): .5g}] "
            f"max|diff|={max(abs(value) for value in differences):.5g} "
            f"rms={rms(differences):.5g}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("clips", nargs="+", type=Path)
    args = parser.parse_args()
    for path in args.clips:
        analyze(path)


if __name__ == "__main__":
    main()
