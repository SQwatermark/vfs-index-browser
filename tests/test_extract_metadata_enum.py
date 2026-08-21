import unittest

from tools.extract_metadata_enum import (
    infer_integer_encoding,
    normalize_type_name,
    read_enum_integer,
)


class ExtractMetadataEnumTests(unittest.TestCase):
    def test_normalizes_nested_type_separator(self):
        self.assertEqual(
            normalize_type_name("Beyond.Gameplay.Core.AbilitySystem+Event"),
            "Beyond.Gameplay.Core.AbilitySystem.Event",
        )

    def test_infers_integer_encoding_from_member_offsets(self):
        self.assertEqual(infer_integer_encoding([100, 108, 116, 124]), "int64")
        self.assertEqual(infer_integer_encoding([20, 21, 22]), "compressed-int32")

    def test_decodes_signed_int64_values(self):
        self.assertEqual(
            read_enum_integer((1 << 40).to_bytes(8, "little"), 0, "int64"),
            1 << 40,
        )
        self.assertEqual(read_enum_integer(b"\xff" * 8, 0, "int64"), -1)

    def test_decodes_compressed_int32_values(self):
        self.assertEqual(read_enum_integer(bytes([0x02]), 0, "compressed-int32"), 1)
        self.assertEqual(read_enum_integer(bytes([0x01]), 0, "compressed-int32"), -1)

    def test_rejects_ambiguous_single_member_width(self):
        with self.assertRaisesRegex(RuntimeError, "fewer than two"):
            infer_integer_encoding([12])


if __name__ == "__main__":
    unittest.main()
