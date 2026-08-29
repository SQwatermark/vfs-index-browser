"""应用层后台任务查询；隔离 HTTP handler 与持久化注册表细节。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from task_registry import BackgroundTaskRegistry, TaskNotFoundError


@dataclass(frozen=True)
class TaskCancellation:
    snapshot: dict
    http_status: int


@dataclass(frozen=True)
class TaskArtifact:
    path: Path
    name: str
    content_type: str


class TaskApplicationService:
    """为请求层提供稳定任务结果，不暴露注册表的存储异常。"""

    def __init__(self, registry: BackgroundTaskRegistry):
        self._registry = registry

    def snapshot(self, task_id: str) -> dict:
        try:
            return self._registry.snapshot(task_id)
        except (TaskNotFoundError, OSError, json.JSONDecodeError) as error:
            raise TaskNotFoundError(task_id) from error

    def cancel(self, task_id: str) -> TaskCancellation:
        try:
            snapshot = self._registry.cancel(task_id)
        except (TaskNotFoundError, OSError, json.JSONDecodeError) as error:
            raise TaskNotFoundError(task_id) from error
        return TaskCancellation(
            snapshot=snapshot,
            http_status=202 if snapshot.get("state") == "cancelling" else 200,
        )

    def artifact(self, task_id: str) -> TaskArtifact:
        try:
            path, name, content_type = self._registry.artifact(task_id)
        except (TaskNotFoundError, OSError, json.JSONDecodeError) as error:
            raise TaskNotFoundError(task_id) from error
        return TaskArtifact(path=path, name=name, content_type=content_type)
