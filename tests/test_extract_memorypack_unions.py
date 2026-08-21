import struct
import unittest

from tools.extract_memorypack_unions import extract_tag_slots


def rip_load(opcode: bytes, instruction_address: int, target: int) -> bytes:
    return opcode + struct.pack("<i", target - (instruction_address + len(opcode) + 4))


class ExtractMemoryPackUnionsTests(unittest.TestCase):
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
