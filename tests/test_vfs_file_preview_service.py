import unittest
from pathlib import Path

from vfs_file_preview_service import VfsFilePreviewService


class VfsFilePreviewServiceTests(unittest.TestCase):
    def record(self, name, length=20, file_id=2):
        return {"id": file_id, "file_name": name, "length": length}

    def service(self, payload=b"data", table=None, memorypack=None):
        return VfsFilePreviewService(
            lambda _record, _path, limit: payload[:limit],
            table or (lambda *_args: self.fail("unexpected TableCfg parse")),
            memorypack or (lambda *_args: None),
        )

    def test_container_keeps_original_and_fallback_identity(self):
        result = self.service().build(
            1,
            self.record("audio.pck", file_id=1),
            self.record("audio.pck", file_id=2),
            Path("chunk.chk"),
        )

        self.assertEqual("container", result["kind"])
        self.assertTrue(result["usedFallback"])
        self.assertEqual("/api/raw?id=1&download=1", result["downloadUrl"])

    def test_tablecfg_success_and_failure_keep_diagnostics(self):
        success = self.service(
            table=lambda *_args: ({"name": "AudioDialog"}, b'{"1":{}}')
        ).build(
            1,
            self.record("Data/TableCfg/AudioDialog.bytes", 100, 1),
            self.record("Data/TableCfg/AudioDialog.bytes", 100, 1),
            Path("chunk.chk"),
        )
        failure = self.service(
            payload=b"bad",
            table=lambda *_args: (_ for _ in ()).throw(ValueError("invalid table")),
        ).build(
            1,
            self.record("Data/TableCfg/AudioDialog.bytes", 100, 1),
            self.record("Data/TableCfg/AudioDialog.bytes", 100, 1),
            Path("chunk.chk"),
        )

        self.assertEqual("sparkbuffer-json", success["encoding"])
        self.assertEqual("AudioDialog", success["tableCfg"]["rootName"])
        self.assertEqual("hex", failure["kind"])
        self.assertIn("invalid table", failure["message"])

    def test_memorypack_success_precedes_binary_json_probe(self):
        meta = {"consumed": 3, "bytes": 3}
        result = self.service(
            payload=b"\x01\x02\x03",
            memorypack=lambda *_args: ("{}", False, meta),
        ).build(
            1,
            self.record("Data/example.json", 3, 1),
            self.record("Data/example.json", 3, 1),
            Path("chunk.chk"),
        )

        self.assertEqual("memorypack-json", result["encoding"])
        self.assertEqual(meta, result["memoryPack"])
        self.assertEqual("/api/memorypack/json?id=1", result["convertedRawUrl"])
        self.assertEqual(
            "/api/memorypack/json?id=1&download=1",
            result["convertedDownloadUrl"],
        )

    def test_failed_memorypack_keeps_binary_probe_and_error(self):
        result = self.service(
            payload=b"\x01\x04\x00\x00\x00test",
            memorypack=lambda *_args: (_ for _ in ()).throw(RuntimeError("no schema")),
        ).build(
            1,
            self.record("Data/example.json", 9, 1),
            self.record("Data/example.json", 9, 1),
            Path("chunk.chk"),
        )

        self.assertEqual("binaryJson", result["kind"])
        self.assertEqual("test", result["probe"]["lengthPrefixedStrings"][0]["text"])
        self.assertIn("no schema", result["message"])


if __name__ == "__main__":
    unittest.main()
