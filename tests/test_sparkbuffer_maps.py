import struct
import unittest

from sparkbuffer import BeanField, RootDef, SparkReader, SparkType, TypeRegistry, _read_map_value, _read_root_map_value


class SparkBufferMapTests(unittest.TestCase):
    def test_bool_map_aligns_between_entries_without_requiring_trailing_padding(self):
        payload = struct.pack('<i', 2) + bytes(16) + struct.pack('<i', 11) + b'\x01\x00\x00\x00' + struct.pack('<i', 22) + b'\x00'
        for decode, definition in [
            (_read_map_value, BeanField('flags', SparkType.MAP, SparkType.INT, SparkType.BOOL)),
            (_read_root_map_value, RootDef(SparkType.MAP, 'flags', type2=SparkType.INT, type3=SparkType.BOOL)),
        ]:
            with self.subTest(decode=decode.__name__):
                reader = SparkReader(payload)
                self.assertEqual({'11': True, '22': False}, decode(reader, definition, TypeRegistry()))
                self.assertEqual(len(payload), reader.pos)
