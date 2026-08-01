"""Compare production and Unity-oracle Humanoid poses in model space."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def quaternion_matrix(value: list[float]) -> np.ndarray:
    x, y, z, w = value
    norm = x * x + y * y + z * z + w * w
    if norm == 0:
        return np.identity(3)
    scale = 2.0 / norm
    return np.array(
        [
            [1 - scale * (y * y + z * z), scale * (x * y - z * w), scale * (x * z + y * w)],
            [scale * (x * y + z * w), 1 - scale * (x * x + z * z), scale * (y * z - x * w)],
            [scale * (x * z - y * w), scale * (y * z + x * w), 1 - scale * (x * x + y * y)],
        ],
        dtype=float,
    )


def transform_matrix(transform: dict) -> np.ndarray:
    matrix = np.identity(4)
    matrix[:3, :3] = quaternion_matrix(transform["rotation"]) @ np.diag(transform["scale"])
    matrix[:3, 3] = transform["translation"]
    return matrix


def rotation_error(left: np.ndarray, right: np.ndarray) -> float:
    delta = left[:3, :3] @ np.linalg.inv(right[:3, :3])
    cosine = max(-1.0, min(1.0, (np.trace(delta) - 1.0) / 2.0))
    return math.degrees(math.acos(cosine))


def world_matrices(nodes: list[dict], local: dict[str, dict]) -> dict[str, np.ndarray]:
    by_id = {node["id"]: node for node in nodes}
    result: dict[str, np.ndarray] = {}

    def visit(node_id: str) -> np.ndarray:
        if node_id in result:
            return result[node_id]
        node = by_id[node_id]
        matrix = transform_matrix(local[node_id])
        parent_id = node.get("parentId")
        if parent_id in by_id:
            matrix = visit(parent_id) @ matrix
        result[node_id] = matrix
        return matrix

    for node_id in by_id:
        visit(node_id)
    return result


def nearest_index(times: list[float], target: float) -> int:
    return min(range(len(times)), key=lambda index: abs(float(times[index]) - target))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("model", type=Path)
    parser.add_argument("animation", type=Path)
    parser.add_argument("oracle", type=Path)
    parser.add_argument("--time", type=float, default=1.0)
    args = parser.parse_args()

    model_payload = load(args.model)
    document = model_payload.get("document", model_payload)
    animation = load(args.animation)
    oracle = load(args.oracle)
    nodes = document["nodes"]
    node_by_id = {node["id"]: node for node in nodes}

    production_local = {
        node["id"]: {
            "translation": list(node["transform"]["translation"]),
            "rotation": list(node["transform"]["rotation"]),
            "scale": list(node["transform"]["scale"]),
        }
        for node in nodes
    }
    for track in animation["tracks"]:
        target_id = track["targetId"]
        if target_id not in production_local:
            continue
        timeline = animation["timelines"][track["timeline"]]
        index = nearest_index(timeline, args.time)
        production_local[target_id][track["property"]] = list(track["values"][index])

    oracle_index = nearest_index(oracle["times"], args.time)
    oracle_local = {
        track["id"]: {
            "translation": [
                track["translations"][oracle_index][axis] for axis in ("x", "y", "z")
            ],
            "rotation": [
                track["rotations"][oracle_index][axis] for axis in ("x", "y", "z", "w")
            ],
            "scale": [track["scales"][oracle_index][axis] for axis in ("x", "y", "z")],
        }
        for track in oracle["tracks"]
        if track["id"] in node_by_id
    }
    for node_id, transform in production_local.items():
        oracle_local.setdefault(node_id, transform)

    production_world = world_matrices(nodes, production_local)
    oracle_world = world_matrices(nodes, oracle_local)
    reference = next(node for node in nodes if node["name"] == "Bip001")
    production_reference_inverse = np.linalg.inv(production_world[reference["id"]])
    oracle_reference_inverse = np.linalg.inv(oracle_world[reference["id"]])

    humanoid_ids = {
        bone["id"]
        for skeleton in document.get("skeletons", [])
        for bone in skeleton.get("bones", [])
        if bone.get("extras", {}).get("humanoid")
    }
    print(
        f"time={args.time:g}s, production sample and oracle sample use nearest stored frame; "
        f"reference={reference['name']}"
    )
    for node in nodes:
        node_id = node["id"]
        if node_id not in humanoid_ids:
            continue
        production_relative = production_reference_inverse @ production_world[node_id]
        oracle_relative = oracle_reference_inverse @ oracle_world[node_id]
        position_error = np.linalg.norm(production_relative[:3, 3] - oracle_relative[:3, 3])
        angle_error = rotation_error(production_relative, oracle_relative)
        print(f"  {node['name']:<24} position={position_error:.7f} rotation={angle_error:.5f} deg")


if __name__ == "__main__":
    main()
