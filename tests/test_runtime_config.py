import tempfile
import unittest
from pathlib import Path

from runtime_config import RuntimeConfig


class RuntimeConfigTests(unittest.TestCase):
    def test_defaults_are_self_contained_in_project(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()

            config = RuntimeConfig.load(project, environ={}, which=lambda _name: None)

            self.assertEqual(project.resolve() / "data", config.data_root)
            self.assertEqual(
                project.resolve() / "data" / "endfield-vfs-index.jsonl.tgz",
                config.default_index,
            )
            self.assertEqual(
                project.resolve() / "data" / "endfield-vfs-index.sqlite",
                config.database,
            )
            self.assertNotIn("Endaxis", str(config.default_index))

    def test_data_root_moves_all_persistent_service_data(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            data = Path(directory) / "service-data"
            project.mkdir()

            config = RuntimeConfig.load(
                project,
                environ={"VFS_BROWSER_DATA_ROOT": str(data)},
                which=lambda _name: None,
            )

            self.assertEqual(data / "endfield-vfs-index.sqlite", config.database)
            self.assertEqual(data / "internal-cache", config.internal_cache)
            self.assertEqual(data / "audio-dialog-index.sqlite", config.audio_dialog_database)
            self.assertEqual(data / "wwise-index.sqlite", config.wwise_database)
            self.assertEqual(data / "shader-archives" / "1.4.4", config.shader_archive_root)

    def test_explicit_overrides_and_path_discovery_are_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            blender = root / "blender.exe"
            values = {
                "VFS_BROWSER_DB": str(root / "custom.sqlite"),
                "VFS_BROWSER_INTERNAL_CACHE": str(root / "cache"),
                "BLENDER_EXE": str(blender),
                "FFMPEG": "ffmpeg-custom",
            }

            config = RuntimeConfig.load(project, environ=values, which=lambda _name: None)

            self.assertEqual(root / "custom.sqlite", config.database)
            self.assertEqual(root / "cache", config.internal_cache)
            self.assertEqual(blender, config.blender_executable)
            self.assertEqual("ffmpeg-custom", config.ffmpeg)

    def test_blender_uses_path_lookup_when_not_explicitly_configured(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            discovered = str(Path(directory) / "bin" / "blender.exe")

            config = RuntimeConfig.load(
                project,
                environ={},
                which=lambda name: discovered if name == "blender" else None,
            )

            self.assertEqual(Path(discovered), config.blender_executable)


if __name__ == "__main__":
    unittest.main()
