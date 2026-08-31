import sqlite3
import unittest
from pathlib import Path
from unittest.mock import Mock

from tablecfg_service import TableCfgResolutionError, TableCfgService


class TableCfgServiceTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.addCleanup(self.connection.close)
        self.sources = Mock()
        self.reader = Mock(return_value=b"spark")
        self.parser = Mock(return_value={"name": "Table", "data": {"rows": [1]}})
        self.service = TableCfgService(
            self.sources,
            self.reader,
            self.parser,
            lambda name: Path(name).stem if name.startswith("Data/TableCfg/") else None,
        )

    def test_resolves_source_and_parses_json_bytes(self):
        original = {"id": 7}
        record = {"id": 8, "file_name": "Data/TableCfg/Sample.bytes"}
        chunk = Path("chunk.bin")
        self.sources.find_record.return_value = original
        self.sources.resolve_record.return_value = (record, chunk)

        resolved = self.service.resolve(self.connection, 7)
        parsed, data = self.service.parse(resolved.record, resolved.chunk_path)

        self.assertEqual("Sample", resolved.table_name)
        self.assertEqual({"rows": [1]}, parsed["data"])
        self.assertIn(b'"rows"', data)
        self.reader.assert_called_once_with(record, chunk)
        self.parser.assert_called_once_with(b"spark")

    def test_rejects_missing_or_non_table_record_with_stable_status(self):
        self.sources.find_record.return_value = None
        with self.assertRaises(TableCfgResolutionError) as missing:
            self.service.resolve(self.connection, 7)
        self.assertEqual(404, missing.exception.status)

        self.sources.find_record.return_value = {"id": 7}
        self.sources.resolve_record.return_value = (
            {"id": 8, "file_name": "Data/Other/Sample.bytes"},
            Path("chunk.bin"),
        )
        with self.assertRaises(TableCfgResolutionError) as wrong_type:
            self.service.resolve(self.connection, 7)
        self.assertEqual(400, wrong_type.exception.status)

    def test_unreadable_source_is_not_found(self):
        self.sources.find_record.return_value = {"id": 7}
        self.sources.resolve_record.return_value = None
        with self.assertRaises(TableCfgResolutionError) as raised:
            self.service.resolve(self.connection, 7)
        self.assertEqual(404, raised.exception.status)


if __name__ == "__main__":
    unittest.main()
