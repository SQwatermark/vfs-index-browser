import unittest
from pathlib import Path

from memorypack_value_decoder import (
    MemoryPackValueDecodeError,
    MemoryPackValueDecoder,
)


class Reader:
    def __init__(self, data):
        self.data = data
        self.position = 0

    def tell(self):
        return self.position


class Decoder:
    def __init__(self, _schema, *, union_map):
        self.discovered_unions = {"Base": {2: "Derived"}}

    def decode(self, reader, class_name):
        reader.position = 2
        return {"class": class_name}


class MemoryPackValueDecoderTests(unittest.TestCase):
    def build(self, *, infer=lambda _logical_id: "Sample", decoder=Decoder):
        return MemoryPackValueDecoder(
            infer,
            lambda: (object(), {}),
            lambda _record, _chunk: b"abc",
            Reader,
            decoder,
            None,
        )

    def test_preserves_consumption_and_discovered_union_diagnostics(self):
        decoded = self.build().decode("sample.json", {}, Path("sample.json"))

        self.assertEqual("Sample", decoded.class_name)
        self.assertEqual({"class": "Sample"}, decoded.value)
        self.assertEqual(3, decoded.byte_count)
        self.assertEqual(2, decoded.consumed)
        self.assertFalse(decoded.complete)
        self.assertEqual({"Base": {2: "Derived"}}, decoded.discovered_unions)

    def test_unknown_class_returns_none_without_loading_inputs(self):
        calls = []
        decoder = MemoryPackValueDecoder(
            lambda _logical_id: None,
            lambda: calls.append("inputs"),
            lambda *_args: calls.append("file"),
            Reader,
            Decoder,
            None,
        )
        self.assertIsNone(decoder.decode("unknown.json", {}, Path("unknown")))
        self.assertEqual([], calls)

    def test_decoder_value_error_has_stable_boundary(self):
        class FailingDecoder(Decoder):
            def decode(self, reader, class_name):
                raise ValueError("invalid payload")

        with self.assertRaisesRegex(MemoryPackValueDecodeError, "invalid payload"):
            self.build(decoder=FailingDecoder).decode(
                "sample.json", {}, Path("sample.json")
            )


if __name__ == "__main__":
    unittest.main()
