"""Resolve a sourceMaterial JSON file against a ShaderLab archive file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from experiments.material_binding_resolver import (
    ShaderSource,
    resolve_material_bindings,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shader", type=Path)
    parser.add_argument("material", type=Path, help="sourceMaterial JSON")
    parser.add_argument("--archive-version", required=True)
    parser.add_argument("--shader-uri")
    parser.add_argument("--texture-metadata", type=Path)
    parser.add_argument("--texture-rules", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def load_optional(path: Path | None) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path else {}


def main() -> int:
    args = parse_args()
    shader_text = args.shader.read_text(encoding="utf-8")
    material = json.loads(args.material.read_text(encoding="utf-8"))
    result = resolve_material_bindings(
        shader_text,
        ShaderSource(
            archive_version=args.archive_version,
            uri=args.shader_uri or args.shader.as_posix(),
        ),
        material,
        texture_metadata=load_optional(args.texture_metadata),
        texture_rules=load_optional(args.texture_rules),
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
