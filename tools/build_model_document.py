#!/usr/bin/env python3
"""Build a hierarchy-only ModelDocument from AnimeStudio JSON snapshots."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from animestudio_model import (
    UnityObjectId,
    attach_mesh_geometry,
    build_hierarchy_document,
    load_animestudio_objects,
)
from model_document import validate_model_document


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build an Endfield ModelDocument from AnimeStudio JSON exports."
    )
    parser.add_argument("input", type=Path, help="directory containing AnimeStudio JSON object exports")
    parser.add_argument("output", type=Path, help="model.json output path")
    parser.add_argument("--source-file", required=True, help="entry GameObject sourceFile from $animestudio")
    parser.add_argument("--path-id", required=True, type=int, help="entry GameObject pathId")
    parser.add_argument("--logical-path", required=True, help="manifest logical asset path")
    parser.add_argument("--bundle", required=True, help="manifest bundle name")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    objects = load_animestudio_objects(args.input)
    if not objects:
        print(f"error: no AnimeStudio JSON objects found below {args.input}", file=sys.stderr)
        return 2

    entry = UnityObjectId(args.source_file, args.path_id)
    try:
        document = build_hierarchy_document(
            objects,
            entry,
            logical_path=args.logical_path,
            bundle=args.bundle,
        )
        geometry = attach_mesh_geometry(document, objects)
    except (KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    errors = validate_model_document(document)
    if errors:
        print(json.dumps({"validationErrors": errors}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    geometry_path = args.output.with_name("geometry.bin")
    if geometry:
        geometry_path.write_bytes(geometry)
    elif geometry_path.exists():
        geometry_path.unlink()
    print(
        f"wrote {args.output}: {len(document['nodes'])} nodes, "
        f"{len(document['dependencies'])} dependencies, {len(document['diagnostics'])} diagnostics"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
