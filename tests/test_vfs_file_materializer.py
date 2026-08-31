import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from vfs_file_materializer import VfsFileMaterializer


class VfsFileMaterializerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.reader = Mock()
        self.materializer = VfsFileMaterializer(self.reader, stream_chunk_size=2)

    def tearDown(self):
        self.temporary.cleanup()

    def test_writes_exact_plain_slice_and_reuses_matching_target(self):
        chunk = self.root / "chunk.bin"
        chunk.write_bytes(b"prefix-DATA-suffix")
        target = self.root / "out" / "file.bin"
        record = {"offset": 7, "length": 4, "encrypted": 0}

        self.materializer.write(record, chunk, target)
        chunk.write_bytes(b"prefix-FAIL-suffix")
        self.materializer.write(record, chunk, target)

        self.assertEqual(b"DATA", target.read_bytes())
        self.reader.assert_not_called()

    def test_encrypted_record_uses_decrypted_reader(self):
        chunk = self.root / "chunk.bin"
        chunk.write_bytes(b"encrypted")
        target = self.root / "file.bin"
        record = {"offset": 0, "length": 5, "encrypted": 1}
        self.reader.return_value = b"clear"

        self.materializer.write(record, chunk, target)

        self.assertEqual(b"clear", target.read_bytes())
        self.reader.assert_called_once_with(record, chunk)

    def test_truncated_source_preserves_old_target_and_cleans_candidate(self):
        chunk = self.root / "chunk.bin"
        chunk.write_bytes(b"xx")
        target = self.root / "file.bin"
        target.write_bytes(b"old")
        record = {"offset": 0, "length": 5, "encrypted": 0}

        with self.assertRaisesRegex(OSError, "expected 5, got 2"):
            self.materializer.write(record, chunk, target)

        self.assertEqual(b"old", target.read_bytes())
        self.assertEqual([], list(self.root.glob(".*.tmp")))


if __name__ == "__main__":
    unittest.main()
