import struct
import unittest

from memorypack_string_pool import MemoryPackStringPool, decode_pool_lz4, string_pool_options
from tools.decode_memorypack_json import MemoryPackReader, SchemaIndex


def block(strings):
    values = [value.encode('utf-8') for value in strings]
    offsets = []
    payload = bytearray()
    for value in values:
        offsets.append(len(payload))
        payload.extend(struct.pack('<i', len(value)) + value)
    raw = struct.pack('<i', len(values)) + b''.join(struct.pack('<i', offset) for offset in offsets) + payload
    return struct.pack('<4s5i', b'USPC', 1, 0, len(values), len(raw), len(raw)) + raw


class MemoryPackStringPoolTests(unittest.TestCase):
    def test_schema_requires_pool_without_guessing_or_falling_back(self):
        schema = SchemaIndex({'classes': [], 'stringEncoding': 'pooled'})
        with self.assertRaisesRegex(ValueError, 'requires a matching VFS'):
            string_pool_options(schema)
        options = string_pool_options(schema, lambda _: struct.pack('<i', 1) + block(['key']))
        self.assertEqual('key', MemoryPackReader(struct.pack('<i', 0), **options).read_string())
        self.assertEqual({}, string_pool_options(SchemaIndex({'classes': []})))

    def test_cross_block_indices_and_native_null_empty_sentinels(self):
        blocks = [struct.pack('<i', 2) + block(['duration', '伤害']), block(['last'])]
        pool = MemoryPackStringPool(blocks.__getitem__)
        reader = MemoryPackReader(struct.pack('<5i', 0, 1, 2, -1, -2), string_pool=pool)
        self.assertEqual(['duration', '伤害', 'last', '', None], [reader.read_string() for _ in range(5)])
        with self.assertRaises(ValueError):
            pool(3)
        with self.assertRaises(ValueError):
            pool(-3)

    def test_native_lz4_final_overlap_match_and_invalid_ranges(self):
        self.assertEqual(b'aaaaa', decode_pool_lz4(b'\x10a\x01\x00', 5))
        for data, size in [(b'\x10a\x00\x00', 5), (b'\x10a\x02\x00', 5), (b'\xf0', 1), (b'\x10a\x01\x00', 4)]:
            with self.subTest(data=data, size=size), self.assertRaises(ValueError):
                decode_pool_lz4(data, size)


if __name__ == '__main__':
    unittest.main()
