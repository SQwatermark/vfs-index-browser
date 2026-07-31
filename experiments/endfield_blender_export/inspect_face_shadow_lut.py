"""Evaluate a face Shadow LUT directly from an Endfield character GLB.

This is an independent CPU reference for the b225 flattened 32x32x32 lookup.
It intentionally uses the encoded PNG channel values as LUT coordinates:
BaseMap is sampled as sRGB into linear space, then b225 converts it back to
sRGB before addressing the LUT.
"""

from __future__ import annotations

import argparse
import io
import json
import struct
from pathlib import Path

import numpy as np
from PIL import Image


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Character GLB")
    parser.add_argument("output", type=Path, help="Output directory")
    return parser.parse_args()


def read_glb(path: Path) -> tuple[dict, bytes]:
    payload = path.read_bytes()
    if payload[:4] != b"glTF":
        raise ValueError(f"Not a binary glTF: {path}")
    json_length = struct.unpack_from("<I", payload, 12)[0]
    document = json.loads(payload[20 : 20 + json_length])
    binary_header = 20 + json_length
    binary_length = struct.unpack_from("<I", payload, binary_header)[0]
    binary = payload[binary_header + 8 : binary_header + 8 + binary_length]
    return document, binary


def embedded_image(document: dict, binary: bytes, texture_id: str) -> Image.Image:
    image = next(
        item
        for item in document["images"]
        if item.get("name") in {texture_id, f"{texture_id}:image"}
    )
    view = document["bufferViews"][image["bufferView"]]
    start = view.get("byteOffset", 0)
    data = binary[start : start + view["byteLength"]]
    return Image.open(io.BytesIO(data)).convert("RGBA")


def face_metadata(document: dict) -> dict:
    return next(
        material["extras"]["endfieldPreview"]
        for material in document["materials"]
        if material.get("name", "").endswith("_face_01")
    )


def apply_shadow_lut(
    base: Image.Image,
    lut: Image.Image,
    *,
    flip_lut_y: bool,
) -> Image.Image:
    if lut.size != (1024, 32):
        raise ValueError(f"Expected a 1024x32 Shadow LUT, got {lut.size}")
    base_rgb = np.asarray(base, dtype=np.float32)[..., :3] / 255.0
    lut_rgba = np.asarray(lut, dtype=np.float32) / 255.0

    red_index = np.floor(base_rgb[..., 0] * 31.0 + 0.5).astype(np.int32)
    green_index = np.floor(base_rgb[..., 1] * 31.0 + 0.5).astype(np.int32)
    if flip_lut_y:
        green_index = 31 - green_index
    blue_scaled = base_rgb[..., 2] * 31.0
    blue_slice = np.floor(blue_scaled).astype(np.int32)
    blue_fraction = (blue_scaled - blue_slice)[..., None]

    x0 = blue_slice * 32 + red_index
    x1 = np.minimum(x0 + 32, 1023)
    first = lut_rgba[green_index, x0]
    second = lut_rgba[green_index, x1]
    output = first * (1.0 - blue_fraction) + second * blue_fraction
    return Image.fromarray(
        np.clip(np.round(output * 255.0), 0, 255).astype(np.uint8),
        "RGBA",
    )


def main() -> None:
    args = parse_args()
    document, binary = read_glb(args.input.resolve())
    metadata = face_metadata(document)
    base = embedded_image(document, binary, metadata["baseColorTextureId"])
    lut = embedded_image(document, binary, metadata["shadowLutTextureId"])

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    base.save(output / "face-base.png")
    lut.save(output / "face-shadow-lut.png")
    apply_shadow_lut(base, lut, flip_lut_y=False).save(
        output / "face-shadow-lut-top-origin.png"
    )
    apply_shadow_lut(base, lut, flip_lut_y=True).save(
        output / "face-shadow-lut-bottom-origin.png"
    )
    print(f"Wrote Shadow LUT references to {output}")


if __name__ == "__main__":
    main()
