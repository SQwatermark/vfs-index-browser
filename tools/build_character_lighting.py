"""Build a portable Endfield character-lighting document from cubemap faces.

Example:
    python tools/build_character_lighting.py \
      path/to/cubemap path/to/character-lighting.json

The input directory must contain one ``*_<Face>.png`` or ``<Face>.png`` file
for each direction from ``PositiveX`` through ``NegativeZ``. Paths stored in
the JSON document are relative to the output document.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from character_lighting import CUBEMAP_FACE_NAMES, cubemap_to_equirectangular


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cubemap_directory", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--base-intensity", type=float, default=1.0)
    parser.add_argument("--azimuth", type=float, default=180.0)
    parser.add_argument("--elevation", type=float, default=0.0)
    parser.add_argument("--directional-intensity", type=float, default=0.6)
    parser.add_argument("--directional-parameter", type=float, default=0.15)
    parser.add_argument("--profile-path", default="")
    parser.add_argument("--cubemap-path", default="")
    return parser.parse_args()


def relative_path(path: Path, base: Path) -> str:
    return Path(os.path.relpath(path.resolve(), base.resolve())).as_posix()


def find_face_paths(directory: Path) -> dict[str, Path]:
    result = {}
    for name in CUBEMAP_FACE_NAMES:
        matches = sorted(
            {
                *directory.glob(f"{name}.png"),
                *directory.glob(f"*_{name}.png"),
            }
        )
        if len(matches) != 1:
            raise ValueError(
                f"expected exactly one {name} PNG in {directory}, found {len(matches)}"
            )
        result[name] = matches[0]
    return result


def main() -> None:
    args = parse_args()
    cubemap_directory = args.cubemap_directory.resolve()
    output_path = args.output.resolve()
    face_paths = find_face_paths(cubemap_directory)

    panorama_path = output_path.with_name(f"{output_path.stem}-equirect.png")
    cubemap_to_equirectangular(face_paths, panorama_path)
    document_directory = output_path.parent
    document = {
        "version": 1,
        "source": {
            "profilePath": args.profile_path,
            "cubemapPath": args.cubemap_path,
        },
        "ambient": {
            "baseIntensity": args.base_intensity,
            "customDirectionDegrees": [args.azimuth, args.elevation],
            "directionalIntensity": args.directional_intensity,
            "directionalParameter": args.directional_parameter,
        },
        "cubemap": {
            "encoding": "ldr-png",
            "faces": {
                name: relative_path(path, document_directory)
                for name, path in face_paths.items()
            },
            "equirectangularPath": relative_path(panorama_path, document_directory),
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {output_path}")
    print(f"Wrote {panorama_path}")


if __name__ == "__main__":
    main()
