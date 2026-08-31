import gzip
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from vfs_index_jsonl import open_index


class VfsIndexJsonlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.lines = ['{"recordType":"header"}\n', '{"recordType":"summary"}\n']

    def test_reads_plain_jsonl(self):
        path = self.root / "index.jsonl"
        path.write_text("".join(self.lines), encoding="utf-8")

        self.assertEqual(self.lines, list(open_index(path)))

    def test_reads_gzip_jsonl(self):
        path = self.root / "index.jsonl.gz"
        with gzip.open(path, "wt", encoding="utf-8") as writer:
            writer.writelines(self.lines)

        self.assertEqual(self.lines, list(open_index(path)))

    def test_reads_single_file_tar_gzip(self):
        path = self.root / "index.tgz"
        payload = "".join(self.lines).encode("utf-8")
        info = tarfile.TarInfo("nested/index.jsonl")
        info.size = len(payload)
        with tarfile.open(path, "w:gz") as archive:
            archive.addfile(info, io.BytesIO(payload))

        self.assertEqual(self.lines, list(open_index(path)))

    def test_rejects_archive_with_multiple_files(self):
        path = self.root / "index.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            for name in ("first.jsonl", "second.jsonl"):
                payload = b"{}\n"
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))

        with self.assertRaisesRegex(ValueError, "expected one JSONL file"):
            list(open_index(path))


if __name__ == "__main__":
    unittest.main()
