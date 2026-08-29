import tempfile
import unittest
from pathlib import Path

from usm_video_service import UsmVideoService


class UsmVideoServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.ffmpeg = self.root / "ffmpeg.exe"
        self.ffmpeg.write_bytes(b"tool")
        self.record = {
            "id": 7,
            "file_name": "opening.usm",
            "length": 12,
            "offset": 3,
            "file_data_md5": "source-a",
        }
        self.calls = []

        def convert(data, output, **options):
            self.calls.append((data, options))
            output.write_bytes(b"mp4:" + data)

        self.service = UsmVideoService(
            self.root / "cache",
            2,
            usm_convert=None,
            ffmpeg=self.ffmpeg,
            converter=convert,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_lists_one_virtual_mp4(self):
        root = self.service.list_directory(self.record, "")
        listing = self.service.list_directory(self.record, "mp4")

        self.assertEqual("mp4", root["dirs"][0]["path"])
        self.assertEqual("mp4/opening.mp4", listing["files"][0]["path"])
        with self.assertRaises(FileNotFoundError):
            self.service.list_directory(self.record, "missing")

    def test_publishes_and_reuses_verified_conversion(self):
        first = self.service.ensure_video(
            self.record,
            "mp4%2Fopening.mp4",
            lambda: b"source",
        )
        second = self.service.ensure_video(
            self.record,
            "mp4/opening.mp4",
            lambda: self.fail("cache hit must not read source"),
        )

        self.assertEqual(first, second)
        self.assertEqual(b"mp4:source", second.read_bytes())
        self.assertEqual(1, len(self.calls))

    def test_source_identity_change_invalidates_same_length_cache(self):
        self.service.ensure_video(self.record, "mp4/opening.mp4", lambda: b"first")
        changed = {**self.record, "file_data_md5": "source-b"}
        result = self.service.ensure_video(
            changed,
            "mp4/opening.mp4",
            lambda: b"later",
        )

        self.assertEqual(b"mp4:later", result.read_bytes())
        self.assertEqual(2, len(self.calls))

    def test_failure_does_not_publish_partial_cache(self):
        def fail(_data, output, **_options):
            output.write_bytes(b"partial")
            raise RuntimeError("conversion failed")

        service = UsmVideoService(
            self.root / "failed-cache",
            1,
            usm_convert=None,
            ffmpeg=self.ffmpeg,
            converter=fail,
        )
        with self.assertRaisesRegex(RuntimeError, "conversion failed"):
            service.ensure_video(self.record, "mp4/opening.mp4", lambda: b"source")

        published = list((self.root / "failed-cache").rglob("*"))
        self.assertFalse(any(path.is_file() for path in published))


if __name__ == "__main__":
    unittest.main()
