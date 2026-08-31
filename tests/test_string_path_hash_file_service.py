import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from string_path_hash_file_service import StringPathHashFileService


class StringPathHashFileServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.chunk = self.root / "source.chk"
        self.chunk.write_bytes(b"current")
        self.record = {
            "id": 2,
            "length": 7,
            "offset": 0,
            "chunk_path": str(self.chunk),
            "file_data_md5": "abc",
        }
        self.writer = Mock(
            side_effect=lambda _record, source, target: target.write_bytes(
                source.read_bytes()
            )
        )
        self.service = StringPathHashFileService(
            lambda _logical_id: (self.record, self.chunk),
            self.writer,
            threading.Lock(),
            self.root / "cache",
            logical_id="StringPathHash.bin",
            cache_version=3,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_publishes_and_reuses_matching_file(self):
        first, first_meta = self.service.ensure()
        second, second_meta = self.service.ensure()

        self.assertEqual(b"current", first.read_bytes())
        self.assertEqual(first, second)
        self.assertEqual(first_meta, second_meta)
        self.assertEqual(3, first_meta["version"])
        self.writer.assert_called_once()

    def test_failed_rebuild_preserves_previous_file_and_metadata(self):
        target, meta = self.service.ensure()
        meta_path = target.with_name("meta.json")
        self.record["file_data_md5"] = "changed"
        self.writer.side_effect = OSError("copy failed")

        with self.assertRaisesRegex(OSError, "copy failed"):
            self.service.ensure()

        self.assertEqual(b"current", target.read_bytes())
        self.assertEqual(meta, json.loads(meta_path.read_text(encoding="utf-8")))
        self.assertEqual([], list(target.parent.glob(".*.tmp")))

    def test_failed_metadata_publication_leaves_recoverable_cache(self):
        target, _ = self.service.ensure()
        self.record["file_data_md5"] = "changed"
        original_replace = __import__("os").replace

        def fail_meta(source, destination):
            if Path(destination).name == "meta.json":
                raise OSError("meta replace failed")
            return original_replace(source, destination)

        with (
            patch("string_path_hash_file_service.os.replace", side_effect=fail_meta),
            self.assertRaisesRegex(OSError, "meta replace failed"),
        ):
            self.service.ensure()

        self.assertEqual(b"current", target.read_bytes())
        self.assertEqual([], list(target.parent.glob(".*.tmp")))

    def test_missing_source_is_explicit(self):
        service = StringPathHashFileService(
            lambda _logical_id: None,
            self.writer,
            threading.Lock(),
            self.root / "cache",
            logical_id="missing.bin",
            cache_version=3,
        )
        with self.assertRaisesRegex(FileNotFoundError, "missing.bin"):
            service.ensure()


if __name__ == "__main__":
    unittest.main()
