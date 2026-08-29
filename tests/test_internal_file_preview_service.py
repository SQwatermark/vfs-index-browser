import tempfile
import unittest
from pathlib import Path

from internal_file_preview_service import InternalFilePreviewService


class InternalFilePreviewServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_builds_preview_and_encodes_full_internal_path(self):
        target = self.root / "配置 file.txt"
        target.write_text("internal text", encoding="utf-8")
        asset = {"Type": "TextAsset", "PathID": 7}

        result = InternalFilePreviewService().build(
            {"id": 42, "file_name": "source.ab"},
            target,
            "folder/配置 file.txt",
            asset=asset,
        )

        self.assertEqual("text", result["kind"])
        self.assertEqual("internal text", result["text"])
        self.assertEqual("folder/配置 file.txt", result["path"])
        self.assertEqual(asset, result["asset"])
        self.assertIsNone(result["audioEntry"])
        self.assertEqual(
            "/api/internal/raw?id=42&path=folder%2F%E9%85%8D%E7%BD%AE%20file.txt",
            result["rawUrl"],
        )
        self.assertEqual(f"{result['rawUrl']}&download=1", result["downloadUrl"])

    def test_preserves_audio_entry_in_binary_preview(self):
        target = self.root / "1060201.wem"
        target.write_bytes(b"\x00\x01\x02")
        audio_entry = {"id": 1060201, "size": 3}

        result = InternalFilePreviewService().build(
            {"id": 9},
            target,
            "wem/10/1060201.wem",
            audio_entry=audio_entry,
        )

        self.assertEqual("hex", result["kind"])
        self.assertEqual(audio_entry, result["audioEntry"])
        self.assertIsNone(result["asset"])


if __name__ == "__main__":
    unittest.main()
