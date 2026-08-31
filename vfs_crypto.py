"""Endfield VFS ChaCha20 primitives shared by services and offline tools."""

from __future__ import annotations

import struct


CHACHA_KEY = bytes.fromhex(
    "e95b317ac4f828569d23a86bf271dcb53e846fa75c924d671dba8e38f4ca52e1"
)
VFS_PROTO_VERSION = 3


def rotl32(value: int, bits: int) -> int:
    return ((value << bits) & 0xFFFFFFFF) | (value >> (32 - bits))


def quarter_round(state: list[int], a: int, b: int, c: int, d: int) -> None:
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] = rotl32(state[d] ^ state[a], 16)
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] = rotl32(state[b] ^ state[c], 12)
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] = rotl32(state[d] ^ state[a], 8)
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] = rotl32(state[b] ^ state[c], 7)


def chacha20_apply(key: bytes, nonce12: bytes, counter: int, data: bytes) -> bytes:
    if len(key) != 32:
        raise ValueError("ChaCha20 key must be 32 bytes")
    if len(nonce12) != 12:
        raise ValueError("ChaCha20 nonce must be 12 bytes")

    constants = (0x61707865, 0x3320646E, 0x79622D32, 0x6B206574)
    key_words = list(struct.unpack("<8I", key))
    nonce_words = list(struct.unpack("<3I", nonce12))
    output = bytearray(data)
    offset = 0
    block_counter = counter & 0xFFFFFFFF
    nonce0 = nonce_words[0]

    while offset < len(output):
        state = [
            *constants,
            *key_words,
            block_counter,
            nonce0,
            nonce_words[1],
            nonce_words[2],
        ]
        working = state.copy()
        for _ in range(10):
            quarter_round(working, 0, 4, 8, 12)
            quarter_round(working, 1, 5, 9, 13)
            quarter_round(working, 2, 6, 10, 14)
            quarter_round(working, 3, 7, 11, 15)
            quarter_round(working, 0, 5, 10, 15)
            quarter_round(working, 1, 6, 11, 12)
            quarter_round(working, 2, 7, 8, 13)
            quarter_round(working, 3, 4, 9, 14)
        block = struct.pack(
            "<16I",
            *((working[index] + state[index]) & 0xFFFFFFFF for index in range(16)),
        )
        for index, value in enumerate(block[: len(output) - offset]):
            output[offset + index] ^= value
        offset += 64
        block_counter = (block_counter + 1) & 0xFFFFFFFF
        if block_counter == 0:
            nonce0 = (nonce0 + 1) & 0xFFFFFFFF

    return bytes(output)


def decrypt_vfs_file(data: bytes, iv_seed: int) -> bytes:
    nonce = struct.pack("<iq", VFS_PROTO_VERSION, int(iv_seed))
    return chacha20_apply(CHACHA_KEY, nonce, 1, data)
