"""Unity worker orchestration shared by ordinary and Avatar model snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from worker_run_service import validate_worker_artifacts


@dataclass(frozen=True)
class ModelBundleInput:
    input_id: str
    record: dict
    chunk_path: Path
    file_name: str


class ModelWorkerService:
    def __init__(
        self,
        worker: object,
        write_file_slice: Callable[[dict, Path, Path], None],
    ) -> None:
        self._worker = worker
        self._write_file_slice = write_file_slice

    def stage_inputs(
        self,
        run_root: Path,
        sources: Iterable[ModelBundleInput],
    ) -> list[dict[str, str]]:
        source_list = list(sources)
        if not source_list:
            raise ValueError("model worker requires at least one Bundle input")
        input_ids = [source.input_id for source in source_list]
        file_names = [source.file_name for source in source_list]
        if (
            any(not value for value in input_ids)
            or len(set(input_ids)) != len(input_ids)
            or len(set(file_names)) != len(file_names)
            or any(Path(name).name != name or not name for name in file_names)
        ):
            raise ValueError("model worker input identities are inconsistent")

        input_root = run_root / "inputs"
        input_root.mkdir(parents=True, exist_ok=True)
        staged = []
        for source in source_list:
            target = input_root / source.file_name
            self._write_file_slice(source.record, source.chunk_path, target)
            staged.append({"inputId": source.input_id, "inputPath": str(target)})
        return staged

    def build_cab_map(
        self,
        staged_inputs: list[dict[str, str]],
        output_root: Path,
        request_id: str,
        *,
        cancel_event: object | None = None,
    ) -> dict:
        result = self._worker.build_cab_map(
            inputs=staged_inputs,
            output_directory=output_root,
            request_id=request_id,
            cancel_event=cancel_event,
        )
        validate_worker_artifacts(output_root, result)
        return result

    def export_objects(
        self,
        staged_inputs: list[dict[str, str]],
        cab_map_path: Path,
        output_root: Path,
        request_id: str,
        *,
        primary_input_id: str,
        selection_input_ids: list[str],
        included_types: Iterable[str],
        containers: Iterable[str],
        cancel_event: object | None = None,
    ) -> dict:
        result = self._worker.export_object_snapshots(
            inputs=staged_inputs,
            cab_map_path=cab_map_path,
            primary_input_id=primary_input_id,
            selection_input_ids=selection_input_ids,
            included_types=list(included_types),
            containers=list(containers),
            output_directory=output_root,
            request_id=request_id,
            cancel_event=cancel_event,
        )
        validate_worker_artifacts(output_root, result)
        return result

    def export_textures(
        self,
        staged_inputs: list[dict[str, str]],
        cab_map_path: Path,
        output_root: Path,
        request_id: str,
        *,
        primary_input_id: str,
        selections: Iterable[dict],
        cancel_event: object | None = None,
    ) -> dict:
        if output_root.is_dir():
            try:
                next(output_root.iterdir())
            except StopIteration:
                output_root.rmdir()
            else:
                raise RuntimeError("model texture output directory is not empty")
        elif output_root.exists():
            raise RuntimeError("model texture output path is not a directory")
        result = self._worker.export_identified_textures(
            inputs=staged_inputs,
            cab_map_path=cab_map_path,
            primary_input_id=primary_input_id,
            selections=list(selections),
            output_directory=output_root,
            request_id=request_id,
            cancel_event=cancel_event,
        )
        validate_worker_artifacts(output_root, result)
        return result
