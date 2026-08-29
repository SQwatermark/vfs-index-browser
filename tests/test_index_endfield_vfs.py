import argparse
import io
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from tools.index_endfield_vfs import (
    CHACHA_KEY,
    chacha20_apply,
    decrypt_blc,
    index_source,
    parse_block_info,
)


def uint128(value: int) -> bytes:
    return value.to_bytes(16, "little", signed=False)


def build_block() -> bytes:
    name = b"BundleManifest"
    file_name = b"Data/Bundles/Windows/manifest.hgmmap"
    payload = b"".join(
        (
            struct.pack("<iiH", 4, 1, len(name)),
            name,
            struct.pack("<qiqB", 123, 1, 7, 4),
            struct.pack("<i", 1),
            uint128(0x11),
            uint128(0x22),
            struct.pack("<qBi", 7, 4, 0),
            struct.pack("<iH", 1, len(file_name)),
            file_name,
            struct.pack("<q", 456),
            uint128(0x33),
            uint128(0x44),
            struct.pack("<qqBBqi", 0, 7, 4, 1, 99, 1),
        )
    )
    return payload + struct.pack("<I", zlib.crc32(payload) & 0xFFFFFFFF)


class EndfieldVfsIndexerTests(unittest.TestCase):
    def test_parses_verified_block_file_and_encrypted_file_metadata(self):
        block = parse_block_info(build_block())

        self.assertEqual("BundleManifest", block.group_cfg_name)
        self.assertEqual(1, len(block.chunks))
        chunk = block.chunks[0]
        self.assertEqual("11000000000000000000000000000000.chk", chunk.chk_file_name)
        file = chunk.files[0]
        self.assertEqual("Data/Bundles/Windows/manifest.hgmmap", file.file_name)
        self.assertTrue(file.use_encrypt)
        self.assertEqual(99, file.iv_seed)
        self.assertEqual(1, file.file_tag)

    def test_decrypts_blc_using_the_verified_nonce_layout(self):
        decrypted = build_block()
        nonce = bytes(range(12))
        encrypted = nonce + chacha20_apply(CHACHA_KEY, nonce, 1, decrypted)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.blc"
            path.write_bytes(encrypted)

            self.assertEqual(decrypted, decrypt_blc(path))

    def test_indexes_real_block_layout_into_stable_json_records(self):
        decrypted = build_block()
        nonce = bytes(range(12))
        with tempfile.TemporaryDirectory() as directory:
            source_root = Path(directory) / "StreamingAssets"
            block_root = source_root / "VFS" / "1CDDBF1F"
            block_root.mkdir(parents=True)
            (block_root / "1CDDBF1F.blc").write_bytes(
                nonce + chacha20_apply(CHACHA_KEY, nonce, 1, decrypted)
            )
            (block_root / "11000000000000000000000000000000.chk").write_bytes(b"payload")
            writer = io.StringIO()
            args = argparse.Namespace(
                block_type=[],
                no_crc=False,
                progress_every=0,
            )

            summary = index_source("StreamingAssets", source_root, writer, args, [])

        records = [json.loads(line) for line in writer.getvalue().splitlines()]
        file_record = next(item for item in records if item["recordType"] == "file")
        self.assertEqual(1, summary["selectedFileCount"])
        self.assertEqual(
            "BundleManifest/Data/Bundles/Windows/manifest.hgmmap",
            file_record["logicalId"],
        )
        self.assertTrue(file_record["chunkExists"])
        self.assertEqual("00000000000000000000000000000044", file_record["fileDataMd5"])


if __name__ == "__main__":
    unittest.main()
