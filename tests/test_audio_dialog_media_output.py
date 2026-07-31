import tempfile
import unittest
from pathlib import Path

import server


class AudioDialogMediaOutputTests(unittest.TestCase):
    def test_same_media_id_at_different_offsets_uses_distinct_cache_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "sample.pck"
            package.write_bytes(b"RIFFaaaaRIFFbbbb")
            record = {
                "id": 7,
                "offset": 0,
                "length": package.stat().st_size,
            }
            first = server.AudioEntry(100, 0, 8, "sound")
            second = server.AudioEntry(100, 8, 8, "sound")
            handler = object.__new__(server.BrowserHandler)
            original_cache = server.INTERNAL_CACHE_DIR
            server.INTERNAL_CACHE_DIR = root / "cache"
            try:
                first_path = handler.ensure_audio_dialog_media_file(
                    record,
                    package,
                    first,
                    "wem",
                )
                second_path = handler.ensure_audio_dialog_media_file(
                    record,
                    package,
                    second,
                    "wem",
                )
            finally:
                server.INTERNAL_CACHE_DIR = original_cache

            self.assertNotEqual(first_path, second_path)
            self.assertEqual(b"RIFFaaaa", first_path.read_bytes())
            self.assertEqual(b"RIFFbbbb", second_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
