import struct
import unittest

from tools.extract_memorypack_unions import (
    extract_tag_slots, read_metadata_wrapper_types, recover_encoded_mapping,
)
from tools.extract_memorypack_schema import include_union_roots


def rip_load(opcode: bytes, instruction_address: int, target: int) -> bytes:
    return opcode + struct.pack("<i", target - (instruction_address + len(opcode) + 4))


class ExtractMemoryPackUnionsTests(unittest.TestCase):
    def test_schema_roots_include_union_variants_stably_without_duplicates(self):
        self.assertEqual(["Root", "A", "B"], include_union_roots(
            ["Root", "A"], {"Base": {"173": "B", "0": "A"}, "Other": {"1": "B"}},
        ))
        for malformed in ([], {"Base": []}, {"Base": {"0": None}}, {"Base": {"0": ""}}):
            with self.subTest(malformed=malformed), self.assertRaises(SystemExit):
                include_union_roots(["Root"], malformed)

    def test_recovers_encoded_type_index_not_wrapper_ordinal(self):
        image = struct.pack("<QQ", 0x40000001 | (19 << 1), 0x40000001 | (7 << 1))
        self.assertEqual(
            {173: "Example.Finish", 0: "Example.Bone"},
            recover_encoded_mapping(image, {173: 0, 0: 8}, {19: "Example.Finish", 7: "Example.Bone"}),
        )

    def test_rejects_non_type_tokens_and_unknown_wrapper_indices(self):
        for encoded in (0, 0x20000001, 0x60000001, 0x40000000, 0x140000001, 0x40000005):
            with self.subTest(encoded=encoded), self.assertRaises(SystemExit):
                recover_encoded_mapping(struct.pack("<Q", encoded), {1: 0}, {0: "Example.Type"})

    @staticmethod
    def metadata_fixture(stride=92, token=0x02000001):
        strings = b"\0Example_WrapperForMemoryPack\0"
        data = bytearray(264 + len(strings) + stride)
        struct.pack_into("<II", data, 0, 0xFAB11BAF, 29)
        struct.pack_into("<II", data, 24, 264, len(strings))
        struct.pack_into("<II", data, 160, 264 + len(strings), stride)
        data[264:264 + len(strings)] = strings
        struct.pack_into("<IIi", data, 264 + len(strings), 1, 0, 19)
        struct.pack_into("<I", data, len(data) - 4, token)
        lines = [
            "CLASS: Example_WrapperForMemoryPack", "TYPE: class", "TOKEN: 0x2000001",
            "FIELDS:", "  private Example.RealType __realInstance  // 0x18", "END_CLASS",
        ]
        return data, lines

    def test_metadata_stride_is_calibrated_and_wrapper_token_is_checked(self):
        for stride in (88, 92):
            with self.subTest(stride=stride):
                metadata, lines = self.metadata_fixture(stride)
                self.assertEqual({19: "Example.RealType"}, read_metadata_wrapper_types(metadata, lines, stride))
        metadata, lines = self.metadata_fixture(token=0x02000002)
        with self.assertRaisesRegex(SystemExit, "token mismatch"):
            read_metadata_wrapper_types(metadata, lines, 92)

    def test_rejects_truncated_wrong_version_or_wrong_stride_metadata(self):
        original, lines = self.metadata_fixture()
        bad_version = bytearray(original)
        struct.pack_into("<I", bad_version, 4, 31)
        bad_name = bytearray(original)
        struct.pack_into("<I", bad_name, len(bad_name) - 92, 0xFFFFFFFF)
        for data, stride in ((original[:100],92), (original[:-1],92), (bad_version,92),
                             (original,88), (original,84), (bad_name,92)):
            with self.subTest(length=len(data), stride=stride), self.assertRaises(SystemExit):
                read_metadata_wrapper_types(data, lines, stride)

    def test_extracts_zero_normal_and_reordered_tags_within_function_boundary(self):
        image = bytearray(0x300)
        code = bytearray()

        def emit(raw: bytes) -> None:
            code.extend(raw)

        def emit_rip_load(opcode: bytes, target: int) -> None:
            emit(rip_load(opcode, len(code), target))

        # tag 0: the type slot is cached through rbx, while r8d is zeroed.
        emit_rip_load(b"\x48\x8b\x1d", 0x200)  # mov rbx, [rip + slot]
        emit(b"\xe8\x00\x00\x00\x00")
        emit_rip_load(b"\x4c\x8b\x0d", 0x280)  # mov r9, [rip + method]
        emit(b"\x45\x31\xc0")  # xor r8d, r8d
        emit(b"\x48\x89\xda\x48\x89\xf9\xe8\x00\x00\x00\x00")

        # tag 1: type construction precedes the regular immediate tag.
        emit_rip_load(b"\x48\x8b\x0d", 0x208)  # mov rcx, [rip + slot]
        emit(b"\x31\xd2\xe8\x00\x00\x00\x00")
        emit_rip_load(b"\x4c\x8b\x0d", 0x288)
        emit(b"\x41\xb8\x01\x00\x00\x00")  # mov r8d, 1
        emit(b"\x48\x89\xc2\x48\x89\xf9\xe8\x00\x00\x00\x00")

        # tag 2: the compiler is free to load the tag before r9.
        emit_rip_load(b"\x48\x8b\x0d", 0x210)
        emit(b"\x31\xd2\xe8\x00\x00\x00\x00")
        emit(b"\x41\xb8\x02\x00\x00\x00")  # mov r8d, 2
        emit_rip_load(b"\x4c\x8b\x0d", 0x290)
        emit(b"\x48\x89\xc2\x48\x89\xf9\xe8\x00\x00\x00\x00")
        emit(b"\xeb\x00")  # tail jump: adjacent function bytes must be ignored
        emit_rip_load(b"\x48\x8b\x0d", 0x218)
        emit(b"\x41\xb8\xfc\xff\xff\xff")

        image[: len(code)] = code

        self.assertEqual({0: 0x200, 1: 0x208, 2: 0x210}, extract_tag_slots(bytes(image), 0, len(image)))


if __name__ == "__main__":
    unittest.main()
