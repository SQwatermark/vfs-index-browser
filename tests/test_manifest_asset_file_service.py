import tempfile
import unittest
from pathlib import Path

from manifest_asset_file_service import (
    ManifestAssetFileError,
    ManifestAssetFileService,
)


class ManifestAssetFileServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.export = self.root / "export"
        self.export.mkdir()
        self.asset = {"path": "assets/sample.asset"}
        self.record = {"id": 7, "file_name": "bundle.ab"}
        self.chunk = self.root / "chunk.chk"
        self.resolved = (object(), self.asset, self.record, self.chunk)

    def service(self, *, bundle=None, fallback=None, cubemap=None):
        return ManifestAssetFileService(
            bundle or (lambda *_args: (self.export, {"assetEntries": []})),
            fallback or (lambda *_args: None),
            cubemap or (lambda *_args: None),
        )

    def test_uses_monobehaviour_fallback_when_assetmap_has_no_match(self):
        target = self.root / "sample.txt"
        target.write_text("dump", encoding="utf-8")
        result = self.service(
            fallback=lambda *_args: (target, {"exportedFiles": ["a.json"]}),
        ).resolve_file(self.resolved)

        self.assertEqual(target, result.target)
        self.assertEqual("MonoBehaviourDump", result.asset["Type"])
        self.assertEqual(["a.json"], result.asset["Components"])

    def test_reports_unsupported_asset_when_fallback_is_unavailable(self):
        with self.assertRaisesRegex(ManifestAssetFileError, "暂不支持") as caught:
            self.service().resolve_file(self.resolved)
        self.assertEqual(404, caught.exception.status)

    def test_reports_missing_export_file_after_metadata_match(self):
        meta = {
            "assetEntries": [{
                "Type": "TextAsset",
                "Name": "sample",
                "PathID": "1",
                "Container": self.asset["path"],
            }]
        }
        with self.assertRaisesRegex(ManifestAssetFileError, "导出文件缺失"):
            self.service(bundle=lambda *_args: (self.export, meta)).resolve_file(
                self.resolved
            )

    def test_builds_cubemap_identity_and_preserves_faces(self):
        faces = {"PositiveX": self.root / "PositiveX.png"}
        result = self.service(
            cubemap=lambda *_args: (faces, {"selectedRun": "run"}),
        ).resolve_cubemap(self.resolved)

        self.assertEqual(faces, result.faces)
        self.assertEqual("Cubemap", result.asset["Type"])
        self.assertEqual(self.asset, result.manifest_asset)

    def test_cubemap_file_absence_remains_a_non_match(self):
        def missing(*_args):
            raise FileNotFoundError("not a cubemap")

        self.assertIsNone(
            self.service(cubemap=missing).resolve_cubemap(self.resolved)
        )


if __name__ == "__main__":
    unittest.main()
