import unittest
from pathlib import Path
from types import SimpleNamespace

import server
from memorypack_preview_service import MemoryPackJsonExport


class ServerMemoryPackExportTests(unittest.TestCase):
    def handler(self):
        handler = object.__new__(server.BrowserHandler)
        handler.file_id_from_query = lambda _query: 7
        handler.resolve_required_file_source = lambda _file_id: (
            {"id": 7},
            {"id": 7, "file_name": "Data/Skill/sample.json"},
            Path("chunk.chk"),
        )
        return handler

    def test_returns_full_json_with_stable_file_response(self):
        handler = self.handler()
        handler.memorypack_preview_service = lambda: SimpleNamespace(
            export=lambda _record, _path: MemoryPackJsonExport(
                b'{"value":true}',
                {"complete": True},
            )
        )
        responses = []
        handler.send_raw_file = responses.append

        handler.handle_memorypack_json({"id": ["7"], "download": ["1"]})

        self.assertEqual(1, len(responses))
        response = responses[0]
        self.assertEqual("application/json; charset=utf-8", response.content_type)
        self.assertEqual(b'{"value":true}', b"".join(response.chunks()))
        self.assertEqual(
            "attachment; filename*=UTF-8''sample.json",
            response.content_disposition,
        )

    def test_reports_missing_schema_as_unprocessable(self):
        handler = self.handler()
        handler.memorypack_preview_service = lambda: SimpleNamespace(
            export=lambda _record, _path: None
        )
        errors = []
        handler.send_error_json = lambda status, message: errors.append((status, message))

        handler.handle_memorypack_json({"id": ["7"]})

        self.assertEqual(
            [(422, "MemoryPack schema is unavailable for this file")],
            errors,
        )

    def test_reports_decode_failure_as_unprocessable(self):
        handler = self.handler()

        def fail(_record, _path):
            raise RuntimeError("union tag 9 is unknown")

        handler.memorypack_preview_service = lambda: SimpleNamespace(export=fail)
        errors = []
        handler.send_error_json = lambda status, message: errors.append((status, message))

        handler.handle_memorypack_json({"id": ["7"]})

        self.assertEqual(
            [(422, "MemoryPack decode failed: union tag 9 is unknown")],
            errors,
        )


if __name__ == "__main__":
    unittest.main()
