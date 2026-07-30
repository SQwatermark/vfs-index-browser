"""Select generated shader variants compatible with a ModelDocument material."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shader_variants import active_material_keywords, fragment_variants


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shader", type=Path, help="Generated .shader file")
    parser.add_argument("model_document", type=Path, help="ModelDocument JSON or API response")
    parser.add_argument("material", help="Material name")
    return parser.parse_args()


def load_document(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("document", payload)


def main() -> int:
    args = parse_args()
    shader_text = args.shader.read_text(encoding="utf-8")
    document = load_document(args.model_document)
    material = next(
        (item for item in document.get("materials", []) if item.get("name") == args.material),
        None,
    )
    if material is None:
        raise SystemExit(f"material not found: {args.material}")

    floats = material.get("properties", {}).get("floats", {})
    active, local = active_material_keywords(shader_text, floats)
    variants = fragment_variants(shader_text, active, local)
    print(
        json.dumps(
            {
                "material": args.material,
                "activeMaterialKeywords": sorted(active),
                "candidates": [
                    {
                        "include": variant.include,
                        "enabledRuntimeKeywords": list(variant.enabled_runtime_keywords),
                        "disabledRuntimeKeywords": list(variant.disabled_runtime_keywords),
                    }
                    for variant in variants
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
