import sqlite3
import unittest
from pathlib import Path
from unittest.mock import Mock

from tablecfg_service import (
    ResolvedTableCfg,
    TableCfgParseError,
    TableCfgResolutionError,
    TableCfgService,
)
from logical_file_source_service import LogicalFileSourceError


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
        exported = self.service.export(resolved)

        self.assertEqual("Sample", resolved.table_name)
        self.assertEqual({"rows": [1]}, parsed["data"])
        self.assertIn(b'"rows"', data)
        self.assertEqual("Table.json", exported.name)
        self.assertEqual(data, exported.data)
        self.assertEqual(2, self.reader.call_count)
        self.assertEqual(2, self.parser.call_count)

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

    def test_resolves_required_file_id_without_caller_connection(self):
        original = {"id": 7}
        record = {"id": 8, "file_name": "Data/TableCfg/Sample.bytes"}
        chunk = Path("chunk.bin")
        self.sources.resolve_file_id_required.return_value = (
            original,
            record,
            chunk,
        )

        resolved = self.service.resolve_file_id(7)

        self.assertEqual(original, resolved.original)
        self.assertEqual(record, resolved.record)
        self.assertEqual("Sample", resolved.table_name)

        self.sources.resolve_file_id_required.side_effect = LogicalFileSourceError(
            404,
            "file not found",
        )
        with self.assertRaisesRegex(TableCfgResolutionError, "file not found") as raised:
            self.service.resolve_file_id(99)
        self.assertEqual(404, raised.exception.status)

    def test_export_translates_format_error(self):
        self.parser.side_effect = ValueError("invalid SparkBuffer")
        resolved = ResolvedTableCfg(
            {"id": 7},
            {"file_name": "Data/TableCfg/Sample.bytes"},
            Path("chunk.bin"),
            "Sample",
        )

        with self.assertRaisesRegex(TableCfgParseError, "invalid SparkBuffer"):
            self.service.export(resolved)


if __name__ == "__main__":
    unittest.main()
