import unittest
from pathlib import Path

from wwise_media_service import WwiseMediaBuildError, WwiseMediaService


class WwiseMediaServiceTests(unittest.TestCase):
    def media(self):
        return {
            "media_id": "00000000000000ff",
            "offset": 12,
            "size": 34,
            "source": "sound",
            "language": "sfx",
            "bank_id": 7,
            "bank_offset": 100,
            "bank_size": 200,
            "bank_media_offset": 24,
            "bank_encrypted": 1,
        }

    def test_resolves_entry_source_artifact_and_download(self):
        calls = []
        service = WwiseMediaService(
            lambda pck, ordinal: (
                calls.append(("lookup", pck, ordinal)),
                self.media(),
            )[1],
            lambda pck: (
                calls.append(("source", pck)),
                ({"id": pck, "file_name": "default.pck", "length": 1000}, Path("pck.chk")),
            )[1],
            lambda record, chunk, entry, mode, namespace: (
                calls.append(("ensure", record, chunk, entry, mode, namespace)),
                Path("cached.wav"),
            )[1],
        )

        result = service.resolve(
            {
                "pckFileId": ["99"],
                "ordinal": ["3"],
                "format": ["WAV"],
                "download": ["true"],
            }
        )

        self.assertEqual(Path("cached.wav"), result.target)
        self.assertEqual(255, result.entry.wem_id)
        self.assertEqual(24, result.entry.bank_wem_offset)
        self.assertTrue(result.entry.bank_encrypted)
        self.assertEqual("wav", result.mode)
        self.assertTrue(result.download)
        self.assertEqual(("lookup", 99, 3), calls[0])
        self.assertEqual("wwise", calls[2][-1])

    def test_rejects_invalid_mode_before_lookup(self):
        service = WwiseMediaService(
            lambda *_args: self.fail("lookup is unexpected"),
            lambda *_args: self.fail("source lookup is unexpected"),
            lambda *_args: self.fail("build is unexpected"),
        )

        with self.assertRaisesRegex(ValueError, "must be wem or wav"):
            service.resolve(
                {"pckFileId": ["99"], "ordinal": ["3"], "format": ["mp3"]}
            )

    def test_distinguishes_missing_media_and_source(self):
        missing_media = WwiseMediaService(
            lambda *_args: None,
            lambda *_args: self.fail("source lookup is unexpected"),
            lambda *_args: self.fail("build is unexpected"),
        )
        with self.assertRaisesRegex(FileNotFoundError, "Wwise media not found"):
            missing_media.resolve({"pckFileId": ["99"], "ordinal": ["3"]})

        missing_source = WwiseMediaService(
            lambda *_args: self.media(),
            lambda *_args: None,
            lambda *_args: self.fail("build is unexpected"),
        )
        with self.assertRaisesRegex(FileNotFoundError, "PCK source is unavailable"):
            missing_source.resolve({"pckFileId": ["99"], "ordinal": ["3"]})

    def test_wraps_artifact_build_failure(self):
        service = WwiseMediaService(
            lambda *_args: self.media(),
            lambda *_args: (
                {"id": 99, "file_name": "default.pck", "length": 1000},
                Path("pck.chk"),
            ),
            lambda *_args: (_ for _ in ()).throw(RuntimeError("conversion failed")),
        )

        with self.assertRaisesRegex(WwiseMediaBuildError, "conversion failed"):
            service.resolve({"pckFileId": ["99"], "ordinal": ["3"]})


if __name__ == "__main__":
    unittest.main()
