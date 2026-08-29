"""Per-run coordination shared by ordinary and Avatar model builds."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class ModelBuildSession:
    runs_root: Path
    kind: str
    record_id: int
    asset_index: int
    total_steps: int
    lod: int | None = None
    progress: Callable[[dict], None] | None = None
    request_id: str = field(init=False)
    steps: list[dict] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        if self.kind not in {"model", "avatar"}:
            raise ValueError(f"unsupported model build kind: {self.kind}")
        if self.kind == "avatar" and self.lod is None:
            raise ValueError("Avatar model build requires LOD")
        lod_part = f"-lod{self.lod}" if self.lod is not None else ""
        self.request_id = (
            f"{self.kind}-{self.record_id}-{self.asset_index}{lod_part}-"
            f"{time.time_ns()}-{uuid.uuid4().hex}"
        )

    @property
    def run_root(self) -> Path:
        return self.runs_root / self.request_id

    @property
    def cab_root(self) -> Path:
        return self.run_root / "cab-map"

    @property
    def object_root(self) -> Path:
        return self.run_root / "objects"

    @property
    def texture_root(self) -> Path:
        return self.run_root / "textures"

    def worker_request_id(self, stage: str) -> str:
        if stage not in {"cab", "objects", "textures"}:
            raise ValueError(f"unsupported model worker stage: {stage}")
        lod_part = f"-lod{self.lod}" if self.lod is not None else ""
        return (
            f"{self.kind}-{stage}-{self.record_id}-{self.asset_index}"
            f"{lod_part}-{time.time_ns()}"
        )

    def report(self, stage: str, completed: int) -> None:
        if self.progress is not None:
            self.progress({"stage": stage, "completed": completed, "total": self.total_steps})

    def add_step(self, name: str, worker_result: dict) -> None:
        self.steps.append({"name": name, "workerResult": worker_result})

    def metadata(self, *, version: int, source: dict, scope: str, **fields: object) -> dict:
        return {
            "version": version,
            "source": source,
            "selectedRun": self.request_id,
            "steps": list(self.steps),
            "builtAtEpoch": int(time.time()),
            "scope": scope,
            **fields,
        }
