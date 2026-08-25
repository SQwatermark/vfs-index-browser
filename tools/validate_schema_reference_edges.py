#!/usr/bin/env python3
"""校验人工取证的跨 schema ID 引用边是否指向真实来源字段。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def validate_reference_graph(
    ownership: dict[str, Any], graph: dict[str, Any]
) -> dict[str, int]:
    if ownership.get("format") != "SparkBufferSchemaOwnership":
        raise ValueError("ownership: unsupported format")
    if graph.get("format") != "GameDataReferenceGraph" or graph.get("version") != 1:
        raise ValueError("graph: expected GameDataReferenceGraph version 1")
    if graph.get("revision") != ownership.get("revision"):
        raise ValueError("graph: revision does not match ownership report")

    types = {item["typeHashHex"].upper(): item for item in ownership.get("types", [])}
    node_ids = [item.get("id") for item in graph.get("nodes", [])]
    if any(not isinstance(value, str) or not value for value in node_ids):
        raise ValueError("graph.nodes: every node requires a non-empty id")
    if len(set(node_ids)) != len(node_ids):
        raise ValueError("graph.nodes: duplicate node id")

    seen_edges: set[tuple[str, str, str]] = set()
    for index, edge in enumerate(graph.get("edges", [])):
        path = f"graph.edges[{index}]"
        source = require_object(edge.get("source"), f"{path}.source")
        type_hash = require_string(source.get("typeHashHex"), f"{path}.source.typeHashHex").upper()
        field_name = require_string(source.get("field"), f"{path}.source.field")
        source_type = types.get(type_hash)
        if source_type is None:
            raise ValueError(f"{path}.source: unknown type hash {type_hash}")
        if source_type.get("kind") != "bean":
            raise ValueError(f"{path}.source: {type_hash} is not a bean")
        fields = source_type.get("definition", {}).get("fields", [])
        if not any(field.get("name") == field_name for field in fields):
            raise ValueError(f"{path}.source: {source_type['name']} has no field {field_name!r}")

        target = require_string(edge.get("target"), f"{path}.target")
        if target not in node_ids:
            raise ValueError(f"{path}.target: unknown node {target!r}")
        owners = edge.get("owners")
        if not isinstance(owners, list) or not owners or any(not isinstance(v, str) for v in owners):
            raise ValueError(f"{path}.owners: expected non-empty string array")
        unknown_owners = set(owners) - set(source_type.get("owners", []))
        if unknown_owners:
            raise ValueError(f"{path}.owners: not reachable from source type: {sorted(unknown_owners)}")

        evidence = edge.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(f"{path}.evidence: at least one explicit evidence item is required")
        for evidence_index, item in enumerate(evidence):
            item_path = f"{path}.evidence[{evidence_index}]"
            record = require_object(item, item_path)
            basis = require_string(record.get("basis"), f"{item_path}.basis")
            if basis not in {"native-code", "native-schema", "artifact-join"}:
                raise ValueError(f"{item_path}.basis: unsupported evidence class {basis!r}")
            require_string(record.get("reference"), f"{item_path}.reference")

        identity = (type_hash, field_name, target)
        if identity in seen_edges:
            raise ValueError(f"{path}: duplicate edge {identity}")
        seen_edges.add(identity)

    return {
        "nodeCount": len(node_ids),
        "edgeCount": len(seen_edges),
        "sourceTypeCount": len({type_hash for type_hash, _, _ in seen_edges}),
    }


def require_object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected object")
    return value


def require_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{path}: expected non-empty string")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ownership", type=Path)
    parser.add_argument("graph", type=Path)
    args = parser.parse_args()
    try:
        ownership = json.loads(args.ownership.read_text(encoding="utf-8"))
        graph = json.loads(args.graph.read_text(encoding="utf-8"))
        summary = validate_reference_graph(ownership, graph)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
