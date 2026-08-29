import unittest
from unittest.mock import patch

import server


class ServerHealthTests(unittest.TestCase):
    def test_health_reports_worker_and_keeps_optional_tools_non_blocking(self):
        class ReadyWorker:
            def diagnose(self, required_capabilities):
                self.required_capabilities = required_capabilities
                return {"status": "ready", "capabilities": required_capabilities}

        worker = ReadyWorker()
        with (
            patch.object(server, "UNITY_WORKER", worker),
            patch.object(server, "BLENDER_EXE", server.Path("missing-blender.exe")),
            patch.object(server, "VGMSTREAM_CLI", server.Path("missing-vgmstream.exe")),
            patch.object(server, "USM_CONVERT", server.Path("missing-usm.exe")),
            patch.object(server, "FFMPEG", "missing-ffmpeg-command"),
            patch.object(server, "ANIMESTUDIO_CLI", server.Path("missing-animestudio.exe")),
        ):
            document = server.build_health_document()

        self.assertEqual("ready", document["status"])
        self.assertEqual(
            [
                "decodeProjectileComponent",
                "exportMonoBehaviourRaw",
                "exportMonoBehaviourTypeTreeDump",
                "buildAssetMap",
                "buildCabMap",
                "exportObjectSnapshots",
                "exportIdentifiedTextures",
            ],
            worker.required_capabilities,
        )
        self.assertTrue(all(not item["available"] for item in document["optionalTools"]))
        self.assertTrue(document["legacyTools"][0]["requiredByUnmigratedPaths"])

    def test_health_is_degraded_when_worker_is_not_ready(self):
        class BrokenWorker:
            def diagnose(self, _required_capabilities):
                return {"status": "unavailable", "error": {"code": "worker_not_found"}}

        with patch.object(server, "UNITY_WORKER", BrokenWorker()):
            document = server.build_health_document()

        self.assertEqual("degraded", document["status"])

    def test_handler_returns_uncached_health_document(self):
        handler = object.__new__(server.BrowserHandler)
        responses = []
        handler.send_json = lambda payload, **options: responses.append((payload, options))

        with patch.object(server, "build_health_document", return_value={"status": "ready"}):
            handler.handle_health()

        self.assertEqual(
            [({"status": "ready"}, {"cache_control": "no-store"})],
            responses,
        )


if __name__ == "__main__":
    unittest.main()
