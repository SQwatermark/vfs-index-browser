import json
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from unity_worker import UnityWorkerClient, UnityWorkerError, UnityWorkerProtocolError


class UnityWorkerClientTests(unittest.TestCase):
    def test_synchronous_worker_uses_hidden_console_flags_on_windows(self):
        observed = {}

        def run(command, **kwargs):
            observed.update(kwargs)
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps({
                    "requestId": None,
                    "ok": True,
                    "result": {
                        "protocol": {"name": "vfs-unity-worker", "version": "1.0.0"},
                        "workerVersion": "test",
                        "capabilities": [],
                    },
                }),
                "",
            )

        UnityWorkerClient(["fake-worker"], runner=run).handshake()

        expected = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        self.assertEqual(expected, observed["creationflags"])

    def test_cancellable_worker_uses_hidden_console_flags_on_windows(self):
        observed = {}
        cancel_event = threading.Event()

        class CompletedProcess:
            returncode = 0

            def communicate(self, timeout=None):
                return json.dumps({"requestId": "hidden-1", "ok": True, "result": {}}), ""

        def start_process(*_args, **kwargs):
            observed.update(kwargs)
            return CompletedProcess()

        UnityWorkerClient(
            ["fake-worker"],
            process_factory=start_process,
        ).request("operation", {}, request_id="hidden-1", cancel_event=cancel_event)

        expected = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        self.assertEqual(expected, observed["creationflags"])

    def test_cancellation_terminates_and_reaps_the_request_process(self):
        cancel_event = threading.Event()

        class BlockingProcess:
            returncode = None
            terminated = False
            killed = False

            def communicate(self, timeout=None):
                if self.terminated or self.killed:
                    self.returncode = -15
                    return "", ""
                cancel_event.set()
                raise subprocess.TimeoutExpired(["fake-worker"], timeout)

            def terminate(self):
                self.terminated = True

            def kill(self):
                self.killed = True

        process = BlockingProcess()
        client = UnityWorkerClient(
            ["fake-worker"],
            process_factory=lambda *_args, **_kwargs: process,
        )

        with self.assertRaises(UnityWorkerError) as caught:
            client.request("operation", {}, request_id="cancel-1", cancel_event=cancel_event)

        self.assertEqual("worker_cancelled", caught.exception.code)
        self.assertTrue(process.terminated)
        self.assertEqual(-15, process.returncode)

    def test_pre_cancelled_request_does_not_start_worker(self):
        cancel_event = threading.Event()
        cancel_event.set()
        started = []
        client = UnityWorkerClient(
            ["fake-worker"],
            process_factory=lambda *_args, **_kwargs: started.append(True),
        )

        with self.assertRaises(UnityWorkerError) as caught:
            client.request("operation", {}, request_id="cancel-2", cancel_event=cancel_event)

        self.assertEqual("worker_cancelled", caught.exception.code)
        self.assertFalse(started)

    def test_diagnose_validates_protocol_and_required_capabilities(self):
        def run(command, **kwargs):
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps({
                    "requestId": None,
                    "ok": True,
                    "result": {
                        "protocol": {"name": "vfs-unity-worker", "version": "1.0.0"},
                        "workerVersion": "0.3.0",
                        "capabilities": ["handshake"],
                    },
                }),
                "",
            )

        client = UnityWorkerClient(["fake-worker"], runner=run)
        diagnostic = client.diagnose(["decodeProjectileComponent"])

        self.assertEqual("incompatible", diagnostic["status"])
        self.assertEqual(
            ["decodeProjectileComponent"],
            diagnostic["missingCapabilities"],
        )

    def test_diagnose_returns_structured_unavailable_state(self):
        def run(command, **kwargs):
            raise FileNotFoundError("missing worker")

        client = UnityWorkerClient(["missing-worker"], runner=run)
        diagnostic = client.diagnose()

        self.assertEqual("unavailable", diagnostic["status"])
        self.assertEqual("worker_not_found", diagnostic["error"]["code"])

    def test_artifact_identity_ignores_non_file_command_parts(self):
        client = UnityWorkerClient(["dotnet", str(Path(__file__))])

        identity = client.artifact_identity()

        self.assertEqual(1, len(identity))
        self.assertEqual(str(Path(__file__).resolve()), identity[0]["path"])

    def test_artifact_identity_changes_when_worker_dependency_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker = root / "Vfs.UnityWorker.exe"
            dependency = root / "Vfs.Endfield.Extensions.dll"
            worker.write_bytes(b"apphost")
            dependency.write_bytes(b"first")
            client = UnityWorkerClient([worker])

            first = client.artifact_identity()
            dependency.write_bytes(b"second-build")
            second = client.artifact_identity()

        self.assertEqual("directoryManifest", first[-1]["kind"])
        self.assertNotEqual(first[-1]["sha256"], second[-1]["sha256"])

    def test_request_writes_versioned_payload_and_returns_result(self):
        observed = {}

        def run(command, **kwargs):
            request = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
            observed.update(request)
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps({"requestId": request["requestId"], "ok": True, "result": {"n": 3}}),
                "",
            )

        client = UnityWorkerClient(["fake-worker"], runner=run)
        result = client.request("operation", {"value": 7}, request_id="request-1")

        self.assertEqual({"n": 3}, result)
        self.assertEqual("1.0.0", observed["protocolVersion"])
        self.assertEqual("operation", observed["operation"])
        self.assertEqual({"value": 7}, observed["arguments"])

    def test_build_cab_map_uses_stable_input_ids_and_resolved_output(self):
        observed = {}

        def run(command, **kwargs):
            request = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
            observed.update(request)
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps({
                    "requestId": request["requestId"],
                    "ok": True,
                    "result": {"entryCount": 1},
                }),
                "",
            )

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            client = UnityWorkerClient(["fake-worker"], runner=run)
            result = client.build_cab_map(
                inputs=[{"inputId": "bundle:7", "inputPath": "staged.ab"}],
                output_directory=output,
                request_id="cab-map-1",
            )

        self.assertEqual({"entryCount": 1}, result)
        self.assertEqual("buildCabMap", observed["operation"])
        self.assertEqual("bundle:7", observed["arguments"]["inputs"][0]["inputId"])
        self.assertTrue(
            Path(observed["arguments"]["inputs"][0]["inputPath"]).is_absolute()
        )
        self.assertTrue(Path(observed["arguments"]["outputDirectory"]).is_absolute())

    def test_export_object_snapshots_uses_cab_map_and_explicit_selection(self):
        observed = {}

        def run(command, **kwargs):
            request = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
            observed.update(request)
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps({
                    "requestId": request["requestId"],
                    "ok": True,
                    "result": {"objectCount": 4},
                }),
                "",
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = UnityWorkerClient(["fake-worker"], runner=run).export_object_snapshots(
                inputs=[
                    {"inputId": "bundle:primary", "inputPath": str(root / "entry.ab")},
                    {"inputId": "bundle:dependency", "inputPath": str(root / "dep.ab")},
                ],
                cab_map_path=root / "cab-map.json",
                primary_input_id="bundle:primary",
                selection_input_ids=["bundle:primary", "bundle:dependency"],
                included_types=["GameObject", "Transform"],
                containers=["assets/characters/test.prefab"],
                output_directory=root / "objects",
                request_id="object-snapshot-1",
            )

        self.assertEqual({"objectCount": 4}, result)
        self.assertEqual("exportObjectSnapshots", observed["operation"])
        arguments = observed["arguments"]
        self.assertEqual("bundle:primary", arguments["primaryInputId"])
        self.assertEqual(
            ["bundle:primary", "bundle:dependency"],
            arguments["selectionInputIds"],
        )
        self.assertEqual(["GameObject", "Transform"], arguments["includedTypes"])
        self.assertEqual(["assets/characters/test.prefab"], arguments["containers"])
        self.assertTrue(Path(arguments["cabMapPath"]).is_absolute())
        self.assertTrue(Path(arguments["inputs"][1]["inputPath"]).is_absolute())

    def test_translates_structured_worker_error(self):
        def run(command, **kwargs):
            return subprocess.CompletedProcess(
                command,
                2,
                json.dumps(
                    {
                        "requestId": "request-2",
                        "ok": False,
                        "error": {"code": "bad_data", "message": "broken", "retryable": False},
                    }
                ),
                "",
            )

        client = UnityWorkerClient(["fake-worker"], runner=run)
        with self.assertRaises(UnityWorkerError) as caught:
            client.request("operation", {}, request_id="request-2")
        self.assertEqual("bad_data", caught.exception.code)
        self.assertFalse(caught.exception.retryable)

    def test_rejects_invalid_json_even_when_process_exits_zero(self):
        def run(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, "not-json", "diagnostic")

        client = UnityWorkerClient(["fake-worker"], runner=run)
        with self.assertRaises(UnityWorkerProtocolError) as caught:
            client.handshake()
        self.assertEqual("invalid_worker_response", caught.exception.code)

    def test_rejects_mismatched_request_identity(self):
        def run(command, **kwargs):
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps({"requestId": "different", "ok": True, "result": {}}),
                "",
            )

        client = UnityWorkerClient(["fake-worker"], runner=run)
        with self.assertRaises(UnityWorkerProtocolError) as caught:
            client.request("operation", {}, request_id="expected")
        self.assertEqual("request_id_mismatch", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
