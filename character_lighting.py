"""Character-lighting configuration and cubemap projection helpers."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

CUBEMAP_FACE_NAMES = (
    "PositiveX",
    "NegativeX",
    "PositiveY",
    "NegativeY",
    "PositiveZ",
    "NegativeZ",
)


@dataclass(frozen=True)
class AmbientLighting:
    base_intensity: float
    azimuth_degrees: float
    elevation_degrees: float
    directional_intensity: float
    directional_parameter: float

    def npr_ramp_transform(self) -> tuple[float, float]:
        """Map NdotL from [-1, 1] into the current ambient preview range."""

        lit_value = self.base_intensity
        shadow_value = lit_value * (
            1.0 - self.directional_intensity * (1.0 - self.directional_parameter)
        )
        return (lit_value - shadow_value) * 0.5, (lit_value + shadow_value) * 0.5

    def blender_direction(self) -> tuple[float, float, float]:
        """Return the assumed Unity azimuth/elevation as a Blender direction.

        Endfield's exact shader-space convention is not yet recovered. The
        preview treats azimuth as rotation around Blender Z, starting at +Y,
        and elevation as the angle above the XY plane.
        """

        azimuth = math.radians(self.azimuth_degrees)
        elevation = math.radians(self.elevation_degrees)
        horizontal = math.cos(elevation)
        return (
            horizontal * math.sin(azimuth),
            horizontal * math.cos(azimuth),
            math.sin(elevation),
        )


@dataclass(frozen=True)
class CubemapLighting:
    encoding: str
    faces: Mapping[str, Path]
    equirectangular_path: Path | None


@dataclass(frozen=True)
class CharacterLighting:
    version: int
    source: Mapping[str, object]
    ambient: AmbientLighting
    cubemap: CubemapLighting


def _finite_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _resolve_path(base_directory: Path, value: object, field: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty path")
    path = Path(value)
    return path if path.is_absolute() else (base_directory / path).resolve()


def load_character_lighting(path: Path) -> CharacterLighting:
    """Load and strictly validate a versioned character-lighting document."""

    document_path = path.resolve()
    raw = json.loads(document_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("character lighting document must be an object")
    if raw.get("version") != 1:
        raise ValueError(f"unsupported character lighting version: {raw.get('version')!r}")

    source = raw.get("source", {})
    if not isinstance(source, dict):
        raise ValueError("source must be an object")

    ambient_raw = raw.get("ambient")
    if not isinstance(ambient_raw, dict):
        raise ValueError("ambient must be an object")
    direction = ambient_raw.get("customDirectionDegrees")
    if not isinstance(direction, list) or len(direction) != 2:
        raise ValueError(
            "ambient.customDirectionDegrees must contain azimuth and elevation"
        )
    ambient = AmbientLighting(
        base_intensity=_finite_number(
            ambient_raw.get("baseIntensity"), "ambient.baseIntensity"
        ),
        azimuth_degrees=_finite_number(
            direction[0], "ambient.customDirectionDegrees[0]"
        ),
        elevation_degrees=_finite_number(
            direction[1], "ambient.customDirectionDegrees[1]"
        ),
        directional_intensity=_finite_number(
            ambient_raw.get("directionalIntensity"), "ambient.directionalIntensity"
        ),
        directional_parameter=_finite_number(
            ambient_raw.get("directionalParameter"), "ambient.directionalParameter"
        ),
    )
    if ambient.base_intensity < 0:
        raise ValueError("ambient.baseIntensity must not be negative")
    if not 0 <= ambient.directional_intensity <= 1:
        raise ValueError("ambient.directionalIntensity must be between 0 and 1")
    if not 0 <= ambient.directional_parameter <= 1:
        raise ValueError("ambient.directionalParameter must be between 0 and 1")

    cubemap_raw = raw.get("cubemap")
    if not isinstance(cubemap_raw, dict):
        raise ValueError("cubemap must be an object")
    encoding = cubemap_raw.get("encoding")
    if encoding not in {"ldr-png", "hdr-exr"}:
        raise ValueError(f"unsupported cubemap encoding: {encoding!r}")
    faces_raw = cubemap_raw.get("faces")
    if not isinstance(faces_raw, dict):
        raise ValueError("cubemap.faces must be an object")
    unknown_faces = set(faces_raw) - set(CUBEMAP_FACE_NAMES)
    missing_faces = set(CUBEMAP_FACE_NAMES) - set(faces_raw)
    if unknown_faces or missing_faces:
        raise ValueError(
            "cubemap.faces must contain exactly "
            f"{', '.join(CUBEMAP_FACE_NAMES)}; "
            f"missing={sorted(missing_faces)}, unknown={sorted(unknown_faces)}"
        )
    base_directory = document_path.parent
    faces = {
        name: _resolve_path(base_directory, faces_raw[name], f"cubemap.faces.{name}")
        for name in CUBEMAP_FACE_NAMES
    }
    panorama_value = cubemap_raw.get("equirectangularPath")
    panorama_path = (
        _resolve_path(
            base_directory,
            panorama_value,
            "cubemap.equirectangularPath",
        )
        if panorama_value is not None
        else None
    )
    return CharacterLighting(
        version=1,
        source=source,
        ambient=ambient,
        cubemap=CubemapLighting(
            encoding=encoding,
            faces=faces,
            equirectangular_path=panorama_path,
        ),
    )


def _cubemap_sample(
    direction: tuple[float, float, float],
) -> tuple[str, float, float]:
    x, y, z = direction
    absolute_x, absolute_y, absolute_z = abs(x), abs(y), abs(z)
    if absolute_x >= absolute_y and absolute_x >= absolute_z:
        if x >= 0:
            return "PositiveX", -z / absolute_x, -y / absolute_x
        return "NegativeX", z / absolute_x, -y / absolute_x
    if absolute_y >= absolute_x and absolute_y >= absolute_z:
        if y >= 0:
            return "PositiveY", x / absolute_y, z / absolute_y
        return "NegativeY", x / absolute_y, -z / absolute_y
    if z >= 0:
        return "PositiveZ", x / absolute_z, -y / absolute_z
    return "NegativeZ", -x / absolute_z, -y / absolute_z


def cubemap_to_equirectangular(
    face_paths: Mapping[str, Path],
    output_path: Path,
    width: int | None = None,
) -> Path:
    """Project six Unity-order cubemap faces into an equirectangular image."""

    from PIL import Image

    if set(face_paths) != set(CUBEMAP_FACE_NAMES):
        raise ValueError("face_paths must contain exactly the six cubemap faces")
    images = {
        name: Image.open(face_paths[name]).convert("RGB") for name in CUBEMAP_FACE_NAMES
    }
    try:
        dimensions = {image.size for image in images.values()}
        if len(dimensions) != 1:
            raise ValueError(f"cubemap faces have different dimensions: {sorted(dimensions)}")
        face_width, face_height = next(iter(dimensions))
        if face_width != face_height:
            raise ValueError("cubemap faces must be square")
        panorama_width = width or face_width * 4
        if panorama_width <= 0 or panorama_width % 2:
            raise ValueError("equirectangular width must be a positive even number")
        panorama_height = panorama_width // 2
        panorama = Image.new("RGB", (panorama_width, panorama_height))
        pixels = panorama.load()
        source_pixels = {name: image.load() for name, image in images.items()}
        for py in range(panorama_height):
            latitude = math.pi * (0.5 - (py + 0.5) / panorama_height)
            cos_latitude = math.cos(latitude)
            y = math.sin(latitude)
            for px in range(panorama_width):
                longitude = 2 * math.pi * ((px + 0.5) / panorama_width - 0.5)
                direction = (
                    cos_latitude * math.sin(longitude),
                    y,
                    cos_latitude * math.cos(longitude),
                )
                face, coordinate_x, coordinate_y = _cubemap_sample(direction)
                source_x = min(
                    face_width - 1,
                    max(0, round((coordinate_x + 1) * 0.5 * (face_width - 1))),
                )
                source_y = min(
                    face_height - 1,
                    max(0, round((coordinate_y + 1) * 0.5 * (face_height - 1))),
                )
                pixels[px, py] = source_pixels[face][source_x, source_y]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        panorama.save(output_path)
        return output_path
    finally:
        for image in images.values():
            image.close()
