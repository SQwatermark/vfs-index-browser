import unittest

from audio_package import parse_audio_package_bytes
from tests.test_wwise_hirc import EVENT, MEDIA, pck_fixture


class AudioPackageTests(unittest.TestCase):
    def test_preserves_pure_soundbank_without_embedded_media(self):
        package = parse_audio_package_bytes(pck_fixture(), "fixture.pck")

        self.assertEqual(1, len(package.banks))
        self.assertEqual(0, len(package.media))
        self.assertEqual(EVENT, package.banks[0].bank_id)
        self.assertEqual("sfx", package.banks[0].language)
        self.assertEqual(MEDIA, package.banks[0].graph.media_ids_for_event(EVENT))


if __name__ == "__main__":
    unittest.main()
