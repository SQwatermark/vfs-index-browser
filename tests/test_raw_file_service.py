import tempfile
import unittest
from pathlib import Path

from raw_file_service import RawFileService


class RawFileServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_clear_vfs_file_streams_only_declared_slice(self):
        chunk = self.root / "source.chk"
        chunk.write_bytes(b"prefixPAYLOADsuffix")
        decrypted_calls = []
        response = RawFileService(chunk_size=3).prepare_vfs(
            {"file_name": "folder/data.bin"},
            {"encrypted": 0, "offset": 6, "length": 7},
            chunk,
            download=False,
            read_decrypted=lambda: decrypted_calls.append(True) or b"wrong",
        )

        self.assertEqual([], decrypted_calls)
        self.assertEqual(b"PAYLOAD", b"".join(response.chunks()))
        self.assertEqual(7, response.content_length)
        self.assertEqual("inline; filename*=UTF-8''data.bin", response.content_disposition)

    def test_encrypted_file_uses_decrypted_bytes_and_rejects_false_text_type(self):
        chunk = self.root / "unused.chk"
        response = RawFileService(chunk_size=2).prepare_vfs(
            {"file_name": "bad.json"},
            {"encrypted": 1, "offset": 0, "length": 999},
            chunk,
            download=True,
            read_decrypted=lambda: b"\x00\x01\x02",
        )

        self.assertEqual("application/octet-stream", response.content_type)
        self.assertEqual(3, response.content_length)
        self.assertEqual(b"\x00\x01\x02", b"".join(response.chunks()))
        self.assertTrue(response.content_disposition.startswith("attachment;"))

    def test_path_response_sniffs_media_and_streams_in_chunks(self):
        target = self.root / "image.dat"
        target.write_bytes(b"\x89PNG\r\n\x1a\nbody")

        response = RawFileService(chunk_size=4).prepare_path(target, download=False)

        chunks = list(response.chunks())
        self.assertEqual("image/png", response.content_type)
        self.assertEqual(target.stat().st_size, response.content_length)
        self.assertTrue(all(len(chunk) <= 4 for chunk in chunks))
        self.assertEqual(target.read_bytes(), b"".join(chunks))

    def test_path_response_accepts_transport_metadata_overrides(self):
        target = self.root / "opaque.bin"
        target.write_bytes(b"data")

        response = RawFileService().prepare_path(
            target,
            download=True,
            download_name="模型 文件.glb",
            content_type="model/gltf-binary",
        )
        no_disposition = RawFileService().prepare_path(
            target,
            download=None,
            content_type="application/octet-stream",
        )
        legacy_name = RawFileService().prepare_path(
            target,
            download=False,
            download_name="123.wem",
            content_type="audio/x-wem",
            encode_filename=False,
        )

        self.assertEqual("model/gltf-binary", response.content_type)
        self.assertEqual(
            "attachment; filename*=UTF-8''%E6%A8%A1%E5%9E%8B%20%E6%96%87%E4%BB%B6.glb",
            response.content_disposition,
        )
        self.assertIsNone(no_disposition.content_disposition)
        self.assertEqual("inline; filename=123.wem", legacy_name.content_disposition)

    def test_bytes_response_is_chunked_and_requires_a_download_name(self):
        response = RawFileService(chunk_size=2).prepare_bytes(
            b"abcde",
            content_type="application/json; charset=utf-8",
            download=False,
            download_name="table.json",
        )

        self.assertEqual([b"ab", b"cd", b"e"], list(response.chunks()))
        self.assertEqual(5, response.content_length)
        self.assertEqual(
            "inline; filename*=UTF-8''table.json",
            response.content_disposition,
        )
        with self.assertRaisesRegex(ValueError, "download_name"):
            RawFileService().prepare_bytes(
                b"x",
                content_type="application/octet-stream",
                download=True,
            )


if __name__ == "__main__":
    unittest.main()
