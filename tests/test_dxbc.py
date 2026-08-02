import struct
import unittest

from dxbc import DxbcError, inspect_dxbc


def build_dxbc(*, stage: int, major: int = 5, minor: int = 0) -> bytes:
    version = (stage << 16) | (major << 4) | minor
    bytecode = struct.pack("<II", version, 2)
    chunk = b"SHEX" + struct.pack("<I", len(bytecode)) + bytecode
    total_size = 36 + len(chunk)
    header = (
        b"DXBC"
        + bytes(16)
        + struct.pack("<III", 1, total_size, 1)
        + struct.pack("<I", 36)
    )
    return header + chunk


class DxbcTests(unittest.TestCase):
    def test_reads_vertex_shader_model(self):
        inspection = inspect_dxbc(build_dxbc(stage=1))

        self.assertEqual("vertex", inspection.stage)
        self.assertEqual("5_0", inspection.shader_model)
        self.assertEqual(("SHEX",), tuple(chunk.fourcc for chunk in inspection.chunks))

    def test_reads_pixel_shader_model(self):
        inspection = inspect_dxbc(build_dxbc(stage=0, major=4, minor=1))

        self.assertEqual("pixel", inspection.stage)
        self.assertEqual("4_1", inspection.shader_model)

    def test_rejects_inconsistent_bytecode_token_count(self):
        data = bytearray(build_dxbc(stage=0))
        struct.pack_into("<I", data, 48, 3)

        with self.assertRaisesRegex(DxbcError, "token count"):
            inspect_dxbc(bytes(data))


if __name__ == "__main__":
    unittest.main()
