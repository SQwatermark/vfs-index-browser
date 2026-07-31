"""Generate a self-contained Blender file from a cached Endfield character GLB.

This launcher deliberately reuses ``tools/blender_import_model.py`` so the
material reconstruction logic remains in one place. Run it with normal Python;
it starts Blender in background mode and validates the generated file.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[1]
BLENDER_IMPORTER = PROJECT_ROOT / "tools" / "blender_import_model.py"
VALIDATOR = HERE / "validate_embedded_blend.py"
WINDOWS_BLENDER = Path(
    r"C:\Program Files\Blender Foundation\Blender 4.3\blender.exe"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Cached character GLB")
    parser.add_argument("output", type=Path, help="Generated .blend file")
    parser.add_argument(
        "--blender",
        type=Path,
        help="Blender executable; defaults to BLENDER_EXE, PATH, or Blender 4.3",
    )
    parser.add_argument("--render", type=Path, help="Optional preview PNG")
    parser.add_argument("--lighting", type=Path, help="Optional character-lighting JSON")
    parser.add_argument(
        "--framing",
        choices=("full", "portrait"),
        default="full",
        help="Preview camera framing",
    )
    parser.add_argument(
        "--main-light-direction",
        type=float,
        nargs=3,
        metavar=("X", "Y", "Z"),
        default=(0.0, -1.0, 0.0),
        help=(
            "Blender-space direction from the character toward the preview key "
            "light; independent from CharacterVolume ambient lighting"
        ),
    )
    parser.add_argument("--outline", action="store_true", help="Enable diagnostic outline")
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Do not reopen and inspect the generated Blender file",
    )
    return parser.parse_args()


def find_blender(explicit: Path | None) -> Path:
    candidates = [
        explicit,
        Path(os.environ["BLENDER_EXE"]) if os.environ.get("BLENDER_EXE") else None,
        Path(found) if (found := shutil.which("blender")) else None,
        WINDOWS_BLENDER,
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError("Blender executable not found; pass --blender or set BLENDER_EXE")


def run(command: list[str]) -> None:
    print("+", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def main() -> None:
    args = parse_args()
    blender = find_blender(args.blender)
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    if input_path.suffix.lower() not in {".glb", ".gltf"} or not input_path.is_file():
        raise FileNotFoundError(f"Character GLB does not exist: {input_path}")
    if output_path.suffix.lower() != ".blend":
        raise ValueError(f"Output must use the .blend extension: {output_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(blender),
        "--background",
        "--factory-startup",
        "--python",
        str(BLENDER_IMPORTER),
        "--",
        str(input_path),
        str(output_path),
        "--framing",
        args.framing,
        "--main-light-direction",
        *(str(component) for component in args.main_light_direction),
    ]
    if args.render:
        command.extend(("--render", str(args.render.resolve())))
    if args.lighting:
        command.extend(("--lighting", str(args.lighting.resolve())))
    if args.outline:
        command.append("--outline")
    run(command)

    if not args.skip_validation:
        run(
            [
                str(blender),
                "--background",
                str(output_path),
                "--python-exit-code",
                "1",
                "--python",
                str(VALIDATOR),
            ]
        )
    print(f"Generated self-contained Blender character: {output_path}")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
