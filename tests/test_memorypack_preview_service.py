import unittest
from pathlib import Path

from memorypack_preview_service import MemoryPackPreviewService
from memorypack_value_decoder import (
    DecodedMemoryPackValue,
    MemoryPackValueDecodeError,
)


class MemoryPackPreviewServiceTests(unittest.TestCase):
    def test_formats_metadata_value_and_sorted_union_tags(self):
        decoded = DecodedMemoryPackValue(
            class_name="ExampleConfig",
            value={"name": "测试"},
            byte_count=12,
            consumed=12,
            discovered_unions={"Effect": {2: "Second", 1: "First"}},
        )
        service = MemoryPackPreviewService(
            lambda logical_id, record, path: decoded
        )

        text, truncated, meta = service.build(
            {"logical_id": "Data/Example.json"},
            Path("chunk.chk"),
        )

        self.assertFalse(truncated)
        self.assertIn('"name": "测试"', text)
        self.assertEqual("ExampleConfig", meta["class"])
        self.assertTrue(meta["complete"])
        self.assertEqual(
            {"1": "First", "2": "Second"},
            meta["discoveredUnions"]["Effect"],
        )

    def test_translates_decoder_error_for_preview_fallback(self):
        def fail(*_args):
            raise MemoryPackValueDecodeError("schema mismatch")

        with self.assertRaisesRegex(RuntimeError, "schema mismatch"):
            MemoryPackPreviewService(fail).build({}, Path("chunk.chk"))


if __name__ == "__main__":
    unittest.main()
