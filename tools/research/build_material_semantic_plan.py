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
from shader_variants import (  # noqa: E402
    active_material_keywords,
    fragment_variants,
    select_preview_fragment_variant,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shader", type=Path)
    parser.add_argument("material", type=Path, help="sourceMaterial JSON")
    parser.add_argument("--archive-version", required=True)
    parser.add_argument("--shader-uri")
    parser.add_argument("--material-id", required=True)
    parser.add_argument("--pass-name")
    parser.add_argument("--blob", type=int)
    parser.add_argument("--hlsl-uri")
    parser.add_argument("--material-keyword", action="append")
    parser.add_argument("--runtime-keyword", action="append")
    parser.add_argument("--disabled-runtime-keyword", action="append")
    parser.add_argument("--texture-metadata", type=Path)
    parser.add_argument("--texture-rules", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def load_json(path: Path | None) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path else {}


def main() -> int:
    args = parse_args()
    shader_text = args.shader.read_text(encoding="utf-8")
    material = load_json(args.material)
    variant, material_keywords = resolve_variant(args, shader_text, material)
    resolution = resolve_material_bindings(
        shader_text,
        ShaderSource(
            archive_version=args.archive_version,
            uri=args.shader_uri or args.shader.as_posix(),
        ),
        material,
        texture_metadata=load_json(args.texture_metadata),
        texture_rules=load_json(args.texture_rules),
    )
    semantic_ir = build_character_npr_silk_ir(
        resolution,
        material_id=args.material_id,
        variant=VariantIdentity(
            pass_name=variant["passName"],
            blob=variant["blob"],
            hlsl_uri=variant["hlslUri"],
            material_keywords=tuple(material_keywords),
            runtime_enabled_keywords=tuple(variant["runtimeEnabledKeywords"]),
            runtime_disabled_keywords=tuple(variant["runtimeDisabledKeywords"]),
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


def resolve_variant(args, shader_text: str, material: dict) -> tuple[dict, list[str]]:
    manual_values = (args.pass_name, args.blob, args.hlsl_uri)
    if any(value is not None for value in manual_values):
        if not all(value is not None for value in manual_values):
            raise SystemExit("--pass-name, --blob and --hlsl-uri must be supplied together")
        return (
            {
                "passName": args.pass_name,
                "blob": args.blob,
                "hlslUri": args.hlsl_uri,
                "runtimeEnabledKeywords": args.runtime_keyword or [],
                "runtimeDisabledKeywords": args.disabled_runtime_keyword or [],
            },
            args.material_keyword or [],
        )

    if any(
        value is not None
        for value in (
            args.material_keyword,
            args.runtime_keyword,
            args.disabled_runtime_keyword,
        )
    ):
        raise SystemExit("keyword overrides require an explicit variant")

    active_keywords, local_keywords = active_material_keywords(
        shader_text,
        material.get("floats", {}),
    )
    selected = select_preview_fragment_variant(
        fragment_variants(shader_text, active_keywords, local_keywords)
    )
    return (
        {
            "passName": selected.pass_name,
            "blob": selected.blob,
            "hlslUri": (args.shader.parent / selected.include).as_posix(),
            "runtimeEnabledKeywords": list(selected.enabled_runtime_keywords),
            "runtimeDisabledKeywords": list(selected.disabled_runtime_keywords),
        },
        sorted(active_keywords),
    )


if __name__ == "__main__":
    raise SystemExit(main())
