#!/usr/bin/env python3
"""Build a conservative type-reference graph from an IL2CPP type index."""

from __future__ import annotations

import argparse
import json
import re
from collections import deque
from pathlib import Path


FORMAT = "Il2CppTypeReferenceGraph"
VERSION = 1
QUALIFIED_TYPE_RE = re.compile(
    r"(?:[A-Za-z_][A-Za-z0-9_]*\.)+[A-Za-z_][A-Za-z0-9_]*(?:`\d+)?"
)


def _member_signatures(type_info: dict):
    for kind in ("fields", "properties", "methods"):
        for member in type_info[kind]:
            yield kind[:-1], member["signature"]


def build_graph(index: dict) -> dict:
    known_types = {type_info["qualifiedName"] for type_info in index["types"]}
    nodes = []
    edges = []

    for type_info in index["types"]:
        source = type_info["qualifiedName"]
        nodes.append(
            {
                "id": source,
                "token": type_info["token"],
                "size": type_info["size"],
            }
        )
        seen_edges = set()
        for member_kind, signature in _member_signatures(type_info):
            for target in QUALIFIED_TYPE_RE.findall(signature):
                if target == source or target not in known_types:
                    continue
                identity = (target, member_kind, signature)
                if identity in seen_edges:
                    continue
                seen_edges.add(identity)
                edges.append(
                    {
                        "source": source,
                        "target": target,
                        "memberKind": member_kind,
                        "member": signature,
                    }
                )

    return {
        "format": FORMAT,
        "version": VERSION,
        "source": index["source"],
        "nodeCount": len(nodes),
        "edgeCount": len(edges),
        "nodes": nodes,
        "edges": edges,
    }


def select_subgraph(graph: dict, roots: list[str], max_depth: int) -> dict:
    if not roots:
        return graph

    node_ids = {node["id"] for node in graph["nodes"]}
    missing = sorted(set(roots) - node_ids)
    if missing:
        raise ValueError(f"unknown root types: {', '.join(missing)}")

    adjacency: dict[str, set[str]] = {}
    for edge in graph["edges"]:
        adjacency.setdefault(edge["source"], set()).add(edge["target"])

    selected = set(roots)
    queue = deque((root, 0) for root in roots)
    while queue:
        source, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for target in adjacency.get(source, ()):
            if target in selected:
                continue
            selected.add(target)
            queue.append((target, depth + 1))

    nodes = [node for node in graph["nodes"] if node["id"] in selected]
    edges = [
        edge
        for edge in graph["edges"]
        if edge["source"] in selected and edge["target"] in selected
    ]
    return {
        **graph,
        "roots": roots,
        "maxDepth": max_depth,
        "nodeCount": len(nodes),
        "edgeCount": len(edges),
        "nodes": nodes,
        "edges": edges,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Il2CppTypeIndex JSON")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root", action="append", default=[], help="Root type")
    parser.add_argument("--max-depth", type=int, default=2)
    args = parser.parse_args()

    if args.max_depth < 0:
        raise SystemExit("--max-depth must be non-negative")
    index = json.loads(args.input.read_text(encoding="utf-8"))
    if index.get("format") != "Il2CppTypeIndex":
        raise SystemExit(f"unsupported input format: {index.get('format')!r}")

    try:
        graph = select_subgraph(build_graph(index), args.root, args.max_depth)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(graph, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"wrote {graph['nodeCount']} nodes and {graph['edgeCount']} edges "
        f"to {args.output}"
    )


if __name__ == "__main__":
    main()

