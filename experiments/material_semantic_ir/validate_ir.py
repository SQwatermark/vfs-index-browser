"""Validate cross-reference constraints that JSON Schema cannot express."""

from collections import Counter


class MaterialSemanticIrError(ValueError):
    """Raised when a MaterialSemanticIR document is structurally inconsistent."""


def validate_references(document):
    """Validate IDs, graph references, binding references, and graph acyclicity."""
    bindings = _index_unique(document["bindings"], "binding")
    nodes = _index_unique(document["graph"]["nodes"], "node")

    for node in nodes.values():
        for input_name, edge in node["inputs"].items():
            _validate_edge(edge, nodes, f"node {node['id']!r} input {input_name!r}")

        binding_id = node.get("parameters", {}).get("bindingId")
        if binding_id is not None and binding_id not in bindings:
            raise MaterialSemanticIrError(
                f"node {node['id']!r} references unknown binding {binding_id!r}"
            )
        if node["op"] == "input.binding":
            if binding_id is None:
                raise MaterialSemanticIrError(
                    f"input node {node['id']!r} has no bindingId"
                )
            outputs = node["outputs"]
            if set(outputs) != {"value"} or outputs["value"] != bindings[binding_id]["type"]:
                raise MaterialSemanticIrError(
                    f"input node {node['id']!r} type does not match binding {binding_id!r}"
                )
        if node["op"] == "texture.sample2d" and binding_id is not None:
            binding = bindings[binding_id]
            if binding["kind"] != "texture" or binding["type"]["kind"] != "texture2d":
                raise MaterialSemanticIrError(
                    f"texture node {node['id']!r} requires a texture2d binding"
                )

    for output_name, edge in document["graph"]["outputs"].items():
        _validate_edge(edge, nodes, f"graph output {output_name!r}")

    graph_outputs = set(document["graph"]["outputs"])
    for diagnostic in document.get("diagnostics", []):
        unknown = sorted(set(diagnostic["affectedOutputs"]) - graph_outputs)
        if unknown:
            raise MaterialSemanticIrError(
                f"diagnostic {diagnostic['code']!r} references unknown outputs: {unknown}"
            )

    _validate_acyclic(nodes)


def _index_unique(items, kind):
    counts = Counter(item["id"] for item in items)
    duplicates = sorted(item_id for item_id, count in counts.items() if count > 1)
    if duplicates:
        raise MaterialSemanticIrError(f"duplicate {kind} ids: {duplicates}")
    return {item["id"]: item for item in items}


def _validate_edge(edge, nodes, owner):
    node_id = edge["node"]
    output_name = edge["output"]
    if node_id not in nodes:
        raise MaterialSemanticIrError(f"{owner} references unknown node {node_id!r}")
    if output_name not in nodes[node_id]["outputs"]:
        raise MaterialSemanticIrError(
            f"{owner} references unknown output {node_id!r}.{output_name}"
        )


def _validate_acyclic(nodes):
    state = {}

    def visit(node_id, path):
        if state.get(node_id) == "done":
            return
        if state.get(node_id) == "visiting":
            cycle_start = path.index(node_id)
            cycle = path[cycle_start:] + [node_id]
            raise MaterialSemanticIrError(
                f"material graph contains a cycle: {' -> '.join(cycle)}"
            )

        state[node_id] = "visiting"
        dependencies = {
            edge["node"] for edge in nodes[node_id]["inputs"].values()
        }
        for dependency_id in sorted(dependencies):
            visit(dependency_id, path + [node_id])
        state[node_id] = "done"

    for node_id in sorted(nodes):
        visit(node_id, [])
