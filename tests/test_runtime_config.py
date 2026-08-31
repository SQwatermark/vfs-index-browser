import tempfile
import unittest
from pathlib import Path

from runtime_config import RuntimeConfig, resolve_application_root


class RuntimeConfigTests(unittest.TestCase):
    def test_application_root_uses_executable_only_when_frozen(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = root / "source" / "server.py"
            executable = root / "package" / "vfs-browser.exe"

            self.assertEqual(
                module.parent.resolve(),
                resolve_application_root(
                    module,
                    executable=executable,
                    frozen=False,
                ),
            )
            self.assertEqual(
                executable.parent.resolve(),
                resolve_application_root(
                    module,
                    executable=executable,
                    frozen=True,
                ),
            )

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
            self.assertEqual("127.0.0.1", config.host)
            self.assertEqual(8765, config.port)
            self.assertEqual("info", config.log_level)
            self.assertEqual("json", config.log_format)

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
                "VFS_BROWSER_HOST": "0.0.0.0",
                "VFS_BROWSER_PORT": "9000",
                "VFS_BROWSER_LOG_LEVEL": "WARNING",
                "VFS_BROWSER_LOG_FORMAT": "text",
            }

            config = RuntimeConfig.load(project, environ=values, which=lambda _name: None)

            self.assertEqual(root / "custom.sqlite", config.database)
            self.assertEqual(root / "cache", config.internal_cache)
            self.assertEqual(blender, config.blender_executable)
            self.assertEqual("ffmpeg-custom", config.ffmpeg)
            self.assertEqual("0.0.0.0", config.host)
            self.assertEqual(9000, config.port)
            self.assertEqual("warning", config.log_level)
            self.assertEqual("text", config.log_format)

    def test_rejects_invalid_server_and_logging_values(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            invalid_values = (
                ({"VFS_BROWSER_PORT": "0"}, "port must be between"),
                ({"VFS_BROWSER_PORT": "many"}, "port must be an integer"),
                ({"VFS_BROWSER_LOG_LEVEL": "verbose"}, "VFS_BROWSER_LOG_LEVEL"),
                ({"VFS_BROWSER_LOG_FORMAT": "xml"}, "VFS_BROWSER_LOG_FORMAT"),
            )
            for environ, message in invalid_values:
                with self.subTest(environ=environ):
                    with self.assertRaisesRegex(ValueError, message):
                        RuntimeConfig.load(project, environ=environ, which=lambda _name: None)

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
