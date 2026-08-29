import tempfile
import unittest
from pathlib import Path

from manifest_asset_preview_service import ManifestAssetPreviewService


class ManifestAssetPreviewServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bundle = {"id": 11, "file_name": "bundle.ab", "length": 999}

    def test_builds_ordered_cubemap_document_with_positive_z_default(self):
        face_names = (
            "PositiveX", "NegativeX", "PositiveY",
            "NegativeY", "PositiveZ", "NegativeZ",
        )
        faces = {}
        for index, name in enumerate(face_names, 1):
            target = self.root / f"{name}.png"
            target.write_bytes(bytes([index]) * index)
            faces[name] = target

        result = ManifestAssetPreviewService(face_names).build_cubemap(
            self.bundle,
            faces,
            {"Type": "Cubemap"},
            {"path": "assets/sky.exr", "bundle_name": "main/sky.ab"},
            manifest_id=42,
            asset_index=7,
        )

        self.assertEqual("cubemap", result["kind"])
        self.assertEqual(list(face_names), [item["name"] for item in result["faces"]])
        self.assertEqual(21, result["size"])
        self.assertEqual(21, result["file"]["length"])
        self.assertEqual(
            "/api/manifest-asset/raw?manifestId=42&assetIndex=7&face=PositiveZ",
            result["rawUrl"],
        )
        self.assertEqual("image/png", result["faces"][0]["contentType"])
        self.assertIn("main/sky.ab", result["message"])

    def test_builds_normal_file_through_shared_preview_classifier(self):
        target = self.root / "combined-dump.txt"
        target.write_text("hello manifest", encoding="utf-8")

        result = ManifestAssetPreviewService().build_file(
            self.bundle,
            target,
            {"Type": "MonoBehaviourDump"},
            {"path": "assets/source.asset", "bundle_name": "main/data.ab"},
            manifest_id="9",
            asset_index="3",
        )

        self.assertEqual("text", result["kind"])
        self.assertEqual("hello manifest", result["text"])
        self.assertEqual("assets/source.asset", result["file"]["file_name"])
        self.assertEqual(
            "/api/manifest-asset/raw?manifestId=9&assetIndex=3&download=1",
            result["downloadUrl"],
        )
        self.assertEqual("来自 main/data.ab", result["message"])


if __name__ == "__main__":
    unittest.main()
