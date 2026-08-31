import unittest
from types import SimpleNamespace

import server
from tablecfg_service import TableCfgJsonExport, TableCfgParseError


class ServerTableCfgExportTests(unittest.TestCase):
    def handler(self):
        handler = object.__new__(server.BrowserHandler)
        handler.file_id_from_query = lambda _query: 7
        return handler

    def test_returns_service_export_with_download_contract(self):
        handler = self.handler()
        service = SimpleNamespace(
            resolve_file_id=lambda file_id: SimpleNamespace(file_id=file_id),
            export=lambda _resolved: TableCfgJsonExport(
                b'{"rows":[1]}',
                "AudioDialog.json",
            ),
        )
        handler.tablecfg_service = lambda: service
        responses = []
        handler.send_raw_file = responses.append

        handler.handle_tablecfg_json({"id": ["7"], "download": ["1"]})

        self.assertEqual(1, len(responses))
        response = responses[0]
        self.assertEqual(b'{"rows":[1]}', b"".join(response.chunks()))
        self.assertEqual(
            "attachment; filename*=UTF-8''AudioDialog.json",
            response.content_disposition,
        )

    def test_maps_format_failure_without_interpreting_it(self):
        handler = self.handler()

        def fail(_resolved):
            raise TableCfgParseError("invalid member header")

        handler.tablecfg_service = lambda: SimpleNamespace(
            resolve_file_id=lambda _file_id: object(),
            export=fail,
        )
        errors = []
        handler.send_error_json = lambda status, message: errors.append((status, message))

        handler.handle_tablecfg_json({"id": ["7"]})

        self.assertEqual(
            [(422, "SparkBuffer parse failed: invalid member header")],
            errors,
        )


if __name__ == "__main__":
    unittest.main()
