"""持久化长任务状态；内存仅保存当前进程可取消的控制句柄。"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Callable


TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled"})
INTERRUPTIBLE_STATES = frozenset({"pending", "running", "cancelling"})


class TaskNotFoundError(KeyError):
    pass


class BackgroundTaskRegistry:
    """执行线程只持有控制事件；结果写盘后才由原子状态文件发布。"""

    def __init__(
        self,
        root_provider: Callable[[], Path],
        *,
        retention_seconds: int = 7 * 24 * 60 * 60,
        max_terminal_tasks: int = 512,
    ):
        self._root_provider = root_provider
        self._retention_ms = max(int(retention_seconds), 0) * 1000
        self._max_terminal_tasks = max(int(max_terminal_tasks), 0)
        self._lock = threading.Lock()
        self._active: dict[str, threading.Event] = {}

    def submit(
        self,
        kind: str,
        operation: Callable[[threading.Event], object],
    ) -> dict:
        return self._submit(kind, operation, with_progress=False)

    def submit_with_progress(
        self,
        kind: str,
        operation: Callable[[threading.Event, Callable[[dict], None]], object],
    ) -> dict:
        return self._submit(kind, operation, with_progress=True)

    def _submit(
        self,
        kind: str,
        operation: Callable[..., object],
        *,
        with_progress: bool,
    ) -> dict:
        task_id = uuid.uuid4().hex
        cancel_event = threading.Event()
        created = int(time.time() * 1000)
        record = {
            "taskId": task_id,
            "kind": kind,
            "state": "pending",
            "createdAtEpochMs": created,
            "updatedAtEpochMs": created,
        }
        with self._lock:
            self._cleanup_locked(created)
            self._active[task_id] = cancel_event
            self._write_status(task_id, record)
        thread = threading.Thread(
            target=self._run,
            args=(task_id, operation, cancel_event, with_progress),
            name=f"vfs-task-{task_id}",
            daemon=True,
        )
        thread.start()
        return record

    def snapshot(self, task_id: str, *, include_result: bool = True) -> dict:
        task_id = self._normalize_task_id(task_id)
        with self._lock:
            record = self._read_status(task_id)
            if record["state"] in INTERRUPTIBLE_STATES and task_id not in self._active:
                record = {
                    **record,
                    "state": "failed",
                    "updatedAtEpochMs": int(time.time() * 1000),
                    "error": {
                        "code": "task_interrupted",
                        "message": "Task was interrupted by a previous server process",
                    },
                }
                self._write_status(task_id, record)
        if include_result and record["state"] == "succeeded":
            result_path = self._task_root(task_id) / str(record["resultFile"])
            result = json.loads(result_path.read_text(encoding="utf-8"))
            public_result = (
                {
                    key: value
                    for key, value in result.items()
                    if not str(key).startswith("_")
                }
                if isinstance(result, dict)
                else result
            )
            record = {
                **record,
                "result": public_result,
            }
        return record

    def artifact(self, task_id: str) -> tuple[Path, str, str]:
        task_id = self._normalize_task_id(task_id)
        with self._lock:
            record = self._read_status(task_id)
            if record["state"] != "succeeded":
                raise TaskNotFoundError(task_id)
            result_path = self._task_root(task_id) / str(record["resultFile"])
            result = json.loads(result_path.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            raise TaskNotFoundError(task_id)
        artifact_path = Path(str(result.get("_artifactPath") or ""))
        if not artifact_path.is_file():
            raise TaskNotFoundError(task_id)
        name = str(result.get("_artifactName") or artifact_path.name)
        content_type = str(
            result.get("_artifactContentType") or "application/octet-stream"
        )
        return artifact_path, name, content_type

    def cancel(self, task_id: str) -> dict:
        task_id = self._normalize_task_id(task_id)
        with self._lock:
            record = self._read_status(task_id)
            if record["state"] in TERMINAL_STATES:
                return record
            cancel_event = self._active.get(task_id)
            if cancel_event is None:
                record = {
                    **record,
                    "state": "failed",
                    "updatedAtEpochMs": int(time.time() * 1000),
                    "error": {
                        "code": "task_interrupted",
                        "message": "Task is not owned by the current server process",
                    },
                }
            else:
                cancel_event.set()
                record = {
                    **record,
                    "state": "cancelling",
                    "updatedAtEpochMs": int(time.time() * 1000),
                }
            self._write_status(task_id, record)
            return record

    def cleanup(self) -> int:
        with self._lock:
            return self._cleanup_locked(int(time.time() * 1000))

    def _cleanup_locked(self, now_ms: int) -> int:
        root = self._root_provider().resolve()
        if not root.is_dir():
            return 0
        terminal = []
        for child in root.iterdir():
            if not child.is_dir() or child.is_symlink():
                continue
            try:
                task_id = self._normalize_task_id(child.name)
                if child.resolve().parent != root or task_id in self._active:
                    continue
                record = self._read_status(task_id)
                if record.get("state") in INTERRUPTIBLE_STATES:
                    record = {
                        **record,
                        "state": "failed",
                        "updatedAtEpochMs": now_ms,
                        "error": {
                            "code": "task_interrupted",
                            "message": "Task was interrupted by a previous server process",
                        },
                    }
                    self._write_status(task_id, record)
                if record.get("state") not in TERMINAL_STATES:
                    continue
                updated = int(record.get("updatedAtEpochMs", 0))
            except (TaskNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError):
                continue
            terminal.append((updated, child))

        terminal.sort(key=lambda item: item[0], reverse=True)
        cutoff = now_ms - self._retention_ms
        targets = {
            path
            for index, (updated, path) in enumerate(terminal)
            if updated < cutoff or index >= self._max_terminal_tasks
        }
        removed = 0
        for target in targets:
            try:
                shutil.rmtree(target)
                removed += 1
            except OSError:
                pass
        return removed

    def _run(
        self,
        task_id: str,
        operation: Callable[..., object],
        cancel_event: threading.Event,
        with_progress: bool,
    ) -> None:
        temporary_result = self._task_root(task_id) / ".result.json.tmp"
        try:
            self._set_state(task_id, "running")
            if cancel_event.is_set():
                with self._lock:
                    record = self._read_status(task_id)
                    self._write_status(task_id, {
                        **record,
                        "state": "cancelled",
                        "updatedAtEpochMs": int(time.time() * 1000),
                    })
                return
            def report_progress(progress: dict) -> None:
                with self._lock:
                    current = self._read_status(task_id)
                    if current["state"] not in INTERRUPTIBLE_STATES:
                        return
                    self._write_status(task_id, {
                        **current,
                        "updatedAtEpochMs": int(time.time() * 1000),
                        "progress": dict(progress),
                    })

            result = (
                operation(cancel_event, report_progress)
                if with_progress
                else operation(cancel_event)
            )
            encoded = (json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8")
            temporary_result.parent.mkdir(parents=True, exist_ok=True)
            temporary_result.write_bytes(encoded)
            with self._lock:
                record = self._read_status(task_id)
                if cancel_event.is_set():
                    temporary_result.unlink(missing_ok=True)
                    self._write_status(task_id, {
                        **record,
                        "state": "cancelled",
                        "updatedAtEpochMs": int(time.time() * 1000),
                    })
                else:
                    result_path = self._task_root(task_id) / "result.json"
                    os.replace(temporary_result, result_path)
                    self._write_status(task_id, {
                        **record,
                        "state": "succeeded",
                        "updatedAtEpochMs": int(time.time() * 1000),
                        "resultFile": "result.json",
                    })
        except Exception as error:
            temporary_result.unlink(missing_ok=True)
            with self._lock:
                record = self._read_status(task_id)
                if cancel_event.is_set():
                    record = {
                        **record,
                        "state": "cancelled",
                        "updatedAtEpochMs": int(time.time() * 1000),
                    }
                else:
                    record = {
                        **record,
                        "state": "failed",
                        "updatedAtEpochMs": int(time.time() * 1000),
                        "error": {
                            "code": getattr(error, "code", "task_failed"),
                            "message": str(error),
                        },
                    }
                self._write_status(task_id, record)
        finally:
            with self._lock:
                self._active.pop(task_id, None)

    def _set_state(self, task_id: str, state: str) -> None:
        with self._lock:
            record = self._read_status(task_id)
            if record["state"] == "cancelling":
                return
            self._write_status(task_id, {
                **record,
                "state": state,
                "updatedAtEpochMs": int(time.time() * 1000),
            })

    def _task_root(self, task_id: str) -> Path:
        return self._root_provider() / task_id

    def _status_path(self, task_id: str) -> Path:
        return self._task_root(task_id) / "status.json"

    def _read_status(self, task_id: str) -> dict:
        path = self._status_path(task_id)
        if not path.is_file():
            raise TaskNotFoundError(task_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_status(self, task_id: str, record: dict) -> None:
        path = self._status_path(task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)

    @staticmethod
    def _normalize_task_id(task_id: str) -> str:
        try:
            parsed = uuid.UUID(hex=task_id)
        except (ValueError, AttributeError) as error:
            raise TaskNotFoundError(str(task_id)) from error
        normalized = parsed.hex
        if normalized != task_id:
            raise TaskNotFoundError(task_id)
        return normalized
