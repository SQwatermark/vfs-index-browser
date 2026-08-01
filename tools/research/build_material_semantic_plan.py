"""Build binding resolution, semantic IR, and Blender parameter plan JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.material_binding_resolver import (  # noqa: E402
    ShaderSource,
    resolve_material_bindings,
)
from experiments.material_semantic_ir.character_npr_mapping import (  # noqa: E402
    VariantIdentity,
    build_blender_parameter_plan,
    build_character_npr_silk_ir,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shader", type=Path)
    parser.add_argument("material", type=Path, help="sourceMaterial JSON")
    parser.add_argument("--archive-version", required=True)
    parser.add_argument("--shader-uri")
    parser.add_argument("--material-id", required=True)
    parser.add_argument("--pass-name", required=True)
    parser.add_argument("--blob", type=int, required=True)
    parser.add_argument("--hlsl-uri", required=True)
    parser.add_argument("--material-keyword", action="append", default=[])
    parser.add_argument("--runtime-keyword", action="append", default=[])
    parser.add_argument("--disabled-runtime-keyword", action="append", default=[])
    parser.add_argument("--texture-metadata", type=Path)
    parser.add_argument("--texture-rules", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def load_json(path: Path | None) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path else {}


def main() -> int:
    args = parse_args()
    resolution = resolve_material_bindings(
        args.shader.read_text(encoding="utf-8"),
        ShaderSource(
            archive_version=args.archive_version,
            uri=args.shader_uri or args.shader.as_posix(),
        ),
        load_json(args.material),
        texture_metadata=load_json(args.texture_metadata),
        texture_rules=load_json(args.texture_rules),
    )
    semantic_ir = build_character_npr_silk_ir(
        resolution,
        material_id=args.material_id,
        variant=VariantIdentity(
            pass_name=args.pass_name,
            blob=args.blob,
            hlsl_uri=args.hlsl_uri,
            material_keywords=tuple(args.material_keyword),
            runtime_enabled_keywords=tuple(args.runtime_keyword),
            runtime_disabled_keywords=tuple(args.disabled_runtime_keyword),
        ),
    )
    result = {
        "bindingResolution": resolution,
        "semanticIr": semantic_ir,
        "blenderParameterPlan": build_blender_parameter_plan(semantic_ir),
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
