import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class ServerTaskTests(unittest.TestCase):
    def test_start_projectile_task_uses_detached_service_and_returns_202(self):
        operations = []
        service_instances = []

        class FakeTasks:
            def submit(self, kind, operation):
                operations.append((kind, operation))
                return {"taskId": "a" * 32, "kind": kind, "state": "pending"}

        handler = object.__new__(server.BrowserHandler)
        handler.db_path = Path("index.sqlite")
        handler.read_json_body = lambda: {"projectileId": "projectile_sample"}
        responses = []
        handler.send_json = lambda payload, **options: responses.append((payload, options))
        handler.send_error_json = lambda status, message: self.fail(f"{status}: {message}")

        def build(service, projectile_id, *, cancel_event=None):
            service_instances.append(service)
            return {"projectileId": projectile_id, "cancelEvent": cancel_event is not None}

        with (
            patch.object(server, "TASKS", FakeTasks()),
            patch.object(server.BrowserHandler, "build_projectile_document", build),
        ):
            handler.handle_start_projectile_task()
            result = operations[0][1](threading.Event())

        self.assertEqual("projectile", operations[0][0])
        self.assertIsNot(handler, service_instances[0])
        self.assertEqual(handler.db_path, service_instances[0].db_path)
        self.assertEqual(
            {"projectileId": "projectile_sample", "cancelEvent": True},
            result,
        )
        self.assertEqual(202, responses[0][1]["status"])
        self.assertEqual("no-store", responses[0][1]["cache_control"])

    def test_task_status_and_cancel_map_registry_states(self):
        class FakeTasks:
            def snapshot(self, task_id):
                return {"taskId": task_id, "state": "running"}

            def cancel(self, task_id):
                return {"taskId": task_id, "state": "cancelling"}

        handler = object.__new__(server.BrowserHandler)
        responses = []
        handler.send_json = lambda payload, **options: responses.append((payload, options))
        handler.send_error_json = lambda status, message: self.fail(f"{status}: {message}")
        query = {"taskId": ["b" * 32]}

        with patch.object(server, "TASKS", FakeTasks()):
            handler.handle_task_status(query)
            handler.handle_cancel_task(query)

        self.assertEqual("running", responses[0][0]["state"])
        self.assertEqual(202, responses[1][1]["status"])

    def test_start_model_task_resolves_identity_and_propagates_cancellation(self):
        operations = []
        service_instances = []
        cancel_event = threading.Event()
        resolved = (
            object(),
            {"path": "assets/model.prefab", "asset_index": 11},
            {"id": 7},
            Path("bundle.ab"),
        )

        class FakeTasks:
            def submit_with_progress(self, kind, operation):
                operations.append((kind, operation))
                return {"taskId": "c" * 32, "kind": kind, "state": "pending"}

        handler = object.__new__(server.BrowserHandler)
        handler.db_path = Path("index.sqlite")
        handler.read_json_body = lambda: {
            "manifestId": 3,
            "assetIndex": 11,
            "lod": 0,
        }
        handler.resolve_manifest_asset_source = lambda _query: resolved
        responses = []
        handler.send_json = lambda payload, **options: responses.append((payload, options))
        handler.send_error_json = lambda status, message: self.fail(f"{status}: {message}")

        progress_reports = []

        def build(
            service,
            manifest_id,
            model,
            animation,
            lod,
            *,
            cancel_event=None,
            progress=None,
        ):
            service_instances.append(service)
            self.assertEqual(resolved, model)
            self.assertIsNone(animation)
            self.assertIs(cancel_event, cancel_event_outer)
            progress({"stage": "test", "completed": 1, "total": 1})
            return {"manifestId": manifest_id, "lod": lod}

        cancel_event_outer = cancel_event
        with (
            patch.object(server, "TASKS", FakeTasks()),
            patch.object(server.BrowserHandler, "build_model_preview_result", build),
        ):
            handler.handle_start_model_task()
            result = operations[0][1](cancel_event, progress_reports.append)

        self.assertEqual("model", operations[0][0])
        self.assertIsNot(handler, service_instances[0])
        self.assertEqual(handler.db_path, service_instances[0].db_path)
        self.assertEqual({"manifestId": 3, "lod": 0}, result)
        self.assertEqual([{"stage": "test", "completed": 1, "total": 1}], progress_reports)
        self.assertEqual(202, responses[0][1]["status"])

    def test_model_task_result_passes_cancel_event_into_model_pipeline(self):
        cancel_event = threading.Event()
        observed = []

        class FakeIndex:
            def bundle_dependencies(self, bundle_index):
                self.bundle_index = bundle_index
                return [{"bundleIndex": 2, "name": "dependency.ab"}]

        index = FakeIndex()
        asset = {
            "path": "assets/model.prefab",
            "asset_index": 11,
            "bundle_index": 1,
            "bundle_name": "model.ab",
        }
        record = {"id": 7}
        handler = object.__new__(server.BrowserHandler)
        handler.resolve_bundle_sources = lambda dependencies: ([], dependencies)

        def ensure(
            _record,
            _chunk,
            _asset,
            dependencies,
            dependency_sources,
            missing_dependencies,
            *,
            cancel_event=None,
            progress=None,
        ):
            observed.append(cancel_event)
            self.assertIsNotNone(progress)
            self.assertEqual([], dependency_sources)
            self.assertEqual(dependencies, missing_dependencies)
            return (
                {
                    "nodes": [],
                    "meshes": [],
                    "skins": [],
                    "materials": [],
                    "images": [],
                },
                {"scope": "test", "builtAtEpoch": 1},
            )

        handler.ensure_model_hierarchy = ensure
        progress_reports = []
        with (
            patch.object(server, "BLENDER_EXE", Path("missing-blender.exe")),
            patch.object(server, "BLENDER_MODEL_IMPORTER", Path("missing-importer.py")),
        ):
            result = handler.build_model_preview_result(
                3,
                (index, asset, record, Path("bundle.ab")),
                None,
                0,
                cancel_event=cancel_event,
                progress=progress_reports.append,
            )

        self.assertEqual([cancel_event], observed)
        self.assertEqual([], progress_reports)
        self.assertEqual("modelDocument", result["kind"])
        self.assertEqual("hierarchyOnly", result["status"])
        self.assertEqual("test", result["run"]["scope"])


if __name__ == "__main__":
    unittest.main()
