"""后台领域任务组装；不依赖 HTTP handler 的请求生命周期。"""

from __future__ import annotations

from typing import Callable, Protocol

from task_service import TaskApplicationService


class TaskBuildService(Protocol):
    def build_projectile_document(self, projectile_id: str, *, cancel_event): ...

    def build_model_task_result(
        self,
        manifest_id: int,
        model,
        animation,
        lod: int,
        *,
        cancel_event,
        progress,
    ): ...

    def build_model_blend_task_result(
        self,
        model,
        animations,
        lod: int,
        *,
        cancel_event,
        progress,
    ): ...

    def build_model_animation_result(
        self,
        model,
        animation,
        lod: int,
        *,
        cancel_event,
        progress,
    ): ...


class BackgroundTaskOperations:
    """将已解析领域输入绑定到独立服务实例和持久化任务。"""

    def __init__(
        self,
        tasks: TaskApplicationService,
        service_factory: Callable[[], TaskBuildService],
    ):
        self._tasks = tasks
        self._service_factory = service_factory

    def start_projectile(self, projectile_id: str) -> dict:
        service = self._service_factory()
        return self._tasks.submit(
            "projectile",
            lambda cancel_event: service.build_projectile_document(
                projectile_id,
                cancel_event=cancel_event,
            ),
        )

    def start_model(
        self,
        manifest_id: int,
        model,
        animation,
        lod: int,
    ) -> dict:
        service = self._service_factory()
        return self._tasks.submit_with_progress(
            "model",
            lambda cancel_event, report_progress: service.build_model_task_result(
                manifest_id,
                model,
                animation,
                lod,
                cancel_event=cancel_event,
                progress=report_progress,
            ),
        )

    def start_model_blend(self, model, animations, lod: int) -> dict:
        service = self._service_factory()
        return self._tasks.submit_with_progress(
            "modelBlend",
            lambda cancel_event, report_progress: service.build_model_blend_task_result(
                model,
                animations,
                lod,
                cancel_event=cancel_event,
                progress=report_progress,
            ),
        )

    def start_model_animation(self, model, animation, lod: int) -> dict:
        service = self._service_factory()
        return self._tasks.submit_with_progress(
            "modelAnimation",
            lambda cancel_event, report_progress: service.build_model_animation_result(
                model,
                animation,
                lod,
                cancel_event=cancel_event,
                progress=report_progress,
            ),
        )
