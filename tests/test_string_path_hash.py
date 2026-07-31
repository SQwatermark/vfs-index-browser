import struct
import tempfile
import unittest
from pathlib import Path

from string_path_hash import StringPathHashIndex


def build_index(entries):
    strings = bytearray()
    mappings = []
    for path_hash, path in entries:
        encoded = path.encode("utf-16-le")
        offset = len(strings)
        strings.extend(struct.pack("<i", len(encoded)))
        strings.extend(encoded)
        mappings.append(struct.pack("<qii", path_hash, offset, 0))

    count = len(entries)
    string_base = 8 + count * 8 + count * 16
    slots = b"\0" * (count * 8)
    return struct.pack("<II", string_base, count) + slots + b"".join(mappings) + strings


class StringPathHashIndexTests(unittest.TestCase):
    def test_resolves_requested_paths_and_preserves_hash_collisions(self):
        payload = build_index([
            (101, "Assets/Body.asset"),
            (202, "Assets/Face.mat"),
            (101, "Assets/BodyAlias.asset"),
        ])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "StringPathHash.bin"
            path.write_bytes(payload)
            index = StringPathHashIndex(path)

            self.assertEqual(
                ("Assets/Body.asset", "Assets/BodyAlias.asset"),
                index.resolve_one(101),
            )
            self.assertEqual((), index.resolve_one(999))
            self.assertEqual(
                {202: ("Assets/Face.mat",), 999: ()},
                index.resolve_many((202, 999)),
            )

    def test_rejects_layout_with_detached_string_region(self):
        payload = bytearray(build_index([(101, "Assets/Body.asset")]))
        struct.pack_into("<I", payload, 0, 999)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "StringPathHash.bin"
            path.write_bytes(payload)
            with self.assertRaisesRegex(ValueError, "未与字符串区相接"):
                StringPathHashIndex(path).resolve_one(101)

    def test_rejects_nonzero_mapping_padding(self):
        payload = bytearray(build_index([(101, "Assets/Body.asset")]))
        struct.pack_into("<i", payload, 8 + 8 + 12, 7)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "StringPathHash.bin"
            path.write_bytes(payload)
            with self.assertRaisesRegex(ValueError, "保留字段不是 0"):
                StringPathHashIndex(path).resolve_one(101)


if __name__ == "__main__":
    unittest.main()
