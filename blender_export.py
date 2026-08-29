"""Cancelable Blender export adapter for model GLB artifacts."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path


MODEL_BLEND_EXPORT_LOCK = threading.Lock()


class BlenderExportService:
    def __init__(
        self,
        executable: Path,
        project_root: Path,
        importer: Path,
        cache_dependencies: tuple[Path, ...],
        *,
        timeout_seconds: float = 300,
    ):
        self._executable = executable
        self._project_root = project_root
        self._importer = importer
        self._cache_dependencies = cache_dependencies
        self._timeout_seconds = timeout_seconds

    def ensure_model_blend(
        self,
        glb_path: Path,
        *,
        cancel_event: threading.Event | None = None,
    ) -> Path:
        blend_path = glb_path.with_suffix(".blend")
        newest_source_mtime = max(
            path.stat().st_mtime_ns
            for path in (glb_path, self._importer, *self._cache_dependencies)
        )
        with MODEL_BLEND_EXPORT_LOCK:
            if (
                blend_path.is_file()
                and blend_path.stat().st_mtime_ns >= newest_source_mtime
            ):
                return blend_path
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("worker_cancelled")
            temporary = blend_path.with_name("model.tmp.blend")
            temporary.unlink(missing_ok=True)
            command = [
                str(self._executable),
                "--background",
                "--factory-startup",
                "--python",
                str(self._importer),
                "--",
                str(glb_path),
                str(temporary),
            ]
            try:
                stdout = self._run(command, cancel_event)
                if not temporary.is_file():
                    raise RuntimeError(
                        "Blender export completed without producing a file: "
                        + stdout[-2000:]
                    )
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError("worker_cancelled")
                os.replace(temporary, blend_path)
            finally:
                temporary.unlink(missing_ok=True)
        return blend_path

    def _run(
        self,
        command: list[str],
        cancel_event: threading.Event | None,
    ) -> str:
        if cancel_event is None:
            result = subprocess.run(
                command,
                cwd=self._project_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout_seconds,
                check=True,
            )
            return getattr(result, "stdout", "") or ""

        process = subprocess.Popen(
            command,
            cwd=self._project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        started = time.monotonic()
        while True:
            try:
                stdout, stderr = process.communicate(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                if cancel_event.is_set():
                    process.terminate()
                    try:
                        process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()
                    raise RuntimeError("worker_cancelled")
                if time.monotonic() - started >= self._timeout_seconds:
                    process.kill()
                    process.communicate()
                    raise subprocess.TimeoutExpired(command, self._timeout_seconds)
        if process.returncode:
            raise subprocess.CalledProcessError(
                process.returncode,
                command,
                output=stdout,
                stderr=stderr,
            )
        return stdout
