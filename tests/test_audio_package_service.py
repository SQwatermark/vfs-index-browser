import tempfile
import unittest
from pathlib import Path

from audio_package_service import (
    AudioEntry,
    AudioPackageIndexService,
    StaleAudioIndexError,
    parse_audio_internal_path,
    validate_indexed_audio_source,
)
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

    def test_rejects_stale_secondary_index_source_identity_and_range(self):
        entry = AudioEntry(100, 12, 34, "sound")
        with self.assertRaisesRegex(StaleAudioIndexError, "no longer a PCK"):
            validate_indexed_audio_source(
                {"id": 7, "file_name": "unrelated.bytes", "length": 1000},
                entry,
                index_name="fixture index",
            )
        with self.assertRaisesRegex(StaleAudioIndexError, "range exceeds"):
            validate_indexed_audio_source(
                {"id": 7, "file_name": "audio.pck", "length": 20},
                entry,
                index_name="fixture index",
            )
        with self.assertRaisesRegex(StaleAudioIndexError, "size changed"):
            validate_indexed_audio_source(
                {"id": 7, "file_name": "audio.pck", "length": 1000},
                entry,
                index_name="fixture index",
                expected_file_size=999,
            )

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

    def test_media_cache_separates_same_length_package_identities(self):
        entry = AudioEntry(100, 0, 8, "sound")
        first = self.service.ensure_indexed_media(
            self.record,
            entry,
            "wem",
            "dialog",
            lambda _offset, _size: b"RIFFaaaa",
        )
        changed = {**self.record, "file_data_md5": "source-b"}
        second = self.service.ensure_indexed_media(
            changed,
            entry,
            "wem",
            "dialog",
            lambda _offset, _size: b"RIFFbbbb",
        )

        self.assertNotEqual(first, second)
        self.assertEqual(b"RIFFaaaa", first.read_bytes())
        self.assertEqual(b"RIFFbbbb", second.read_bytes())

    def test_short_media_read_is_not_published(self):
        entry = AudioEntry(100, 0, 8, "sound")
        with self.assertRaisesRegex(ValueError, "expected 8 bytes"):
            self.service.ensure_indexed_media(
                self.record,
                entry,
                "wem",
                "dialog",
                lambda _offset, _size: b"short",
            )
        self.assertFalse(any(path.is_file() for path in (self.root / "cache").rglob("*.wem")))


if __name__ == "__main__":
    unittest.main()
