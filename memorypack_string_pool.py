"""Read the USPC string blocks used by current MemoryPack resources.

The caller supplies blocks from the same VFS snapshot as the payload. No global
cache or fallback to another version is used. Layout is verified against 1.6.3
StringPoolBlockCodec and RuntimeUniqueStringPool.
"""
import struct
from collections.abc import Callable


def string_pool_options(schema, load_block=None) -> dict:
    """Select the wire format explicitly; never guess from decoded values."""
    encoding = getattr(schema, 'payload', {}).get('stringEncoding', 'inline')
    if encoding == 'inline':
        return {}
    if encoding != 'pooled':
        raise ValueError(f'unsupported MemoryPack string encoding: {encoding}')
    if load_block is None:
        raise ValueError('pooled MemoryPack schema requires a matching VFS string pool')
    return {'string_pool': MemoryPackStringPool(load_block)}


def string_pool_logical_id(index: int) -> str:
    return f'JsonData/Data/Json/StringPool/StringPool{index}.byte'

def decode_pool_lz4(data: bytes, expected_size: int) -> bytes:
    """Native Lz4Codec accepts a final match, unlike strict liblz4 blocks."""
    output = bytearray()
    position = 0

    def length(initial):
        nonlocal position
        result = initial
        if initial == 15:
            while True:
                if position >= len(data):
                    raise ValueError('truncated LZ4 length')
                extra = data[position]
                position += 1
                result += extra
                if extra != 255:
                    break
        return result

    while position < len(data):
        token = data[position]
        position += 1
        literals = length(token >> 4)
        if position + literals > len(data) or len(output) + literals > expected_size:
            raise ValueError('invalid LZ4 literal range')
        output.extend(data[position:position + literals])
        position += literals
        if position == len(data):
            break
        if position + 2 > len(data):
            raise ValueError('truncated LZ4 offset')
        offset = int.from_bytes(data[position:position + 2], 'little')
        position += 2
        count = length(token & 15) + 4
        if offset == 0 or offset > len(output) or len(output) + count > expected_size:
            raise ValueError('invalid LZ4 match range')
        for _ in range(count):
            output.append(output[-offset])
    if len(output) != expected_size:
        raise ValueError('invalid LZ4 decoded length')
    return bytes(output)


class MemoryPackStringPool:
    def __init__(self, load_block: Callable[[int], bytes]):
        self._load_block = load_block
        self._blocks = {}
        first = load_block(0)
        self._file_count = struct.unpack_from('<i', first)[0]
        self._blocks[0] = self._decode(first[4:])
        self._block_size = len(self._blocks[0])
        if self._file_count <= 0 or self._block_size <= 0:
            raise ValueError('invalid string pool dimensions')

    @staticmethod
    def _decode(data: bytes) -> list[str]:
        magic, version, flags, count, raw_size, stored_size = struct.unpack_from('<4s5i', data)
        if magic != b'USPC' or version != 1 or flags not in (0, 1):
            raise ValueError('unsupported string pool header')
        if min(count, raw_size, stored_size) < 0 or len(data) != 24 + stored_size:
            raise ValueError('invalid string pool block length')
        raw = decode_pool_lz4(data[24:], raw_size) if flags else data[24:]
        if len(raw) != raw_size or struct.unpack_from('<i', raw)[0] != count:
            raise ValueError('invalid decoded string pool block')
        start = 4 + count * 4
        if start > len(raw):
            raise ValueError('truncated string pool offsets')
        result = []
        expected_offset = 0
        for index in range(count):
            offset = struct.unpack_from('<i', raw, 4 + index * 4)[0]
            if offset != expected_offset or start + offset + 4 > len(raw):
                raise ValueError('invalid string pool offset')
            length = struct.unpack_from('<i', raw, start + offset)[0]
            if length < 0 or start + offset + 4 + length > len(raw):
                raise ValueError('invalid string pool string length')
            result.append(raw[start + offset + 4:start + offset + 4 + length].decode('utf-8'))
            expected_offset += 4 + length
        if start + expected_offset != len(raw):
            raise ValueError('trailing string pool bytes')
        return result

    def __call__(self, index: int) -> str:
        if index < 0:
            raise ValueError(f'invalid string pool index: {index}')
        block, local = divmod(index, self._block_size)
        if block >= self._file_count:
            raise ValueError(f'string pool index out of bounds: {index}')
        if block not in self._blocks:
            self._blocks[block] = self._decode(self._load_block(block))
        if local >= len(self._blocks[block]):
            raise ValueError(f'string pool index out of bounds: {index}')
        return self._blocks[block][local]
