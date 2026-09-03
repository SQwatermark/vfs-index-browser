"""VFS 自有 Unity worker 的唯一 Python 进程适配器。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Mapping, Sequence


PROTOCOL_VERSION = "1.0.0"
PROTOCOL_NAME = "vfs-unity-worker"


def worker_creation_flags() -> int:
    """Keep per-request console workers invisible on Windows."""

    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


class UnityWorkerError(RuntimeError):
    """worker 返回的稳定结构化错误。"""

    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class UnityWorkerProtocolError(UnityWorkerError):
    """worker 进程没有遵守 VFS 协议。"""


def default_worker_command(project_root: Path) -> tuple[str, ...]:
    """返回发布包优先、仓库构建输出次之的确定性命令，不扫描相邻项目。"""

    configured = os.environ.get("VFS_BROWSER_UNITY_WORKER")
    if configured:
        path = Path(configured)
    else:
        candidates = (
            project_root / "unity-worker" / "artifacts" / "Vfs.UnityWorker.exe",
            project_root
            / "unity-worker"
            / "src"
            / "Vfs.UnityWorker"
            / "bin"
            / "Release"
            / "net9.0-windows"
            / "Vfs.UnityWorker.exe",
            project_root
            / "unity-worker"
            / "src"
            / "Vfs.UnityWorker"
            / "bin"
            / "Release"
            / "net9.0-windows"
            / "Vfs.UnityWorker.dll",
        )
        path = next((candidate for candidate in candidates if candidate.is_file()), candidates[0])
    if path.suffix.casefold() == ".dll":
        return ("dotnet", str(path))
    return (str(path),)


class UnityWorkerClient:
    """封装请求文件、超时、退出码和 JSON 错误翻译。"""

    def __init__(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: float = 180,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        process_factory: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
    ):
        if not command:
            raise ValueError("worker command cannot be empty")
        self.command = tuple(str(value) for value in command)
        self.timeout_seconds = timeout_seconds
        self._runner = runner
        self._process_factory = process_factory

    @classmethod
    def discover(cls, project_root: Path) -> "UnityWorkerClient":
        return cls(default_worker_command(project_root))

    def handshake(self) -> dict:
        completed = self._run([*self.command, "handshake"])
        return self._parse_response(completed, expected_request_id=None)["result"]

    def diagnose(self, required_capabilities: Sequence[str] = ()) -> dict:
        """执行无副作用握手，并把可用性与协议不兼容转换为稳定诊断。"""

        base = {
            "command": list(self.command),
            "artifacts": self.artifact_identity(),
        }
        try:
            handshake = self.handshake()
        except UnityWorkerError as error:
            return {
                **base,
                "status": "unavailable",
                "error": {
                    "code": error.code,
                    "message": str(error),
                    "retryable": error.retryable,
                },
            }

        protocol = handshake.get("protocol") if isinstance(handshake, dict) else None
        capabilities = handshake.get("capabilities") if isinstance(handshake, dict) else None
        missing = sorted(
            set(required_capabilities)
            - set(capabilities if isinstance(capabilities, list) else [])
        )
        compatible = (
            isinstance(protocol, dict)
            and protocol.get("name") == PROTOCOL_NAME
            and protocol.get("version") == PROTOCOL_VERSION
            and isinstance(capabilities, list)
            and not missing
        )
        return {
            **base,
            "status": "ready" if compatible else "incompatible",
            "handshake": handshake,
            "missingCapabilities": missing,
        }

    def artifact_identity(self) -> list[dict]:
        """返回启动文件及 worker 发布目录身份，供上层缓存键使用。"""

        artifacts = []
        worker_directories: set[Path] = set()
        for value in self.command:
            path = Path(value)
            if not path.is_file():
                continue
            stat = path.stat()
            artifacts.append(
                {
                    "path": str(path.resolve()),
                    "size": stat.st_size,
                    "mtimeNs": stat.st_mtime_ns,
                }
            )
            if path.stem.casefold() == "vfs.unityworker":
                worker_directories.add(path.resolve().parent)

        # 解码逻辑主要位于依赖 DLL；只记录 apphost 会让 DLL 单独重编译后错误命中旧缓存。
        for directory in sorted(worker_directories, key=lambda item: str(item).casefold()):
            entries = []
            for path in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
                if not path.is_file():
                    continue
                stat = path.stat()
                entries.append(
                    {
                        "name": path.name,
                        "size": stat.st_size,
                        "mtimeNs": stat.st_mtime_ns,
                    }
                )
            encoded = json.dumps(entries, separators=(",", ":"), ensure_ascii=True).encode()
            artifacts.append(
                {
                    "kind": "directoryManifest",
                    "path": str(directory),
                    "fileCount": len(entries),
                    "sha256": hashlib.sha256(encoded).hexdigest(),
                }
            )
        return artifacts

    def request(
        self,
        operation: str,
        arguments: Mapping[str, object],
        *,
        request_id: str,
        cancel_event: object | None = None,
    ) -> dict:
        if not request_id.strip():
            raise ValueError("request_id cannot be empty")
        payload = {
            "protocolVersion": PROTOCOL_VERSION,
            "requestId": request_id,
            "operation": operation,
            "arguments": dict(arguments),
        }
        with tempfile.TemporaryDirectory(prefix="vfs-unity-worker-request-") as directory:
            request_path = Path(directory) / "request.json"
            request_path.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            completed = self._run(
                [*self.command, "request", str(request_path)],
                cancel_event=cancel_event,
            )
        return self._parse_response(completed, expected_request_id=request_id)["result"]

    def decode_character_template(
        self,
        *,
        input_path: Path,
        expected_id: str,
        request_id: str,
        cancel_event: object | None = None,
    ) -> dict:
        return self.request(
            "decodeCharacterTemplate",
            {"inputPath": str(input_path.resolve()), "expectedId": expected_id},
            request_id=request_id,
            cancel_event=cancel_event,
        )

    def decode_projectile_component(
        self,
        *,
        input_path: Path,
        output_directory: Path,
        container: str,
        projectile_id: str,
        request_id: str,
        cancel_event: object | None = None,
    ) -> dict:
        return self.request(
            "decodeProjectileComponent",
            {
                "inputPath": str(input_path.resolve()),
                "outputDirectory": str(output_directory.resolve()),
                "container": container,
                "expectedProjectileId": projectile_id,
            },
            request_id=request_id,
            cancel_event=cancel_event,
        )

    def export_monobehaviour_raw(
        self,
        *,
        input_path: Path,
        output_directory: Path,
        container: str,
        request_id: str,
        cancel_event: object | None = None,
    ) -> dict:
        return self.request(
            "exportMonoBehaviourRaw",
            {
                "inputPath": str(input_path.resolve()),
                "outputDirectory": str(output_directory.resolve()),
                "container": container,
            },
            request_id=request_id,
            cancel_event=cancel_event,
        )

    def export_monobehaviour_typetree_dump(
        self,
        *,
        input_path: Path,
        output_directory: Path,
        container: str,
        request_id: str,
        cancel_event: object | None = None,
    ) -> dict:
        return self.request(
            "exportMonoBehaviourTypeTreeDump",
            {
                "inputPath": str(input_path.resolve()),
                "outputDirectory": str(output_directory.resolve()),
                "container": container,
            },
            request_id=request_id,
            cancel_event=cancel_event,
        )

    def build_asset_map(
        self,
        *,
        input_path: Path,
        output_directory: Path,
        source_label: str,
        included_types: Sequence[str],
        request_id: str,
        cancel_event: object | None = None,
    ) -> dict:
        return self.request(
            "buildAssetMap",
            {
                "inputPath": str(input_path.resolve()),
                "outputDirectory": str(output_directory.resolve()),
                "sourceLabel": source_label,
                "includedTypes": list(included_types),
            },
            request_id=request_id,
            cancel_event=cancel_event,
        )

    def build_cab_map(
        self,
        *,
        inputs: Sequence[Mapping[str, str]],
        output_directory: Path,
        request_id: str,
        cancel_event: object | None = None,
    ) -> dict:
        return self.request(
            "buildCabMap",
            {
                "inputs": [
                    {
                        "inputId": value["inputId"],
                        "inputPath": str(Path(value["inputPath"]).resolve()),
                    }
                    for value in inputs
                ],
                "outputDirectory": str(output_directory.resolve()),
            },
            request_id=request_id,
            cancel_event=cancel_event,
        )

    def export_object_snapshots(
        self,
        *,
        inputs: Sequence[Mapping[str, str]],
        cab_map_path: Path,
        primary_input_id: str,
        selection_input_ids: Sequence[str],
        included_types: Sequence[str],
        containers: Sequence[str],
        output_directory: Path,
        request_id: str,
        cancel_event: object | None = None,
    ) -> dict:
        return self.request(
            "exportObjectSnapshots",
            {
                "inputs": [
                    {
                        "inputId": value["inputId"],
                        "inputPath": str(Path(value["inputPath"]).resolve()),
                    }
                    for value in inputs
                ],
                "cabMapPath": str(cab_map_path.resolve()),
                "primaryInputId": primary_input_id,
                "selectionInputIds": list(selection_input_ids),
                "includedTypes": list(included_types),
                "containers": list(containers),
                "outputDirectory": str(output_directory.resolve()),
            },
            request_id=request_id,
            cancel_event=cancel_event,
        )

    def export_identified_textures(
        self,
        *,
        inputs: Sequence[Mapping[str, str]],
        cab_map_path: Path,
        primary_input_id: str,
        selections: Sequence[Mapping[str, object]],
        output_directory: Path,
        request_id: str,
        cancel_event: object | None = None,
    ) -> dict:
        return self.request(
            "exportIdentifiedTextures",
            {
                "inputs": [
                    {
                        "inputId": value["inputId"],
                        "inputPath": str(Path(value["inputPath"]).resolve()),
                    }
                    for value in inputs
                ],
                "cabMapPath": str(cab_map_path.resolve()),
                "primaryInputId": primary_input_id,
                "selections": [
                    {
                        "sourceFile": str(value["sourceFile"]),
                        "pathId": int(value["pathId"]),
                    }
                    for value in selections
                ],
                "outputDirectory": str(output_directory.resolve()),
            },
            request_id=request_id,
            cancel_event=cancel_event,
        )

    def export_cubemap_faces(
        self,
        *,
        input_path: Path,
        output_directory: Path,
        container: str,
        request_id: str,
        cancel_event: object | None = None,
    ) -> dict:
        return self.request(
            "exportCubemapFaces",
            {
                "inputPath": str(input_path.resolve()),
                "outputDirectory": str(output_directory.resolve()),
                "container": container,
            },
            request_id=request_id,
            cancel_event=cancel_event,
        )

    def export_bundle_preview_media(
        self,
        *,
        input_path: Path,
        output_directory: Path,
        included_types: Sequence[str],
        request_id: str,
        cancel_event: object | None = None,
    ) -> dict:
        return self.request(
            "exportBundlePreviewMedia",
            {
                "inputPath": str(input_path.resolve()),
                "outputDirectory": str(output_directory.resolve()),
                "includedTypes": list(included_types),
            },
            request_id=request_id,
            cancel_event=cancel_event,
        )

    def export_animation_clip_json(
        self,
        *,
        input_path: Path,
        output_directory: Path,
        path_id: int,
        expected_name: str,
        request_id: str,
        cancel_event: object | None = None,
    ) -> dict:
        return self.request(
            "exportAnimationClipJson",
            {
                "inputPath": str(input_path.resolve()),
                "outputDirectory": str(output_directory.resolve()),
                "pathId": path_id,
                "expectedName": expected_name,
            },
            request_id=request_id,
            cancel_event=cancel_event,
        )

    def _run(
        self,
        command: Sequence[str],
        *,
        cancel_event: object | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if cancel_event is not None:
            return self._run_cancellable(command, cancel_event)
        try:
            return self._runner(
                list(command),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
                creationflags=worker_creation_flags(),
            )
        except FileNotFoundError as error:
            raise UnityWorkerError("worker_not_found", str(error), False) from error
        except subprocess.TimeoutExpired as error:
            raise UnityWorkerError(
                "worker_timeout",
                f"Unity worker timed out after {self.timeout_seconds:g} seconds",
                True,
            ) from error

    def _run_cancellable(
        self,
        command: Sequence[str],
        cancel_event: object,
    ) -> subprocess.CompletedProcess[str]:
        """运行单请求 worker；取消时终止并回收该请求独占的进程。"""

        is_set = getattr(cancel_event, "is_set", None)
        if not callable(is_set):
            raise TypeError("cancel_event must provide is_set()")
        if is_set():
            raise UnityWorkerError("worker_cancelled", "Unity worker request was cancelled", False)
        command_list = list(command)
        try:
            process = self._process_factory(
                command_list,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=worker_creation_flags(),
            )
        except FileNotFoundError as error:
            raise UnityWorkerError("worker_not_found", str(error), False) from error

        deadline = time.monotonic() + self.timeout_seconds
        while True:
            if is_set():
                self._terminate_process(process)
                raise UnityWorkerError(
                    "worker_cancelled",
                    "Unity worker request was cancelled",
                    False,
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._terminate_process(process)
                raise UnityWorkerError(
                    "worker_timeout",
                    f"Unity worker timed out after {self.timeout_seconds:g} seconds",
                    True,
                )
            try:
                stdout, stderr = process.communicate(timeout=min(0.05, remaining))
                return subprocess.CompletedProcess(
                    command_list,
                    process.returncode,
                    stdout,
                    stderr,
                )
            except subprocess.TimeoutExpired:
                continue

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str]) -> None:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()

    @staticmethod
    def _parse_response(
        completed: subprocess.CompletedProcess[str],
        *,
        expected_request_id: str | None,
    ) -> dict:
        try:
            response = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise UnityWorkerProtocolError(
                "invalid_worker_response",
                f"Unity worker returned invalid JSON (exit {completed.returncode}): "
                f"{completed.stderr.strip()}",
                False,
            ) from error
        if not isinstance(response, dict) or not isinstance(response.get("ok"), bool):
            raise UnityWorkerProtocolError(
                "invalid_worker_response",
                "Unity worker response is missing the boolean ok field",
                False,
            )
        if response.get("requestId") != expected_request_id:
            raise UnityWorkerProtocolError(
                "request_id_mismatch",
                f"Unity worker response requestId does not match {expected_request_id!r}",
                False,
            )
        if response["ok"]:
            if completed.returncode != 0 or "result" not in response:
                raise UnityWorkerProtocolError(
                    "invalid_worker_response",
                    f"Unity worker success response is inconsistent with exit {completed.returncode}",
                    False,
                )
            return response

        error = response.get("error")
        if not isinstance(error, dict):
            raise UnityWorkerProtocolError(
                "invalid_worker_response",
                "Unity worker failure response is missing error details",
                False,
            )
        raise UnityWorkerError(
            str(error.get("code") or "worker_error"),
            str(error.get("message") or "Unity worker failed"),
            bool(error.get("retryable", False)),
        )
