import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from vfs_file_reader import VfsFileReader


class VfsFileReaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.chunk = self.root / "chunk.bin"
        self.chunk.write_bytes(b"prefix-DATA-suffix")
        self.decryptor = Mock(side_effect=lambda data, seed: data[::-1])
        self.reader = VfsFileReader(self.decryptor)

    def tearDown(self):
        self.temporary.cleanup()

    def test_reads_plain_record_offset_and_limit(self):
        record = {"offset": 7, "length": 4, "encrypted": 0}

        self.assertEqual(b"DATA", self.reader.read(record, self.chunk))
        self.assertEqual(b"DA", self.reader.read(record, self.chunk, 2))
        self.decryptor.assert_not_called()

    def test_decrypts_record_with_exact_iv_seed(self):
        record = {"offset": 7, "length": 4, "encrypted": 1, "iv_seed": 23}

        result = self.reader.read(record, self.chunk)

        self.assertEqual(b"ATAD", result)
        self.decryptor.assert_called_once_with(b"DATA", 23)

    def test_reads_plain_and_encrypted_ranges(self):
        plain = {"offset": 7, "length": 4, "encrypted": 0}
        encrypted = {"offset": 7, "length": 4, "encrypted": 1, "iv_seed": 23}

        self.assertEqual(b"AT", self.reader.read_range(plain, self.chunk, 1, 2))
        self.assertEqual(b"TA", self.reader.read_range(encrypted, self.chunk, 1, 2))

    def test_rejects_range_outside_record(self):
        record = {"offset": 7, "length": 4, "encrypted": 0}
        for offset, length in ((-1, 1), (0, -1), (3, 2)):
            with self.subTest(offset=offset, length=length):
                with self.assertRaisesRegex(ValueError, "outside the VFS record"):
                    self.reader.read_range(record, self.chunk, offset, length)


if __name__ == "__main__":
    unittest.main()
