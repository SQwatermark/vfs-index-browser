import unittest

from vfs_crypto import CHACHA_KEY, chacha20_apply, decrypt_vfs_file


class VfsCryptoTests(unittest.TestCase):
    def test_chacha20_round_trip_with_vfs_nonce(self):
        payload = bytes(range(251)) * 3
        encrypted = decrypt_vfs_file(payload, 123456789)

        self.assertNotEqual(payload, encrypted)
        self.assertEqual(payload, decrypt_vfs_file(encrypted, 123456789))

    def test_stream_crosses_block_and_counter_boundaries(self):
        nonce = bytes.fromhex("000102030405060708090a0b")
        payload = bytes(range(256)) * 2

        encrypted = chacha20_apply(CHACHA_KEY, nonce, 0xFFFFFFFF, payload)

        self.assertEqual(
            payload,
            chacha20_apply(CHACHA_KEY, nonce, 0xFFFFFFFF, encrypted),
        )

    def test_rejects_invalid_key_and_nonce_lengths(self):
        with self.assertRaisesRegex(ValueError, "key"):
            chacha20_apply(b"short", bytes(12), 1, b"")
        with self.assertRaisesRegex(ValueError, "nonce"):
            chacha20_apply(CHACHA_KEY, b"short", 1, b"")


if __name__ == "__main__":
    unittest.main()
