"""Assemble preview documents for exported BundleManifest assets."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping

from file_preview_service import FilePreviewService, guess_content_type
from manifest_worker_service import CUBEMAP_FACE_NAMES


class ManifestAssetPreviewService:
    def __init__(self, face_names: Iterable[str] = CUBEMAP_FACE_NAMES) -> None:
        self._face_names = tuple(face_names)

    def build_cubemap(
        self,
        bundle_record: dict,
        faces: Mapping[str, Path],
        asset_meta: dict,
        manifest_asset: dict,
        *,
        manifest_id: int | str,
        asset_index: int | str,
    ) -> dict:
        face_payload = []
        for face_name in self._face_names:
            target = faces[face_name]
            raw_url = self._raw_url(manifest_id, asset_index, face=face_name)
            face_payload.append({
                "name": face_name,
                "size": target.stat().st_size,
                "contentType": guess_content_type(target.name),
                "rawUrl": raw_url,
                "downloadUrl": f"{raw_url}&download=1",
            })
        default_face = next(item for item in face_payload if item["name"] == "PositiveZ")
        total_size = sum(item["size"] for item in face_payload)
        return {
            "file": {
                **bundle_record,
                "file_name": manifest_asset["path"],
                "length": total_size,
            },
            "resolvedFile": bundle_record,
            "usedFallback": False,
            "name": Path(str(manifest_asset["path"])).name,
            "size": total_size,
            "rawUrl": default_face["rawUrl"],
            "downloadUrl": default_face["downloadUrl"],
            "asset": asset_meta,
            "message": (
                f"来自 {manifest_asset['bundle_name']}，按 Unity Cubemap 面序导出"
            ),
            "kind": "cubemap",
            "faces": face_payload,
        }

    def build_file(
        self,
        bundle_record: dict,
        target: Path,
        asset_meta: dict,
        manifest_asset: dict,
        *,
        manifest_id: int | str,
        asset_index: int | str,
    ) -> dict:
        size = target.stat().st_size
        raw_url = self._raw_url(manifest_id, asset_index)
        base = {
            "file": {
                **bundle_record,
                "file_name": manifest_asset["path"],
                "length": size,
            },
            "resolvedFile": bundle_record,
            "usedFallback": False,
            "name": target.name,
            "size": size,
            "rawUrl": raw_url,
            "downloadUrl": f"{raw_url}&download=1",
            "asset": asset_meta,
            "message": f"来自 {manifest_asset['bundle_name']}",
        }
        return FilePreviewService.build_path(base, target)

    @staticmethod
    def _raw_url(
        manifest_id: int | str,
        asset_index: int | str,
        *,
        face: str | None = None,
    ) -> str:
        url = (
            f"/api/manifest-asset/raw?manifestId={manifest_id}"
            f"&assetIndex={asset_index}"
        )
        return f"{url}&face={face}" if face is not None else url
