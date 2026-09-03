import struct
import unittest

from ifix_patch import InjectFixPatchError, parse_injectfix_patch


def _int32(value):
    return struct.pack("<i", value)


def _string(value):
    encoded = value.encode("utf-8")
    length = len(encoded)
    prefix = bytearray()
    while length >= 0x80:
        prefix.append((length & 0x7F) | 0x80)
        length >>= 7
    prefix.append(length)
    return bytes(prefix) + encoded


def _method(type_index, name, parameters=()):
    return (
        b"\x00"
        + _int32(type_index)
        + _string(name)
        + _int32(len(parameters))
        + b"".join(_int32(parameter) for parameter in parameters)
    )


def _fixture_patch():
    types = [
        "Beyond.Gameplay.Core.Buff, Gameplay.Beyond",
        "System.Int32, mscorlib",
    ]
    payload = bytearray()
    payload += struct.pack("<Q", 123456)
    payload += _string("IFix.ILFixInterfaceBridge, Gameplay.Beyond")
    payload += _int32(len(types))
    for type_name in types:
        payload += _string(type_name)
    payload += _int32(0)
    payload += _int32(0)
    payload += _int32(0)
    payload += _int32(0)
    payload += _int32(0)
    payload += _int32(0)
    payload += _string("IFix.WrappersManagerImpl, Gameplay.Beyond")
    payload += _string(", Gameplay.Beyond")
    payload += _int32(1)
    payload += _method(0, "get_maxStackCount", (1,))
    payload += _int32(7)
    payload += _int32(0)
    return b"opaque envelope" + bytes(payload)


class InjectFixPatchTests(unittest.TestCase):
    def test_reads_patched_method_metadata_after_game_envelope(self):
        result = parse_injectfix_patch(_fixture_patch())

        self.assertEqual(len(b"opaque envelope"), result.payload_offset)
        self.assertEqual(1, len(result.patched_methods))
        method = result.patched_methods[0]
        self.assertEqual("Beyond.Gameplay.Core.Buff", method.declaring_type)
        self.assertEqual("get_maxStackCount", method.name)
        self.assertEqual(("System.Int32",), method.parameters)
        self.assertEqual(7, method.vm_method_id)

    def test_rejects_non_patch_bytes(self):
        with self.assertRaisesRegex(InjectFixPatchError, "marker was not found"):
            parse_injectfix_patch(b"not an InjectFix patch")


if __name__ == "__main__":
    unittest.main()
