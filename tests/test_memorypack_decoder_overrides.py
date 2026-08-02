import struct
import unittest

from tools.decode_memorypack_json import Decoder, MemoryPackReader, SchemaIndex


class MemoryPackDecoderOverrideTests(unittest.TestCase):
    def test_super_armor_blackboard_value_is_int32(self):
        decoder = Decoder(SchemaIndex({"classes": []}))
        reader = MemoryPackReader(struct.pack("<i", 280))

        value = decoder.read_value(
            reader,
            "TSerializeValue",
            "$.value",
            "Beyond.Gameplay.Core.BlackboardSuperArmorValue",
            "value",
        )

        self.assertEqual(280, value)
        self.assertEqual(4, reader.tell())


if __name__ == "__main__":
    unittest.main()
