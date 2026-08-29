import threading
import unittest

from task_operations import BackgroundTaskOperations


class ImmediateTasks:
    def __init__(self):
        self.observed = []

    def submit(self, kind, operation):
        result = operation(threading.Event())
        self.observed.append((kind, result, []))
        return {"kind": kind, "state": "pending"}

    def submit_with_progress(self, kind, operation):
        reports = []
        result = operation(threading.Event(), reports.append)
        self.observed.append((kind, result, reports))
        return {"kind": kind, "state": "pending"}


class BuildService:
    def build_projectile_document(self, projectile_id, *, cancel_event):
        return {"projectileId": projectile_id, "cancel": cancel_event is not None}

    def build_model_task_result(
        self, manifest_id, model, animation, lod, *, cancel_event, progress
    ):
        progress({"stage": "model"})
        return [manifest_id, model, animation, lod, cancel_event is not None]

    def build_model_blend_task_result(
        self, model, animations, lod, *, cancel_event, progress
    ):
        progress({"stage": "blend"})
        return [model, animations, lod, cancel_event is not None]

    def build_model_animation_result(
        self, model, animation, lod, *, cancel_event, progress
    ):
        progress({"stage": "animation"})
        return [model, animation, lod, cancel_event is not None]


class BackgroundTaskOperationsTests(unittest.TestCase):
    def test_binds_all_task_kinds_to_detached_services(self):
        tasks = ImmediateTasks()
        services = []

        def create_service():
            service = BuildService()
            services.append(service)
            return service

        operations = BackgroundTaskOperations(tasks, create_service)

        operations.start_projectile("projectile_sample")
        operations.start_model(3, "model", "animation", 1)
        operations.start_model_blend("model", ("a", "b"), 2)
        operations.start_model_animation("model", "animation", 0)

        self.assertEqual(4, len(services))
        self.assertEqual(
            ["projectile", "model", "modelBlend", "modelAnimation"],
            [value[0] for value in tasks.observed],
        )
        self.assertEqual({"stage": "model"}, tasks.observed[1][2][0])
        self.assertEqual({"stage": "blend"}, tasks.observed[2][2][0])
        self.assertEqual({"stage": "animation"}, tasks.observed[3][2][0])
