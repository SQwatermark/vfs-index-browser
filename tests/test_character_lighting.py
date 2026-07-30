import json
import math
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from character_lighting import (
    CUBEMAP_FACE_NAMES,
    cubemap_to_equirectangular,
    load_character_lighting,
)
from tools.build_character_lighting import find_face_paths


class CharacterLightingTests(unittest.TestCase):
    def write_document(self, directory: Path) -> Path:
        document = {
            "version": 1,
            "source": {"profilePath": "profile.asset"},
            "ambient": {
                "baseIntensity": 1.0,
                "customDirectionDegrees": [180, 0],
                "directionalIntensity": 0.6,
                "directionalParameter": 0.15,
            },
            "cubemap": {
                "encoding": "ldr-png",
                "faces": {name: f"faces/{name}.png" for name in CUBEMAP_FACE_NAMES},
                "equirectangularPath": "environment.png",
            },
        }
        path = directory / "lighting.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def test_load_resolves_paths_and_direction(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            lighting = load_character_lighting(self.write_document(directory))

            self.assertEqual(
                lighting.cubemap.faces["PositiveX"],
                (directory / "faces/PositiveX.png").resolve(),
            )
            direction = lighting.ambient.blender_direction()
            self.assertAlmostEqual(direction[0], 0.0, places=7)
            self.assertAlmostEqual(direction[1], -1.0, places=7)
            self.assertAlmostEqual(direction[2], 0.0, places=7)
            self.assertAlmostEqual(math.sqrt(sum(value * value for value in direction)), 1.0)
            multiplier, addend = lighting.ambient.npr_ramp_transform()
            self.assertAlmostEqual(multiplier, 0.255)
            self.assertAlmostEqual(addend, 0.745)

    def test_load_rejects_incomplete_faces(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = self.write_document(directory)
            document = json.loads(path.read_text(encoding="utf-8"))
            del document["cubemap"]["faces"]["NegativeZ"]
            path.write_text(json.dumps(document), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "missing=.*NegativeZ"):
                load_character_lighting(path)

    def test_projection_places_positive_z_at_panorama_center(self):
        colors = {
            "PositiveX": (255, 0, 0),
            "NegativeX": (0, 255, 0),
            "PositiveY": (0, 0, 255),
            "NegativeY": (255, 255, 0),
            "PositiveZ": (255, 0, 255),
            "NegativeZ": (0, 255, 255),
        }
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            faces = {}
            for name, color in colors.items():
                path = directory / f"{name}.png"
                Image.new("RGB", (4, 4), color).save(path)
                faces[name] = path
            output = directory / "environment.png"

            cubemap_to_equirectangular(faces, output, width=16)

            with Image.open(output) as panorama:
                self.assertEqual(panorama.size, (16, 8))
                self.assertEqual(panorama.getpixel((8, 4)), colors["PositiveZ"])

    def test_find_faces_accepts_animestudio_filename_prefix(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name in CUBEMAP_FACE_NAMES:
                (directory / f"T_character_{name}.png").touch()

            faces = find_face_paths(directory)

            self.assertEqual(faces["PositiveX"].name, "T_character_PositiveX.png")


if __name__ == "__main__":
    unittest.main()
