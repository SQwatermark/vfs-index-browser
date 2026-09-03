from __future__ import annotations

import math
import struct
import unittest

from float32_value import canonical_float32


class CanonicalFloat32Tests(unittest.TestCase):
    def test_shortens_familiar_decimal_without_changing_binary32(self) -> None:
        widened = struct.unpack("<f", struct.pack("<f", 0.05))[0]

        canonical = canonical_float32(widened)

        self.assertEqual(canonical, 0.05)
        self.assertEqual(struct.pack("<f", canonical), struct.pack("<f", widened))

    def test_keeps_enough_digits_for_arbitrary_binary32(self) -> None:
        widened = struct.unpack("<f", bytes.fromhex("79e9f642"))[0]

        canonical = canonical_float32(widened)

        self.assertEqual(struct.pack("<f", canonical), struct.pack("<f", widened))

    def test_preserves_non_finite_values_for_existing_validation(self) -> None:
        self.assertTrue(math.isnan(canonical_float32(float("nan"))))
        self.assertEqual(canonical_float32(float("inf")), float("inf"))

    def test_extreme_normal_subnormal_and_signed_zero_roundtrip(self) -> None:
        for bits in [0, 0x80000000, 1, 0x80000001, 0x007FFFFF, 0x00800000, 0x7F7FFFFF, 0xFF7FFFFF]:
            with self.subTest(bits=hex(bits)):
                encoded = struct.pack("<I", bits)
                value = struct.unpack("<f", encoded)[0]
                self.assertEqual(encoded, struct.pack("<f", canonical_float32(value)))


if __name__ == "__main__":
    unittest.main()
