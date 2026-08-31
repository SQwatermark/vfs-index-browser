import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from manifest_index import ManifestIndex
from manifest_index_service import ManifestIndexService


class ManifestIndexServiceTests(unittest.TestCase):
    def test_builds_stable_source_identity_and_reuses_memory_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            service = ManifestIndexService(cache)
            record = {"id": 7, "length": 42, "file_data_md5": "ABCD"}
            index = object()
            payload = lambda: b"manifest"
            with patch.object(
                ManifestIndex, "ensure_for_source", return_value=index
            ) as ensure:
                first = service.ensure(record, payload)
                second = service.ensure(record, lambda: self.fail("cache missed"))

        self.assertIs(index, first)
        self.assertIs(first, second)
        ensure.assert_called_once_with(payload, cache, "vfs-md5:abcd:length:42")

    def test_record_identity_change_uses_separate_index(self):
        with tempfile.TemporaryDirectory() as directory:
            service = ManifestIndexService(Path(directory))
            indexes = [object(), object()]
            with patch.object(
                ManifestIndex, "ensure_for_source", side_effect=indexes
            ) as ensure:
                first = service.ensure(
                    {"id": 7, "length": 42, "file_data_md5": "a"},
                    lambda: b"first",
                )
                second = service.ensure(
                    {"id": 7, "length": 43, "file_data_md5": "b"},
                    lambda: b"second",
                )

        self.assertIs(indexes[0], first)
        self.assertIs(indexes[1], second)
        self.assertEqual(2, ensure.call_count)


if __name__ == "__main__":
    unittest.main()
