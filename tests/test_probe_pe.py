import unittest
import struct

from tools.inspect_probe_pe import (
    IMAGE_SCN_MEM_EXECUTE,
    is_executable,
    parse_rva,
    scan_image_pointers,
)


class ProbePeTests(unittest.TestCase):
    def test_parses_decimal_and_hex_rvas(self):
        self.assertEqual(0x06CA72BC, parse_rva("0x06CA72BC"))
        self.assertEqual(1234, parse_rva(1234))

    def test_identifies_executable_sections(self):
        self.assertTrue(is_executable(IMAGE_SCN_MEM_EXECUTE | 0x40000000))
        self.assertFalse(is_executable(0x40000000))

    def test_scans_aligned_pointers_inside_image(self):
        data = struct.pack("<QQQ", 0x180001000, 3, 0x1800FFFFF)

        pointers = list(scan_image_pointers(data, 0x180000000, 0x10000))

        self.assertEqual(
            [{"offset": 0, "value": 0x180001000, "rva": 0x1000}], pointers
        )


if __name__ == "__main__":
    unittest.main()
