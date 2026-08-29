import unittest
from pathlib import Path

from internal_directory_service import InternalDirectoryService


class InternalDirectoryServiceTests(unittest.TestCase):
    def service(self, **overrides):
        callbacks = {
            "ensure_assetbundle": lambda *_args: (
                Path("export"),
                {"returncode": 0, "builtAtEpoch": 1, "exportTypes": ["TextAsset"]},
            ),
            "list_assetbundle": lambda _root, path, _meta: self.listing(path),
            "ensure_audio": lambda *_args: {"builtAtEpoch": 2, "entryCount": 3},
            "list_audio": lambda _meta, path: self.listing(path),
            "list_usm": lambda _record, path: self.listing(path),
        }
        callbacks.update(overrides)
        return InternalDirectoryService(**callbacks)

    @staticmethod
    def listing(path):
        return {"path": path, "dirs": [{"name": "d"}], "files": [{"name": "f"}]}

    @staticmethod
    def tools():
        return {
            "audio": {"wavPreviewAvailable": True, "vgmstreamCli": "vgmstream.exe"},
            "usm": {
                "usmConvertAvailable": True,
                "ffmpegAvailable": False,
                "usmConvert": "usm.exe",
                "ffmpeg": "ffmpeg",
            },
        }

    def test_builds_each_supported_container_document(self):
        original = {"id": 1}
        for name, kind in (
            ("bundle.ab", "assetBundle"),
            ("voice.pck", "audioPackage"),
            ("movie.usm", "criVideo"),
        ):
            with self.subTest(name=name):
                result = self.service().build(
                    original,
                    {"id": 2, "file_name": name},
                    Path("chunk.chk"),
                    "inside",
                    self.tools(),
                )
                self.assertEqual(kind, result["kind"])
                self.assertEqual("ready", result["status"])
                self.assertEqual("inside", result["path"])

    def test_assetbundle_failure_remains_already_reported_none(self):
        result = self.service(ensure_assetbundle=lambda *_args: None).build(
            {"id": 1},
            {"id": 1, "file_name": "bundle.ab"},
            Path("chunk.chk"),
            "",
            self.tools(),
        )
        self.assertIsNone(result)

    def test_plain_file_is_not_guessed_as_container(self):
        result = self.service().build(
            {"id": 1},
            {"id": 1, "file_name": "notes.txt"},
            Path("chunk.chk"),
            "",
            self.tools(),
        )
        self.assertEqual("plainFile", result["kind"])
        self.assertEqual("notContainer", result["status"])


if __name__ == "__main__":
    unittest.main()
