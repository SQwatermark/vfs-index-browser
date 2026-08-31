"""把 Manifest 资源解析为普通导出文件或 Cubemap 面集合。"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from assetbundle_browser import find_exported_file
from assetbundle_worker_service import manifest_asset_entries


class ManifestAssetFileError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ManifestAssetFile:
    bundle_record: dict
    target: Path
    asset: dict
    manifest_asset: dict


@dataclass(frozen=True)
class ManifestAssetCubemap:
    bundle_record: dict
    faces: Mapping[str, Path]
    asset: dict
    manifest_asset: dict


class ManifestAssetFileService:
    def __init__(
        self,
        ensure_assetbundle: Callable[[dict, Path], tuple[Path, dict] | None],
        ensure_monobehaviour: Callable[[dict, Path, dict], tuple[Path, dict] | None],
        ensure_cubemap: Callable[[dict, Path, dict], tuple[dict[str, Path], dict] | None],
    ) -> None:
        self._ensure_assetbundle = ensure_assetbundle
        self._ensure_monobehaviour = ensure_monobehaviour
        self._ensure_cubemap = ensure_cubemap

    def resolve_file(self, resolved_source: tuple) -> ManifestAssetFile | None:
        _, manifest_asset, bundle_record, bundle_chunk = resolved_source
        ensured = self._ensure_assetbundle(bundle_record, bundle_chunk)
        if ensured is None:
            # AssetBundle worker 已通过原回调发送结构化错误。
            return None
        export_root, meta = ensured
        matches = manifest_asset_entries(meta, manifest_asset["path"])
        if not matches:
            fallback = self._try_monobehaviour(
                bundle_record,
                bundle_chunk,
                manifest_asset,
            )
            if fallback is None:
                raise ManifestAssetFileError(
                    404,
                    "已解析对应 AssetBundle，但 AnimeStudio 暂不支持导出该资源类型。",
                )
            target, dump_meta = fallback
            asset = {
                "Type": "MonoBehaviourDump",
                "Name": Path(str(manifest_asset["path"])).stem,
                "Container": manifest_asset["path"],
                "Components": dump_meta.get("exportedFiles", []),
            }
            return ManifestAssetFile(
                bundle_record,
                target,
                asset,
                manifest_asset,
            )

        for entry in matches:
            found = find_exported_file(
                export_root,
                meta,
                str(entry.get("Type") or ""),
                str(entry.get("Name") or ""),
                str(entry.get("PathID") or ""),
            )
            if found is not None:
                target, asset = found
                return ManifestAssetFile(
                    bundle_record,
                    target,
                    asset,
                    manifest_asset,
                )
        raise ManifestAssetFileError(
            404,
            "已找到资源元数据，但对应的导出文件缺失。",
        )

    def resolve_cubemap(
        self,
        resolved_source: tuple,
    ) -> ManifestAssetCubemap | None:
        _, manifest_asset, bundle_record, bundle_chunk = resolved_source
        try:
            ensured = self._ensure_cubemap(
                bundle_record,
                bundle_chunk,
                manifest_asset,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            return None
        if ensured is None:
            return None
        faces, _ = ensured
        asset = {
            "Type": "Cubemap",
            "Name": Path(str(manifest_asset["path"])).stem,
            "Container": manifest_asset["path"],
        }
        return ManifestAssetCubemap(
            bundle_record,
            faces,
            asset,
            manifest_asset,
        )

    def _try_monobehaviour(
        self,
        bundle_record: dict,
        bundle_chunk: Path,
        manifest_asset: dict,
    ) -> tuple[Path, dict] | None:
        try:
            return self._ensure_monobehaviour(
                bundle_record,
                bundle_chunk,
                manifest_asset,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            return None
