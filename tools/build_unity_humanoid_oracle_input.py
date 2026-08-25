#!/usr/bin/env python3
"""Build the minimal JSON consumed by the Unity Humanoid bake oracle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _vector(value: dict, names: str) -> list[float]:
    return [float(value[name]) for name in names]


def build_input(model: dict, avatar: dict, animation: dict) -> dict:
    document = model.get("document", model)
    description = avatar["m_HumanDescription"]
    timelines = animation["timelines"]
    if len(timelines) != 1:
        raise ValueError(f"expected one animation timeline, found {len(timelines)}")

    nodes = []
    for node in document["nodes"]:
        transform = node.get("transform", {})
        nodes.append(
            {
                "id": node["id"],
                "name": node["name"],
                "parentId": node.get("parentId", ""),
                "translation": [float(value) for value in transform.get("translation", [0, 0, 0])],
                "rotation": [float(value) for value in transform.get("rotation", [0, 0, 0, 1])],
                "scale": [float(value) for value in transform.get("scale", [1, 1, 1])],
            }
        )

    human_bones = []
    for item in description["m_Human"]:
        limit = item["m_Limit"]
        human_bones.append(
            {
                "boneName": item["m_BoneName"],
                "humanName": item["m_HumanName"],
                "useDefaultValues": not bool(limit.get("m_Modified")),
                "min": _vector(limit["m_Min"], "XYZ"),
                "max": _vector(limit["m_Max"], "XYZ"),
                "center": _vector(limit["m_Value"], "XYZ"),
                "axisLength": float(limit["m_Length"]),
            }
        )

    muscles = [
        {
            "name": curve["propertyName"],
            "values": [float(value) for value in curve["values"]],
        }
        for curve in animation["curves"]
        if curve.get("property") == "float"
        and curve.get("classId") == 95
        and curve.get("timeline") == 0
    ]
    return {
        "name": animation["name"],
        "sampleRate": float(animation["sampleRate"]),
        "times": [float(value) for value in timelines[0]],
        "nodes": nodes,
        "humanBones": human_bones,
        "muscles": muscles,
        "avatar": {
            "armTwist": float(description["m_ArmTwist"]),
            "foreArmTwist": float(description["m_ForeArmTwist"]),
            "upperLegTwist": float(description["m_UpperLegTwist"]),
            "legTwist": float(description["m_LegTwist"]),
            "armStretch": float(description["m_ArmStretch"]),
            "legStretch": float(description["m_LegStretch"]),
            "feetSpacing": float(description["m_FeetSpacing"]),
            "hasTranslationDoF": bool(description["m_HasTranslationDoF"]),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True, help="EndfieldModelDocument JSON")
    parser.add_argument("--avatar", type=Path, required=True, help="AnimeStudio Avatar JSON")
    parser.add_argument("--animation", type=Path, required=True, help="AnimeStudio compact animation JSON")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    result = build_input(
        _read_json(args.model),
        _read_json(args.avatar),
        _read_json(args.animation),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print(
        f"wrote {args.output}: {len(result['nodes'])} nodes, "
        f"{len(result['humanBones'])} human bones, {len(result['muscles'])} float curves"
    )


if __name__ == "__main__":
    main()
