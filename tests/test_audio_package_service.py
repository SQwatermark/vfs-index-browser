import tempfile
import unittest
from pathlib import Path

from audio_package_service import AudioPackageIndexService, parse_audio_internal_path
from tests.test_audio_package import pck_fixture


class AudioPackageIndexServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.payload = pck_fixture()
        self.record = {
            "id": 4,
            "file_name": "fixture.pck",
            "length": len(self.payload),
            "offset": 10,
            "file_data_md5": "source-a",
        }
        self.service = AudioPackageIndexService(self.root / "cache", 2)
        self.read_count = 0

    def tearDown(self):
        self.temporary.cleanup()

    def read_range(self, offset, size):
        self.read_count += 1
        return self.payload[offset : offset + size]

    def test_builds_reuses_and_lists_cached_media_index(self):
        first = self.service.ensure_index(self.record, self.read_range)
        reads_after_build = self.read_count
        second = self.service.ensure_index(
            self.record,
            lambda _offset, _size: self.fail("cache hit must not read package"),
        )

        self.assertEqual(first, second)
        self.assertGreater(reads_after_build, 0)
        root = self.service.list_directory(first, "")
        self.assertEqual(["wem", "wav"], [item["path"] for item in root["dirs"]])
        populated = {
            **first,
            "entries": [
                {
                    "id": 0xABCDEF,
                    "offset": 4,
                    "size": 12,
                    "source": "sound",
                    "language": "sfx",
                    "bankId": None,
                }
            ],
        }
        listing = self.service.list_directory(populated, "wem/ab")
        self.assertTrue(listing["files"])

    def test_same_length_source_change_invalidates_cache(self):
        self.service.ensure_index(self.record, self.read_range)
        reads_after_build = self.read_count
        changed = {**self.record, "file_data_md5": "source-b"}

        rebuilt = self.service.ensure_index(changed, self.read_range)

        self.assertGreater(self.read_count, reads_after_build)
        self.assertEqual("source-b", rebuilt["identity"]["fileDataMd5"])

    def test_rejects_unknown_virtual_directory(self):
        meta = self.service.ensure_index(self.record, self.read_range)
        with self.assertRaises(FileNotFoundError):
            self.service.list_directory(meta, "mp3")

    def test_parses_only_matching_wem_and_wav_paths(self):
        self.assertEqual(("wav", 123), parse_audio_internal_path("wav%2Fab%2F123.wav"))
        self.assertIsNone(parse_audio_internal_path("wav/ab/123.wem"))


if __name__ == "__main__":
    unittest.main()
