"""Prepare stable HTTP file descriptions for derived model artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from raw_file_service import RawFileResponse, RawFileService


@dataclass(frozen=True)
class PreparedModelFileResponse:
    response: RawFileResponse
    headers: dict[str, str]


class ModelFileResponseService:
    def __init__(self, stream_chunk_size: int) -> None:
        self._raw = RawFileService(stream_chunk_size)

    def glb(
        self,
        asset: dict,
        path: Path,
        *,
        download: bool,
    ) -> PreparedModelFileResponse:
        name = f"{Path(str(asset['path'])).stem}.glb"
        return PreparedModelFileResponse(
            self._raw.prepare_path(
                path,
                download=download,
                download_name=name,
                content_type="model/gltf-binary",
            ),
            {"Cache-Control": "private, max-age=3600"},
        )

    def blend(self, artifact: object) -> PreparedModelFileResponse:
        return PreparedModelFileResponse(
            self._raw.prepare_path(
                artifact.path,
                download=True,
                download_name=artifact.name,
                content_type="application/x-blender",
            ),
            {
                "X-Endfield-Skipped-Animation-Count": str(
                    artifact.skipped_animation_count
                ),
                "Cache-Control": "private, max-age=3600",
            },
        )
