import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from memorypack_schema_service import MemoryPackSchemaService


class MemoryPackSchemaServiceTests(unittest.TestCase):
    def test_loads_each_input_once_and_reuses_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            schema = object()
            schema_type = Mock()
            schema_type.load.return_value = schema
            union_map = {"Base": {1: "Derived"}}
            union_loader = Mock(return_value=union_map)
            service = MemoryPackSchemaService(
                root / "schema.json",
                root / "unions.json",
                schema_type,
                union_loader,
            )

            first = service.load()
            second = service.load()

        self.assertIs(schema, first[0])
        self.assertIs(union_map, first[1])
        self.assertEqual(first, second)
        schema_type.load.assert_called_once()
        union_loader.assert_called_once()

    def test_unavailable_decoder_has_stable_error(self):
        service = MemoryPackSchemaService(
            Path("schema"), Path("unions"), None, Mock()
        )
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, "module is unavailable"):
                service.load()

    def test_load_failure_is_cached_without_retrying_partial_state(self):
        schema_type = Mock()
        schema_type.load.side_effect = ValueError("invalid schema")
        service = MemoryPackSchemaService(
            Path("schema"), Path("unions"), schema_type, Mock()
        )

        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, "invalid schema"):
                service.load()

        schema_type.load.assert_called_once()


if __name__ == "__main__":
    unittest.main()
