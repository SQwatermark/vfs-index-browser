import tempfile
import unittest
from pathlib import Path

from internal_file_resolver_service import (
    InternalFileResolutionError,
    InternalFileResolverService,
)


class InternalFileResolverServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.export = self.root / "export"
        self.export.mkdir()

    def service(self, *, ensure_assetbundle=None, ensure_audio=None, ensure_usm=None):
        return InternalFileResolverService(
            ensure_assetbundle or (lambda *_args: (self.export, {"assetEntries": []})),
            ensure_audio or (lambda *_args: (_ for _ in ()).throw(AssertionError())),
            ensure_usm or (lambda *_args: (_ for _ in ()).throw(AssertionError())),
        )

    def test_resolves_assetbundle_path_without_leaving_export_root(self):
        target = self.export / "TextAsset" / "sample.txt"
        target.parent.mkdir()
        target.write_text("sample", encoding="utf-8")
        record = {"file_name": "sample.ab"}

        result = self.service().resolve(
            record,
            Path("chunk.chk"),
            {"path": ["TextAsset/sample.txt"]},
        )

        self.assertEqual(target, result.target)
        with self.assertRaisesRegex(InternalFileResolutionError, "not found"):
            self.service().resolve(
                record,
                Path("chunk.chk"),
                {"path": ["../outside.txt"]},
            )

    def test_resolves_audio_and_preserves_entry(self):
        target = self.root / "sample.wem"
        entry = object()
        result = self.service(
            ensure_audio=lambda *_args: (target, entry),
        ).resolve(
            {"file_name": "voice.pck"},
            Path("chunk.chk"),
            {"path": ["wem/sample.wem"]},
        )

        self.assertEqual(target, result.target)
        self.assertIs(entry, result.audio_entry)

    def test_builds_usm_asset_metadata(self):
        target = self.root / "movie.mp4"
        result = self.service(ensure_usm=lambda *_args: target).resolve(
            {"file_name": "movie.usm"},
            Path("chunk.chk"),
            {"path": ["mp4/movie.mp4"]},
        )

        self.assertEqual("MP4", result.asset["Type"])
        self.assertEqual("movie.usm", result.asset["Container"])

    def test_rejects_plain_files(self):
        with self.assertRaisesRegex(InternalFileResolutionError, "unsupported") as caught:
            self.service().resolve(
                {"file_name": "notes.txt"},
                Path("chunk.chk"),
                {},
            )
        self.assertEqual(400, caught.exception.status)


if __name__ == "__main__":
    unittest.main()
