import tempfile
import unittest
from pathlib import Path

from tool_registry import ToolRegistry


class ToolRegistryTests(unittest.TestCase):
    def test_resolves_explicit_files_and_path_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            explicit = root / "blender.exe"
            discovered = root / "ffmpeg.exe"
            explicit.write_bytes(b"tool")
            discovered.write_bytes(b"tool")
            registry = ToolRegistry(
                {"blender": explicit, "ffmpeg": "ffmpeg"},
                which=lambda name: str(discovered) if name == "ffmpeg" else None,
            )

            self.assertEqual(explicit.resolve(), registry.capability("blender").resolved_path)
            self.assertEqual(discovered.resolve(), registry.capability("ffmpeg").resolved_path)
            self.assertTrue(registry.available("blender"))

    def test_missing_tools_remain_nonfatal_diagnostics(self):
        registry = ToolRegistry(
            {"vgmstream": "missing-vgmstream"},
            which=lambda _name: None,
        )

        self.assertFalse(registry.available("vgmstream"))
        self.assertEqual(
            [
                {
                    "name": "vgmstream",
                    "configured": "missing-vgmstream",
                    "available": False,
                    "resolvedPath": None,
                }
            ],
            registry.diagnostics(),
        )

    def test_unknown_capability_is_an_explicit_programming_error(self):
        registry = ToolRegistry({}, which=lambda _name: None)
        with self.assertRaisesRegex(KeyError, "not registered"):
            registry.capability("blender")


if __name__ == "__main__":
    unittest.main()
